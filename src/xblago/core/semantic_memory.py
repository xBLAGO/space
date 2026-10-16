from __future__ import annotations

import json
import re
from dataclasses import dataclass
from xblago.core.llm import ChatContext, ChatProvider, LlmProviderError
from xblago.core.memory import SemanticMemoryRecord, is_sensitive_memory


ALLOWED_CATEGORIES = {"fact", "interest", "preference", "skill", "goal", "need"}
MAX_CANDIDATES = 8


@dataclass(frozen=True)
class MemoryCandidate:
    key: str
    value: str
    category: str
    status: str
    confidence: float
    source: str = "llm_extraction"


class SemanticMemoryExtractor:
    """One bounded, JSON-only LLM pass for durable personal memory candidates."""

    def __init__(self, provider: ChatProvider, max_text_length: int = 4000) -> None:
        self.provider = provider
        self.max_text_length = max_text_length

    def extract(self, context: ChatContext) -> tuple[MemoryCandidate, ...]:
        text = context.user_text[: self.max_text_length]
        prompt = _extraction_prompt(text)
        raw = self.provider.generate(
            ChatContext(
                agent_profile=context.agent_profile,
                user_text=prompt,
                memory={},
                history=(),
            )
        )
        return _parse_candidates(raw)


def candidates_to_records(candidates: tuple[MemoryCandidate, ...]) -> tuple[SemanticMemoryRecord, ...]:
    return tuple(
        SemanticMemoryRecord(
            record_id="",
            key=candidate.key,
            value=candidate.value,
            category=candidate.category,
            source=candidate.source,
            status=candidate.status,
            confidence=candidate.confidence,
            created_at="",
            updated_at="",
        )
        for candidate in candidates
    )


def _extraction_prompt(text: str) -> str:
    return (
        "Extract only durable personal memory from the user's latest message. "
        "Return JSON only, with this exact shape: "
        '{"items":[{"key":"...","value":"...","category":"fact|interest|preference|skill|goal|need",'
        '"claim":"assertion|hypothesis|quote|negation|about_other","subject":"self|other|unknown",'
        '"confidence":0.0}]}.'
        " Use a stable English key such as name, city, interest:books, preference:morning, skill:python, "
        "goal:learn_python, or need:childcare. Extract only a direct assertion by the user about themselves. "
        "Reject questions, guesses, hypotheticals, quoted text, negations, and facts about other people. "
        "A need may be recorded only as a candidate memory, never as an external request. "
        "Do not extract passwords, tokens, financial credentials, health details, or other sensitive data. "
        "Do not include explanations or reasoning. Latest user message: "
        + text
    )


def _parse_candidates(raw: str) -> tuple[MemoryCandidate, ...]:
    try:
        payload = json.loads(_strip_json_fence(raw))
    except (TypeError, json.JSONDecodeError) as exc:
        raise LlmProviderError("The memory analysis returned an invalid response.") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise LlmProviderError("The memory analysis returned an unusable response.")

    candidates: list[MemoryCandidate] = []
    seen: set[str] = set()
    for item in payload["items"][:MAX_CANDIDATES]:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        value = item.get("value")
        category = item.get("category")
        claim = item.get("claim")
        subject = item.get("subject")
        confidence = item.get("confidence")
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[a-z][a-z0-9_.:-]{1,63}", key)
            or not isinstance(value, str)
            or not 1 <= len(value.strip()) <= 240
            or category not in ALLOWED_CATEGORIES
            or claim != "assertion"
            or subject != "self"
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0.8 <= float(confidence) <= 1.0
            or is_sensitive_memory(key, value)
            or key in seen
        ):
            continue
        seen.add(key)
        candidates.append(
            MemoryCandidate(
                key=key,
                value=" ".join(value.split()),
                category=category,
                status="candidate" if category == "need" else "confirmed",
                confidence=float(confidence),
            )
        )
    return tuple(candidates)


def _strip_json_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return text
