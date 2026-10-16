from __future__ import annotations

import unittest

from xblago.agents.personal_assistant import PersonalAssistantAgent
from xblago.core.llm import ChatContext, ChatProvider
from xblago.core.memory import MemoryStore
from xblago.core.messages import UserMessage


class InMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def all(self) -> dict[str, str]:
        return dict(self.data)

    def set(self, key: str, value: str) -> None:
        self.data[key] = value

    def delete(self, key: str) -> bool:
        if key not in self.data:
            return False

        del self.data[key]
        return True

    def clear(self) -> None:
        self.data.clear()


class RecordingProvider(ChatProvider):
    def __init__(self) -> None:
        self.contexts: list[ChatContext] = []

    def generate(self, context: ChatContext) -> str:
        self.contexts.append(context)
        return "generated response"


class PersonalAssistantAgentTest(unittest.TestCase):
    def test_agent_remembers_user_fact(self) -> None:
        memory = InMemoryStore()
        agent = PersonalAssistantAgent(memory)

        response = agent.respond(UserMessage("remember my timezone is Europe/Moscow"))

        self.assertEqual(response.text, "I remembered: timezone = Europe/Moscow.")
        self.assertEqual(memory.all(), {"timezone": "Europe/Moscow"})

    def test_agent_remembers_user_fact_in_russian(self) -> None:
        memory = InMemoryStore()
        agent = PersonalAssistantAgent(memory)

        response = agent.respond(UserMessage("запомни город = Москва"))

        self.assertEqual(response.text, "Запомнил: город = Москва.")
        self.assertEqual(memory.all(), {"город": "Москва"})

    def test_agent_automatically_remembers_low_risk_personal_fact(self) -> None:
        memory = InMemoryStore()
        agent = PersonalAssistantAgent(memory)

        agent.respond(UserMessage("Меня зовут Анна"))

        self.assertEqual(memory.all(), {"name": "Анна"})

    def test_agent_does_not_store_sensitive_fact(self) -> None:
        memory = InMemoryStore()
        agent = PersonalAssistantAgent(memory)

        response = agent.respond(UserMessage("remember my password is hunter2"))

        self.assertIn("won't save", response.text)
        self.assertEqual(memory.all(), {})

    def test_agent_can_show_memory(self) -> None:
        memory = InMemoryStore()
        memory.set("timezone", "Europe/Moscow")
        agent = PersonalAssistantAgent(memory)

        response = agent.respond(UserMessage("/memory"))

        self.assertIn("- timezone: Europe/Moscow", response.text)

    def test_agent_can_forget_one_memory_fact(self) -> None:
        memory = InMemoryStore()
        memory.set("timezone", "Europe/Moscow")
        memory.set("city", "Moscow")
        agent = PersonalAssistantAgent(memory)

        response = agent.respond(UserMessage("/forget timezone"))

        self.assertEqual(response.text, "Done. I forgot `timezone`.")
        self.assertEqual(memory.all(), {"city": "Moscow"})

    def test_agent_can_forget_one_memory_fact_in_russian(self) -> None:
        memory = InMemoryStore()
        memory.set("город", "Москва")
        agent = PersonalAssistantAgent(memory)

        response = agent.respond(UserMessage("забудь город"))

        self.assertEqual(response.text, "Готово. Забыл `город`.")
        self.assertEqual(memory.all(), {})

    def test_agent_reports_missing_memory_fact_when_forgetting(self) -> None:
        agent = PersonalAssistantAgent(InMemoryStore())

        response = agent.respond(UserMessage("/forget timezone"))

        self.assertEqual(response.text, "I did not find `timezone` in memory.")

    def test_profile_shows_user_memory(self) -> None:
        memory = InMemoryStore()
        memory.set("timezone", "Europe/Moscow")
        agent = PersonalAssistantAgent(memory)

        response = agent.respond(UserMessage("/profile"))

        self.assertIn("User profile:", response.text)
        self.assertIn("- timezone: Europe/Moscow", response.text)

    def test_profile_shows_user_memory_in_russian(self) -> None:
        memory = InMemoryStore()
        memory.set("город", "Москва")
        agent = PersonalAssistantAgent(memory)

        response = agent.respond(UserMessage("профиль"))

        self.assertIn("Профиль пользователя:", response.text)
        self.assertIn("- город: Москва", response.text)

    def test_agent_exit_ends_session(self) -> None:
        agent = PersonalAssistantAgent(InMemoryStore())

        response = agent.respond(UserMessage("/exit"))

        self.assertTrue(response.should_end_session)

    def test_agent_passes_session_history_to_provider(self) -> None:
        provider = RecordingProvider()
        agent = PersonalAssistantAgent(InMemoryStore(), chat_provider=provider)

        agent.respond(UserMessage("Собери план недели"))
        agent.respond(UserMessage("Добавь спорт"))

        self.assertEqual(len(provider.contexts), 2)
        self.assertEqual(provider.contexts[0].history, ())
        self.assertEqual(provider.contexts[1].history[0].role, "user")
        self.assertEqual(provider.contexts[1].history[0].text, "Собери план недели")
        self.assertEqual(provider.contexts[1].history[1].role, "assistant")
        self.assertEqual(provider.contexts[1].history[1].text, "generated response")

    def test_agent_trims_session_history(self) -> None:
        agent = PersonalAssistantAgent(InMemoryStore(), history_limit=2)

        agent.respond(UserMessage("one"))
        agent.respond(UserMessage("two"))

        history = agent.history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].role, "user")
        self.assertEqual(history[0].text, "two")

    def test_agent_can_show_and_clear_session_history(self) -> None:
        agent = PersonalAssistantAgent(InMemoryStore())

        agent.respond(UserMessage("Собери план недели"))
        history_response = agent.respond(UserMessage("/history"))
        clear_response = agent.respond(UserMessage("/clear-history"))

        self.assertIn("Собери план недели", history_response.text)
        self.assertEqual(clear_response.text, "Done. This conversation context is cleared.")
        self.assertEqual(len(agent.history()), 2)
        self.assertEqual(agent.history()[0].text, "/clear-history")


if __name__ == "__main__":
    unittest.main()
