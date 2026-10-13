"""One secure, shared HTTP entrypoint for BLAGO Web/PWA and Telegram Mini App.

Web is opt-in: users register with an invite, or log in through verified
Telegram initData. The existing Telegram webhook routes remain intact.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
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


class WebApp:
    def __init__(self, store: SQLiteBlagoStore) -> None:
        self.store = store
        self._chat_lock = RLock()
        self._auth_lock = RLock()
        self._auth_attempts: dict[str, list[float]] = {}
        self._ensure_schema()

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

    def login(self, username: str, password: str) -> tuple[str, str]:
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
        return self.new_session(str(row["blago_user_id"]))

    def telegram_login(self, init_data: str) -> tuple[str, str]:
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

    def new_link_code(self, web_uid: str) -> str:
        # Only a signed-in web account can issue a Telegram linking code.
        with self.store._connect() as db:
            credential = db.execute(
                "SELECT 1 FROM web_credentials WHERE blago_user_id = ?", (web_uid,),
            ).fetchone()
        if credential is None:
            raise ApiError(403, "Sign in through the website to create a linking code")
        code = secrets.token_hex(8).upper()
        with self.store._locked_transaction() as db:
            db.execute("DELETE FROM account_link_codes WHERE target_user_id = ? OR expires_at < ?",
                       (web_uid, _iso()))
            db.execute("INSERT INTO account_link_codes VALUES (?, ?, ?)",
                       (hashlib.sha256(code.encode()).hexdigest(), web_uid, _iso(_now() + timedelta(minutes=10))))
        return code

    def redeem_telegram_link(self, telegram_uid: str, code: str) -> str:
        # Caller MUST authenticate Telegram sender (webhook or verified initData).
        # A one-time secret proves control of the destination web account.
        if len(code) != 16 or any(c not in "0123456789ABCDEF" for c in code.upper()):
            raise ApiError(400, "Invalid linking code")
        with self.store._locked_transaction() as db:
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
            # Preserve all dialogue, needs, resources, and event audit records.
            for table in ("dialogue_turns", "events", "blago_needs", "blago_resources", "web_sessions"):
                db.execute(f"UPDATE {table} SET blago_user_id = ? WHERE blago_user_id = ?", (target_uid, telegram_uid))
            # Memory keys can collide. Keep both values with a namespaced key.
            memories = db.execute(
                "SELECT key, value_json, updated_at FROM memory_facts WHERE blago_user_id = ?",
                (telegram_uid,),
            ).fetchall()
            for memory in memories:
                key = str(memory["key"])
                if db.execute(
                    "SELECT 1 FROM memory_facts WHERE blago_user_id = ? AND key = ?", (target_uid, key),
                ).fetchone():
                    candidate = "telegram." + key
                    suffix = 2
                    while db.execute(
                        "SELECT 1 FROM memory_facts WHERE blago_user_id = ? AND key = ?", (target_uid, candidate),
                    ).fetchone():
                        candidate = f"telegram.{key}.{suffix}"
                        suffix += 1
                    key = candidate
                db.execute(
                    "INSERT INTO memory_facts (blago_user_id,key,value_json,source_event_id,updated_at) VALUES (?,?,?,?,?)",
                    (target_uid, key, memory["value_json"], None, memory["updated_at"]),
                )
            db.execute("DELETE FROM memory_facts WHERE blago_user_id = ?", (telegram_uid,))
            db.execute(
                "UPDATE channel_links SET blago_user_id = ? WHERE channel = 'telegram' AND blago_user_id = ?",
                (target_uid, telegram_uid),
            )
            db.execute("UPDATE users SET status = 'merged' WHERE blago_user_id = ?", (telegram_uid,))
        return target_uid

    def new_session(self, uid: str) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        with self.store._locked_transaction() as db:
            db.execute(
                "INSERT INTO web_sessions VALUES (?, ?, ?, ?)",
                (hashlib.sha256(token.encode()).hexdigest(), uid, csrf, _iso(_now() + timedelta(days=7))),
            )
        return token, csrf

    def session(self, token: str | None) -> tuple[str, str]:
        if not token:
            raise ApiError(401, "Please sign in")
        with self.store._connect() as db:
            row = db.execute(
                "SELECT blago_user_id, csrf FROM web_sessions WHERE session_hash = ? AND expires_at > ?",
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
        with self._chat_lock:
            agent = PersonalAssistantAgent(
                memory=self.store.memory_for_user(uid),
                chat_provider=build_chat_provider(load_llm_config()),
                initial_history=self.store.recent_history(uid, 12),
                history_recorder=lambda t: self.store.append_history(uid, t.role, t.text, channel="web"),
                history_clearer=lambda: self.store.clear_history(uid),
            )
            return agent.respond(UserMessage(text=text)).text

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
                    "participants": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                    "published_needs": db.execute("SELECT COUNT(*) FROM blago_needs WHERE visibility = 'community'").fetchone()[0],
                    "published_resources": db.execute("SELECT COUNT(*) FROM blago_resources WHERE visibility = 'community'").fetchone()[0],
                }
            return {
                "needs": db.execute("SELECT COUNT(*) FROM blago_needs WHERE blago_user_id = ?", (uid,)).fetchone()[0],
                "open_needs": db.execute("SELECT COUNT(*) FROM blago_needs WHERE blago_user_id = ? AND status = 'open'", (uid,)).fetchone()[0],
                "resources": db.execute("SELECT COUNT(*) FROM blago_resources WHERE blago_user_id = ?", (uid,)).fetchone()[0],
                "memory_facts": db.execute("SELECT COUNT(*) FROM memory_facts WHERE blago_user_id = ?", (uid,)).fetchone()[0],
            }


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

    def _token(self) -> str | None:
        from http.cookies import SimpleCookie
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            morsel = cookies.get("blago_session")
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
        if method == "POST" and path in ("/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/auth/telegram"):
            app.check_auth_rate(self.client_address[0])
            body = self._body()
            if path.endswith("/register"):
                token, csrf = app.register(str(body.get("username", "")), str(body.get("password", "")), str(body.get("invite", "")))
            elif path.endswith("/telegram"):
                token, csrf = app.telegram_login(str(body.get("init_data", "")))
            else:
                token, csrf = app.login(str(body.get("username", "")), str(body.get("password", "")))
            self._json({"ok": True, "csrf": csrf}, cookie=self._cookie(token))
            return

        token = self._token()
        uid, csrf = app.session(token)
        if method != "GET" and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), csrf):
            raise ApiError(403, "Invalid CSRF token")

        if path == "/api/v1/auth/link-code" and method == "POST":
            code = app.new_link_code(uid)
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
            self._json({"facts": app.store.all_memory(uid)})
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
        else:
            raise ApiError(404, "Not found")
