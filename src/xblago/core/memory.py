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


def is_sensitive_memory(key: str, value: Any) -> bool:
    text = f"{key} {value}".lower()
    return any(term in text for term in SENSITIVE_MEMORY_TERMS)


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
        "preferences": {"предпочитаю", "удобнее", "prefer"},
    }
    ranked = []
    for key, value in memory.items():
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
        self.set(record.key, record.value)

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
