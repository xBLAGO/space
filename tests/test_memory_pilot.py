"""Регрессии безопасной персональной памяти, только временные SQLite и fake LLM."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xblago.agents.personal_assistant import PersonalAssistantAgent
from xblago.core.llm import ChatProvider, LlmProviderError
from xblago.core.memory import JsonMemoryStore, SemanticMemoryRecord
from xblago.core.messages import UserMessage
from xblago.core.semantic_memory import SemanticMemoryExtractor
from xblago.core.storage import SQLiteBlagoStore, TelegramIdentity
from xblago.integrations.web import WebApp


class Provider(ChatProvider):
    def __init__(self, items=(), failure=None, reply="Я рядом."):
        self.items, self.failure, self.reply = items, failure, reply
        self.calls = []

    def generate(self, context):
        self.calls.append(context)
        if self.failure:
            raise self.failure
        if context.user_text.startswith("Extract only durable personal memory"):
            return json.dumps({"items": self.items}, ensure_ascii=False)
        return self.reply


def item(key="skill:python", value="Python", evidence="Я умею писать на Python", **kwargs):
    return dict(key=key, value=value, evidence=evidence, category="skill",
                claim="assertion", subject="self", confidence=0.95, **kwargs)


def record(key, value, category="fact", status="confirmed"):
    return SemanticMemoryRecord("", key, value, category, "llm_extraction", status, .95, "", "")


class БезопаснаяПамять(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "test.sqlite3"
        self.store = SQLiteBlagoStore(self.path)
        self.uid = self.store.resolve_telegram_user(TelegramIdentity(1, 1, "private"))
        self.memory = self.store.memory_for_user(self.uid)

    def agent(self, provider=None):
        provider = provider or Provider()
        return PersonalAssistantAgent(
            self.memory, provider, memory_extractor=SemanticMemoryExtractor(provider),
            history_recorder=lambda t: self.store.append_history(self.uid, t.role, t.text),
        )

    def test_исправление_перекрывает_семантический_факт(self):
        self.memory.save_semantic(record("city", "Москва"))
        agent = self.agent()
        agent.respond(UserMessage("исправь city на Казань"))
        self.assertEqual(self.memory.all(), {"city": "Казань"})
        self.assertEqual(self.store.all_semantic_memory(self.uid, include_inactive=True)[0].status, "superseded")
        self.memory.save_semantic(record("city", "Тула"))
        self.assertEqual(self.memory.all(), {"city": "Тула"})
        with self.store._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_facts").fetchone()[0], 0)

    def test_удаление_всех_версий_с_двоеточием(self):
        self.memory.save_semantic(record("skill:python", "Python"))
        self.memory.save_semantic(record("skill:python", "Python 3"))
        self.agent().respond(UserMessage("/forget skill:python"))
        self.assertEqual(self.memory.all(), {})
        self.assertEqual(self.store.all_semantic_memory(self.uid, include_inactive=True), ())
        self.assertEqual(SQLiteBlagoStore(self.path).all_memory(self.uid), {})

    def test_очистка_удаляет_также_устаревшие_значения(self):
        self.memory.save_semantic(record("city", "Москва"))
        self.memory.set("city", "Казань")
        self.memory.clear()
        self.assertEqual(self.store.all_semantic_memory(self.uid, include_inactive=True), ())
        self.assertEqual(self.memory.all(), {})

    def test_отрицание_удаляет_точно_опровергнутый_город(self):
        self.memory.set("city", "Москве")
        self.agent().respond(UserMessage("Я больше не живу в Москве"))
        self.assertEqual(self.memory.all(), {})
        self.memory.set("city", "Казани")
        self.agent().respond(UserMessage("Я не живу в Москве"))
        self.assertEqual(self.memory.all(), {"city": "Казани"})

    def test_ошибка_sqlite_откатывает_изменение_памяти(self):
        self.memory.save_semantic(record("city", "Москва"))
        with self.store._locked_transaction() as db:
            db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON semantic_memory BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.memory.save_semantic(record("city", "Казань"))
        self.assertEqual(self.memory.all(), {"city": "Москва"})
        self.assertEqual(len(self.store.all_semantic_memory(self.uid, include_inactive=True)), 1)

    def test_противоречивые_кандидаты_отклоняются(self):
        p = Provider([item(), item(value="Java")])
        self.agent(p).respond(UserMessage("Я умею писать на Python и Java"))
        self.assertEqual(self.memory.all(), {})

    def test_отказ_от_запоминания_важнее_разметки_модели(self):
        for text in ("Я живу в Москве, не запоминай это", "Не сохраняй: меня зовут Анна",
                     "Я умею Python. Не запомни это", "Don't remember my name is Anna"):
            with self.subTest(text=text):
                p = Provider([item()])
                self.agent(p).respond(UserMessage(text))
                self.assertEqual(p.calls, [])
                self.assertEqual(self.memory.all(), {})

    def test_отрицания_цитаты_предположения_и_другие_люди(self):
        for text in ("Я не живу в Москве", "Возможно, я умею Python", "Если я живу в Москве",
                     "«Я умею писать на Python»", "Мой брат умеет Python",
                     "Я знаю, что Анна живет в Москве", "Я люблю книги, но не фантастику"):
            with self.subTest(text=text):
                p = Provider([item()])
                self.agent(p).respond(UserMessage(text))
                self.assertEqual(self.memory.all(), {})
                self.assertEqual(len(p.calls), 1)

    def test_галлюцинация_без_подтверждения_в_тексте_отклоняется(self):
        for candidate in (item(value="Java"), item(evidence="Я умею Python"),
                          {k: v for k, v in item().items() if k != "evidence"}):
            with self.subTest(candidate=candidate):
                self.agent(Provider([candidate])).respond(UserMessage("Я умею писать на Python"))
                self.assertEqual(self.memory.all(), {})

    def test_навыки_интересы_предпочтения_и_цели(self):
        for key, value, text, category in (
            ("skill:python", "Python", "Я умею писать на Python", "skill"),
            ("interest:books", "книги", "Я интересуюсь темой книги", "interest"),
            ("preference:morning", "утро", "Я предпочитаю утро; вечером отдыхаю", "preference"),
            ("goal:learn", "испанский", "Я хочу выучить испанский", "goal"),
        ):
            candidate = item(key=key, value=value, evidence=text)
            candidate["category"] = category
            p = Provider([candidate])
            self.agent(p).respond(UserMessage(text))
            self.assertEqual(self.memory.all()[key], value)
            self.assertEqual(len(p.calls), 2)

    def test_простые_факты_не_требуют_второго_вызова(self):
        p = Provider()
        self.agent(p).respond(UserMessage("Меня зовут Анна"))
        self.assertEqual(self.memory.all(), {"name": "Анна"})
        self.assertEqual(len(p.calls), 1)

    def test_сбой_провайдера_не_меняет_память(self):
        self.memory.set("city", "Казань")
        for failure in (LlmProviderError("unavailable"), TimeoutError()):
            p = Provider(failure=failure)
            response = self.agent(p).respond(UserMessage("Я живу в Москве"))
            self.assertIn("не получается связаться", response.text)
            self.assertEqual(self.memory.all(), {"city": "Казань"})
            self.assertEqual(len(p.calls), 1)

    def test_поврежденный_ответ_не_портит_историю(self):
        for reply in (None, {}, [], "", " " * 3):
            p = Provider(reply=reply)
            agent = self.agent(p)
            response = agent.respond(UserMessage("Я живу в Москве"))
            self.assertIsInstance(response.text, str)
            self.assertEqual(len(agent.history()), 2)
            self.assertEqual(self.memory.all(), {})

    def test_ошибка_экстрактора_не_портит_полезный_ответ(self):
        for invalid in ({"category": []}, {"confidence": {}}, {"value": []}):
            candidate = item()
            candidate.update(invalid)
            p = Provider([candidate])
            response = self.agent(p).respond(UserMessage("Я умею писать на Python"))
            self.assertEqual(response.text, "Я рядом.")
            self.assertEqual(self.memory.all(), {})

    def test_ошибка_второго_вызова_не_меняет_ответ_и_память(self):
        class BrokenExtraction(Provider):
            def generate(self, context):
                if context.user_text.startswith("Extract only durable personal memory"):
                    raise TimeoutError()
                return "Давай обсудим Python."
        response = self.agent(BrokenExtraction()).respond(UserMessage("Я умею писать на Python"))
        self.assertEqual(response.text, "Давай обсудим Python.")
        self.assertEqual(self.memory.all(), {})
        self.assertEqual(len(self.store.recent_history(self.uid, 10)), 2)

    def test_невалидный_json_экстрактора_изолирован(self):
        for raw in ("not json", "[]", '{"items":{}}', None, "x" * 17000, "[" * 1100 + "]" * 1100):
            class BrokenJSON(Provider):
                def generate(self, context):
                    if context.user_text.startswith("Extract only durable personal memory"):
                        return raw
                    return "Я рядом."
            self.assertEqual(self.agent(BrokenJSON()).respond(UserMessage("Я умею писать на Python")).text, "Я рядом.")
            self.assertEqual(self.memory.all(), {})

    def test_модель_не_превращает_упоминание_города_в_место_жительства(self):
        candidate = item(key="city", value="Москве", evidence="Я хочу выучить Python в Москве")
        candidate["category"] = "fact"
        self.agent(Provider([candidate])).respond(UserMessage(candidate["evidence"]))
        self.assertEqual(self.memory.all(), {})

    def test_изоляция_памяти_истории_и_удаления(self):
        other = self.store.resolve_telegram_user(TelegramIdentity(2, 2, "private"))
        self.store.memory_for_user(other).save_semantic(record("city", "Москва"))
        self.agent().respond(UserMessage("Меня зовут Анна"))
        self.memory.clear()
        self.assertEqual(self.store.all_memory(other), {"city": "Москва"})
        self.assertEqual(self.store.recent_history(other, 10), ())

    def test_секреты_не_сохраняются_в_памяти_и_истории(self):
        for secret in ("4111 1111 1111 1111", "hf_abcdefghijklmnopqrstuvwxyz",
                       "sk-abcdefghijklmnopqrstuv", "мой пароль hunter2", "паспорт 1234 567890"):
            p = Provider(reply=secret)
            self.agent(p).respond(UserMessage("Я предпочитаю " + secret))
            self.assertEqual(self.memory.all(), {})
            self.assertNotIn(secret, str(self.store.recent_history(self.uid, 100)))
            self.assertNotIn(secret, p.calls[0].user_text)

    def test_потребность_не_становится_реальной_заявкой(self):
        text = "Мне нужна помощь с ремонтом"
        candidate = item(key="need:repair", value="помощь с ремонтом", evidence=text)
        candidate["category"] = "need"
        self.agent(Provider([candidate])).respond(UserMessage(text))
        self.assertEqual(self.memory.all(), {})
        self.assertEqual(self.memory.semantic_records()[0].status, "candidate")
        self.assertIn("не заявка", self.agent().respond(UserMessage("что ты помнишь")).text)
        web = WebApp(self.store)
        self.assertEqual(web.rows("needs", self.uid), [])
        # JSON storage cannot represent candidates: fail closed rather than confirming them.
        legacy = JsonMemoryStore(self.path.parent / "legacy.json")
        legacy.save_semantic(record("need:repair", "ремонт", "need", "candidate"))
        self.assertEqual(legacy.all(), {})

    def test_привязка_каналов_сохраняет_историю_и_коллизии_памяти(self):
        web = WebApp(self.store)
        with patch.dict(os.environ, {"XBLAGO_WEB_INVITE_CODE": "invite"}):
            token, _ = web.register("tester", "long-test-password", "invite")
        target = web.session(token)[0]
        self.store.memory_for_user(target).set("city", "Казань")
        self.store.memory_for_user(target).save_semantic(record("telegram.city", "Тула"))
        self.memory.save_semantic(record("city", "Москва"))
        self.memory.save_semantic(record("city", "Сочи"))
        self.agent().respond(UserMessage("/memory"))
        code = web.new_link_code(target)
        self.assertEqual(web.redeem_telegram_link(self.uid, code), target)
        self.assertEqual(self.store.resolve_telegram_user(TelegramIdentity(1, 1, "private")), target)
        self.assertEqual(self.store.all_memory(target), {"city": "Казань", "telegram.city": "Тула", "telegram.city.2": "Сочи"})
        self.assertEqual(len(self.store.recent_history(target, 10)), 2)
        self.assertEqual(self.store.all_semantic_memory(self.uid, include_inactive=True), ())
        self.store.memory_for_user(target).delete("telegram.city.2")
        self.assertEqual(len(self.store.all_semantic_memory(target, include_inactive=True)), 1)

    def test_база_foundation04_открывается_без_изменения_схемы(self):
        with self.store._connect() as db:
            before = list(db.execute("SELECT sql FROM sqlite_master ORDER BY name"))
            before = [tuple(row) for row in before]
        restarted = SQLiteBlagoStore(self.path)
        with restarted._connect() as db:
            after = [tuple(row) for row in db.execute("SELECT sql FROM sqlite_master ORDER BY name")]
            self.assertEqual(before, after)
            self.assertEqual(list(db.execute("PRAGMA foreign_key_check")), [])


if __name__ == "__main__":
    unittest.main()
