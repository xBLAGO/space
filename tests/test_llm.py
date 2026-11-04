from __future__ import annotations

import json
import urllib.error
import unittest
import unittest.mock

from xblago.agents.personal_assistant import PersonalAssistantAgent
from xblago.core.config import LlmConfig, load_llm_config
from xblago.core.llm import (
    ChatContext,
    LlmProviderError,
    UnavailableChatProvider,
    LocalPlanningProvider,
    OpenAICompatibleProvider,
    build_chat_provider,
)
from xblago.core.messages import ConversationTurn


class LocalPlanningProviderTest(unittest.TestCase):
    def test_generates_natural_fallback_without_template_labels(self) -> None:
        provider = LocalPlanningProvider()
        context = ChatContext(
            agent_profile=PersonalAssistantAgent.profile,
            user_text="Собери план недели",
            memory={"timezone": "Europe/Moscow"},
        )

        response = provider.generate(context)

        self.assertIn("плана недели", response)
        self.assertNotIn("?", response)
        self.assertNotIn("цель:", response)
        self.assertNotIn("следующий шаг:", response)

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

        self.assertIn("Продолжим", response)
        self.assertNotIn("user:", response)
        self.assertNotIn("assistant:", response)
        self.assertNotIn("Понял.\n", response)

    def test_generates_english_fallback_for_english_user_text(self) -> None:
        provider = LocalPlanningProvider()
        context = ChatContext(
            agent_profile=PersonalAssistantAgent.profile,
            user_text="Build a weekly plan",
            memory={"timezone": "Europe/Moscow"},
        )

        response = provider.generate(context)

        self.assertIn("weekly plan", response)
        self.assertNotIn("?", response)
        self.assertNotIn("- goal:", response)
        self.assertNotIn("memory context", response)

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

    def test_builds_hugging_face_provider_from_environment(self) -> None:
        with unittest.mock.patch.dict(
            "os.environ",
            {
                "XBLAGO_LLM_PROVIDER": "hf",
                "XBLAGO_LLM_MODEL": "Qwen/Qwen3-14B:cheapest",
                "HF_TOKEN": "hf_test_token",
            },
            clear=True,
        ):
            config = load_llm_config()

        self.assertEqual(config.base_url, "https://router.huggingface.co/v1")
        self.assertEqual(config.model, "Qwen/Qwen3-14B:cheapest")
        self.assertEqual(config.api_key, "hf_test_token")
        self.assertEqual(config.thinking, "off")
        self.assertIsInstance(build_chat_provider(config), OpenAICompatibleProvider)

    def test_missing_provider_token_builds_non_networking_fallback(self) -> None:
        config = LlmConfig(
            provider="hf",
            model="Qwen/Qwen3-14B:cheapest",
            api_key=None,
            base_url="https://router.huggingface.co/v1",
            timeout_seconds=7.0,
        )

        with unittest.mock.patch("xblago.core.llm.urllib.request.urlopen") as urlopen:
            provider = build_chat_provider(config)
            with self.assertRaises(LlmProviderError):
                provider.generate(ChatContext(PersonalAssistantAgent.profile, "Привет", {}, ()))

        self.assertIsInstance(provider, UnavailableChatProvider)
        urlopen.assert_not_called()

    def test_openai_compatible_request_matches_hf_chat_completion_shape(self) -> None:
        config = LlmConfig(
            provider="hf",
            model="Qwen/Qwen3-14B:cheapest",
            api_key="hf_test_token",
            base_url="https://router.huggingface.co/v1",
            timeout_seconds=7.0,
            max_tokens=384,
        )
        provider = OpenAICompatibleProvider(config)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "Привет!"}}]}
        ).encode()

        with unittest.mock.patch("xblago.core.llm.urllib.request.urlopen", return_value=response) as urlopen:
            result = provider.generate(ChatContext(PersonalAssistantAgent.profile, "Привет", {}, ()))

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode())
        self.assertEqual(result, "Привет!")
        self.assertEqual(request.full_url, "https://router.huggingface.co/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer hf_test_token")
        self.assertEqual(payload["model"], "Qwen/Qwen3-14B:cheapest")
        self.assertEqual(payload["messages"][-1], {"role": "user", "content": "Привет"})
        self.assertEqual(payload["max_tokens"], 384)
        self.assertFalse(payload["stream"])
        self.assertNotIn("chat_template_kwargs", payload)
        self.assertEqual(urlopen.call_count, 1)

    def test_http_errors_log_only_status_and_category_without_retry(self) -> None:
        config = LlmConfig("hf", "Qwen/Qwen3-14B:cheapest", "hf_secret", "https://router.huggingface.co/v1", 7.0)
        provider = OpenAICompatibleProvider(config)
        context = ChatContext(PersonalAssistantAgent.profile, "личное сообщение", {}, ())

        cases = ((400, "invalid_request"), (401, "authentication"), (402, "billing_or_credits"),
                 (404, "not_found"), (429, "rate_limit"), (503, "provider_server_error"))
        for status, category in cases:
            error = urllib.error.HTTPError(
                "https://router.huggingface.co/v1/chat/completions",
                status,
                "private provider body",
                {},
                None,
            )
            with self.subTest(status=status), self.assertLogs("xblago.core.llm", level="WARNING") as logs:
                with unittest.mock.patch("xblago.core.llm.urllib.request.urlopen", side_effect=error) as urlopen:
                    with self.assertRaises(LlmProviderError) as raised:
                        provider.generate(context)

            self.assertEqual(urlopen.call_count, 1)
            diagnostic = "\n".join(logs.output)
            self.assertIn(f"status={status}", diagnostic)
            self.assertIn(f"category={category}", diagnostic)
            self.assertNotIn("hf_secret", diagnostic)
            self.assertNotIn("private provider body", diagnostic)
            self.assertNotIn("личное сообщение", diagnostic)
            self.assertEqual(raised.exception.status, status)

    def test_thinking_auto_omits_provider_specific_parameter(self) -> None:
        config = LlmConfig(
            "hf", "Qwen/Qwen3-14B:nscale", "hf_test_token", "https://router.huggingface.co/v1", 7.0, thinking="auto"
        )
        provider = OpenAICompatibleProvider(config)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "Ответ"}, "finish_reason": "stop"}]}
        ).encode()

        with unittest.mock.patch("xblago.core.llm.urllib.request.urlopen", return_value=response) as urlopen:
            provider.generate(ChatContext(PersonalAssistantAgent.profile, "Вопрос", {}, ()))

        payload = json.loads(urlopen.call_args.args[0].data.decode())
        self.assertNotIn("chat_template_kwargs", payload)

    def test_null_content_is_rejected_without_stringifying_none(self) -> None:
        result = self._provider_response({"content": None, "reasoning_content": "internal reasoning"})

        with self.assertRaises(LlmProviderError) as raised:
            result.generate(ChatContext(PersonalAssistantAgent.profile, "Развёрнутый вопрос", {}, ()))

        self.assertNotIn("None", str(raised.exception))
        self.assertIn("usable answer", str(raised.exception))

    def test_empty_content_is_rejected(self) -> None:
        result = self._provider_response({"content": "   "})

        with self.assertRaises(LlmProviderError):
            result.generate(ChatContext(PersonalAssistantAgent.profile, "Вопрос", {}, ()))

    def test_reasoning_only_response_with_length_finish_is_rejected(self) -> None:
        result = self._provider_response(
            {"content": None, "reasoning_content": "internal reasoning"},
            finish_reason="length",
            usage={"prompt_tokens": 210, "completion_tokens": 384, "total_tokens": 594},
        )

        with self.assertRaises(LlmProviderError):
            result.generate(ChatContext(PersonalAssistantAgent.profile, "Сложный вопрос", {}, ()))

    def test_successful_completion_returns_only_content(self) -> None:
        result = self._provider_response(
            {"content": "  Готовый ответ  ", "reasoning_content": "internal reasoning"},
            finish_reason="stop",
            usage={"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        )

        self.assertEqual(result.generate(ChatContext(PersonalAssistantAgent.profile, "Вопрос", {}, ())), "Готовый ответ")

    def test_hugging_face_thinking_mode_is_configurable(self) -> None:
        config = LlmConfig(
            provider="hf",
            model="Qwen/Qwen3-14B:cheapest",
            api_key="hf_test_token",
            base_url="https://router.huggingface.co/v1",
            timeout_seconds=7.0,
            thinking="off",
        )
        provider = OpenAICompatibleProvider(config)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "Ответ"}, "finish_reason": "stop"}]}
        ).encode()

        with unittest.mock.patch("xblago.core.llm.urllib.request.urlopen", return_value=response) as urlopen:
            provider.generate(ChatContext(PersonalAssistantAgent.profile, "Вопрос", {}, ()))

        payload = json.loads(urlopen.call_args.args[0].data.decode())
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})

    def _provider_response(
        self,
        message: dict[str, object],
        finish_reason: str = "stop",
        usage: dict[str, int] | None = None,
    ) -> OpenAICompatibleProvider:
        config = LlmConfig("hf", "Qwen/Qwen3-14B:cheapest", "hf_test_token", "https://router.huggingface.co/v1", 7.0)
        provider = OpenAICompatibleProvider(config)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {"choices": [{"message": message, "finish_reason": finish_reason}], "usage": usage or {}}
        ).encode()
        self.addCleanup(unittest.mock.patch.stopall)
        unittest.mock.patch("xblago.core.llm.urllib.request.urlopen", return_value=response).start()
        return provider

    def test_provider_errors_are_sanitized_and_never_retried(self) -> None:
        config = LlmConfig("hf", "Qwen/Qwen3-14B:cheapest", "hf_secret", "https://router.huggingface.co/v1", 7.0)
        provider = OpenAICompatibleProvider(config)
        context = ChatContext(PersonalAssistantAgent.profile, "Привет", {}, ())

        for status, expected in ((401, "credentials"), (402, "credits"), (429, "rate-limited"), (404, "unavailable")):
            error = urllib.error.HTTPError("https://router.huggingface.co/v1/chat/completions", status, "secret body", {}, None)
            with self.subTest(status=status), unittest.mock.patch(
                "xblago.core.llm.urllib.request.urlopen", side_effect=error
            ) as urlopen:
                with self.assertRaises(LlmProviderError) as raised:
                    provider.generate(context)
            self.assertIn(expected, str(raised.exception))
            self.assertNotIn("hf_secret", str(raised.exception))
            self.assertNotIn("secret body", str(raised.exception))
            self.assertEqual(urlopen.call_count, 1)

    def test_timeout_is_sanitized_without_retry(self) -> None:
        config = LlmConfig("hf", "Qwen/Qwen3-14B:cheapest", "hf_secret", "https://router.huggingface.co/v1", 7.0)
        provider = OpenAICompatibleProvider(config)
        with unittest.mock.patch(
            "xblago.core.llm.urllib.request.urlopen", side_effect=TimeoutError("hf_secret")
        ) as urlopen:
            with self.assertRaises(LlmProviderError) as raised:
                provider.generate(ChatContext(PersonalAssistantAgent.profile, "Hi", {}, ()))

        self.assertIn("temporarily unavailable", str(raised.exception))
        self.assertNotIn("hf_secret", str(raised.exception))
        self.assertEqual(urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
