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
from threading import Thread
from typing import Any

from xblago.agents.personal_assistant import PersonalAssistantAgent
from xblago.core.config import load_llm_config
from xblago.core.llm import build_chat_provider
from xblago.core.memory import JsonMemoryStore
from xblago.core.messages import UserMessage


class TelegramApi:
    def __init__(self, token: str, poll_timeout_seconds: float = 20.0, request_timeout_seconds: float = 90.0) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"
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
            response = requests.post(
                f"{self.base_url}/{method}",
                data=params,
                timeout=self.request_timeout_seconds,
                verify=certifi.where(),
            )
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(f"Telegram API request failed for {method}: {exc}") from exc

        if not body.get("ok"):
            raise RuntimeError(f"Telegram API returned an error for {method}: {body}")

        return dict(body)


class TelegramBot:
    def __init__(self, api: TelegramApi, memory_dir: Path) -> None:
        self.api = api
        self.memory_dir = memory_dir
        self.agents: dict[int, PersonalAssistantAgent] = {}

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
        try:
            bot = self.api.get_me()
        except (RuntimeError, urllib.error.URLError, TimeoutError) as exc:
            print(f"Telegram startup check failed: {exc}")
            return

        username = bot.get("username", "unknown")
        print(f"Telegram startup check succeeded: @{username}")

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return

        chat = message.get("chat")
        text = message.get("text")
        if not isinstance(chat, dict) or not isinstance(text, str):
            return

        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            return

        if text.strip() == "/start":
            self.api.send_message(chat_id, self._agent_for_chat(chat_id).welcome_message().removeprefix("xBLAGO: "))
            return

        response = self._agent_for_chat(chat_id).respond(UserMessage(text=text.strip()))
        self.api.send_message(chat_id, response.text)

    _handle_update = handle_update

    def _agent_for_chat(self, chat_id: int) -> PersonalAssistantAgent:
        if chat_id not in self.agents:
            memory = JsonMemoryStore(self.memory_dir / f"telegram_{chat_id}.json")
            provider = build_chat_provider(load_llm_config())
            self.agents[chat_id] = PersonalAssistantAgent(memory=memory, chat_provider=provider)

        return self.agents[chat_id]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run xBLAGO as a Telegram bot.")
    parser.add_argument(
        "--memory-dir",
        type=Path,
        default=Path.home() / ".xblago" / "telegram",
        help="Directory for per-chat memory files.",
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
    api = TelegramApi(
        token=token,
        poll_timeout_seconds=args.poll_timeout,
        request_timeout_seconds=args.request_timeout,
    )
    bot = TelegramBot(api=api, memory_dir=args.memory_dir)

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


def _float_from_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        return default


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
            getattr(server, "telegram_bot").handle_update(update)
        except Exception as exc:
            print(f"Telegram webhook error: {exc}")

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok\n")

    def log_message(self, format: str, *args: Any) -> None:
        return


HealthHandler = TelegramWebhookHandler


def _start_health_server_from_env() -> None:
    port = os.getenv("PORT")
    if not port:
        return

    server = ThreadingHTTPServer(("0.0.0.0", int(port)), TelegramWebhookHandler)
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

    server = ThreadingHTTPServer(("0.0.0.0", port), TelegramWebhookHandler)
    server.telegram_bot = bot
    server.telegram_webhook_path = urllib.parse.urlparse(webhook_url).path
    server.telegram_webhook_secret = secret_token

    print(f"xBLAGO Telegram webhook server is listening on port {port}.")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    bot._print_startup_identity()
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
