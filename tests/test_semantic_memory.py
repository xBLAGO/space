from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xblago.agents.personal_assistant import PersonalAssistantAgent
from xblago.core.llm import ChatContext, ChatProvider
from xblago.core.memory import MemoryStore, SemanticMemoryRecord
from xblago.core.messages import UserMessage
from xblago.core.semantic_memory import SemanticMemoryExtractor
from xblago.core.storage import SQLiteBlagoStore, TelegramIdentity


class ScriptedProvider(ChatProvider):
    def __init__(self, extraction_payloads: list[dict]) -> None:
        self.extraction_payloads = list(extraction_payloads)
        self.calls: list[ChatContext] = []

    def generate(self, context: ChatContext) -> str:
        self.calls.append(context)
        if context.user_text.startswith("Extract only durable personal memory"):
            return json.dumps(self.extraction_payloads.pop(0))
        return "Ответ без заявления о сохранении памяти"


class SemanticInMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.records: list[SemanticMemoryRecord] = []

    def all(self) -> dict[str, str]:
        result = dict(self.data)
        for record in self.semantic_records():
            if record.status == "confirmed":
                result[record.key] = record.value
        return result

    def set(self, key: str, value: str) -> None:
        self.data[key] = value

    def delete(self, key: str) -> bool:
        deleted = key in self.data
        self.data.pop(key, None)
        before = len(self.records)
        self.records = [record for record in self.records if record.key != key]
        return deleted or len(self.records) != before

    def clear(self) -> None:
        self.data.clear()
        self.records.clear()

    def semantic_records(self) -> tuple[SemanticMemoryRecord, ...]:
        return tuple(record for record in self.records if record.status in {"confirmed", "candidate"})

    def save_semantic(self, record: SemanticMemoryRecord) -> None:
        current = next((item for item in self.semantic_records() if item.key == record.key), None)
        if current is not None and current.value == record.value and current.status == record.status:
            return
        if current is not None:
            self.records = [
                SemanticMemoryRecord(
                    item.record_id, item.key, item.value, item.category, item.source,
                    "superseded" if item.key == record.key else item.status,
                    item.confidence, item.created_at, item.updated_at,
                )
                for item in self.records
            ]
        self.records.append(record)


class SemanticMemoryTest(unittest.TestCase):
    def test_extracts_only_direct_self_assertions(self) -> None:
        provider = ScriptedProvider([{
            "items": [
                {"key": "interest:books", "value": "научная фантастика", "evidence": "Мне близка научная фантастика", "category": "interest", "claim": "assertion", "subject": "self", "confidence": 0.95},
                {"key": "city", "value": "Берлин", "category": "fact", "claim": "negation", "subject": "self", "confidence": 0.99},
                {"key": "city", "value": "Москва", "category": "fact", "claim": "assertion", "subject": "other", "confidence": 0.99},
                {"key": "goal:learn", "value": "французский", "category": "goal", "claim": "hypothesis", "subject": "self", "confidence": 0.95},
                {"key": "need:childcare", "value": "присмотр", "evidence": "Мне нужен присмотр", "category": "need", "claim": "assertion", "subject": "self", "confidence": 0.9},
            ]
        }])
        memory = SemanticInMemoryStore()
        agent = PersonalAssistantAgent(memory, chat_provider=provider, memory_extractor=SemanticMemoryExtractor(provider))

        response = agent.respond(UserMessage("Мне близка научная фантастика; Мне нужен присмотр"))

        self.assertEqual(response.text, "Ответ без заявления о сохранении памяти")
        self.assertEqual(memory.all(), {"interest:books": "научная фантастика"})
        self.assertEqual(len(memory.semantic_records()), 2)
        self.assertEqual(next(item for item in memory.semantic_records() if item.category == "need").status, "candidate")
        self.assertEqual(len(provider.calls), 2)

    def test_local_correction_avoids_extra_extraction(self) -> None:
        provider = ScriptedProvider([
            {"items": [{"key": "city", "value": "Москва", "category": "fact", "claim": "assertion", "subject": "self", "confidence": 0.95}]},
            {"items": [{"key": "city", "value": "Берлин", "category": "fact", "claim": "assertion", "subject": "self", "confidence": 0.95}]},
        ])
        memory = SemanticInMemoryStore()
        agent = PersonalAssistantAgent(memory, chat_provider=provider, memory_extractor=SemanticMemoryExtractor(provider))

        agent.respond(UserMessage("Мой город Москва"))
        agent.respond(UserMessage("Исправление: мой город Берлин"))

        self.assertEqual(memory.all(), {"city": "Берлин"})
        self.assertEqual(len(memory.records), 0)
        self.assertEqual(len(provider.calls), 2)

    def test_sensitive_and_invalid_extraction_does_not_reach_memory(self) -> None:
        provider = ScriptedProvider([{
            "items": [
                {"key": "password", "value": "secret", "category": "fact", "claim": "assertion", "subject": "self", "confidence": 1},
                {"key": "bad key", "value": "x", "category": "fact", "claim": "assertion", "subject": "self", "confidence": 1},
            ]
        }])
        memory = SemanticInMemoryStore()
        PersonalAssistantAgent(memory, chat_provider=provider, memory_extractor=SemanticMemoryExtractor(provider)).respond(
            UserMessage("Не сохраняй это")
        )
        self.assertEqual(memory.all(), {})

    def test_sqlite_semantic_memory_isolated_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blago.sqlite3"
            store = SQLiteBlagoStore(path)
            first = store.resolve_telegram_user(TelegramIdentity(1, 101, "private"))
            second = store.resolve_telegram_user(TelegramIdentity(2, 202, "private"))
            record = SemanticMemoryRecord("", "skill:python", "Python", "skill", "llm_extraction", "confirmed", 0.9, "", "")
            store.memory_for_user(first).save_semantic(record)

            restarted = SQLiteBlagoStore(path)
            self.assertEqual(restarted.all_memory(first), {"skill:python": "Python"})
            self.assertEqual(restarted.all_memory(second), {})
            self.assertEqual(restarted.memory_for_user(first).delete("skill:python"), True)
            self.assertEqual(restarted.all_memory(first), {})


if __name__ == "__main__":
    unittest.main()
