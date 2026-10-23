"""One secure, shared HTTP entrypoint for BLAGO Web/PWA and Telegram Mini App.

Web is opt-in: users register with an invite, or log in through verified
Telegram initData. The existing Telegram webhook routes remain intact.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from xblago.agents.personal_assistant import PersonalAssistantAgent
from xblago.core.config import load_llm_config
from xblago.core.llm import build_chat_provider
from xblago.core.messages import UserMessage
from xblago.core.storage import SQLiteBlagoStore, TelegramIdentity
from xblago.integrations.telegram import TelegramWebhookHandler


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(date: datetime | None = None) -> str:
    return (date or _now()).isoformat()


def verify_telegram_init_data(init_data: str, bot_token: str, max_age: int = 3600) -> dict[str, Any]:
    """Validate raw Telegram WebApp initData, never initDataUnsafe."""
    pairs = parse_qsl(init_data, keep_blank_values=True)
    values = dict(pairs)
    if len(pairs) != len(values) or not values.get("hash") or not values.get("user"):
        raise ApiError(401, "Invalid Telegram authentication")
    check = "\n".join(f"{key}={value}" for key, value in sorted(values.items()) if key != "hash")
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, values["hash"]):
        raise ApiError(401, "Invalid Telegram authentication")
    try:
        age = _now().timestamp() - int(values["auth_date"])
        user = json.loads(values["user"])
        if not isinstance(user, dict) or isinstance(user.get("id"), bool) or not isinstance(user.get("id"), int):
            raise ValueError("invalid Telegram user")
        if age < -60 or age > max_age:
            raise ValueError("expired Telegram login")
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ApiError(401, "Expired or invalid Telegram authentication") from exc
    return user


def _required_text(value: Any, label: str, min_length: int, max_length: int) -> str:
    if not isinstance(value, str) or not min_length <= len(value.strip()) <= max_length:
        raise ApiError(400, f"{label} must contain {min_length}-{max_length} characters")
    return value.strip()


def _optional_text(value: Any, label: str, max_length: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > max_length:
        raise ApiError(400, f"{label} is too long")
    return value.strip()


def _choice(value: Any, allowed: set[str], message: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ApiError(400, message)
    return value


def _optional_amount(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ApiError(400, "Invalid amount")
    try:
        amount = int(value)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "Invalid amount") from exc
    if amount <= 0 or amount > 100_000_000_00:
        raise ApiError(400, "Invalid amount")
    return amount


def _currency(value: Any) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "Invalid currency")
    currency = value.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ApiError(400, "Invalid currency")
    return currency


def _decode_json_field(row: dict[str, Any], source: str, target: str) -> dict[str, Any]:
    raw = row.pop(source, "{}")
    try:
        decoded = json.loads(str(raw))
    except json.JSONDecodeError:
        decoded = {}
    row[target] = decoded if isinstance(decoded, dict) else {}
    return row


def _classify_payment_risk(
    title: str,
    description: str,
    payment_purpose: str,
    amount_minor: int | None,
) -> tuple[str, str]:
    text = " ".join((title, description, payment_purpose)).lower()
    blocked = ("pin", "cvv", "пароль", "пин", "код карты", "доступ к банку", "password", "bank login")
    if any(term in text for term in blocked):
        return "rejected", "Запрос содержит признаки запрещённых банковских секретов."
    if amount_minor is None:
        return "medium", "Нет суммы: потребуется ручная проверка назначения."
    if amount_minor > 300_000_00:
        return "high", "Крупная сумма для MVP: требуется дополнительное ручное решение."
    return "low", "Запрос допустим для симуляции и ручного подтверждения в MVP."


class WebApp:
    def __init__(self, store: SQLiteBlagoStore) -> None:
        self.store = store
        self._auth_lock = RLock()
        self._auth_attempts: dict[str, list[float]] = {}
        self._ensure_schema()
        from xblago.integrations.identity import IdentityRuntime
        self.identity = IdentityRuntime(store)

    def check_auth_rate(self, key: str) -> None:
        # In-memory coarse protection for a single-instance pilot deployment.
        import time
        now = time.monotonic()
        with self._auth_lock:
            if len(self._auth_attempts) > 500:
                self._auth_attempts = {
                    k: [x for x in v if x > now - 60]
                    for k, v in self._auth_attempts.items() if any(x > now - 60 for x in v)
                }
            attempts = [x for x in self._auth_attempts.get(key, ()) if x > now - 60]
            if len(attempts) >= 30:
                raise ApiError(429, "Too many login attempts. Try again later")
            attempts.append(now)
            self._auth_attempts[key] = attempts

    def _ensure_schema(self) -> None:
        with self.store._locked_transaction() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS web_credentials (
                username TEXT PRIMARY KEY COLLATE NOCASE,
                blago_user_id TEXT NOT NULL UNIQUE REFERENCES users(blago_user_id),
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS web_sessions (
                session_hash TEXT PRIMARY KEY,
                blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
                csrf TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_web_sessions_user ON web_sessions(blago_user_id);
            CREATE TABLE IF NOT EXISTS account_link_codes (
                code_hash TEXT PRIMARY KEY,
                target_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS blago_needs (
                id TEXT PRIMARY KEY, blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
                title TEXT NOT NULL, description TEXT NOT NULL, visibility TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_blago_needs_user ON blago_needs(blago_user_id);
            CREATE TABLE IF NOT EXISTS blago_resources (
                id TEXT PRIMARY KEY, blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
                title TEXT NOT NULL, description TEXT NOT NULL, visibility TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_blago_resources_user ON blago_resources(blago_user_id);
            CREATE TABLE IF NOT EXISTS blago_assets (
                id TEXT PRIMARY KEY,
                blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                ownership_claim TEXT NOT NULL,
                use_permission TEXT NOT NULL,
                management_status TEXT NOT NULL,
                verification_status TEXT NOT NULL,
                visibility TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_blago_assets_user ON blago_assets(blago_user_id);
            CREATE TABLE IF NOT EXISTS blago_resource_offers (
                id TEXT PRIMARY KEY,
                blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                offer_type TEXT NOT NULL,
                availability_status TEXT NOT NULL,
                visibility TEXT NOT NULL,
                max_amount_minor INTEGER,
                currency TEXT,
                constraints_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_blago_resource_offers_user ON blago_resource_offers(blago_user_id);
            CREATE TABLE IF NOT EXISTS blago_payment_requests (
                id TEXT PRIMARY KEY,
                need_id TEXT REFERENCES blago_needs(id),
                requester_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
                executor_user_id TEXT REFERENCES users(blago_user_id),
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                amount_minor INTEGER,
                currency TEXT,
                payment_purpose TEXT NOT NULL,
                payment_reference_digest TEXT,
                risk_level TEXT NOT NULL,
                risk_reason TEXT NOT NULL,
                status TEXT NOT NULL,
                contribution_recorded INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_blago_payment_requests_requester ON blago_payment_requests(requester_user_id);
            CREATE INDEX IF NOT EXISTS idx_blago_payment_requests_executor ON blago_payment_requests(executor_user_id);
            CREATE TABLE IF NOT EXISTS blago_payment_request_offers (
                id TEXT PRIMARY KEY,
                payment_request_id TEXT NOT NULL REFERENCES blago_payment_requests(id),
                resource_offer_id TEXT NOT NULL REFERENCES blago_resource_offers(id),
                offered_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(payment_request_id, offered_user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_blago_payment_request_offers_user ON blago_payment_request_offers(offered_user_id);
            CREATE TABLE IF NOT EXISTS blago_fulfillment_events (
                id TEXT PRIMARY KEY,
                payment_request_id TEXT REFERENCES blago_payment_requests(id),
                need_id TEXT REFERENCES blago_needs(id),
                actor_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
                event_type TEXT NOT NULL,
                status_after TEXT NOT NULL,
                note TEXT NOT NULL,
                evidence_digest TEXT,
                event_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_blago_fulfillment_events_request ON blago_fulfillment_events(payment_request_id);
            CREATE TABLE IF NOT EXISTS blago_contribution_events (
                id TEXT PRIMARY KEY,
                payment_request_id TEXT REFERENCES blago_payment_requests(id),
                contributor_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
                recipient_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
                contribution_type TEXT NOT NULL,
                amount_minor INTEGER,
                currency TEXT,
                xblago_estimate REAL,
                exchange_rate_source TEXT,
                exchange_rate_at TEXT,
                note TEXT NOT NULL,
                event_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_blago_contribution_events_contributor ON blago_contribution_events(contributor_user_id);
            """)

    def register(self, username: str, password: str, invite: str) -> tuple[str, str]:
        configured = os.getenv("XBLAGO_WEB_INVITE_CODE", "")
        if not configured or not hmac.compare_digest(invite, configured):
            raise ApiError(403, "A valid invitation is required")
        username = username.strip()
        if not 3 <= len(username) <= 32 or not all(c.isascii() and (c.isalnum() or c in "_-") for c in username):
            raise ApiError(400, "Username must be 3-32 letters, digits, _ or -")
        if not 12 <= len(password) <= 256:
            raise ApiError(400, "Password must contain 12-256 characters")
        uid, salt = str(uuid4()), secrets.token_hex(16)
        encoded = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 400_000).hex()
        timestamp = _iso()
        try:
            with self.store._locked_transaction() as db:
                db.execute("INSERT INTO users VALUES (?, ?, ?)", (uid, timestamp, "active"))
                db.execute(
                    "INSERT INTO profiles (blago_user_id, display_name, language, timezone, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (uid, username, "ru", None, timestamp),
                )
                db.execute(
                    "INSERT INTO web_credentials VALUES (?, ?, ?, ?, ?)",
                    (username, uid, encoded, salt, timestamp),
                )
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, "Username already exists") from exc
        return self.new_session(uid)

    def login(self, username: str, password: str, old_token: str | None = None, expected_uid: str | None = None) -> tuple[str, str]:
        if len(password) > 256:
            raise ApiError(400, 'Password is too long')
        with self.store._connect() as db:
            row = db.execute(
                "SELECT blago_user_id, password_hash, salt FROM web_credentials WHERE username = ?",
                (username.strip(),),
            ).fetchone()
        # Run the expensive hash even for unknown usernames to avoid a cheap oracle.
        salt = row["salt"] if row else "00" * 16
        expected = row["password_hash"] if row else "00" * 32
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 400_000).hex()
        if not row or not hmac.compare_digest(expected, candidate):
            raise ApiError(401, "Incorrect username or password")
        if expected_uid is not None and expected_uid != row['blago_user_id']:
            raise ApiError(403, 'Account mismatch')
        if self.identity.enabled:
            with self.store._locked_transaction() as db:
                current = db.execute('SELECT password_hash,salt FROM web_credentials WHERE blago_user_id=?', (row['blago_user_id'],)).fetchone()
                if current is None or current['password_hash'] != expected or current['salt'] != salt:
                    raise ApiError(401, 'Access method changed. Please sign in again')
                if expected_uid is not None:
                    current_session = self.identity.session_row(db, old_token)
                    if current_session['blago_user_id'] != expected_uid:
                        raise ApiError(403, 'Account mismatch')
                return self.identity.mint(db, str(row['blago_user_id']), 'password', old_token)
        return self.new_session(str(row["blago_user_id"]))

    def telegram_login(self, init_data: str, challenge: str = '', binding: str = '', old_token: str | None = None) -> tuple[str, str]:
        if self.identity.enabled:
            return self.identity.telegram_login(init_data, challenge, binding, old_token)
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise ApiError(503, "Telegram login is not configured")
        user = verify_telegram_init_data(init_data, token)
        identity = TelegramIdentity(
            telegram_user_id=user["id"], chat_id=user["id"], chat_type="private",
            username=user.get("username") if isinstance(user.get("username"), str) else None,
            first_name=user.get("first_name") if isinstance(user.get("first_name"), str) else None,
            last_name=user.get("last_name") if isinstance(user.get("last_name"), str) else None,
        )
        return self.new_session(self.store.resolve_telegram_user(identity))

    def new_link_code(self, web_uid: str, token: str | None = None) -> str:
        if self.identity.enabled:
            self.identity.require_fresh(token, web_uid)
        # Only a signed-in web account can issue a Telegram linking code.
        with self.store._connect() as db:
            credential = db.execute(
                "SELECT 1 FROM web_credentials WHERE blago_user_id = ?", (web_uid,),
            ).fetchone()
            if credential is None and self.identity.enabled:
                credential = db.execute('SELECT 1 FROM passkey_credentials WHERE blago_user_id=? AND revoked_at IS NULL', (web_uid,)).fetchone()
        if credential is None:
            raise ApiError(403, "Sign in through the website to create a linking code")
        code = secrets.token_hex(8).upper()
        with self.store._locked_transaction() as db:
            if self.identity.enabled:
                authenticated = self.identity.session_row(db, token, fresh=True)
                if authenticated['blago_user_id'] != web_uid:
                    raise ApiError(403, 'Account mismatch')
            db.execute("DELETE FROM account_link_codes WHERE target_user_id = ? OR expires_at < ?",
                       (web_uid, _iso()))
            db.execute("INSERT INTO account_link_codes VALUES (?, ?, ?)",
                       (hashlib.sha256(code.encode()).hexdigest(), web_uid, _iso(_now() + timedelta(minutes=10))))
        return code

    def redeem_telegram_link(self, telegram_uid: str, code: str) -> str:
        try:
            return self._redeem_telegram_link(telegram_uid, code)
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, 'Account merge conflicts with existing data; no changes were applied') from exc

    def _redeem_telegram_link(self, telegram_uid: str, code: str) -> str:
        # Caller MUST authenticate Telegram sender (webhook or verified initData).
        # A one-time secret proves control of the destination web account.
        if len(code) != 16 or any(c not in "0123456789ABCDEF" for c in code.upper()):
            raise ApiError(400, "Invalid linking code")
        with self.store.conversation_lock, self.store._locked_transaction() as db:
            row = db.execute(
                "SELECT target_user_id FROM account_link_codes WHERE code_hash = ? AND expires_at > ?",
                (hashlib.sha256(code.upper().encode()).hexdigest(), _iso()),
            ).fetchone()
            if row is None:
                raise ApiError(400, "Linking code is invalid or expired")
            target_uid = str(row["target_user_id"])
            # Revoke the single-use code before any account modifications.
            db.execute("DELETE FROM account_link_codes WHERE target_user_id = ?", (target_uid,))
            if telegram_uid == target_uid:
                return target_uid
            if db.execute("SELECT 1 FROM web_credentials WHERE blago_user_id = ?", (telegram_uid,)).fetchone():
                raise ApiError(409, "This Telegram identity belongs to another web account")
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='passkey_credentials'").fetchone():
                self.identity.merge(db, telegram_uid, target_uid, revoke_sessions=self.identity.enabled)
            # Preserve all dialogue, needs, resources, and event audit records.
            for table in (
                "dialogue_turns",
                "events",
                "blago_needs",
                "blago_resources",
                "blago_assets",
                "blago_resource_offers",
                "web_sessions",
            ):
                db.execute(f"UPDATE {table} SET blago_user_id = ? WHERE blago_user_id = ?", (target_uid, telegram_uid))
            db.execute(
                "UPDATE blago_payment_requests SET requester_user_id = ? WHERE requester_user_id = ?",
                (target_uid, telegram_uid),
            )
            db.execute(
                "UPDATE blago_payment_requests SET executor_user_id = ? WHERE executor_user_id = ?",
                (target_uid, telegram_uid),
            )
            db.execute(
                "UPDATE blago_payment_request_offers SET offered_user_id = ? WHERE offered_user_id = ?",
                (target_uid, telegram_uid),
            )
            db.execute(
                "UPDATE blago_fulfillment_events SET actor_user_id = ? WHERE actor_user_id = ?",
                (target_uid, telegram_uid),
            )
            db.execute(
                "UPDATE blago_contribution_events SET contributor_user_id = ? WHERE contributor_user_id = ?",
                (target_uid, telegram_uid),
            )
            db.execute(
                "UPDATE blago_contribution_events SET recipient_user_id = ? WHERE recipient_user_id = ?",
                (target_uid, telegram_uid),
            )
            # Allocate once per logical key across both memory representations.
            # Keep the destination's facts and all source versions under a unique key.
            source_keys = db.execute(
                "SELECT key FROM memory_facts WHERE blago_user_id = ? UNION SELECT memory_key FROM semantic_memory WHERE blago_user_id = ?",
                (telegram_uid, telegram_uid),
            ).fetchall()
            occupied = {str(row[0]) for row in db.execute(
                "SELECT key FROM memory_facts WHERE blago_user_id = ? UNION SELECT memory_key FROM semantic_memory WHERE blago_user_id = ?",
                (target_uid, target_uid),
            )}
            reserved = occupied | {str(row[0]) for row in source_keys}
            for row in source_keys:
                original = str(row[0])
                key = original
                if key in occupied:
                    key = "telegram." + original
                    suffix = 2
                    while key in reserved:
                        key = f"telegram.{original}.{suffix}"
                        suffix += 1
                reserved.add(key)
                db.execute(
                    "UPDATE memory_facts SET blago_user_id = ?, key = ? WHERE blago_user_id = ? AND key = ?",
                    (target_uid, key, telegram_uid, original),
                )
                db.execute(
                    "UPDATE semantic_memory SET blago_user_id = ?, memory_key = ? WHERE blago_user_id = ? AND memory_key = ?",
                    (target_uid, key, telegram_uid, original),
                )
            db.execute(
                "UPDATE channel_links SET blago_user_id = ? WHERE channel = 'telegram' AND blago_user_id = ?",
                (target_uid, telegram_uid),
            )
            db.execute("UPDATE users SET status = 'merged' WHERE blago_user_id = ?", (telegram_uid,))
        return target_uid

    def new_session(self, uid: str) -> tuple[str, str]:
        if self.identity.enabled:
            return self.identity.new_session(uid)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        with self.store._locked_transaction() as db:
            self.identity.active(db, uid)
            db.execute(
                "INSERT INTO web_sessions VALUES (?, ?, ?, ?)",
                (hashlib.sha256(token.encode()).hexdigest(), uid, csrf, _iso(_now() + timedelta(days=7))),
            )
        return token, csrf

    def session(self, token: str | None) -> tuple[str, str]:
        if self.identity.enabled:
            return self.identity.session(token)
        if not token:
            raise ApiError(401, "Please sign in")
        with self.store._connect() as db:
            row = db.execute(
                "SELECT s.blago_user_id, s.csrf FROM web_sessions s JOIN users u USING(blago_user_id) WHERE session_hash = ? AND expires_at > ? AND u.status='active'",
                (hashlib.sha256(token.encode()).hexdigest(), _iso()),
            ).fetchone()
        if row is None:
            raise ApiError(401, "Session expired. Please sign in again")
        return str(row["blago_user_id"]), str(row["csrf"])

    def logout(self, token: str | None) -> None:
        if token:
            with self.store._locked_transaction() as db:
                db.execute("DELETE FROM web_sessions WHERE session_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))

    def me(self, uid: str) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute(
                "SELECT p.display_name, p.language, p.timezone FROM profiles p WHERE p.blago_user_id = ?", (uid,),
            ).fetchone()
        if row is None:
            raise ApiError(404, "Profile not found")
        return {"blago_user_id": uid, **dict(row)}

    def update_me(self, uid: str, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"display_name", "language", "timezone"}
        if not set(payload).issubset(allowed):
            raise ApiError(400, "Unsupported profile field")
        name = payload.get("display_name")
        language = payload.get("language")
        zone = payload.get("timezone")
        if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 80):
            raise ApiError(400, "Invalid display name")
        if language is not None and language not in ("ru", "en"):
            raise ApiError(400, "Invalid language")
        if zone is not None and (not isinstance(zone, str) or len(zone) > 80):
            raise ApiError(400, "Invalid timezone")
        fields = []
        values: list[Any] = []
        for key in ("display_name", "language", "timezone"):
            if key in payload:
                fields.append(f"{key} = ?")
                values.append(payload[key].strip() if key == "display_name" else payload[key])
        if fields:
            with self.store._locked_transaction() as db:
                db.execute(
                    f"UPDATE profiles SET {', '.join(fields)}, updated_at = ? WHERE blago_user_id = ?",
                    (*values, _iso(), uid),
                )
        return self.me(uid)

    def history(self, uid: str) -> list[dict[str, str]]:
        return [{"role": t.role, "text": t.text} for t in self.store.recent_history(uid, 100)]

    def chat(self, uid: str, text: str) -> str:
        with self.store.conversation_lock:
            agent = PersonalAssistantAgent(
                memory=self.store.memory_for_user(uid),
                chat_provider=build_chat_provider(load_llm_config()),
                initial_history=self.store.recent_history(uid, 12),
                history_recorder=lambda t: self.store.append_history(uid, t.role, t.text, channel="web"),
                history_clearer=lambda: self.store.clear_history(uid),
            )
            return agent.respond(UserMessage(text=text)).text

    def delete_memory(self, uid: str, key: str) -> bool:
        # A completed delete must not be undone by an already running chat turn.
        with self.store.conversation_lock:
            return self.store.memory_for_user(uid).delete(key)

    def rows(self, kind: str, uid: str) -> list[dict[str, Any]]:
        table = self._table(kind)
        with self.store._connect() as db:
            records = db.execute(
                f"SELECT * FROM {table} WHERE blago_user_id = ? ORDER BY created_at DESC LIMIT 200", (uid,),
            ).fetchall()
        return [dict(record) for record in records]

    def create_item(self, kind: str, uid: str, payload: dict[str, Any]) -> dict[str, Any]:
        table = self._table(kind)
        title, description = payload.get("title"), payload.get("description", "")
        visibility = payload.get("visibility", "private")
        if not isinstance(title, str) or not 2 <= len(title.strip()) <= 160:
            raise ApiError(400, "Title must contain 2-160 characters")
        if not isinstance(description, str) or len(description) > 2000:
            raise ApiError(400, "Description is too long")
        if visibility not in ("private", "community"):
            raise ApiError(400, "Invalid visibility")
        identifier, now = str(uuid4()), _iso()
        with self.store._locked_transaction() as db:
            if kind == "needs":
                db.execute(
                    f"INSERT INTO {table} (id, blago_user_id, title, description, visibility, status, created_at) VALUES (?, ?, ?, ?, ?, 'open', ?)",
                    (identifier, uid, title.strip(), description.strip(), visibility, now),
                )
            else:
                db.execute(
                    f"INSERT INTO {table} (id, blago_user_id, title, description, visibility, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (identifier, uid, title.strip(), description.strip(), visibility, now),
                )
        return {"id": identifier, "title": title.strip(), "description": description.strip(), "visibility": visibility, "created_at": now, **({"status": "open"} if kind == "needs" else {})}

    @staticmethod
    def _table(kind: str) -> str:
        if kind not in {"needs", "resources"}:
            raise ApiError(404, "Not found")
        return f"blago_{kind}"

    def analytics(self, uid: str, community: bool = False) -> dict[str, int]:
        with self.store._connect() as db:
            if community:
                return {
                    "participants": db.execute("SELECT COUNT(*) FROM users WHERE status = 'active'").fetchone()[0],
                    "published_needs": db.execute("SELECT COUNT(*) FROM blago_needs WHERE visibility = 'community'").fetchone()[0],
                    "published_resources": db.execute("SELECT COUNT(*) FROM blago_resources WHERE visibility = 'community'").fetchone()[0],
                    "eligible_payment_requests": db.execute("SELECT COUNT(*) FROM blago_payment_requests WHERE status IN ('eligible','offered','accepted','awaiting_payment','verification_pending')").fetchone()[0],
                    "completed_fulfillments": db.execute("SELECT COUNT(*) FROM blago_payment_requests WHERE status = 'completed'").fetchone()[0],
                    "voluntary_offers": db.execute("SELECT COUNT(*) FROM blago_resource_offers WHERE availability_status = 'available' AND visibility = 'community'").fetchone()[0],
                    "external_fiat_demand_minor": db.execute("SELECT COALESCE(SUM(amount_minor), 0) FROM blago_payment_requests WHERE risk_level != 'rejected'").fetchone()[0],
                }
            return {
                "needs": db.execute("SELECT COUNT(*) FROM blago_needs WHERE blago_user_id = ?", (uid,)).fetchone()[0],
                "open_needs": db.execute("SELECT COUNT(*) FROM blago_needs WHERE blago_user_id = ? AND status = 'open'", (uid,)).fetchone()[0],
                "resources": db.execute("SELECT COUNT(*) FROM blago_resources WHERE blago_user_id = ?", (uid,)).fetchone()[0],
                "memory_facts": db.execute("SELECT COUNT(*) FROM memory_facts WHERE blago_user_id = ?", (uid,)).fetchone()[0],
                "assets": db.execute("SELECT COUNT(*) FROM blago_assets WHERE blago_user_id = ?", (uid,)).fetchone()[0],
                "resource_offers": db.execute("SELECT COUNT(*) FROM blago_resource_offers WHERE blago_user_id = ?", (uid,)).fetchone()[0],
                "payment_requests": db.execute("SELECT COUNT(*) FROM blago_payment_requests WHERE requester_user_id = ?", (uid,)).fetchone()[0],
                "completed_payment_requests": db.execute("SELECT COUNT(*) FROM blago_payment_requests WHERE requester_user_id = ? AND status = 'completed'", (uid,)).fetchone()[0],
                "contributions": db.execute("SELECT COUNT(*) FROM blago_contribution_events WHERE contributor_user_id = ?", (uid,)).fetchone()[0],
            }

    def create_asset(self, uid: str, payload: dict[str, Any]) -> dict[str, Any]:
        title = _required_text(payload.get("title"), "Title", 2, 160)
        description = _optional_text(payload.get("description", ""), "Description", 2000)
        asset_type = _choice(payload.get("asset_type", "other"), {"tool", "space", "vehicle", "skill", "money", "other"}, "Invalid asset type")
        ownership_claim = _choice(payload.get("ownership_claim", "self_declared"), {"self_declared", "custodian", "unknown"}, "Invalid ownership claim")
        use_permission = _choice(payload.get("use_permission", "owner_only"), {"owner_only", "permission_required", "community_allowed"}, "Invalid use permission")
        management_status = _choice(payload.get("management_status", "not_under_management"), {"not_under_management", "offered_for_use", "under_management"}, "Invalid management status")
        visibility = _choice(payload.get("visibility", "private"), {"private", "community"}, "Invalid visibility")
        identifier, now = str(uuid4()), _iso()
        with self.store._locked_transaction() as db:
            db.execute(
                """
                INSERT INTO blago_assets (
                    id, blago_user_id, title, description, asset_type, ownership_claim,
                    use_permission, management_status, verification_status, visibility,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'self_declared_unverified', ?, ?, ?)
                """,
                (identifier, uid, title, description, asset_type, ownership_claim, use_permission, management_status, visibility, now, now),
            )
        return self.asset(uid, identifier)

    def assets(self, uid: str) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute(
                "SELECT * FROM blago_assets WHERE blago_user_id = ? ORDER BY created_at DESC LIMIT 200",
                (uid,),
            ).fetchall()
        return [dict(row) for row in rows]

    def asset(self, uid: str, asset_id: str) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute(
                "SELECT * FROM blago_assets WHERE id = ? AND blago_user_id = ?",
                (asset_id, uid),
            ).fetchone()
        if row is None:
            raise ApiError(404, "Asset not found")
        return dict(row)

    def create_resource_offer(self, uid: str, payload: dict[str, Any]) -> dict[str, Any]:
        title = _required_text(payload.get("title"), "Title", 2, 160)
        description = _optional_text(payload.get("description", ""), "Description", 2000)
        offer_type = _choice(payload.get("offer_type", "service"), {"service", "asset_use", "external_payment", "coordination"}, "Invalid offer type")
        visibility = _choice(payload.get("visibility", "private"), {"private", "community"}, "Invalid visibility")
        max_amount_minor = _optional_amount(payload.get("max_amount_minor"))
        currency = _currency(payload.get("currency")) if max_amount_minor is not None else None
        constraints = payload.get("constraints", {})
        if not isinstance(constraints, dict):
            raise ApiError(400, "Constraints must be an object")
        identifier, now = str(uuid4()), _iso()
        with self.store._locked_transaction() as db:
            db.execute(
                """
                INSERT INTO blago_resource_offers (
                    id, blago_user_id, title, description, offer_type, availability_status,
                    visibility, max_amount_minor, currency, constraints_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'available', ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    uid,
                    title,
                    description,
                    offer_type,
                    visibility,
                    max_amount_minor,
                    currency,
                    json.dumps(constraints, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
        return self.resource_offer(uid, identifier)

    def resource_offers(self, uid: str) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute(
                "SELECT * FROM blago_resource_offers WHERE blago_user_id = ? ORDER BY created_at DESC LIMIT 200",
                (uid,),
            ).fetchall()
        return [_decode_json_field(dict(row), "constraints_json", "constraints") for row in rows]

    def resource_offer(self, uid: str, offer_id: str) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute(
                "SELECT * FROM blago_resource_offers WHERE id = ? AND blago_user_id = ?",
                (offer_id, uid),
            ).fetchone()
        if row is None:
            raise ApiError(404, "Resource offer not found")
        return _decode_json_field(dict(row), "constraints_json", "constraints")

    def create_payment_request(self, uid: str, payload: dict[str, Any]) -> dict[str, Any]:
        title = _required_text(payload.get("title"), "Title", 2, 160)
        description = _optional_text(payload.get("description", ""), "Description", 3000)
        payment_purpose = _required_text(payload.get("payment_purpose"), "Payment purpose", 3, 500)
        amount_minor = _optional_amount(payload.get("amount_minor"))
        currency = _currency(payload.get("currency", "RUB")) if amount_minor is not None else None
        need_id = payload.get("need_id")
        if need_id is not None and not isinstance(need_id, str):
            raise ApiError(400, "Invalid need id")
        payment_reference = payload.get("payment_reference", "")
        if payment_reference is not None and not isinstance(payment_reference, str):
            raise ApiError(400, "Invalid payment reference")
        risk_level, risk_reason = _classify_payment_risk(title, description, payment_purpose, amount_minor)
        status = "rejected" if risk_level == "rejected" else "eligible"
        identifier, now = str(uuid4()), _iso()
        digest = hashlib.sha256(payment_reference.strip().encode()).hexdigest() if payment_reference.strip() else None
        with self.store._locked_transaction() as db:
            if need_id:
                if db.execute(
                    "SELECT 1 FROM blago_needs WHERE id = ? AND blago_user_id = ?",
                    (need_id, uid),
                ).fetchone() is None:
                    raise ApiError(404, "Need not found")
            db.execute(
                """
                INSERT INTO blago_payment_requests (
                    id, need_id, requester_user_id, title, description, amount_minor, currency,
                    payment_purpose, payment_reference_digest, risk_level, risk_reason, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    need_id,
                    uid,
                    title,
                    description,
                    amount_minor,
                    currency,
                    payment_purpose,
                    digest,
                    risk_level,
                    risk_reason,
                    status,
                    now,
                    now,
                ),
            )
            self._record_fulfillment_event(
                db,
                payment_request_id=identifier,
                need_id=need_id,
                actor_user_id=uid,
                event_type="payment_request_created",
                status_after=status,
                note="Payment request created. No real transfer was initiated.",
                event_key=f"payment-request-created:{identifier}",
            )
        return self.payment_request(uid, identifier)

    def payment_requests(self, uid: str) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM blago_payment_requests
                WHERE requester_user_id = ? OR executor_user_id = ?
                ORDER BY created_at DESC
                LIMIT 200
                """,
                (uid, uid),
            ).fetchall()
        return [dict(row) for row in rows]

    def payment_request(self, uid: str, request_id: str) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute(
                """
                SELECT * FROM blago_payment_requests
                WHERE id = ? AND (requester_user_id = ? OR executor_user_id = ?)
                """,
                (request_id, uid, uid),
            ).fetchone()
            if row is None:
                offer = db.execute(
                    """
                    SELECT p.*
                    FROM blago_payment_requests p
                    JOIN blago_payment_request_offers o ON o.payment_request_id = p.id
                    WHERE p.id = ? AND o.offered_user_id = ?
                    """,
                    (request_id, uid),
                ).fetchone()
                row = offer
        if row is None:
            raise ApiError(404, "Payment request not found")
        return dict(row)

    def offer_payment_request(self, uid: str, request_id: str, limit: int = 3) -> dict[str, Any]:
        with self.store._locked_transaction() as db:
            request = db.execute(
                "SELECT * FROM blago_payment_requests WHERE id = ? AND requester_user_id = ?",
                (request_id, uid),
            ).fetchone()
            if request is None:
                raise ApiError(404, "Payment request not found")
            if request["status"] not in ("eligible", "offered"):
                raise ApiError(409, "Payment request cannot be offered in its current status")
            if request["risk_level"] == "rejected":
                raise ApiError(409, "Rejected requests cannot be offered")
            candidates = db.execute(
                """
                SELECT *
                FROM blago_resource_offers
                WHERE offer_type = 'external_payment'
                  AND availability_status = 'available'
                  AND visibility = 'community'
                  AND blago_user_id != ?
                  AND (max_amount_minor IS NULL OR ? IS NULL OR max_amount_minor >= ?)
                  AND (currency IS NULL OR ? IS NULL OR currency = ?)
                """,
                (uid, request["amount_minor"], request["amount_minor"], request["currency"], request["currency"]),
            ).fetchall()
            candidates = list(candidates)
            random.SystemRandom().shuffle(candidates)
            created: list[dict[str, Any]] = []
            now = _iso()
            for candidate in candidates[: max(1, min(limit, 10))]:
                offer_id = str(uuid4())
                try:
                    db.execute(
                        """
                        INSERT INTO blago_payment_request_offers (
                            id, payment_request_id, resource_offer_id, offered_user_id,
                            status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'offered', ?, ?)
                        """,
                        (offer_id, request_id, candidate["id"], candidate["blago_user_id"], now, now),
                    )
                except sqlite3.IntegrityError:
                    continue
                created.append({"id": offer_id, "offered_user_id": candidate["blago_user_id"], "resource_offer_id": candidate["id"], "status": "offered"})
            if created and request["status"] == "eligible":
                db.execute(
                    "UPDATE blago_payment_requests SET status = 'offered', updated_at = ? WHERE id = ?",
                    (now, request_id),
                )
                self._record_fulfillment_event(
                    db,
                    payment_request_id=request_id,
                    need_id=request["need_id"],
                    actor_user_id=uid,
                    event_type="executor_offered",
                    status_after="offered",
                    note=f"Offered to {len(created)} voluntary participants.",
                    event_key=f"payment-request-offered:{request_id}:{now}",
                )
            return {"request": self._payment_request_in_tx(db, request_id), "offers": created}

    def incoming_payment_offers(self, uid: str) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute(
                """
                SELECT o.*, p.title, p.amount_minor, p.currency, p.risk_level, p.risk_reason
                FROM blago_payment_request_offers o
                JOIN blago_payment_requests p ON p.id = o.payment_request_id
                WHERE o.offered_user_id = ?
                ORDER BY o.created_at DESC
                LIMIT 200
                """,
                (uid,),
            ).fetchall()
        return [dict(row) for row in rows]

    def accept_payment_offer(self, uid: str, offer_id: str) -> dict[str, Any]:
        with self.store._locked_transaction() as db:
            offer = db.execute(
                "SELECT * FROM blago_payment_request_offers WHERE id = ? AND offered_user_id = ?",
                (offer_id, uid),
            ).fetchone()
            if offer is None:
                raise ApiError(404, "Offer not found")
            if offer["status"] != "offered":
                raise ApiError(409, "Offer is no longer available")
            now = _iso()
            cursor = db.execute(
                """
                UPDATE blago_payment_requests
                SET status = 'accepted', executor_user_id = ?, updated_at = ?
                WHERE id = ? AND status = 'offered' AND executor_user_id IS NULL
                """,
                (uid, now, offer["payment_request_id"]),
            )
            if cursor.rowcount != 1:
                db.execute(
                    "UPDATE blago_payment_request_offers SET status = 'expired', updated_at = ? WHERE id = ?",
                    (now, offer_id),
                )
                raise ApiError(409, "Another participant already accepted this request")
            db.execute(
                "UPDATE blago_payment_request_offers SET status = 'accepted', updated_at = ? WHERE id = ?",
                (now, offer_id),
            )
            db.execute(
                "UPDATE blago_payment_request_offers SET status = 'expired', updated_at = ? WHERE payment_request_id = ? AND id != ? AND status = 'offered'",
                (now, offer["payment_request_id"], offer_id),
            )
            request = self._payment_request_in_tx(db, offer["payment_request_id"])
            self._record_fulfillment_event(
                db,
                payment_request_id=offer["payment_request_id"],
                need_id=request["need_id"],
                actor_user_id=uid,
                event_type="executor_accepted",
                status_after="accepted",
                note="Participant voluntarily accepted the simulated execution request.",
                event_key=f"payment-request-accepted:{offer['payment_request_id']}",
            )
            return request

    def simulate_payment(self, uid: str, request_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        note = _optional_text((payload or {}).get("note", ""), "Note", 1000)
        with self.store._locked_transaction() as db:
            request = db.execute(
                "SELECT * FROM blago_payment_requests WHERE id = ? AND executor_user_id = ?",
                (request_id, uid),
            ).fetchone()
            if request is None:
                raise ApiError(404, "Payment request not found")
            if request["status"] not in ("accepted", "awaiting_payment"):
                raise ApiError(409, "Payment request is not ready for simulated payment")
            now = _iso()
            db.execute(
                "UPDATE blago_payment_requests SET status = 'verification_pending', updated_at = ? WHERE id = ?",
                (now, request_id),
            )
            self._record_fulfillment_event(
                db,
                payment_request_id=request_id,
                need_id=request["need_id"],
                actor_user_id=uid,
                event_type="simulated_payment_submitted",
                status_after="verification_pending",
                note=note or "Executor marked a simulated payment as submitted. This is not bank verification.",
                event_key=f"payment-request-simulated-payment:{request_id}",
            )
            return self._payment_request_in_tx(db, request_id)

    def confirm_fulfillment(self, uid: str, request_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        note = _optional_text((payload or {}).get("note", ""), "Note", 1000)
        rate = (payload or {}).get("xblago_rate")
        rate_source = _optional_text((payload or {}).get("exchange_rate_source", "manual_mvp"), "Exchange rate source", 160)
        xblago_rate: float | None = None
        if rate is not None:
            try:
                xblago_rate = float(rate)
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "Invalid xBLAGO rate") from exc
            if xblago_rate <= 0 or xblago_rate > 1_000_000:
                raise ApiError(400, "Invalid xBLAGO rate")
        with self.store._locked_transaction() as db:
            request = db.execute(
                "SELECT * FROM blago_payment_requests WHERE id = ? AND requester_user_id = ?",
                (request_id, uid),
            ).fetchone()
            if request is None:
                raise ApiError(404, "Payment request not found")
            if request["status"] != "verification_pending":
                raise ApiError(409, "Payment request is not awaiting manual confirmation")
            if request["contribution_recorded"]:
                raise ApiError(409, "Contribution has already been recorded")
            now = _iso()
            cursor = db.execute(
                "UPDATE blago_payment_requests SET status = 'completed', contribution_recorded = 1, updated_at = ? WHERE id = ? AND contribution_recorded = 0",
                (now, request_id),
            )
            if cursor.rowcount != 1:
                raise ApiError(409, "Contribution has already been recorded")
            self._record_fulfillment_event(
                db,
                payment_request_id=request_id,
                need_id=request["need_id"],
                actor_user_id=uid,
                event_type="manual_fulfillment_confirmed",
                status_after="completed",
                note=note or "Requester manually confirmed fulfillment. This is not bank verification.",
                event_key=f"payment-request-completed:{request_id}",
            )
            estimate = None
            if xblago_rate is not None and request["amount_minor"] is not None:
                estimate = round(float(request["amount_minor"]) / 100.0 * xblago_rate, 6)
            self._record_contribution_event(
                db,
                payment_request_id=request_id,
                contributor_user_id=str(request["executor_user_id"]),
                recipient_user_id=uid,
                contribution_type="simulated_external_payment",
                amount_minor=request["amount_minor"],
                currency=request["currency"],
                xblago_estimate=estimate,
                exchange_rate_source=rate_source if estimate is not None else None,
                exchange_rate_at=now if estimate is not None else None,
                note="Accounting record only. It creates no balance, claim, payout right, or money obligation.",
                event_key=f"contribution:payment-request:{request_id}",
            )
            return self._payment_request_in_tx(db, request_id)

    def economy_events(self, uid: str, request_id: str) -> dict[str, list[dict[str, Any]]]:
        self.payment_request(uid, request_id)
        with self.store._connect() as db:
            fulfillment = db.execute(
                "SELECT * FROM blago_fulfillment_events WHERE payment_request_id = ? ORDER BY created_at ASC",
                (request_id,),
            ).fetchall()
            contributions = db.execute(
                """
                SELECT *
                FROM blago_contribution_events
                WHERE payment_request_id = ?
                  AND (contributor_user_id = ? OR recipient_user_id = ?)
                ORDER BY created_at ASC
                """,
                (request_id, uid, uid),
            ).fetchall()
        return {
            "fulfillment_events": [dict(row) for row in fulfillment],
            "contribution_events": [dict(row) for row in contributions],
        }

    def economy_analytics(self, uid: str) -> dict[str, Any]:
        with self.store._connect() as db:
            total = db.execute("SELECT COUNT(*) FROM blago_payment_requests WHERE requester_user_id = ?", (uid,)).fetchone()[0]
            completed = db.execute("SELECT COUNT(*) FROM blago_payment_requests WHERE requester_user_id = ? AND status = 'completed'", (uid,)).fetchone()[0]
            external_sum = db.execute("SELECT COALESCE(SUM(amount_minor), 0) FROM blago_payment_requests WHERE requester_user_id = ?", (uid,)).fetchone()[0]
            offers = db.execute("SELECT COUNT(*) FROM blago_resource_offers WHERE blago_user_id = ? AND availability_status = 'available'", (uid,)).fetchone()[0]
            contributions = db.execute("SELECT COUNT(*) FROM blago_contribution_events WHERE contributor_user_id = ?", (uid,)).fetchone()[0]
            xblago = db.execute("SELECT COALESCE(SUM(xblago_estimate), 0) FROM blago_contribution_events WHERE contributor_user_id = ?", (uid,)).fetchone()[0]
        return {
            "registered_payment_requests": total,
            "fulfilled_payment_requests": completed,
            "fulfilled_share": round(completed / total, 4) if total else 0,
            "external_fiat_demand_minor": external_sum,
            "available_voluntary_offers": offers,
            "recorded_contributions": contributions,
            "xblago_statistical_estimate": xblago,
            "xblago_note": "Statistical estimate only; no token, payout balance, or monetary claim is issued.",
        }

    def _payment_request_in_tx(self, db: sqlite3.Connection, request_id: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM blago_payment_requests WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            raise ApiError(404, "Payment request not found")
        return dict(row)

    def _record_fulfillment_event(
        self,
        db: sqlite3.Connection,
        *,
        payment_request_id: str,
        need_id: str | None,
        actor_user_id: str,
        event_type: str,
        status_after: str,
        note: str,
        event_key: str,
        evidence_digest: str | None = None,
    ) -> None:
        db.execute(
            """
            INSERT OR IGNORE INTO blago_fulfillment_events (
                id, payment_request_id, need_id, actor_user_id, event_type, status_after,
                note, evidence_digest, event_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid4()), payment_request_id, need_id, actor_user_id, event_type, status_after, note, evidence_digest, event_key, _iso()),
        )

    def _record_contribution_event(
        self,
        db: sqlite3.Connection,
        *,
        payment_request_id: str,
        contributor_user_id: str,
        recipient_user_id: str,
        contribution_type: str,
        amount_minor: int | None,
        currency: str | None,
        xblago_estimate: float | None,
        exchange_rate_source: str | None,
        exchange_rate_at: str | None,
        note: str,
        event_key: str,
    ) -> None:
        db.execute(
            """
            INSERT OR IGNORE INTO blago_contribution_events (
                id, payment_request_id, contributor_user_id, recipient_user_id, contribution_type,
                amount_minor, currency, xblago_estimate, exchange_rate_source, exchange_rate_at,
                note, event_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                payment_request_id,
                contributor_user_id,
                recipient_user_id,
                contribution_type,
                amount_minor,
                currency,
                xblago_estimate,
                exchange_rate_source,
                exchange_rate_at,
                note,
                event_key,
                _iso(),
            ),
        )


class BlagoWebHandler(TelegramWebhookHandler):
    """Same listening socket as Telegram webhook; never a second PORT server."""

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in {"/app", "/app/", "/"}:
            page = Path(__file__).with_name("static") / "index.html"
            body = page.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        assets = {
            "/app/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
            "/app/icon.svg": ("icon.svg", "image/svg+xml"),
            "/app/sw.js": ("sw.js", "text/javascript"),
        }
        if path in assets:
            filename, content_type = assets[path]
            body = (Path(__file__).with_name("static") / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/api/v1/"):
            self._api("GET", path)
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/api/v1/"):
            self._api("POST", path)
        else:
            super().do_POST()

    def do_PATCH(self) -> None:
        self._api("PATCH", urlsplit(self.path).path)

    def do_DELETE(self) -> None:
        self._api("DELETE", urlsplit(self.path).path)

    def _cookie(self, value: str, clear: bool = False) -> str:
        secure = os.getenv("XBLAGO_COOKIE_SECURE", "true").lower() not in ("false", "0", "no")
        return (
            f"blago_session={value}; Path=/; HttpOnly; SameSite=Lax; "
            f"{'Secure; ' if secure else ''}{'Max-Age=0' if clear else 'Max-Age=604800'}"
        )

    def _token(self, name: str = 'blago_session') -> str | None:
        from http.cookies import SimpleCookie
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            morsel = cookies.get(name)
            return morsel.value if morsel else None
        except Exception:
            return None

    def _json(self, payload: dict[str, Any], status: int = 200, cookie: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            raise ApiError(415, "JSON required")
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 64 * 1024:
                raise ValueError("invalid size")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("invalid JSON object")
            return data
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, "Invalid JSON payload or size") from exc

    def _check_origin(self) -> None:
        origin = self.headers.get("Origin")
        if origin:
            host = self.headers.get("Host", "")
            allowed = {f"http://{host}", f"https://{host}"}
            configured = os.getenv("XBLAGO_PUBLIC_ORIGIN", "").rstrip("/")
            if configured:
                allowed.add(configured)
            if origin not in allowed:
                raise ApiError(403, "Cross-origin request blocked")
        if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            raise ApiError(403, "Cross-site request blocked")

    def _api(self, method: str, path: str) -> None:
        try:
            self._api_impl(method, path)
        except ApiError as exc:
            self._json({"error": str(exc)}, status=exc.status)
        except Exception:
            # Never leak password/token/database diagnostics to public clients.
            self._json({"error": "Internal server error"}, status=500)
            raise

    def _api_impl(self, method: str, path: str) -> None:
        app: WebApp = getattr(self.server, "blago_web_app")
        if method != "GET":
            self._check_origin()
        if method == 'GET' and path == '/api/v1/auth/capabilities':
            configured = False
            try:
                app.identity.rp()
                configured = True
            except ApiError:
                pass
            self._json({'secure_identity': app.identity.enabled, 'passkeys': configured and app.identity.enabled})
            return
        if method == 'POST' and path in ('/api/v1/auth/telegram/challenge', '/api/v1/auth/passkeys/authenticate/options'):
            app.check_auth_rate(self.client_address[0])
            self._body()
            binding = self._token('blago_auth_binding') or secrets.token_urlsafe(32)
            if not 32 <= len(binding) <= 128:
                binding = secrets.token_urlsafe(32)
            if path.endswith('/challenge'):
                data = {'challenge': app.identity.telegram_challenge(binding)}
            else:
                data = app.identity.passkey_options(binding)
            cookie = self._cookie(binding).replace('blago_session=', 'blago_auth_binding=', 1).replace('Max-Age=604800', 'Max-Age=600')
            self._json(data, cookie=cookie)
            return
        if method == 'POST' and path == '/api/v1/auth/passkeys/authenticate/verify':
            app.check_auth_rate(self.client_address[0])
            token, csrf = app.identity.passkey_verify(self._body(), self._token('blago_auth_binding') or '', self._token())
            self._json({'ok': True, 'csrf': csrf}, cookie=self._cookie(token))
            return
        if method == "POST" and path in ("/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/auth/telegram"):
            app.check_auth_rate(self.client_address[0])
            body = self._body()
            if path.endswith("/register"):
                token, csrf = app.register(str(body.get("username", "")), str(body.get("password", "")), str(body.get("invite", "")))
            elif path.endswith("/telegram"):
                token, csrf = app.telegram_login(str(body.get("init_data", "")), str(body.get('challenge', '')),
                                                self._token('blago_auth_binding') or '', self._token())
            else:
                token, csrf = app.login(str(body.get("username", "")), str(body.get("password", "")), self._token())
            self._json({"ok": True, "csrf": csrf}, cookie=self._cookie(token))
            return

        token = self._token()
        uid, csrf = app.session(token)
        if method != "GET" and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), csrf):
            raise ApiError(403, "Invalid CSRF token")

        if path == '/api/v1/auth/sessions' and method == 'GET':
            self._json({'sessions': app.identity.sessions(token)})
        elif path == '/api/v1/auth/sessions/revoke' and method == 'POST':
            body = self._body()
            app.identity.revoke(token, body.get('id'), others=body.get('others') is True)
            self._json({'ok': True})
        elif path == '/api/v1/auth/reauthenticate' and method == 'POST':
            body = self._body()
            replacement, csrf = app.login(str(body.get('username', '')), str(body.get('password', '')), token, uid)
            self._json({'ok': True, 'csrf': csrf}, cookie=self._cookie(replacement))
        elif path == '/api/v1/auth/methods' and method == 'GET':
            self._json({'methods': app.identity.methods(token)})
        elif path == '/api/v1/auth/methods' and method == 'DELETE':
            body = self._body()
            replacement, csrf = app.identity.remove_method(token, body.get('kind'), body.get('id'))
            self._json({'ok': True, 'csrf': csrf}, cookie=self._cookie(replacement))
        elif path == '/api/v1/auth/password' and method == 'POST':
            body = self._body()
            replacement, csrf = app.identity.set_password(token, str(body.get('username', '')), str(body.get('password', '')))
            self._json({'ok': True, 'csrf': csrf}, cookie=self._cookie(replacement))
        elif path == '/api/v1/auth/passkeys/register/options' and method == 'POST':
            self._body()
            binding = self._token('blago_auth_binding') or secrets.token_urlsafe(32)
            data = app.identity.passkey_options(binding, token, register=True)
            self._json(data, cookie=self._cookie(binding).replace('blago_session=', 'blago_auth_binding=', 1).replace('Max-Age=604800', 'Max-Age=600'))
        elif path == '/api/v1/auth/passkeys/register/verify' and method == 'POST':
            replacement, csrf = app.identity.passkey_verify(self._body(), self._token('blago_auth_binding') or '', token, register=True)
            self._json({'ok': True, 'csrf': csrf}, cookie=self._cookie(replacement))
        elif path == "/api/v1/auth/link-code" and method == "POST":
            code = app.new_link_code(uid, token)
            self._json({"code": code, "expires_in_seconds": 600})
        elif path == "/api/v1/auth/logout" and method == "POST":
            app.logout(token)
            self._json({"ok": True}, cookie=self._cookie("", clear=True))
        elif path == "/api/v1/me" and method == "GET":
            self._json({"profile": app.me(uid), "csrf": csrf})
        elif path == "/api/v1/me" and method == "PATCH":
            self._json({"profile": app.update_me(uid, self._body())})
        elif path == "/api/v1/chat/messages" and method == "GET":
            self._json({"messages": app.history(uid)})
        elif path == "/api/v1/chat/messages" and method == "POST":
            data = self._body()
            message = data.get("text")
            if not isinstance(message, str) or not 1 <= len(message.strip()) <= 4000:
                raise ApiError(400, "Message must contain 1-4000 characters")
            self._json({"reply": app.chat(uid, message.strip())})
        elif path == "/api/v1/memory" and method == "GET":
            self._json({
                "facts": app.store.all_memory(uid),
                "records": [
                    {
                        "id": record.record_id,
                        "key": record.key,
                        "value": record.value,
                        "category": record.category,
                        "source": record.source,
                        "status": record.status,
                        "confidence": record.confidence,
                        "created_at": record.created_at,
                        "updated_at": record.updated_at,
                    }
                    for record in app.store.all_semantic_memory(uid, include_candidates=True)
                ],
            })
        elif path == "/api/v1/memory" and method == "DELETE":
            body = self._body()
            key = _required_text(body.get("key"), "Memory key", 1, 80)
            self._json({"deleted": app.delete_memory(uid, key)})
        elif path == "/api/v1/analytics/me" and method == "GET":
            self._json({"analytics": app.analytics(uid)})
        elif path == "/api/v1/analytics/community" and method == "GET":
            self._json({"analytics": app.analytics(uid, community=True)})
        elif path in ("/api/v1/needs", "/api/v1/resources") and method in ("GET", "POST"):
            kind = path.removeprefix("/api/v1/")
            if method == "GET":
                self._json({"items": app.rows(kind, uid)})
            else:
                self._json({"item": app.create_item(kind, uid, self._body())}, status=201)
        elif path == "/api/v1/economy/assets" and method in ("GET", "POST"):
            if method == "GET":
                self._json({"items": app.assets(uid)})
            else:
                self._json({"item": app.create_asset(uid, self._body())}, status=201)
        elif path == "/api/v1/economy/resource-offers" and method in ("GET", "POST"):
            if method == "GET":
                self._json({"items": app.resource_offers(uid)})
            else:
                self._json({"item": app.create_resource_offer(uid, self._body())}, status=201)
        elif path == "/api/v1/economy/payment-requests" and method in ("GET", "POST"):
            if method == "GET":
                self._json({"items": app.payment_requests(uid)})
            else:
                self._json({"item": app.create_payment_request(uid, self._body())}, status=201)
        elif path == "/api/v1/economy/payment-offers" and method == "GET":
            self._json({"items": app.incoming_payment_offers(uid)})
        elif path == "/api/v1/economy/analytics" and method == "GET":
            self._json({"analytics": app.economy_analytics(uid)})
        elif path.startswith("/api/v1/economy/payment-requests/"):
            suffix = path.removeprefix("/api/v1/economy/payment-requests/")
            parts = [part for part in suffix.split("/") if part]
            if len(parts) == 1 and method == "GET":
                self._json({"item": app.payment_request(uid, parts[0])})
            elif len(parts) == 2 and method == "POST" and parts[1] == "offer":
                self._json(app.offer_payment_request(uid, parts[0]))
            elif len(parts) == 2 and method == "POST" and parts[1] == "simulate-payment":
                self._json({"item": app.simulate_payment(uid, parts[0], self._body())})
            elif len(parts) == 2 and method == "POST" and parts[1] == "confirm":
                self._json({"item": app.confirm_fulfillment(uid, parts[0], self._body())})
            elif len(parts) == 2 and method == "GET" and parts[1] == "events":
                self._json(app.economy_events(uid, parts[0]))
            else:
                raise ApiError(404, "Not found")
        elif path.startswith("/api/v1/economy/payment-offers/") and method == "POST":
            suffix = path.removeprefix("/api/v1/economy/payment-offers/")
            parts = [part for part in suffix.split("/") if part]
            if len(parts) == 2 and parts[1] == "accept":
                self._json({"item": app.accept_payment_offer(uid, parts[0])})
            else:
                raise ApiError(404, "Not found")
        else:
            raise ApiError(404, "Not found")
