"""Synthetic Russian dialogue regressions; model quality is evaluated separately."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xblago.agents.personal_assistant import PersonalAssistantAgent, _without_unconfirmed_claims
from xblago.core.conversation import understand, model_history, SERVICE_STATUS_TEXTS
from xblago.core.llm import ChatProvider, ChatContext, OpenAICompatibleProvider
from xblago.core.config import LlmConfig
from xblago.core.messages import ConversationTurn, UserMessage
from xblago.core.storage import SQLiteBlagoStore, TelegramIdentity
from xblago.integrations.web import WebApp
from xblago.integrations.telegram import TelegramBot

SCENARIOS = json.loads((Path(__file__).parent/'fixtures/natural_dialogues.json').read_text())['scenarios']
ECONOMIC_TABLES = ('blago_needs', 'blago_resources', 'blago_resource_offers',
                   'blago_payment_requests', 'blago_fulfillment_events', 'blago_contribution_events')


class Recorder(ChatProvider):
    def __init__(self, reply='Можно обсудить это без каких-либо действий.'):
        self.contexts = []
        self.reply = reply

    def generate(self, context):
        self.contexts.append(context)
        return self.reply


class NaturalDialogueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/'synthetic.sqlite3'
        self.store = SQLiteBlagoStore(self.path)
        self.uid = self.store.resolve_telegram_user(TelegramIdentity(1000000001,1000000001,'private'))
        self.app = WebApp(self.store)

    def test_повторное_открытие_и_перезапуск_не_добавляют_приветствие(self):
        provider = Recorder('Для начала проверь натяжение цепи.')
        with patch('xblago.integrations.web.build_chat_provider',return_value=provider):
            self.app.chat(self.uid,'Расскажи, как проверить цепь велосипеда')
            before = self.app.history(self.uid)
            restarted = WebApp(SQLiteBlagoStore(self.path))
            self.assertEqual(restarted.history(self.uid),before)
            self.assertEqual(len(provider.contexts),1)
            restarted.chat(self.uid,'А если она провисает?')
        latest = provider.contexts[-1]
        self.assertTrue(latest.conversation.continuing)
        self.assertEqual(tuple(t.text for t in latest.history),tuple(t['text'] for t in before))
        self.assertEqual(sum(t.text=='А если она провисает?' for t in latest.history),0)

    def test_telegram_start_продолжает_существующий_диалог_без_llm(self):
        self.store.append_exchange(self.uid,(ConversationTurn('user','Расскажи про звёзды'),ConversationTurn('assistant','Звёзды излучают свет.')))
        bot = TelegramBot(None,self.path.parent,store=self.store)
        with patch('xblago.integrations.telegram.build_chat_provider',return_value=Recorder()) as factory:
            replies = bot.replies_for_update({'update_id':1,'message':{'from':{'id':1000000001},'chat':{'id':1000000001,'type':'private'},'text':'/start'}})
        self.assertNotIn('Привет',replies[0]['text'])
        self.assertEqual(len(self.store.recent_history(self.uid,100)),2)
        self.assertEqual(factory.call_count,1)

    def test_краткое_приветствие_без_обязательного_вопроса(self):
        agent = PersonalAssistantAgent(self.store.memory_for_user(self.uid))
        self.assertEqual(agent.respond(UserMessage('привет')).text,'Привет.')
        self.assertEqual(agent.respond(UserMessage('привет')).text,'Я здесь.')

    def test_telegram_и_web_используют_одинаковый_контекст_после_restart(self):
        provider = Recorder('Короткий ответ по теме.')
        with patch('xblago.integrations.web.build_chat_provider',return_value=provider), patch('xblago.integrations.telegram.build_chat_provider',return_value=provider):
            self.app.chat(self.uid,'Объясни устройство велосипеда')
            restarted = SQLiteBlagoStore(self.path)
            bot = TelegramBot(None,self.path.parent,store=restarted)
            update = {'update_id':21,'message':{'from':{'id':1000000001},'chat':{'id':1000000001,'type':'private'},'text':'А тормоза?'}}
            reply = bot.replies_for_update(update)
            self.assertEqual(bot.replies_for_update(update),reply)
        self.assertEqual(len(provider.contexts),2)
        self.assertEqual(provider.contexts[1].history[0].text,'Объясни устройство велосипеда')
        self.assertEqual(len(restarted.recent_history(self.uid,100)),4)

    def test_пара_истории_атомарна_при_сбое_второй_записи(self):
        with self.store._locked_transaction() as db:
            db.execute("CREATE TRIGGER synthetic_failure BEFORE INSERT ON dialogue_turns WHEN NEW.role='assistant' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.app.chat(self.uid,'Почему Луна видна днём?')
        self.assertEqual(self.store.recent_history(self.uid,100),())
        with self.store._locked_transaction() as db:
            db.execute('DROP TRIGGER synthetic_failure')
        self.app.chat(self.uid,'Почему Луна видна днём?')
        self.assertEqual(len(self.store.recent_history(self.uid,100)),2)

    def test_двое_участников_не_разделяют_память_и_историю(self):
        other = self.store.resolve_telegram_user(TelegramIdentity(1000000002,1000000002,'private'))
        self.store.memory_for_user(self.uid).set('city','Синтетический город A')
        self.store.memory_for_user(other).set('city','Синтетический город B')
        provider = Recorder()
        with patch('xblago.integrations.web.build_chat_provider',return_value=provider):
            self.app.chat(self.uid,'Где я живу?')
            self.app.chat(other,'Где я живу?')
        self.assertEqual(provider.contexts[0].memory['city'],'Синтетический город A')
        self.assertEqual(provider.contexts[1].memory['city'],'Синтетический город B')
        self.assertEqual(provider.contexts[1].history,())

    def test_обычные_слова_не_служат_запретом_ответа(self):
        for text in ('Я забыл, как называется эта деталь.', 'Он сохранил файл и закрыл редактор.',
                     'Чтобы обновить настройки, открой меню.', 'В рассказе герой сказал: «Запомнил».',
                     'Я изменил программу, чтобы она читала данные.'):
            self.assertEqual(_without_unconfirmed_claims(text),text)

    def test_предпочтение_стиля_применяется_к_обычному_вопросу(self):
        context = ChatContext(PersonalAssistantAgent.profile,'Почему Луна видна днём?',
                              {'preference:response_style':'кратко','api_token':'sensitive-fixture','city':'Нерелевантный город',
                               'preference:drink':'Нерелевантный напиток','preference:hobby':'Личное увлечение'})
        provider = OpenAICompatibleProvider(LlmConfig('hf','synthetic','synthetic','https://example.invalid',7))
        prompt = provider._system_prompt(context)
        self.assertIn('кратко',prompt)
        self.assertNotIn('sensitive-fixture',prompt)
        self.assertNotIn('Нерелевантный город',prompt)
        self.assertNotIn('Нерелевантный напиток',prompt)
        self.assertNotIn('Личное увлечение',prompt)

    def test_отказ_ложному_подтверждению_не_путает_платёж_с_памятью(self):
        self.assertEqual(_without_unconfirmed_claims('Я перевёл деньги.'),'Не могу подтвердить выполнение этого действия.')
        self.assertEqual(_without_unconfirmed_claims('I have submitted the request.'),"I can't confirm that this action was carried out.")

    def test_скопированный_моделью_статус_сбоя_не_разрешает_обновление_памяти(self):
        memory=self.store.memory_for_user(self.uid);memory.set('city','Казань')
        provider=Recorder('Сейчас не получается связаться. Твоё сообщение осталось в диалоге.')
        agent=PersonalAssistantAgent(memory,provider)
        answer=agent.respond(UserMessage('Я живу в Москве'))
        self.assertEqual(memory.all(),{'city':'Казань'})
        self.assertEqual(len(agent.history()),2)
        self.assertEqual(answer.text,provider.reply)

    def test_неподтверждённые_сохранения_и_внешние_действия_не_подтверждаются(self):
        for text in ('Запомнил, ты любишь кофе.', 'Теперь я помню, что ты живёшь в Москве.',
                     'Я сохранил твою информацию.', 'Я удалил факт.', 'Готово, я создал заявку.',
                     'Я опубликовал твой номер.', 'Я перевёл деньги.', 'Заявка отправлена.',
                     'I have saved your name.', 'I have submitted the request.'):
            with self.subTest(text=text):
                self.assertNotEqual(_without_unconfirmed_claims(text),text)
        for text in ('Заявку можно создать после подтверждения.', 'Не отправляй деньги незнакомым людям.',
                     'Он сказал: «Я создал заявку».', 'Я написал пример кода ниже.'):
            self.assertEqual(_without_unconfirmed_claims(text),text)

    def test_ограничение_контекста_не_обрезает_хранимые_реплики(self):
        turns = tuple(ConversationTurn(role,('тема '+str(n)+' ')*1000) for n in range(20) for role in ('user','assistant'))
        selected = model_history(turns)
        self.assertLessEqual(sum(len(t.text) for t in selected),24000)
        self.assertEqual(selected[0].role,'user')
        self.assertTrue(all(t in turns for t in selected))
        self.assertEqual(len(turns),40)

    def test_служебные_сбои_не_выдаются_за_содержание_беседы(self):
        status='Сейчас не получается связаться. Твоё сообщение осталось в диалоге.'
        history=(ConversationTurn('user','Расскажи про радугу'),ConversationTurn('assistant',status),
                 ConversationTurn('user','Мне грустно'),ConversationTurn('assistant',status))
        provider=OpenAICompatibleProvider(LlmConfig('hf','synthetic','synthetic','https://example.invalid',7))
        messages=provider._messages(ChatContext(PersonalAssistantAgent.profile,'Продолжим разговор',{},history))
        self.assertNotIn(status,str(messages))
        self.assertEqual([m['content'] for m in messages if m['role']=='user'],['Расскажи про радугу','Мне грустно','Продолжим разговор'])
        self.assertEqual(len(history),4)

    def test_проекция_не_фильтрует_обычные_слова_или_слова_пользователя(self):
        status=next(iter(SERVICE_STATUS_TEXTS))
        turns=(ConversationTurn('user',status),ConversationTurn('assistant','Не получается связаться с другом? Причины могут быть разными.'))
        self.assertEqual(model_history(turns),turns)


def dialogue_test(scenario):
    def run(self):
        provider = Recorder()
        agent = PersonalAssistantAgent(self.store.memory_for_user(self.uid),provider,
            exchange_recorder=lambda turns:self.store.append_exchange(self.uid,turns))
        for index, turn in enumerate(scenario['turns']):
            with self.subTest(turn=index):
                hint = understand(turn['user'],agent.history())
                self.assertEqual(hint.intent.value,turn['intent'])
                before = len(provider.contexts)
                agent.respond(UserMessage(turn['user']))
                self.assertEqual(len(provider.contexts),before+1)  # no classifier LLM call
                received = provider.contexts[-1]
                self.assertEqual(received.conversation,hint)
                self.assertEqual(received.history,tuple(agent.history()[:-2]))
                self.assertEqual(len(self.store.recent_history(self.uid,100)),2*(index+1))
                with self.store._connect() as db:
                    for table in ECONOMIC_TABLES:
                        self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0],0)
    return run


for _scenario in SCENARIOS:
    setattr(NaturalDialogueTests,'test_диалог_'+_scenario['id'],dialogue_test(_scenario))
