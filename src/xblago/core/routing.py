from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    name: str
    key: str | None = None
    value: str | None = None


REMEMBER_PATTERNS = (
    re.compile(r"^remember\s+(?:my\s+)?(?P<key>[\w\s-]+?)\s+(?:is|=)\s+(?P<value>.+)$", re.I),
    re.compile(r"^запомни[:,]?\s+(?:мой|моя|мое|моё|мои)?\s*(?P<key>[\w\s-]+?)\s+(?:это|=|-)\s+(?P<value>.+)$", re.I),
)

FORGET_PATTERNS = (
    re.compile(r"^/(?:forget|delete-memory)\s+(?P<key>[\w\s-]+)$", re.I),
    re.compile(r"^forget\s+(?:my\s+)?(?P<key>[\w\s-]+)$", re.I),
    re.compile(r"^забудь[:,]?\s+(?:мой|моя|мое|моё|мои)?\s*(?P<key>[\w\s-]+)$", re.I),
)


def detect_intent(text: str) -> Intent:
    normalized = text.strip()
    lower = normalized.lower()

    if lower in {"/exit", "/quit", "exit", "quit", "пока", "выход"}:
        return Intent("exit")

    if lower in {"/help", "help", "помощь", "что ты умеешь"}:
        return Intent("help")

    if lower in {"/profile", "profile", "профиль"}:
        return Intent("profile")

    if lower in {"/agent", "/agent-profile", "agent", "профиль агента"}:
        return Intent("agent_profile")

    if lower in {"/memory", "memory", "что ты помнишь", "память"}:
        return Intent("memory")

    if lower in {"/history", "history", "история", "контекст"}:
        return Intent("history")

    if lower in {"/clear-memory", "clear memory", "очисти память"}:
        return Intent("clear_memory")

    if lower in {"/clear-history", "clear history", "очисти историю", "сбрось контекст"}:
        return Intent("clear_history")

    if lower in {"hi", "hello", "hey", "привет", "здравствуй", "добрый день"}:
        return Intent("greeting")

    for pattern in REMEMBER_PATTERNS:
        match = pattern.match(normalized)
        if match:
            return Intent(
                "remember",
                key=_normalize_key(match.group("key")),
                value=match.group("value").strip(),
            )

    for pattern in FORGET_PATTERNS:
        match = pattern.match(normalized)
        if match:
            return Intent("forget", key=_normalize_key(match.group("key")))

    return Intent("unknown")


def _normalize_key(key: str) -> str:
    return re.sub(r"\s+", "_", key.strip().lower())
