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

from xblago.core.memory import MemoryStore, SemanticMemoryRecord, is_sensitive_memory, MemoryConflictError
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
        from xblago.core.sqlite_safety import enforce_pilot_sqlite
        enforce_pilot_sqlite()
        self.path = path
        self.legacy_memory_dir = legacy_memory_dir
        self._lock = RLock()
        self.conversation_lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def memory_for_user(self, blago_user_id: str) -> MemoryStore:
        return SQLiteUserMemoryStore(self, blago_user_id)

    def resolve_telegram_user(self, identity: TelegramIdentity, allow_reenroll: bool = False) -> str:
        with self._locked_transaction() as connection:
            blago_user_id = self._resolve_telegram_user(connection, identity, allow_reenroll)
            privacy_enabled = connection.execute("SELECT 1 FROM sqlite_master WHERE name='privacy_state'").fetchone() is not None

        if not privacy_enabled:
            self.migrate_legacy_telegram_memory(identity.chat_id, blago_user_id)
        return blago_user_id

    def _resolve_telegram_user(self, connection: sqlite3.Connection, identity: TelegramIdentity, allow_reenroll: bool = False) -> str:
        """Resolve within an existing transaction (auth proof + session commit)."""
        channel_user_id = str(identity.telegram_user_id) if identity.telegram_user_id is not None else f"chat:{identity.chat_id}"
        now = _utc_now()
        from xblago.core.privacy_control import PrivacyControl
        privacy = PrivacyControl.open(connection)
        expected_uid, blocked = privacy.channel_owner('telegram',channel_user_id) if privacy else (None,False)
        if blocked and not allow_reenroll:
            from xblago.core.security_journal import JournalUnavailable
            raise JournalUnavailable('Telegram link was removed. Use /start to explicitly create a new account')

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
            if expected_uid is not None and blago_user_id != expected_uid:
                from xblago.core.security_journal import JournalUnavailable
                raise JournalUnavailable('Telegram identity requires offline reconciliation')
        elif expected_uid is not None:
            status = connection.execute('SELECT status FROM users WHERE blago_user_id=?',(expected_uid,)).fetchone()
            if not status or status[0] != 'active':
                from xblago.core.security_journal import JournalUnavailable
                raise JournalUnavailable('Telegram identity is not active')
            blago_user_id = expected_uid
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

        return blago_user_id

    def migrate_legacy_telegram_memory(self, chat_id: int, blago_user_id: str) -> bool:
        with self._connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='privacy_state'").fetchone():
                from xblago.core.security_journal import JournalUnavailable
                raise JournalUnavailable('Legacy JSON imports are disabled after privacy activation')
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
                if is_sensitive_memory(str(key), value):
                    continue
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

        facts = {str(row["key"]): json.loads(str(row["value_json"])) for row in rows}
        for record in self.all_semantic_memory(blago_user_id):
            facts[record.key] = record.value
        return facts

    def all_semantic_memory(
        self,
        blago_user_id: str,
        include_inactive: bool = False,
        include_candidates: bool = False,
    ) -> tuple[SemanticMemoryRecord, ...]:
        query = """
            SELECT id, memory_key, value_json, category, source, status, confidence, created_at, updated_at
            FROM semantic_memory
            WHERE blago_user_id = ?
        """
        parameters: list[Any] = [blago_user_id]
        if not include_inactive:
            query += " AND status IN ('confirmed', 'candidate')" if include_candidates else " AND status = 'confirmed'"
        query += " ORDER BY updated_at DESC, id DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(
            SemanticMemoryRecord(
                record_id=str(row["id"]),
                key=str(row["memory_key"]),
                value=json.loads(str(row["value_json"])),
                category=str(row["category"]),
                source=str(row["source"]),
                status=str(row["status"]),
                confidence=float(row["confidence"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        )

    def upsert_semantic_memory(
        self,
        blago_user_id: str,
        key: str,
        value: Any,
        category: str,
        source: str,
        status: str,
        confidence: float,
    ) -> SemanticMemoryRecord:
        _validate_semantic_input(key, value, category, status, confidence)
        now = _utc_now()
        encoded = json.dumps(value, ensure_ascii=False)
        with self._locked_transaction() as connection:
            current = connection.execute(
                """
                SELECT id, value_json, category, source, status, confidence, created_at, updated_at
                FROM semantic_memory
                WHERE blago_user_id = ? AND memory_key = ? AND status IN ('confirmed', 'candidate')
                ORDER BY updated_at DESC, id DESC LIMIT 1
                """,
                (blago_user_id, key),
            ).fetchone()
            if current is not None and current["value_json"] == encoded and current["status"] == status:
                return _semantic_record_from_row(current, key=key)
            supersedes_id = str(current["id"]) if current is not None else None
            if current is not None:
                connection.execute(
                    "UPDATE semantic_memory SET status = 'superseded', updated_at = ? WHERE id = ?",
                    (now, supersedes_id),
                )
            connection.execute("DELETE FROM memory_facts WHERE blago_user_id = ? AND key = ?", (blago_user_id, key))
            record_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO semantic_memory (
                    id, blago_user_id, memory_key, value_json, category, source, status,
                    confidence, created_at, updated_at, supersedes_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (record_id, blago_user_id, key, encoded, category, source, status, confidence, now, now, supersedes_id),
            )
        return SemanticMemoryRecord(record_id, key, value, category, source, status, confidence, now, now)

    def replace_semantic_memory(
        self, uid: str, record: SemanticMemoryRecord, keys: tuple[str, ...], expected: dict[str, Any],
    ) -> None:
        """Archive aliases and legacy values, then publish one version in one transaction."""
        _validate_semantic_input(record.key, record.value, record.category, record.status, record.confidence)
        keys = tuple(dict.fromkeys((*keys, record.key)))
        marks = ",".join("?" for _ in keys)
        now = _utc_now()
        with self._locked_transaction() as db:
            facts = {str(r["key"]): json.loads(r["value_json"]) for r in db.execute(
                "SELECT key, value_json FROM memory_facts WHERE blago_user_id = ?", (uid,),
            )}
            for r in db.execute(
                "SELECT memory_key, value_json FROM semantic_memory WHERE blago_user_id = ? AND status = 'confirmed' ORDER BY updated_at DESC, id DESC", (uid,),
            ):
                facts[str(r["memory_key"])] = json.loads(r["value_json"])
            if facts != expected:
                raise MemoryConflictError("Memory changed")
            if keys == (record.key,) and facts.get(record.key) == record.value:
                return
            parent = db.execute(
                f"SELECT id FROM semantic_memory WHERE blago_user_id = ? AND memory_key IN ({marks}) AND status IN ('confirmed','candidate') ORDER BY updated_at DESC, id DESC LIMIT 1",
                (uid, *keys),
            ).fetchone()
            parent_id = str(parent["id"]) if parent else None
            db.execute(
                f"""UPDATE semantic_memory SET
                    source = CASE WHEN memory_key != ? THEN source || '|previous_key=' || memory_key ELSE source END,
                    memory_key = ?, status = CASE WHEN status IN ('confirmed','candidate') THEN 'superseded' ELSE status END,
                    updated_at = ? WHERE blago_user_id = ? AND memory_key IN ({marks})""",
                (record.key, record.key, now, uid, *keys),
            )
            for old in db.execute(
                f"SELECT key, value_json, updated_at FROM memory_facts WHERE blago_user_id = ? AND key IN ({marks})", (uid, *keys),
            ).fetchall():
                archive_id = str(uuid4())
                db.execute(
                    "INSERT INTO semantic_memory (id,blago_user_id,memory_key,value_json,category,source,status,confidence,created_at,updated_at,supersedes_id) VALUES (?,?,?,?,?,?,'superseded',1.0,?,?,?)",
                    (archive_id, uid, record.key, old["value_json"], record.category,
                     "legacy_fact|previous_key=" + str(old["key"]), old["updated_at"], now, parent_id),
                )
                parent_id = archive_id
            db.execute(f"DELETE FROM memory_facts WHERE blago_user_id = ? AND key IN ({marks})", (uid, *keys))
            db.execute(
                "INSERT INTO semantic_memory (id,blago_user_id,memory_key,value_json,category,source,status,confidence,created_at,updated_at,supersedes_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid4()), uid, record.key, json.dumps(record.value, ensure_ascii=False), record.category,
                 record.source, record.status, record.confidence, now, now, parent_id),
            )

    def delete_semantic_memory(self, blago_user_id: str, key: str) -> bool:
        return self.delete_memory(blago_user_id, key)

    def set_memory(self, blago_user_id: str, key: str, value: Any) -> None:
        if is_sensitive_memory(key, value):
            raise ValueError("Sensitive memory is not supported")
        with self._locked_transaction() as connection:
            connection.execute(
                "UPDATE semantic_memory SET status = 'superseded', updated_at = ? WHERE blago_user_id = ? AND memory_key = ? AND status IN ('confirmed', 'candidate')",
                (_utc_now(), blago_user_id, key),
            )
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
            semantic = connection.execute(
                "DELETE FROM semantic_memory WHERE blago_user_id = ? AND memory_key = ?",
                (blago_user_id, key),
            )
            return cursor.rowcount > 0 or semantic.rowcount > 0

    def clear_memory(self, blago_user_id: str) -> None:
        with self._locked_transaction() as connection:
            connection.execute("DELETE FROM memory_facts WHERE blago_user_id = ?", (blago_user_id,))
            connection.execute("DELETE FROM semantic_memory WHERE blago_user_id = ?", (blago_user_id,))

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
            connection.execute('UPDATE events SET response_json=NULL,error=NULL WHERE blago_user_id=?',(blago_user_id,))

    def append_exchange(
        self, blago_user_id: str, exchange: tuple[ConversationTurn, ConversationTurn],
        channel: str = "telegram", chat_id: int | None = None,
    ) -> None:
        """Commit a user/assistant pair together using the existing privacy writer."""
        if len(exchange) != 2 or tuple(t.role for t in exchange) != ("user", "assistant"):
            raise ValueError("A complete user/assistant exchange is required")
        with self._locked_transaction() as connection:
            connection.executemany(
                "INSERT INTO dialogue_turns (blago_user_id,channel,chat_id,role,text,created_at) VALUES (?,?,?,?,?,?)",
                [(blago_user_id, channel, str(chat_id) if chat_id is not None else None, turn.role, turn.text, _utc_now())
                 for turn in exchange],
            )

    def backup_database(self, destination_dir: Path | None = None) -> Path:
        target_dir = destination_dir or (self.path.parent / "backups" / "sqlite")
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_path = target_dir / f"{self.path.stem}-{timestamp}.sqlite3"
        with self._lock:
            source = sqlite3.connect(self.path, timeout=30.0)
            try:
                target = sqlite3.connect(backup_path)
                try:
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()
        return backup_path

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

    def cached_update_replies(
        self,
        channel: str,
        external_update_id: str | None,
    ) -> list[dict[str, str | int]] | None:
        if external_update_id is None:
            return None

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status, response_json
                FROM events
                WHERE channel = ? AND external_update_id = ?
                """,
                (channel, external_update_id),
            ).fetchone()

        if row is None or row["status"] != "processed" or row["response_json"] is None:
            return None

        try:
            replies = json.loads(str(row["response_json"]))
        except json.JSONDecodeError:
            return None

        if not isinstance(replies, list):
            return None

        safe_replies: list[dict[str, str | int]] = []
        for reply in replies:
            if not isinstance(reply, dict):
                return None
            chat_id = reply.get("chat_id")
            text = reply.get("text")
            if not isinstance(chat_id, int) or not isinstance(text, str):
                return None
            safe_replies.append({"chat_id": chat_id, "text": text})

        return safe_replies

    def mark_update_processed(
        self,
        channel: str,
        external_update_id: str | None,
        replies: list[dict[str, str | int]] | None = None,
    ) -> None:
        self._mark_update(channel, external_update_id, "processed", None, replies)

    def mark_update_failed(self, channel: str, external_update_id: str | None, error: str) -> None:
        self._mark_update(channel, external_update_id, "failed", error, None)

    def _mark_update(
        self,
        channel: str,
        external_update_id: str | None,
        status: str,
        error: str | None,
        replies: list[dict[str, str | int]] | None,
    ) -> None:
        if external_update_id is None:
            return

        response_json = json.dumps(replies, ensure_ascii=False) if replies is not None else None
        with self._locked_transaction() as connection:
            connection.execute(
                """
                UPDATE events
                SET status = ?, error = ?, response_json = ?, updated_at = ?
                WHERE channel = ? AND external_update_id = ?
                """,
                (status, error, response_json, _utc_now(), channel, external_update_id),
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

                CREATE TABLE IF NOT EXISTS semantic_memory (
                    id TEXT PRIMARY KEY,
                    blago_user_id TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    category TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    supersedes_id TEXT,
                    FOREIGN KEY (blago_user_id) REFERENCES users(blago_user_id),
                    FOREIGN KEY (supersedes_id) REFERENCES semantic_memory(id)
                );

                CREATE INDEX IF NOT EXISTS idx_semantic_memory_user_key
                    ON semantic_memory(blago_user_id, memory_key, updated_at);

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
                    response_json TEXT,
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
            _ensure_column(connection, "events", "response_json", "TEXT")

    @contextmanager
    def _locked_transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            with self._connect() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    from xblago.core.privacy_control import PrivacyControl
                    privacy = PrivacyControl.open(connection)
                    # Persist already-durable revocations before evaluating a request that
                    # might be rejected (and therefore roll back its business transaction).
                    while privacy and privacy.reconcile():
                        connection.commit()
                        connection.execute('BEGIN IMMEDIATE')
                        privacy = PrivacyControl.open(connection)
                    yield connection
                    if privacy:
                        privacy.flush()
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

    def semantic_records(self) -> tuple[SemanticMemoryRecord, ...]:
        return self.store.all_semantic_memory(self.blago_user_id, include_candidates=True)

    def save_semantic(self, record: SemanticMemoryRecord) -> None:
        self.store.upsert_semantic_memory(
            self.blago_user_id,
            record.key,
            record.value,
            record.category,
            record.source,
            record.status,
            record.confidence,
        )

    def semantic_history(self) -> tuple[SemanticMemoryRecord, ...]:
        return self.store.all_semantic_memory(self.blago_user_id, include_inactive=True)

    def replace_semantic(self, record: SemanticMemoryRecord, keys: tuple[str, ...], expected: dict[str, Any]) -> None:
        self.store.replace_semantic_memory(self.blago_user_id, record, keys, expected)

    def delete(self, key: str) -> bool:
        return self.store.delete_memory(self.blago_user_id, key)

    def clear(self) -> None:
        self.store.clear_memory(self.blago_user_id)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _semantic_record_from_row(row: sqlite3.Row, key: str | None = None) -> SemanticMemoryRecord:
    return SemanticMemoryRecord(
        record_id=str(row["id"]),
        key=key or str(row["memory_key"]),
        value=json.loads(str(row["value_json"])),
        category=str(row["category"]),
        source=str(row["source"]),
        status=str(row["status"]),
        confidence=float(row["confidence"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _display_name(identity: TelegramIdentity) -> str | None:
    parts = [part for part in (identity.first_name, identity.last_name) if part]
    if parts:
        return " ".join(parts)

    return identity.username


def _validate_semantic_input(key: str, value: Any, category: str, status: str, confidence: float) -> None:
    if (not isinstance(key, str) or not isinstance(value, str)
            or not 1 <= len(key) <= 64 or not 1 <= len(value) <= 240
            or category not in {"fact", "interest", "preference", "skill", "goal", "need"}
            or status not in {"confirmed", "candidate"}
            or (category == "need" and status != "candidate")
            or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not 0.8 <= confidence <= 1 or is_sensitive_memory(key, value)):
        raise ValueError("Invalid semantic memory")
