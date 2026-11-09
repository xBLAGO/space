"""Unified candidate regressions on the installed Identity/Help/Privacy/G09 stack."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch
from tools.help_readiness_lab import initialize
from tools.unified_mvp_check import run
from xblago.core.config import LlmConfig
from xblago.core.conversation import understand
from xblago.core.integration import preflight
from xblago.core.llm import OpenAICompatibleProvider
from xblago.integrations.web import WebApp


class UnifiedJourneyTests(unittest.TestCase):
    def test_одна_база_Web_Bot_Mini_App_Help_и_recovery(self):
        result=run()
        self.assertTrue(all(result['checks'].values()))
        self.assertEqual(result['extra_external_llm_calls'],0)
        self.assertEqual(result['gaps']['collective_fulfillment_statistics'].split(';')[0],'MISSING')
        self.assertEqual(result['real_pilot'],'NO-GO')


def completion(text,reason='stop'):
    r=MagicMock();r.__enter__.return_value=r
    r.read.return_value=json.dumps({'choices':[{'message':{'content':text},'finish_reason':reason}]}).encode()
    return r


class UnifiedAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'new';self.cfg=initialize(self.root)
        self.env=patch.dict(os.environ,self.cfg);self.env.start();self.addCleanup(self.env.stop)
        self.app=WebApp(preflight(self.root/'app/blago.sqlite3'))
        token=self.app.register('synthetic_unified_agent','synthetic-password-only','synthetic-help011-only')[0]
        self.uid=self.app.session(token)[0]
    def no_actions(self):
        with self.app.store._locked_transaction() as db:
            for table in ('help_requests','blago_needs','blago_resources','blago_payment_requests','blago_contribution_events'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
    def test_сорок_диалогов_с_активными_privacy_и_fencing(self):
        scenarios=json.loads((Path(__file__).parent/'fixtures/natural_dialogues.json').read_text())['scenarios']
        self.assertGreaterEqual(len(scenarios),30)
        for scenario in scenarios:
            self.app.store.clear_history(self.uid)
            for turn in scenario['turns']:
                with self.subTest(scenario=scenario['id'],turn=turn['user']):
                    history=self.app.store.recent_history(self.uid,24)
                    self.assertEqual(understand(turn['user'],history).intent.value,turn['intent'])
                    reply=self.app.chat(self.uid,turn['user'])
                    self.assertIsInstance(reply,str);self.assertTrue(reply.strip());self.no_actions()
    def test_память_предпочтения_исправление_и_отказ_в_едином_runtime(self):
        for text in ('Я живу в Москве','Я предпочитаю чай','Раньше я любил чай, теперь предпочитаю кофе','Я больше не живу в Москве, теперь живу в Казани'):
            self.app.chat(self.uid,text)
        facts=self.app.store.all_memory(self.uid);self.assertEqual(facts['city'],'Казани');self.assertEqual(facts['preference:drink'],'кофе')
        for text in ('Не запоминай это, но как починить велосипед?','Мой сосед живёт в Омске','Возможно, я предпочитаю какао','«Я живу в Самаре»'):
            self.app.chat(self.uid,text)
        self.assertEqual(self.app.store.all_memory(self.uid),facts)
        self.app.chat(self.uid,'Забудь мой город');self.assertNotIn('city',self.app.store.all_memory(self.uid));self.no_actions()
    def test_сбой_HF_и_восстановление_с_тем_же_пользователем(self):
        p=OpenAICompatibleProvider(LlmConfig('hf','synthetic','synthetic-key','https://example.invalid',10))
        failures=[urllib.error.HTTPError('https://example.invalid',503,'private',{},None),TimeoutError('private')]
        for failure in failures:
            with patch('xblago.integrations.web.build_chat_provider',return_value=p),patch('xblago.core.llm.urllib.request.urlopen',side_effect=failure) as calls:
                reply=self.app.chat(self.uid,'Почему цепь проскальзывает?');self.assertEqual(calls.call_count,1)
                self.assertNotIn('private',reply);self.assertNotIn('503',reply)
            with patch('xblago.integrations.web.build_chat_provider',return_value=p),patch('xblago.core.llm.urllib.request.urlopen',return_value=completion('Проверь износ цепи.')):
                self.assertEqual(self.app.chat(self.uid,'Что проверить сначала?'),'Проверь износ цепи.')
        self.assertEqual(len(self.app.history(self.uid)),8);self.no_actions()
    def test_пустой_и_испорченный_ответ_не_создают_память_или_действие(self):
        p=OpenAICompatibleProvider(LlmConfig('hf','synthetic','synthetic-key','https://example.invalid',10))
        bad=MagicMock();bad.__enter__.return_value=bad;bad.read.return_value=b'not-json'
        for response in (completion(None),completion(''),bad):
            with patch('xblago.integrations.web.build_chat_provider',return_value=p),patch('xblago.core.llm.urllib.request.urlopen',return_value=response):
                reply=self.app.chat(self.uid,'Как помочь соседу?');self.assertTrue(reply)
            self.assertFalse(self.app.store.all_memory(self.uid));self.no_actions()
    def test_ограниченное_продолжение_не_дублирует_историю(self):
        p=OpenAICompatibleProvider(LlmConfig('hf','synthetic','synthetic-key','https://example.invalid',10))
        with patch('xblago.integrations.web.build_chat_provider',return_value=p),patch('xblago.core.llm.urllib.request.urlopen',side_effect=[completion('Проверь натя','length'),completion('Проверь натяжение цепи.')]) as calls:
            self.assertEqual(self.app.chat(self.uid,'Как проверить цепь?'),'Проверь натяжение цепи.');self.assertEqual(calls.call_count,2)
        self.assertEqual(len(self.app.history(self.uid)),2);self.no_actions()
    def test_личные_факты_не_попадают_другому_агенту(self):
        self.app.chat(self.uid,'запомни city = Синтетический город')
        token=self.app.register('synthetic_unified_other','synthetic-password-only','synthetic-help011-only')[0];other=self.app.session(token)[0]
        self.assertNotIn('city',self.app.store.all_memory(other));self.assertEqual(self.app.history(other),[])

    def test_модельное_извлечение_навыка_через_тот_же_fenced_контекст(self):
        p=OpenAICompatibleProvider(LlmConfig('hf','synthetic','synthetic-key','https://example.invalid',10))
        payload=json.dumps({'items':[{'key':'skill:python','value':'Python','category':'skill','claim':'assertion','subject':'self','confidence':1.0,'evidence':'Я умею писать на Python'}]})
        with patch('xblago.integrations.web.build_chat_provider',return_value=p),patch('xblago.core.llm.urllib.request.urlopen',side_effect=[completion('Можно обсудить твой опыт.'),completion(payload)]) as calls:
            self.app.chat(self.uid,'Я умею писать на Python')
            self.assertEqual(calls.call_count,2)
        self.assertEqual(self.app.store.all_memory(self.uid).get('skill:python'),'Python');self.no_actions()
