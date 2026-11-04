from __future__ import annotations

import json
import os
import tempfile
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SENSITIVE_MEMORY_TERMS = (
    "password", "пароль", "passcode", "token", "токен", "secret", "секрет",
    "cvv", "cvc", "pin", "пин", "card number", "номер карты", "bank login",
    "логин банка", "api key", "ключ api", "private key", "приватный ключ",
    "health", "здоровье", "medical", "медицин", "diagnosis", "диагноз", "illness", "болезн",
    "medication", "лекарств", "sexual", "сексуаль",
)


@dataclass(frozen=True)
class SemanticMemoryRecord:
    record_id: str
    key: str
    value: Any
    category: str
    source: str
    status: str
    confidence: float
    created_at: str
    updated_at: str


_SECRET_FORMAT = re.compile(
    r"\b(?:pin|пин)(?:-код)?[:= ]*\d{3,8}\b|(?:\d[ -]?){13,19}|sk-[a-zA-Z0-9_-]{12,}|hf_[a-zA-Z0-9]{12,}|"
    r"\b\d{6,12}:[a-zA-Z0-9_-]{20,}|-----BEGIN [^-]*PRIVATE KEY-----[\s\S]+?-----END [^-]*PRIVATE KEY-----|"
    r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*|"
    r"\b[А-Яа-яA-Za-z0-9._%+-]+@[А-Яа-яA-Za-z0-9.-]+\.[A-Za-z]{2,}\b|"
    r"(?:\+7|\+1|8)[ ()-]*(?:\d[ ()-]*){10}", re.I,
)
_SECRET_LABEL = re.compile(
    r"\b(?:password|passcode|пароль|token|токен|secret|секрет|cvv|cvc|pin|пин(?:-код)?|"
    r"номер карты|api[_ -]?key|ключ[_ -]?api|api[_ -]?token|private[_ -]?key|приватный ключ|seed[_ -]?phrase|recovery[_ -]?phrase|iban|паспорт|снилс)\b"
    r"[\"']?(?:\s*[:=]\s*|\s+(?:это|is)\s+|\s+)[\"']?(?P<value>[^\s,;\"'}]+)", re.I,
)
_NOT_VALUES = {
    "доступа", "авторизации", "нужен", "нужны", "нужно", "надо", "следует", "можно", "нельзя",
    "для", "это", "такое", "хранить", "использовать", "поменять", "забыть", "сохранить",
    "банковской", "карты", "никому", "never", "should", "must", "access", "authentication",
    "в", "на", "о", "с", "к", "и", "a", "an", "the", "of", "for", "to",
}


def is_sensitive_memory(key: str, value: Any) -> bool:
    clean_key = str(key).replace("_", " ").replace(":", " ")
    text = f"{clean_key} {value}".lower()
    # PIN and other complete labels must not match tool names like пинцет/camping.
    whole_labels = {
        "pin": r"pin", "пин": r"пин", "token": r"tokens?",
        "токен": r"токен(?:а|ы|ов|у|ом|е)?", "secret": r"secrets?",
        "секрет": r"секрет(?:а|ы|ов|у|ом|е)?",
    }
    terms = any(re.search(
        r"(?<!\w)" + (whole_labels[term] + r"(?!\w)" if term in whole_labels else re.escape(term)), text,
    ) for term in SENSITIVE_MEMORY_TERMS)
    return terms or bool(_SECRET_FORMAT.search(text)) or bool(_SECRET_LABEL.search(text)) or bool(re.search(
        r"\b(?:iban|bic|swift|паспорт|снилс|инн|адрес|address|seed phrase|recovery phrase)\b", text, re.I,
    ))


def redact_sensitive_text(text: str) -> str:
    """Hide actual credential values, keeping ordinary explanatory dialogue intact."""
    def scrub(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: "[скрыто]" if is_sensitive_memory(key, "") else scrub(value)
                    for key, value in node.items()}
        if isinstance(node, list):
            return [scrub(value) for value in node]
        return node

    cursor = 0
    decoder = json.JSONDecoder()
    while start := re.search(r"[\[{]", text[cursor:]):
        offset = cursor + start.start()
        try:
            payload, end = decoder.raw_decode(text, offset)
            cleaned = scrub(payload)
            if cleaned != payload:
                replacement = json.dumps(cleaned, ensure_ascii=False)
                text = text[:offset] + replacement + text[end:]
                cursor = offset + len(replacement)
            else:
                cursor = end
        except (ValueError, RecursionError):
            if text[offset:].startswith(("{", "[[")) and is_sensitive_memory("", text):
                return "[Чувствительные сведения скрыты]"
            cursor = offset + 1
    text = _SECRET_FORMAT.sub("[скрыто]", text)
    def redact_label(match: re.Match[str]) -> str:
        value = match["value"]
        start = match.start("value") - match.start()
        assigned = bool(re.search(r"[:=]|\b(?:это|is)\b", match[0][:start], re.I))
        if value.casefold().strip(".!?():") in _NOT_VALUES and not assigned:
            return match[0]
        return match[0][:start] + "[скрыто]"
    text = _SECRET_LABEL.sub(redact_label, text)
    if re.search(r"\b(?:у меня|мой|моя)\s+(?:диагноз|болезнь|паспорт|адрес)\b", text, re.I):
        return "[Личные чувствительные сведения скрыты]"
    return text


class MemoryConflictError(ValueError):
    """The user's memory changed while an edit was being prepared."""


def relevant_memory(memory: dict[str, Any], text: str, limit: int = 12) -> dict[str, Any]:
    """Return facts useful for this turn, while keeping the prompt small."""
    if not memory:
        return {}
    words = set(re.findall(r"[\w-]{3,}", text.lower()))
    aliases = {
        "name": {"имя", "зовут", "name"},
        "city": {"город", "живу", "city", "live"},
        "timezone": {"часовой", "пояс", "timezone"},
        "work": {"работа", "работаю", "work", "job"},
        "likes": {"люблю", "нравится", "любимое", "like", "love"},
        "preferences": {"предпочитаю", "предпочтения", "удобнее", "prefer"},
        "preference:response_style": {"предпочитаю", "предпочтения", "формат", "ответы", "ответов", "подробно", "кратко", "style", "prefer"},
        "preference:drink": {"предпочитаю", "предпочтения", "напиток", "напитки", "пить", "чай", "кофе", "drink", "prefer"},
    }
    ranked = []
    for key, value in memory.items():
        if is_sensitive_memory(str(key), value):
            continue
        haystack = f"{key} {value}".lower()
        score = sum(1 for word in words if word in haystack)
        score += sum(2 for word in aliases.get(str(key), set()) if word in words)
        ranked.append((score, str(key), value))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return {key: value for score, key, value in ranked[:limit] if score > 0}


class MemoryStore(ABC):
    @abstractmethod
    def all(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def set(self, key: str, value: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def clear(self) -> None:
        raise NotImplementedError

    def semantic_records(self) -> tuple[SemanticMemoryRecord, ...]:
        return ()

    def save_semantic(self, record: SemanticMemoryRecord) -> None:
        if record.status == "confirmed" and record.category != "need":
            self.set(record.key, record.value)

    def semantic_history(self) -> tuple[SemanticMemoryRecord, ...]:
        return self.semantic_records()

    def replace_semantic(self, record: SemanticMemoryRecord, keys: tuple[str, ...], expected: dict[str, Any]) -> None:
        if self.all() != expected:
            raise MemoryConflictError("Memory changed")
        self.save_semantic(record)
        for key in keys:
            if key != record.key:
                self.delete(key)

    def delete_semantic(self, key: str) -> bool:
        return self.delete(key)


class JsonMemoryStore(MemoryStore):
    def __init__(self, path: Path) -> None:
        self.path = path

    def all(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}

        with self.path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return {}

        return data

    def set(self, key: str, value: Any) -> None:
        data = self.all()
        data[key] = value
        self._write(data)

    def delete(self, key: str) -> bool:
        data = self.all()
        if key not in data:
            return False

        del data[key]
        self._write(data)
        return True

    def clear(self) -> None:
        self._write({})

    def replace_semantic(self, record: SemanticMemoryRecord, keys: tuple[str, ...], expected: dict[str, Any]) -> None:
        data = self.all()
        if data != expected:
            raise MemoryConflictError("Memory changed")
        for key in keys:
            data.pop(key, None)
        data[record.key] = record.value
        self._write(data)

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())

            os.replace(temporary_path, self.path)
        except Exception:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise


def default_memory_path() -> Path:
    return Path.home() / ".xblago" / "personal_assistant_memory.json"
