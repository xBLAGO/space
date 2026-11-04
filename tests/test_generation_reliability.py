"""Adversarial provider payloads, bounded repair, no partial writes/actions."""
from __future__ import annotations

import json
import urllib.error
import unittest
from unittest.mock import MagicMock, patch

from test_personal_assistant import InMemoryStore
from xblago.agents.personal_assistant import PersonalAssistantAgent
from xblago.core.config import LlmConfig
from xblago.core.llm import ChatContext, LlmProviderError, OpenAICompatibleProvider
from xblago.core.messages import ConversationTurn, UserMessage
from xblago.core.semantic_memory import SemanticMemoryExtractor


def response(text, finish='stop', **message):
    mocked = MagicMock()
    mocked.__enter__.return_value = mocked
    mocked.read.return_value = json.dumps({'choices':[{'message':{'content':text,**message},'finish_reason':finish}]}).encode()
    return mocked


class GenerationReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.provider = OpenAICompatibleProvider(LlmConfig('hf','Qwen/Qwen3-14B:cheapest','synthetic-secret',
            'https://example.invalid',7,max_tokens=768,thinking='off'))
        self.context = ChatContext(PersonalAssistantAgent.profile,'Почему цепь перескакивает?',{})

    def test_length_продолжается_один_раз_и_не_дублирует_префикс(self):
        with patch('xblago.core.llm.urllib.request.urlopen',side_effect=[response('Проверь цепь и натя','length'),response('Проверь цепь и натяжение троса.')]) as call:
            result = self.provider.generate(self.context)
        self.assertEqual(result,'Проверь цепь и натяжение троса.')
        self.assertEqual(call.call_count,2)
        payload = json.loads(call.call_args_list[1].args[0].data)
        self.assertNotIn('tools',payload)
        self.assertEqual(payload['messages'][-2]['role'],'assistant')
        self.assertLess(call.call_args_list[1].kwargs['timeout'],call.call_args_list[0].kwargs['timeout'])

    def test_overlap_продолжение_без_повтора(self):
        prefix = 'Проверь цепь, затем натяжение троса'
        with patch('xblago.core.llm.urllib.request.urlopen',side_effect=[response(prefix,'length'),response('натяжение троса и крепления.')]):
            self.assertEqual(self.provider.generate(self.context),prefix+' и крепления.')

    def test_неоднозначный_стык_слов_не_угадывается(self):
        with patch('xblago.core.llm.urllib.request.urlopen',side_effect=[response('Проверь велоси','length'),response('пед и цепь.')]) as call:
            with self.assertRaises(LlmProviderError):
                self.provider.generate(self.context)
        self.assertEqual(call.call_count,2)

    def test_повторный_length_не_создаёт_retry_цикл(self):
        with patch('xblago.core.llm.urllib.request.urlopen',return_value=response('Оборванная мысль','length')) as call:
            with self.assertRaises(LlmProviderError):
                self.provider.generate(self.context)
        self.assertEqual(call.call_count,2)

    def test_неизменённый_префикс_не_считается_успешным_завершением(self):
        with patch('xblago.core.llm.urllib.request.urlopen',side_effect=[response('Проверь натя','length'),response('Проверь натя')]) as call:
            with self.assertRaises(LlmProviderError):
                self.provider.generate(self.context)
        self.assertEqual(call.call_count,2)

    def test_бюджет_единственного_repair_ограничен(self):
        with patch('xblago.core.llm.urllib.request.urlopen',side_effect=[response('Проверь натя','length'),response('Проверь натяжение.')]) as call:
            self.provider.generate(self.context)
        budgets = [json.loads(c.args[0].data)['max_tokens'] for c in call.call_args_list]
        self.assertEqual(budgets,[768,1536])

    def test_missing_finish_reason_при_исчерпанном_бюджете_обнаруживается(self):
        first=response('Неполный ответ',None)
        body=json.loads(first.read.return_value);body['usage']={'completion_tokens':768}
        first.read.return_value=json.dumps(body).encode()
        with patch('xblago.core.llm.urllib.request.urlopen',side_effect=[first,response('Неполный ответ теперь закончен.')]) as call:
            self.assertEqual(self.provider.generate(self.context),'Неполный ответ теперь закончен.')
        self.assertEqual(call.call_count,2)

    def test_tls_проверка_включена_и_использует_доверенный_ca(self):
        import ssl
        self.assertEqual(self.provider.ssl_context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(self.provider.ssl_context.check_hostname)
        with patch('xblago.core.llm.urllib.request.urlopen',return_value=response('Ответ.')) as call:
            self.provider.generate(self.context)
        self.assertIs(call.call_args.kwargs['context'],self.provider.ssl_context)

    def test_чрезмерный_ответ_и_служебные_токены_не_выдаются(self):
        oversized=response('unused');oversized.read.return_value=b'x'*131073
        for bad in (oversized,response('<|im_start|>assistant PRIVATE_REASONING')):
            with self.subTest(),patch('xblago.core.llm.urllib.request.urlopen',return_value=bad) as call:
                with self.assertRaises(LlmProviderError):
                    self.provider.generate(self.context)
                self.assertEqual(call.call_count,1)

    def test_неоконченный_code_fence_обнаруживается_при_stop(self):
        first = 'Пример:\n```python\nprint(1)'
        final = first+'\n```'
        with patch('xblago.core.llm.urllib.request.urlopen',side_effect=[response(first),response(final)]) as call:
            self.assertEqual(self.provider.generate(self.context),final)
        self.assertEqual(call.call_count,2)

    def test_ошибка_продолжения_не_меняет_память_и_не_пишет_частичный_ответ(self):
        memory = InMemoryStore();memory.set('city','Казань')
        agent = PersonalAssistantAgent(memory,self.provider)
        with patch('xblago.core.llm.urllib.request.urlopen',side_effect=[response('Неполный секретный ответ','length'),TimeoutError('synthetic-secret')]) as call:
            answer = agent.respond(UserMessage('Я живу в Москве'))
        self.assertEqual(memory.all(),{'city':'Казань'})
        self.assertNotIn('Неполный секретный ответ',str(agent.history()))
        self.assertNotIn('synthetic-secret',answer.text)
        self.assertEqual(len(agent.history()),2)
        self.assertEqual(call.call_count,2)

    def test_успешный_repair_записывает_факт_и_историю_ровно_один_раз(self):
        memory=InMemoryStore();agent=PersonalAssistantAgent(memory,self.provider)
        with patch.object(memory,'set',wraps=memory.set) as saved,patch('xblago.core.llm.urllib.request.urlopen',side_effect=[response('Продолжим бес','length'),response('Продолжим беседу о городе.')]) as call:
            agent.respond(UserMessage('Я живу в Казани'))
        self.assertEqual(memory.all(),{'city':'Казани'})
        self.assertEqual(saved.call_count,1)
        self.assertEqual(call.call_count,2)
        self.assertEqual(len(agent.history()),2)

    def test_503_является_временным_отказом_без_автоповтора(self):
        error = urllib.error.HTTPError('https://example.invalid',503,'private body',{},None)
        with patch('xblago.core.llm.urllib.request.urlopen',side_effect=error) as call:
            with self.assertRaises(LlmProviderError) as raised:
                self.provider.generate(self.context)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.status,503)
        self.assertEqual(call.call_count,1)
        self.assertNotIn('private body',str(raised.exception))

    def test_503_затем_восстановление_диалога_без_потери_вопроса(self):
        memory = InMemoryStore();agent = PersonalAssistantAgent(memory,self.provider)
        error = urllib.error.HTTPError('https://example.invalid',503,'private body',{},None)
        with patch('xblago.core.llm.urllib.request.urlopen',side_effect=[error,response('Проверь износ цепи.')]) as call:
            agent.respond(UserMessage('Почему цепь перескакивает?'))
            answer = agent.respond(UserMessage('Вернёмся к моему вопросу'))
        payload = json.loads(call.call_args_list[1].args[0].data)
        self.assertEqual(answer.text,'Проверь износ цепи.')
        self.assertEqual(sum(m['content']=='Почему цепь перескакивает?' for m in payload['messages']),1)
        self.assertEqual(payload['messages'][-1]['content'],'Вернёмся к моему вопросу')
        self.assertEqual(len(agent.history()),4)
        self.assertEqual(memory.all(),{})

    def test_null_пустота_reasoning_only_и_неверный_тип_не_выдаются(self):
        for content in (None,'', ' ',[],{},42):
            with self.subTest(content=content), patch('xblago.core.llm.urllib.request.urlopen',return_value=response(content,reasoning_content='PRIVATE_REASONING')) as call:
                with self.assertRaises(LlmProviderError):
                    self.provider.generate(self.context)
                self.assertEqual(call.call_count,1)

    def test_qwen_think_отделяется_от_видимого_ответа(self):
        with patch('xblago.core.llm.urllib.request.urlopen',return_value=response('<think>PRIVATE_REASONING</think>\nПроверь цепь.',reasoning_content='PRIVATE_REASONING')):
            self.assertEqual(self.provider.generate(self.context),'Проверь цепь.')
        for content in ('<think>PRIVATE_REASONING','PRIVATE_REASONING</think>Ответ','<think>PRIVATE_REASONING</think>'):
            with self.subTest(content=content),patch('xblago.core.llm.urllib.request.urlopen',return_value=response(content)):
                with self.assertRaises(LlmProviderError):
                    self.provider.generate(self.context)

    def test_tool_calls_не_становятся_действиями_или_подтверждениями(self):
        for finish in ('tool_calls','function_call','content_filter'):
            with self.subTest(finish=finish),patch('xblago.core.llm.urllib.request.urlopen',return_value=response('Готово, опубликовал.',finish)):
                with self.assertRaises(LlmProviderError):
                    self.provider.generate(self.context)
        with patch('xblago.core.llm.urllib.request.urlopen',return_value=response('Готово.',tool_calls=[{'function':{'name':'publish'}}])):
            with self.assertRaises(LlmProviderError):
                self.provider.generate(self.context)

    def test_извлечение_памяти_не_использует_разговорный_prompt_или_repair(self):
        extractor = SemanticMemoryExtractor(self.provider)
        ctx = ChatContext(PersonalAssistantAgent.profile,'Я умею писать на Python',{})
        with patch('xblago.core.llm.urllib.request.urlopen',return_value=response('{"items":[','length')) as call:
            with self.assertRaises(LlmProviderError):
                extractor.extract(ctx)
        self.assertEqual(call.call_count,1)
        prompt = json.loads(call.call_args.args[0].data)['messages'][0]['content']
        self.assertIn('Return JSON only',prompt)
        self.assertNotIn('conversational hint',prompt)

    def test_завершённые_короткие_ответы_не_требуют_пунктуации_или_repair(self):
        for answer in ('Да','42','```python\nprint(1)\n```','Запомнить — не значит понять.'):
            with self.subTest(answer=answer),patch('xblago.core.llm.urllib.request.urlopen',return_value=response(answer)) as call:
                self.assertEqual(self.provider.generate(self.context),answer)
                self.assertEqual(call.call_count,1)

    def test_нет_отдельного_классификатора_или_ненужного_вопроса_в_протоколе(self):
        ctx = ChatContext(PersonalAssistantAgent.profile,'Спасибо',{},(ConversationTurn('user','Расскажи о радуге'),ConversationTurn('assistant','Объяснение.')))
        with patch('xblago.core.llm.urllib.request.urlopen',return_value=response('Пожалуйста.')) as call:
            self.assertEqual(self.provider.generate(ctx),'Пожалуйста.')
        self.assertEqual(call.call_count,1)
        prompt = json.loads(call.call_args.args[0].data)['messages'][0]['content']
        self.assertIn('without a follow-up question',prompt)
        self.assertIn('existing conversation',prompt)
        self.assertNotIn('quietly proactive',prompt)

    def test_сбой_сети_и_невалидный_json_не_раскрывают_payload(self):
        for failure in (TimeoutError('synthetic-secret'),ConnectionResetError('synthetic-secret'),urllib.error.URLError('synthetic-secret')):
            with self.subTest(failure=type(failure).__name__),patch('xblago.core.llm.urllib.request.urlopen',side_effect=failure) as call:
                with self.assertRaises(LlmProviderError) as raised:
                    self.provider.generate(self.context)
                self.assertNotIn('synthetic-secret',str(raised.exception))
                self.assertEqual(call.call_count,1)
        mocked = response('unused');mocked.read.return_value = b'{broken PRIVATE_RESPONSE'
        with patch('xblago.core.llm.urllib.request.urlopen',return_value=mocked):
            with self.assertRaises(LlmProviderError) as raised:
                self.provider.generate(self.context)
        self.assertNotIn('PRIVATE_RESPONSE',str(raised.exception))

    def test_остаток_общего_deadline_не_разрешает_новый_repair(self):
        with patch('xblago.core.llm.time.monotonic',side_effect=[0,1,9]),patch('xblago.core.llm.urllib.request.urlopen',return_value=response('Оборванный ответ','length')) as call:
            with self.assertRaises(LlmProviderError):
                self.provider.generate(self.context)
        self.assertEqual(call.call_count,1)
