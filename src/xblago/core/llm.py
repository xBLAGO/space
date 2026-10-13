from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from xblago.core.config import LlmConfig
from xblago.core.messages import ConversationTurn
from xblago.core.profile import AgentProfile


@dataclass(frozen=True)
class ChatContext:
    agent_profile: AgentProfile
    user_text: str
    memory: dict[str, Any]
    history: tuple[ConversationTurn, ...] = ()


class ChatProvider(ABC):
    @abstractmethod
    def generate(self, context: ChatContext) -> str:
        raise NotImplementedError


class LocalPlanningProvider(ChatProvider):
    """Deterministic fallback that keeps the agent useful without network access."""

    def generate(self, context: ChatContext) -> str:
        language = _language_for_text(context.user_text)
        memory_hint = ""
        if context.memory:
            prefix = "\n- контекст из памяти: " if language == "ru" else "\n- memory context: "
            memory_hint = prefix + _compact_memory(context.memory)

        history_hint = ""
        if context.history:
            prefix = "\n- контекст текущего диалога: " if language == "ru" else "\n- current conversation context: "
            history_hint = prefix + _compact_history(context.history)

        if language == "en":
            return (
                "Understood. I would frame this as a working request:\n"
                f"- goal: {context.user_text}\n"
                "- next step: clarify the desired outcome and time constraint\n"
                "- what I can do now: help turn this into a plan, draft, task list, "
                "or a request for a future specialized agent"
                f"{memory_hint}"
                f"{history_hint}"
            )

        return (
            "Понял. Я бы оформил это как рабочий запрос:\n"
            f"- цель: {context.user_text}\n"
            "- следующий шаг: уточнить желаемый результат и ограничение по времени\n"
            "- что я могу сделать сейчас: помочь превратить это в план, черновик, список задач "
            "или запрос для будущего специализированного агента"
            f"{memory_hint}"
            f"{history_hint}"
        )


class OpenAICompatibleProvider(ChatProvider):
    """Minimal OpenAI-compatible chat completions client using the standard library."""

    def __init__(self, config: LlmConfig) -> None:
        if not config.api_key:
            raise ValueError("XBLAGO_LLM_API_KEY or OPENAI_API_KEY is required for remote LLM providers.")

        self.config = config

    def generate(self, context: ChatContext) -> str:
        payload = {
            "model": self.config.model,
            "messages": self._messages(context),
            "temperature": 0.4,
        }
        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("LLM response did not include assistant content.") from exc

        return str(content).strip()

    def _messages(self, context: ChatContext) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": self._system_prompt(context)}]
        messages.extend(
            {"role": turn.role, "content": turn.text}
            for turn in context.history
            if turn.role in {"user", "assistant"}
        )
        messages.append({"role": "user", "content": context.user_text})
        return messages

    def _system_prompt(self, context: ChatContext) -> str:
        memory = context.memory or {}
        memory_text = _compact_memory(memory) if memory else "No remembered facts yet."

        return (
            f"You are {context.agent_profile.name}.\n"
            f"Role: {context.agent_profile.role}\n"
            f"Mission: {context.agent_profile.mission}\n"
            f"Tone: {context.agent_profile.tone}\n"
            "Respond in the user's language. Be concise, warm, practical, and action-oriented.\n"
            f"Remembered user facts: {memory_text}"
        )


def build_chat_provider(config: LlmConfig) -> ChatProvider:
    if config.provider == "local":
        return LocalPlanningProvider()

    if config.provider in {"openai", "openai-compatible"}:
        return OpenAICompatibleProvider(config)

    raise ValueError(f"Unsupported LLM provider: {config.provider}")


def _compact_memory(memory: dict[str, Any]) -> str:
    return "; ".join(f"{key}={value}" for key, value in sorted(memory.items()))


def _compact_history(history: tuple[ConversationTurn, ...], limit: int = 4) -> str:
    recent_turns = history[-limit:]
    return " | ".join(f"{turn.role}: {_shorten(turn.text)}" for turn in recent_turns)


def _shorten(text: str, limit: int = 120) -> str:
    compact_text = " ".join(text.split())
    if len(compact_text) <= limit:
        return compact_text

    return compact_text[: limit - 3].rstrip() + "..."


def _language_for_text(text: str) -> str:
    return "ru" if any("а" <= char.lower() <= "я" or char.lower() == "ё" for char in text) else "en"
