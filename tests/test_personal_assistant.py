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

    def test_agent_can_show_memory(self) -> None:
        memory = InMemoryStore()
        memory.set("timezone", "Europe/Moscow")
        agent = PersonalAssistantAgent(memory)

        response = agent.respond(UserMessage("/memory"))

        self.assertIn("- timezone: Europe/Moscow", response.text)

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
        self.assertEqual(clear_response.text, "Готово. Сессионный контекст очищен.")
        self.assertEqual(len(agent.history()), 2)
        self.assertEqual(agent.history()[0].text, "/clear-history")


if __name__ == "__main__":
    unittest.main()
