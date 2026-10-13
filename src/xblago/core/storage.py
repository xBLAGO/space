from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4

from xblago.core.memory import MemoryStore
from xblago.core.messages import ConversationTurn, MessageRole


@dataclass(frozen=True)
class TelegramIdentity:
    telegram_user_id: int | None
    chat_id: int
    chat_type: str | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class SQLiteBlagoStore:
    def __init__(self, path: Path, legacy_memory_dir: Path | None = None) -> None:
        self.path = path
        self.legacy_memory_dir = legacy_memory_dir
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def memory_for_user(self, blago_user_id: str) -> MemoryStore:
        return SQLiteUserMemoryStore(self, blago_user_id)

    def resolve_telegram_user(self, identity: TelegramIdentity) -> str:
        channel_user_id = str(identity.telegram_user_id) if identity.telegram_user_id is not None else f"chat:{identity.chat_id}"
        now = _utc_now()

        with self._locked_transaction() as connection:
            row = connection.execute(
                """
                SELECT blago_user_id
                FROM channel_links
                WHERE channel = ? AND channel_user_id = ?
                """,
                ("telegram", channel_user_id),
            ).fetchone()
            if row:
                blago_user_id = str(row["blago_user_id"])
            else:
                blago_user_id = str(uuid4())
                connection.execute(
                    "INSERT INTO users (blago_user_id, created_at, status) VALUES (?, ?, ?)",
                    (blago_user_id, now, "active"),
                )
                connection.execute(
                    """
                    INSERT INTO profiles (blago_user_id, display_name, language, timezone, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (blago_user_id, _display_name(identity), None, None, now),
                )

            connection.execute(
                """
                INSERT INTO channel_links (
                    channel, channel_user_id, channel_chat_id, chat_type, blago_user_id,
                    username, first_name, last_name, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, channel_user_id) DO UPDATE SET
                    channel_chat_id = excluded.channel_chat_id,
                    chat_type = excluded.chat_type,
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    updated_at = excluded.updated_at
                """,
                (
                    "telegram",
                    channel_user_id,
                    str(identity.chat_id),
                    identity.chat_type,
                    blago_user_id,
                    identity.username,
                    identity.first_name,
                    identity.last_name,
                    now,
                    now,
                ),
            )

        self.migrate_legacy_telegram_memory(identity.chat_id, blago_user_id)
        return blago_user_id

    def migrate_legacy_telegram_memory(self, chat_id: int, blago_user_id: str) -> bool:
        if self.legacy_memory_dir is None:
            return False

        legacy_path = self.legacy_memory_dir / f"telegram_{chat_id}.json"
        if not legacy_path.exists():
            return False

        migration_key = f"telegram:{chat_id}:{legacy_path.name}"
        with self._locked_transaction() as connection:
            existing = connection.execute(
                "SELECT 1 FROM migrations WHERE migration_key = ?",
                (migration_key,),
            ).fetchone()
            if existing:
                return False

        with legacy_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            data = {}

        backup_path = self._backup_legacy_file(legacy_path)
        now = _utc_now()
        with self._locked_transaction() as connection:
            for key, value in data.items():
                exists = connection.execute(
                    "SELECT 1 FROM memory_facts WHERE blago_user_id = ? AND key = ?",
                    (blago_user_id, str(key)),
                ).fetchone()
                if exists:
                    continue
                connection.execute(
                    """
                    INSERT INTO memory_facts (blago_user_id, key, value_json, source_event_id, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (blago_user_id, str(key), json.dumps(value, ensure_ascii=False), None, now),
                )

            connection.execute(
                """
                INSERT INTO migrations (migration_key, source_path, backup_path, blago_user_id, migrated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (migration_key, str(legacy_path), str(backup_path), blago_user_id, now),
            )

        return True

    def all_memory(self, blago_user_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT key, value_json
                FROM memory_facts
                WHERE blago_user_id = ?
                ORDER BY key
                """,
                (blago_user_id,),
            ).fetchall()

        return {str(row["key"]): json.loads(str(row["value_json"])) for row in rows}

    def set_memory(self, blago_user_id: str, key: str, value: Any) -> None:
        with self._locked_transaction() as connection:
            connection.execute(
                """
                INSERT INTO memory_facts (blago_user_id, key, value_json, source_event_id, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(blago_user_id, key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (blago_user_id, key, json.dumps(value, ensure_ascii=False), None, _utc_now()),
            )

    def delete_memory(self, blago_user_id: str, key: str) -> bool:
        with self._locked_transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM memory_facts WHERE blago_user_id = ? AND key = ?",
                (blago_user_id, key),
            )
            return cursor.rowcount > 0

    def clear_memory(self, blago_user_id: str) -> None:
        with self._locked_transaction() as connection:
            connection.execute("DELETE FROM memory_facts WHERE blago_user_id = ?", (blago_user_id,))

    def recent_history(self, blago_user_id: str, limit: int) -> tuple[ConversationTurn, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, text
                FROM (
                    SELECT id, role, text
                    FROM dialogue_turns
                    WHERE blago_user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (blago_user_id, limit),
            ).fetchall()

        return tuple(ConversationTurn(role=row["role"], text=row["text"]) for row in rows)

    def append_history(
        self,
        blago_user_id: str,
        role: MessageRole,
        text: str,
        channel: str = "telegram",
        chat_id: int | None = None,
    ) -> None:
        with self._locked_transaction() as connection:
            connection.execute(
                """
                INSERT INTO dialogue_turns (blago_user_id, channel, chat_id, role, text, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (blago_user_id, channel, str(chat_id) if chat_id is not None else None, role, text, _utc_now()),
            )

    def clear_history(self, blago_user_id: str) -> None:
        with self._locked_transaction() as connection:
            connection.execute("DELETE FROM dialogue_turns WHERE blago_user_id = ?", (blago_user_id,))

    def begin_update(self, channel: str, external_update_id: str | None, blago_user_id: str) -> bool:
        if external_update_id is None:
            return True

        now = _utc_now()
        with self._locked_transaction() as connection:
            row = connection.execute(
                """
                SELECT status
                FROM events
                WHERE channel = ? AND external_update_id = ?
                """,
                (channel, external_update_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO events (channel, external_update_id, blago_user_id, status, error, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (channel, external_update_id, blago_user_id, "processing", None, now, now),
                )
                return True

            if row["status"] == "failed":
                connection.execute(
                    """
                    UPDATE events
                    SET blago_user_id = ?, status = ?, error = ?, updated_at = ?
                    WHERE channel = ? AND external_update_id = ?
                    """,
                    (blago_user_id, "processing", None, now, channel, external_update_id),
                )
                return True

            return False

    def mark_update_processed(self, channel: str, external_update_id: str | None) -> None:
        self._mark_update(channel, external_update_id, "processed", None)

    def mark_update_failed(self, channel: str, external_update_id: str | None, error: str) -> None:
        self._mark_update(channel, external_update_id, "failed", error)

    def _mark_update(self, channel: str, external_update_id: str | None, status: str, error: str | None) -> None:
        if external_update_id is None:
            return

        with self._locked_transaction() as connection:
            connection.execute(
                """
                UPDATE events
                SET status = ?, error = ?, updated_at = ?
                WHERE channel = ? AND external_update_id = ?
                """,
                (status, error, _utc_now(), channel, external_update_id),
            )

    def _backup_legacy_file(self, legacy_path: Path) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_dir = legacy_path.parent / "backups" / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / legacy_path.name
        shutil.copy2(legacy_path, backup_path)
        return backup_path

    def _ensure_schema(self) -> None:
        with self._locked_transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    blago_user_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS channel_links (
                    channel TEXT NOT NULL,
                    channel_user_id TEXT NOT NULL,
                    channel_chat_id TEXT NOT NULL,
                    chat_type TEXT,
                    blago_user_id TEXT NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (channel, channel_user_id),
                    FOREIGN KEY (blago_user_id) REFERENCES users(blago_user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_channel_links_user
                    ON channel_links(blago_user_id);

                CREATE TABLE IF NOT EXISTS profiles (
                    blago_user_id TEXT PRIMARY KEY,
                    display_name TEXT,
                    language TEXT,
                    timezone TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (blago_user_id) REFERENCES users(blago_user_id)
                );

                CREATE TABLE IF NOT EXISTS memory_facts (
                    blago_user_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    source_event_id INTEGER,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (blago_user_id, key),
                    FOREIGN KEY (blago_user_id) REFERENCES users(blago_user_id),
                    FOREIGN KEY (source_event_id) REFERENCES events(id)
                );

                CREATE TABLE IF NOT EXISTS dialogue_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    blago_user_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    chat_id TEXT,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (blago_user_id) REFERENCES users(blago_user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_dialogue_turns_user_id
                    ON dialogue_turns(blago_user_id, id);

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel TEXT NOT NULL,
                    external_update_id TEXT NOT NULL,
                    blago_user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(channel, external_update_id),
                    FOREIGN KEY (blago_user_id) REFERENCES users(blago_user_id)
                );

                CREATE TABLE IF NOT EXISTS migrations (
                    migration_key TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    backup_path TEXT NOT NULL,
                    blago_user_id TEXT NOT NULL,
                    migrated_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _locked_transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            with self._connect() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    yield connection
                except Exception:
                    connection.rollback()
                    raise
                else:
                    connection.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()


class SQLiteUserMemoryStore(MemoryStore):
    def __init__(self, store: SQLiteBlagoStore, blago_user_id: str) -> None:
        self.store = store
        self.blago_user_id = blago_user_id

    def all(self) -> dict[str, Any]:
        return self.store.all_memory(self.blago_user_id)

    def set(self, key: str, value: Any) -> None:
        self.store.set_memory(self.blago_user_id, key, value)

    def delete(self, key: str) -> bool:
        return self.store.delete_memory(self.blago_user_id, key)

    def clear(self) -> None:
        self.store.clear_memory(self.blago_user_id)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _display_name(identity: TelegramIdentity) -> str | None:
    parts = [part for part in (identity.first_name, identity.last_name) if part]
    if parts:
        return " ".join(parts)

    return identity.username
