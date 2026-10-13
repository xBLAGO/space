from __future__ import annotations

import json
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


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
