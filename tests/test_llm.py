from __future__ import annotations

import unittest

from xblago.agents.personal_assistant import PersonalAssistantAgent
from xblago.core.config import LlmConfig
from xblago.core.llm import ChatContext, LocalPlanningProvider, build_chat_provider
from xblago.core.messages import ConversationTurn


class LocalPlanningProviderTest(unittest.TestCase):
    def test_generates_actionable_fallback_with_memory(self) -> None:
        provider = LocalPlanningProvider()
        context = ChatContext(
            agent_profile=PersonalAssistantAgent.profile,
            user_text="Собери план недели",
            memory={"timezone": "Europe/Moscow"},
        )

        response = provider.generate(context)

        self.assertIn("Собери план недели", response)
        self.assertIn("timezone=Europe/Moscow", response)

    def test_generates_compact_fallback_with_session_history(self) -> None:
        provider = LocalPlanningProvider()
        context = ChatContext(
            agent_profile=PersonalAssistantAgent.profile,
            user_text="Добавь спорт",
            memory={},
            history=(
                ConversationTurn("user", "Составь план недели"),
                ConversationTurn(
                    "assistant",
                    "Понял.\n"
                    "Вот длинный ответ с несколькими строками, который должен стать компактным.",
                ),
            ),
        )

        response = provider.generate(context)

        self.assertIn("контекст текущего диалога", response)
        self.assertNotIn("Понял.\n", response)

    def test_generates_english_fallback_for_english_user_text(self) -> None:
        provider = LocalPlanningProvider()
        context = ChatContext(
            agent_profile=PersonalAssistantAgent.profile,
            user_text="Build a weekly plan",
            memory={"timezone": "Europe/Moscow"},
        )

        response = provider.generate(context)

        self.assertIn("Understood.", response)
        self.assertIn("- goal: Build a weekly plan", response)
        self.assertIn("memory context", response)

    def test_builds_local_provider_by_default(self) -> None:
        config = LlmConfig(
            provider="local",
            model="gpt-5-mini",
            api_key=None,
            base_url="https://api.openai.com/v1",
            timeout_seconds=30.0,
        )

        provider = build_chat_provider(config)

        self.assertIsInstance(provider, LocalPlanningProvider)


if __name__ == "__main__":
    unittest.main()
