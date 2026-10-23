"""Регрессии пяти интеграционных блокеров Foundation 04.2."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from xblago.agents.personal_assistant import PersonalAssistantAgent
from xblago.core.llm import ChatProvider, LlmProviderError
from xblago.core.memory import MemoryConflictError, SemanticMemoryRecord, is_sensitive_memory, redact_sensitive_text, relevant_memory
from xblago.core.messages import UserMessage
from xblago.core.semantic_memory import SemanticMemoryExtractor
from xblago.core.storage import SQLiteBlagoStore, TelegramIdentity
from xblago.integrations.telegram import TelegramBot
from xblago.integrations.web import WebApp


class Provider(ChatProvider):
    def __init__(self, reply="Для ремонта понадобится пинцет и набор ключей.", items=(), fail=False):
        self.reply, self.items, self.fail = reply, items, fail
        self.calls = []

    def generate(self, context):
        self.calls.append(context)
        if self.fail:
            raise LlmProviderError("synthetic failure")
        if context.user_text.startswith("Extract only durable personal memory"):
            return json.dumps({"items":self.items}, ensure_ascii=False)
        return self.reply


def record(key, value):
    return SemanticMemoryRecord("",key,value,"preference","fixture","confirmed",1.0,"","")


class Foundation042(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/"test.sqlite3"
        self.store = SQLiteBlagoStore(self.path)
        self.uid = self.store.resolve_telegram_user(TelegramIdentity(42,42,"private"))
        self.memory = self.store.memory_for_user(self.uid)

    def agent(self, provider=None):
        provider=provider or Provider()
        return PersonalAssistantAgent(self.memory,provider,memory_extractor=SemanticMemoryExtractor(provider))

    def say(self,text,provider=None):
        return self.agent(provider).respond(UserMessage(text)).text

    def test_чай_заменяется_кофе_с_историей_и_синонимами(self):
        self.memory.set("likes","чай")
        self.memory.save_semantic(record("preference:tea","чай"))
        self.say("Раньше я любил чай, теперь предпочитаю кофе")
        self.assertEqual(self.memory.all(),{"preference:drink":"кофе"})
        history=self.memory.semantic_history()
        self.assertEqual(sum(r.status=="confirmed" for r in history),1)
        self.assertEqual(sum(r.status=="superseded" and r.value=="чай" for r in history),2)
        self.assertTrue(any("previous_key=likes" in r.source for r in history))
        restarted=SQLiteBlagoStore(self.path)
        self.assertEqual(restarted.all_memory(self.uid),{"preference:drink":"кофе"})

    def test_стиль_ответов_обновляет_все_известные_ключи(self):
        self.memory.set("preferences","короткие ответы")
        self.memory.save_semantic(record("preference:examples","краткие объяснения"))
        self.say("Теперь мне удобнее подробные объяснения с примерами.")
        self.assertEqual(self.memory.all(),{"preference:response_style":"подробные объяснения с примерами"})
        self.assertEqual(sum(r.status=="superseded" for r in self.memory.semantic_history()),2)

    def test_смена_предпочтения_не_затрагивает_другую_тему(self):
        self.memory.set("likes","чай")
        self.memory.set("preferences","короткие ответы")
        self.say("Я не люблю чай, предпочитаю кофе")
        self.assertEqual(self.memory.all(),{"preferences":"короткие ответы","preference:drink":"кофе"})

    def test_неизвестная_замена_спрашивает_уточнение(self):
        self.memory.set("preference:walking","пешие прогулки")
        self.memory.set("preference:music","классическая музыка")
        before=self.memory.all()
        provider=Provider()
        response=self.say("Теперь я предпочитаю тишину",provider)
        self.assertIn("Какое предпочтение",response)
        self.assertEqual(self.memory.all(),before)
        self.assertEqual(provider.calls,[])
        self.say("замени классическая музыка на тишину")
        self.assertEqual(self.memory.all(),{"preference:walking":"пешие прогулки","preference:music":"тишину"})

    def test_несколько_совпадений_старого_значения_не_угадываются(self):
        self.memory.set("preference:first","чай с молоком")
        self.memory.set("preference:second","чай с лимоном")
        before=self.memory.all()
        self.assertIn("Какое предпочтение",self.say("Я не люблю чай, предпочитаю кофе"))
        self.assertEqual(self.memory.all(),before)

    def test_исправление_города_с_отрицанием(self):
        for text in ("Я больше не живу в Москве, теперь живу в Казани",
                     "Исправь, я теперь живу в Казани, а не в Москве."):
            with self.subTest(text=text):
                self.memory.clear()
                self.memory.set("город","Москва")
                response=self.say(text,Provider(reply="Теперь я помню, что ты живёшь в Москве."))
                self.assertEqual(self.memory.all(),{"city":"Казани"})
                self.assertNotIn("Москве",response)
                self.assertTrue(any(r.value=="Москва" and r.status=="superseded" for r in self.memory.semantic_history()))

    def test_гипотезы_цитаты_и_запрет_не_становятся_исправлением(self):
        self.memory.set("city","Москва")
        for text in ('«Я больше не живу в Москве, теперь живу в Казани»',
                     'Возможно, я больше не живу в Москве, теперь живу в Казани',
                     'Не запоминай: я больше не живу в Москве, теперь живу в Казани'):
            self.say(text)
            self.assertEqual(self.memory.all(),{"city":"Москва"})

    def test_сбой_провайдера_не_подтверждает_изменение(self):
        self.memory.set("likes","чай")
        response=self.say("Я не люблю чай, предпочитаю кофе",Provider(fail=True))
        self.assertEqual(self.memory.all(),{"likes":"чай"})
        self.assertIn("не получается связаться",response)

    def test_неподтвержденная_фраза_модели_не_выдается_за_запись(self):
        response=self.say("Возможно, я люблю кофе",Provider(reply="Запомнил, ты любишь кофе. Чем ещё помочь?"))
        self.assertNotIn("Запомнил",response)
        self.assertIn("Чем ещё помочь",response)
        self.assertEqual(self.memory.all(),{})

    def test_откат_обновления_включает_архивирование_и_все_алиасы(self):
        self.memory.set("likes","чай")
        self.memory.save_semantic(record("preference:tea","чай"))
        before=self.memory.all()
        history=self.memory.semantic_history()
        with self.store._locked_transaction() as db:
            db.execute("CREATE TRIGGER reject_new BEFORE INSERT ON semantic_memory WHEN NEW.status='confirmed' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.say("Раньше я любил чай, теперь предпочитаю кофе")
        self.assertEqual(self.memory.all(),before)
        self.assertEqual(self.memory.semantic_history(),history)

    def test_конкурентное_изменение_не_перезаписывается(self):
        self.memory.set("likes","чай")
        snapshot=self.memory.all()
        self.memory.set("city","Казань")
        with self.assertRaises(MemoryConflictError):
            self.memory.replace_semantic(record("preference:drink","кофе"),("likes",),snapshot)
        self.assertEqual(self.memory.all(),{"likes":"чай","city":"Казань"})

    def test_естественные_варианты_удаления_города(self):
        for text in ("Забудь мой город.","Не помни, где я живу", "Удали информацию о моём городе"):
            with self.subTest(text=text):
                self.memory.set("city","Москва")
                self.say("Я больше не живу в Москве, теперь живу в Казани")
                response=self.say(text)
                self.assertIn("Забыл",response)
                self.assertEqual(self.memory.all(),{})
                self.assertEqual(self.memory.semantic_history(),())

    def test_удаление_кофе_удаляет_всю_цепочку_версий(self):
        self.memory.set("likes","чай")
        self.say("Раньше я любил чай, теперь предпочитаю кофе")
        self.say("Забудь, что я предпочитаю кофе")
        self.assertEqual(self.memory.all(),{})
        self.assertEqual(self.memory.semantic_history(),())

    def test_неоднозначное_удаление_просит_уточнение(self):
        self.memory.set("city","Москва")
        self.memory.set("город","Казань")
        before=self.memory.all()
        response=self.say("Забудь мой город")
        self.assertIn("Что именно забыть",response)
        self.assertEqual(self.memory.all(),before)
        self.say("Забудь Москва")
        self.assertEqual(self.memory.all(),{"город":"Казань"})

    def test_удаление_ищет_также_устаревшие_значения(self):
        self.memory.set("likes","чай")
        self.say("Раньше я любил чай, теперь предпочитаю кофе")
        self.say("Забудь чай")
        self.assertEqual(self.memory.semantic_history(),())

    def test_запрет_не_мешает_ответу_и_не_вызывает_экстрактор(self):
        provider=Provider()
        response=self.say("Не запоминай это, но расскажи, как починить велосипед",provider)
        self.assertEqual(response,provider.reply)
        self.assertEqual(len(provider.calls),1)
        self.assertIn("починить велосипед",provider.calls[0].user_text)
        self.assertEqual(self.memory.all(),{})

    def test_запрет_блокирует_также_локальное_исправление(self):
        self.memory.set("likes","чай")
        self.say("Я не люблю чай, предпочитаю кофе, не запоминай это")
        self.assertEqual(self.memory.all(),{"likes":"чай"})

    def test_инструменты_и_объяснения_не_считаются_секретами(self):
        for text in ("Для ремонта нужен пинцет.","Можно использовать camping tools.",
                     "Что такое PIN-код?", "Объясни, что такое токен доступа."):
            with self.subTest(text=text):
                self.assertEqual(redact_sensitive_text(text),text)
                self.assertEqual(self.say("Какие инструменты нужны?",Provider(reply=text)),text)
        self.assertFalse(is_sensitive_memory("", "пинцет camping"))

    def test_настоящие_секреты_по_прежнему_скрываются(self):
        for text,secret in (("пароль = hunter2","hunter2"),("PIN-код: 1234","1234"),
                            ("токен hf_abcdefghijklmnopqrstuvwxyz","hf_abcdefghijklmnopqrstuvwxyz"),
                            ("4111 1111 1111 1111","4111 1111 1111 1111")):
            with self.subTest(text=text):
                self.assertNotIn(secret,redact_sensitive_text(text))
                self.assertTrue(is_sensitive_memory("",text))
                provider=Provider(reply=text)
                self.say("Не запоминай это, " + text,provider)
                self.assertNotIn(secret,provider.calls[0].user_text)
                self.assertEqual(self.memory.all(),{})
        self.assertTrue(is_sensitive_memory("api_token","abc"))
        agent=self.agent()
        agent.respond(UserMessage("remember my api_token is abc"))
        self.assertNotIn("abc",str(agent.history()))

    def test_синонимы_ключей_сводятся_к_одному_предпочтению(self):
        for key in ("preferences:drink", "like:tea", "drink_preference", "любимый_напиток"):
            with self.subTest(key=key):
                self.memory.clear()
                self.memory.set(key,"чай")
                self.say("Раньше я любил чай, теперь предпочитаю кофе")
                self.assertEqual(self.memory.all(),{"preference:drink":"кофе"})

    def test_старое_увлечение_можно_явно_заменить_предпочтением(self):
        self.memory.save_semantic(SemanticMemoryRecord("","interest:tea","чай","interest","fixture","confirmed",1.0,"",""))
        self.memory.save_semantic(SemanticMemoryRecord("","interest:books","книги","interest","fixture","confirmed",1.0,"",""))
        self.say("Я не люблю чай, предпочитаю кофе")
        self.assertEqual(self.memory.all(),{"interest:books":"книги","preference:drink":"кофе"})

    def test_удаление_предпочтения_не_удаляет_цель_с_тем_же_словом(self):
        self.memory.set("goal:buy","кофе")
        self.say("Забудь, что я предпочитаю кофе")
        self.assertEqual(self.memory.all(),{"goal:buy":"кофе"})

    def test_естественное_удаление_навыка(self):
        self.memory.save_semantic(SemanticMemoryRecord("","skill:python","Python","skill","fixture","confirmed",1.0,"",""))
        self.say("Забудь мой навык Python")
        self.assertEqual(self.memory.semantic_history(),())

    def test_повтор_того_же_предпочтения_не_создает_версию(self):
        self.say("Я предпочитаю кофе")
        history=self.memory.semantic_history()
        self.say("Я предпочитаю кофе")
        self.assertEqual(self.memory.semantic_history(),history)

    def test_канонические_предпочтения_находятся_без_короткой_истории(self):
        self.say("Я предпочитаю короткие ответы")
        self.say("Теперь мне удобнее подробные объяснения с примерами")
        restarted=SQLiteBlagoStore(self.path)
        selected=relevant_memory(restarted.all_memory(self.uid),"Какой мой формат ответов?")
        self.assertEqual(selected,{"preference:response_style":"подробные объяснения с примерами"})

    def test_удаление_из_веб_не_отменяется_текущим_ответом(self):
        started, release, deleted = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []
        class WaitingProvider(Provider):
            def generate(self, context):
                started.set()
                if not release.wait(3):
                    raise TimeoutError()
                return "Привет!"
        app=WebApp(self.store)
        def chat():
            try:
                app.chat(self.uid,"Меня зовут Анна")
            except Exception as exc:
                errors.append(exc)
        def remove():
            try:
                results.append(app.delete_memory(self.uid,"name"))
            except Exception as exc:
                errors.append(exc)
            finally:
                deleted.set()
        with patch("xblago.integrations.web.build_chat_provider",return_value=WaitingProvider()):
            first=threading.Thread(target=chat)
            second=threading.Thread(target=remove)
            first.start()
            try:
                self.assertTrue(started.wait(2))
                second.start()
                self.assertFalse(deleted.wait(.1))
            finally:
                release.set()
                first.join(4)
                if second.ident is not None:
                    second.join(4)
        self.assertEqual(errors,[])
        self.assertEqual(results,[True])
        self.assertEqual(self.memory.all(),{})

    def test_неизвестный_заменяемый_факт_не_придумывается(self):
        response=self.say("замени старое на новое")
        self.assertIn("Что именно нужно изменить",response)
        self.assertEqual(self.memory.all(),{})

    def test_отрицательное_значение_не_подменяет_положительное(self):
        self.memory.set("preference:drink","кофе")
        self.say("Я предпочитаю не чай")
        self.assertEqual(self.memory.all(),{"preference:drink":"кофе"})

    def test_повтор_ручного_сохранения_не_выдается_за_изменение(self):
        self.say("запомни city = Казань")
        response=self.say("запомни city = Казань")
        self.assertIn("уже есть",response)
        self.assertNotIn("Запомнил",response)

    def test_значения_секретов_скрываются_также_в_json_и_коротких_полях(self):
        for text, value in (('{"password":"hunter2"}',"hunter2"),
                            ('{"token":"access"}',"access"),
                            ("пароль = ab","ab"), ("пароль = foo.bar!","foo.bar!")):
            with self.subTest(text=text):
                self.assertNotIn(value,redact_sensitive_text(text))

    def test_ключи_api_вложенные_секреты_и_неполный_pem_скрываются(self):
        for text, secret in (("api_key = abc123","abc123"),
                             ("private_key = sensitive-value","sensitive-value"),
                             ('{"nested":[{"password":"a b c"}]}',"a b c"),
                             ('{"seed_phrase":"one two three four"}',"one two three four"),
                             ("-----BEGIN PRIVATE KEY-----\nSYNTHETIC-KEY-DATA","SYNTHETIC-KEY-DATA")):
            with self.subTest(text=text):
                self.assertTrue(is_sensitive_memory("",text))
                self.assertNotIn(secret,redact_sensitive_text(text))
                provider=Provider()
                self.say("Не запоминай это: " + text,provider)
                self.assertNotIn(secret,provider.calls[0].user_text)

    def test_единая_база_телеграм_веб_и_повторное_открытие(self):
        # Telegram and Mini App use this same verified Telegram identity resolver.
        app=WebApp(self.store)
        bot=TelegramBot(None,Path(self.tmp.name),store=self.store)
        provider=Provider()
        with patch("xblago.integrations.telegram.build_chat_provider",return_value=provider), patch(
            "xblago.integrations.web.build_chat_provider",return_value=provider):
            bot.replies_for_update({"update_id":1,"message":{"chat":{"id":42,"type":"private"},"from":{"id":42},"text":"Я предпочитаю короткие ответы"}})
            app.chat(self.uid,"Теперь мне удобнее подробные объяснения с примерами")
            restarted=SQLiteBlagoStore(self.path)
            self.assertEqual(restarted.all_memory(self.uid),{"preference:response_style":"подробные объяснения с примерами"})
            self.assertEqual(len(restarted.recent_history(self.uid,10)),4)
            app.chat(self.uid,"Забудь, что я предпочитаю подробные объяснения с примерами")
            self.assertEqual(self.memory.all(),{})
        other=self.store.resolve_telegram_user(TelegramIdentity(43,43,"private"))
        self.assertEqual(self.store.recent_history(other,10),())
