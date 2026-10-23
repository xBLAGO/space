from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock, Thread
from typing import Any

from xblago.agents.personal_assistant import PersonalAssistantAgent
from xblago.core.config import load_llm_config
from xblago.core.llm import build_chat_provider
from xblago.core.messages import ConversationTurn, UserMessage
from xblago.core.storage import SQLiteBlagoStore, TelegramIdentity


class TelegramApi:
    def __init__(self, token: str, poll_timeout_seconds: float = 20.0, request_timeout_seconds: float = 90.0) -> None:
        self.token = token
        self.base_url = _telegram_api_base_url(token)
        self.use_query_transport = bool(os.getenv("TELEGRAM_API_RELAY_URL"))
        print(f"Telegram API base URL: {self._redact_secret(self.base_url)}")
        self.poll_timeout_seconds = poll_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds

    def get_me(self) -> dict[str, Any]:
        response = self._request("getMe", {})
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise RuntimeError(f"Telegram API returned an unexpected getMe response: {response}")
        return result

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {"timeout": int(self.poll_timeout_seconds)}
        if offset is not None:
            params["offset"] = offset

        response = self._request("getUpdates", params)
        return list(response.get("result", []))

    def send_message(self, chat_id: int, text: str) -> None:
        chunks = _split_telegram_text(text)
        for chunk in chunks:
            self._request("sendMessage", {"chat_id": chat_id, "text": chunk})

    def set_webhook(self, url: str, secret_token: str) -> None:
        self._request("setWebhook", {"url": url, "secret_token": secret_token})

    def _request(self, method: str, params: dict[str, str | int]) -> dict[str, Any]:
        import certifi
        import requests

        try:
            if self.use_query_transport:
                response = requests.get(
                    _url_with_query(f"{self.base_url}/{method}", params),
                    timeout=self.request_timeout_seconds,
                    verify=certifi.where(),
                )
            else:
                response = requests.post(
                    f"{self.base_url}/{method}",
                    data=params,
                    timeout=self.request_timeout_seconds,
                    verify=certifi.where(),
                )
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(f"Telegram API request failed for {method}: {self._redact_secret(str(exc))}") from exc

        if not body.get("ok"):
            raise RuntimeError(f"Telegram API returned an error for {method}: {self._redact_secret(str(body))}")

        return dict(body)

    def _redact_secret(self, text: str) -> str:
        return text.replace(self.token, "[TELEGRAM_BOT_TOKEN]")


class TelegramBot:
    def __init__(self, api: TelegramApi, memory_dir: Path, store: SQLiteBlagoStore | None = None) -> None:
        self.api = api
        self.memory_dir = memory_dir
        self.store = store or SQLiteBlagoStore(memory_dir / "blago.sqlite3", legacy_memory_dir=memory_dir)
        self.agents: dict[str, PersonalAssistantAgent] = {}
        self._lock = RLock()

    def run_forever(self) -> None:
        print("xBLAGO Telegram bot is running.")
        self._print_startup_identity()
        offset: int | None = None
        retry_delay_seconds = 3

        while True:
            try:
                updates = self.api.get_updates(offset)
                retry_delay_seconds = 3
                for update in updates:
                    offset = int(update["update_id"]) + 1
                    self._handle_update(update)
            except (RuntimeError, urllib.error.URLError, TimeoutError) as exc:
                print(f"Telegram polling error: {exc}")
                time.sleep(retry_delay_seconds)
                retry_delay_seconds = min(retry_delay_seconds * 2, 60)

    def _print_startup_identity(self) -> None:
        if _bool_from_env("TELEGRAM_SKIP_STARTUP_CHECK", False):
            print("Telegram startup check is skipped by configuration.")
            return

        try:
            bot = self.api.get_me()
        except (RuntimeError, urllib.error.URLError, TimeoutError) as exc:
            print(f"Telegram startup check failed: {exc}")
            return

        username = bot.get("username", "unknown")
        print(f"Telegram startup check succeeded: @{username}")

    def handle_update(self, update: dict[str, Any]) -> None:
        for reply in self.replies_for_update(update):
            self.api.send_message(int(reply["chat_id"]), str(reply["text"]))

    def replies_for_update(self, update: dict[str, Any]) -> list[dict[str, str | int]]:
        with self._lock, self.store.conversation_lock:
            message = update.get("message")
            if not isinstance(message, dict):
                return []

            chat = message.get("chat")
            text = message.get("text")
            if not isinstance(chat, dict) or not isinstance(text, str):
                return []

            chat_id = chat.get("id")
            if not isinstance(chat_id, int):
                return []

            identity = _telegram_identity_from_message(message, chat_id)
            blago_user_id = self.store.resolve_telegram_user(identity)
            with self.store._connect() as db:
                status = db.execute('SELECT status FROM users WHERE blago_user_id=?', (blago_user_id,)).fetchone()
            if status is None or status[0] != 'active':
                return _telegram_message_replies(chat_id, 'Доступ к этому аккаунту приостановлен. Используйте другой ранее подтверждённый способ входа.')
            if identity.telegram_user_id is None and text.strip().startswith('/link'):
                return _telegram_message_replies(chat_id, 'Для привязки нужен подтверждённый отправитель Telegram.')
            update_id = _external_update_id(update)
            if not self.store.begin_update("telegram", update_id, blago_user_id):
                return self.store.cached_update_replies("telegram", update_id) or []

            try:
                replies = self._replies_for_message(blago_user_id, chat_id, text, identity.chat_type)
            except Exception as exc:
                self.store.mark_update_failed("telegram", update_id, str(exc))
                raise

            self.store.mark_update_processed("telegram", update_id, replies)
            return replies

    _handle_update = handle_update

    def _replies_for_message(
        self, blago_user_id: str, chat_id: int, text: str, chat_type: str | None = None,
    ) -> list[dict[str, str | int]]:
        if text.strip().startswith("/link"):
            if chat_type != "private":
                return _telegram_message_replies(chat_id, "Привязка возможна только в личном чате с ботом.")
            parts = text.strip().split()
            if len(parts) != 2 or parts[0] != "/link":
                return _telegram_message_replies(chat_id, "Введите /link КОД из веб-профиля BLAGO.")
            from xblago.integrations.web import ApiError, WebApp

            try:
                app = WebApp(self.store)
                app.redeem_telegram_link(blago_user_id, parts[1])
            except ApiError as exc:
                return _telegram_message_replies(chat_id, f"Не удалось привязать аккаунт: {exc}")
            suffix = ' Для безопасности войдите в Web или Mini App повторно.' if app.identity.enabled else ''
            return _telegram_message_replies(chat_id, "Готово. Telegram и веб-аккаунт используют один BLAGO ID и общую память." + suffix)

        if text.strip() == "/start":
            welcome_text = self._agent_for_user(blago_user_id, chat_id).welcome_message().removeprefix("xBLAGO: ")
            return _telegram_message_replies(chat_id, welcome_text)

        response = self._agent_for_user(blago_user_id, chat_id).respond(UserMessage(text=text.strip()))
        return _telegram_message_replies(chat_id, response.text)

    def _agent_for_user(self, blago_user_id: str, chat_id: int) -> PersonalAssistantAgent:
        # Load current shared history for every update, including turns from the web.
        # Keeping an in-process agent cache would make other channels invisible.
        return PersonalAssistantAgent(
            memory=self.store.memory_for_user(blago_user_id),
            chat_provider=build_chat_provider(load_llm_config()),
            initial_history=self.store.recent_history(blago_user_id, 12),
            history_recorder=lambda turn: self._record_history(blago_user_id, chat_id, turn),
            history_clearer=lambda: self.store.clear_history(blago_user_id),
        )

    def _record_history(self, blago_user_id: str, chat_id: int, turn: ConversationTurn) -> None:
        self.store.append_history(blago_user_id, turn.role, turn.text, channel="telegram", chat_id=chat_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run xBLAGO as a Telegram bot.")
    parser.add_argument(
        "--memory-dir",
        type=Path,
        default=Path.home() / ".xblago" / "telegram",
        help="Directory for legacy per-chat JSON memory files.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=_path_from_env("XBLAGO_DATA_DIR", Path.home() / ".xblago" / "telegram"),
        help="Directory for xBLAGO persistent data.",
    )
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=_float_from_env("TELEGRAM_POLL_TIMEOUT_SECONDS", 20.0),
        help="Telegram long-poll timeout in seconds.",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=_float_from_env("TELEGRAM_REQUEST_TIMEOUT_SECONDS", 90.0),
        help="Telegram HTTP request timeout in seconds.",
    )
    parser.add_argument(
        "--delivery-mode",
        choices=["auto", "polling", "webhook"],
        default=os.getenv("TELEGRAM_DELIVERY_MODE", "auto").strip().lower(),
        help="How Telegram updates should be delivered.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required to run @xBLAGObot.")

    args.memory_dir.mkdir(parents=True, exist_ok=True)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    api = TelegramApi(
        token=token,
        poll_timeout_seconds=args.poll_timeout,
        request_timeout_seconds=args.request_timeout,
    )
    store = SQLiteBlagoStore(args.data_dir / "blago.sqlite3", legacy_memory_dir=args.memory_dir)
    bot = TelegramBot(api=api, memory_dir=args.memory_dir, store=store)

    if _should_use_webhook(args.delivery_mode):
        _run_webhook_server(bot=bot, api=api, token=token)
        return

    _start_health_server_from_env()
    bot.run_forever()


def _split_telegram_text(text: str, limit: int = 4096) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text
    while remaining:
        chunks.append(remaining[:limit])
        remaining = remaining[limit:]
    return chunks


def _telegram_message_replies(chat_id: int, text: str) -> list[dict[str, str | int]]:
    return [{"chat_id": chat_id, "text": chunk} for chunk in _split_telegram_text(text)]


def _float_from_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        return default


def _path_from_env(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if not value:
        return default

    return Path(value)


def _url_with_query(url: str, params: dict[str, str | int]) -> str:
    if not params:
        return url

    return f"{url}?{urllib.parse.urlencode(params)}"


def _telegram_api_base_url(token: str) -> str:
    relay_url = os.getenv("TELEGRAM_API_RELAY_URL")
    if relay_url:
        return relay_url.rstrip("/")

    configured_url = os.getenv("TELEGRAM_API_BASE_URL")
    if configured_url:
        return f"{configured_url.rstrip('/')}/bot{token}"

    return f"https://api.telegram.org/bot{token}"


def _telegram_identity_from_message(message: dict[str, Any], chat_id: int) -> TelegramIdentity:
    chat = message.get("chat")
    chat_type = chat.get("type") if isinstance(chat, dict) else None
    from_user = message.get("from")
    if not isinstance(from_user, dict):
        return TelegramIdentity(telegram_user_id=None, chat_id=chat_id, chat_type=chat_type)

    telegram_user_id = from_user.get("id")
    if not isinstance(telegram_user_id, int):
        telegram_user_id = None

    return TelegramIdentity(
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        chat_type=chat_type if isinstance(chat_type, str) else None,
        username=_optional_string(from_user.get("username")),
        first_name=_optional_string(from_user.get("first_name")),
        last_name=_optional_string(from_user.get("last_name")),
    )


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _external_update_id(update: dict[str, Any]) -> str | None:
    update_id = update.get("update_id")
    if isinstance(update_id, int):
        return str(update_id)

    return None


class TelegramWebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in {"/", "/healthz"}:
            self.send_response(404)
            self.end_headers()
            return

        body = b"ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        server = self.server
        expected_path = getattr(server, "telegram_webhook_path", "")
        expected_secret = getattr(server, "telegram_webhook_secret", "")

        if self.path != expected_path:
            self.send_response(404)
            self.end_headers()
            return

        if self.headers.get("X-Telegram-Bot-Api-Secret-Token") != expected_secret:
            self.send_response(403)
            self.end_headers()
            return

        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError:
            length = 0

        try:
            update = json.loads(self.rfile.read(length).decode("utf-8"))
            bot = getattr(server, "telegram_bot")
            if _bool_from_env("TELEGRAM_RETURN_WEBHOOK_REPLIES", False):
                self._send_json({"ok": True, "replies": bot.replies_for_update(update)})
                return

            bot.handle_update(update)
        except Exception as exc:
            print(f"Telegram webhook error: {exc}")
            self._send_json({"ok": False, "error": "update processing failed"}, status=500)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok\n")

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


HealthHandler = TelegramWebhookHandler


def _start_health_server_from_env() -> None:
    port = os.getenv("PORT")
    if not port:
        return

    from xblago.integrations.web import BlagoWebHandler, WebApp

    data_dir = _path_from_env("XBLAGO_DATA_DIR", Path.home() / ".xblago" / "telegram")
    server = ThreadingHTTPServer(("0.0.0.0", int(port)), BlagoWebHandler)
    server.blago_web_app = WebApp(SQLiteBlagoStore(data_dir / "blago.sqlite3"))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"xBLAGO health server is listening on port {port}.")


def _should_use_webhook(delivery_mode: str) -> bool:
    if delivery_mode == "webhook":
        return True
    if delivery_mode == "polling":
        return False
    return bool(os.getenv("SPACE_HOST") or os.getenv("TELEGRAM_WEBHOOK_URL"))


def _run_webhook_server(bot: TelegramBot, api: TelegramApi, token: str) -> None:
    port = int(os.getenv("PORT", "7860"))
    public_base_url = _webhook_base_url()
    secret_token = _webhook_secret(token)
    webhook_path = f"/telegram/{secret_token}"
    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", f"{public_base_url}{webhook_path}")

    from xblago.integrations.web import BlagoWebHandler, WebApp

    server = ThreadingHTTPServer(("0.0.0.0", port), BlagoWebHandler)
    server.blago_web_app = WebApp(bot.store)
    server.telegram_bot = bot
    server.telegram_webhook_path = urllib.parse.urlparse(webhook_url).path
    server.telegram_webhook_secret = secret_token

    print(f"xBLAGO Telegram webhook server is listening on port {port}.")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    bot._print_startup_identity()
    if _bool_from_env("TELEGRAM_SKIP_WEBHOOK_REGISTRATION", False):
        print("Telegram webhook registration is skipped by configuration.")
    else:
        _register_webhook_with_retry(api, webhook_url, secret_token)

    while True:
        time.sleep(3600)


def _webhook_base_url() -> str:
    explicit_url = os.getenv("TELEGRAM_WEBHOOK_URL")
    if explicit_url:
        parsed = urllib.parse.urlparse(explicit_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    host = os.getenv("SPACE_HOST")
    if host:
        return f"https://{host}"

    raise SystemExit("TELEGRAM_WEBHOOK_URL or SPACE_HOST is required for Telegram webhook mode.")


def _webhook_secret(token: str) -> str:
    return os.getenv("TELEGRAM_WEBHOOK_SECRET") or hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bool_from_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


def _register_webhook_with_retry(api: TelegramApi, webhook_url: str, secret_token: str) -> None:
    retry_delay_seconds = 3
    while True:
        try:
            api.set_webhook(webhook_url, secret_token)
        except (RuntimeError, urllib.error.URLError, TimeoutError) as exc:
            print(f"Telegram webhook registration error: {exc}")
            time.sleep(retry_delay_seconds)
            retry_delay_seconds = min(retry_delay_seconds * 2, 60)
            continue

        print("Telegram webhook is registered.")
        return


if __name__ == "__main__":
    main()
