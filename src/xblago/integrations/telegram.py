from __future__ import annotations

import argparse
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

    def _request(self, method: str, params: dict[str, str | int]) -> dict[str, Any]:
        data = urllib.parse.urlencode(params).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}/{method}", data=data, method="POST")

        with urllib.request.urlopen(request, timeout=self.request_timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))

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

    def _handle_update(self, update: dict[str, Any]) -> None:
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
    return parser


def main() -> None:
    args = build_parser().parse_args()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required to run @xBLAGObot.")

    _start_health_server_from_env()
    args.memory_dir.mkdir(parents=True, exist_ok=True)
    api = TelegramApi(
        token=token,
        poll_timeout_seconds=args.poll_timeout,
        request_timeout_seconds=args.request_timeout,
    )
    TelegramBot(api=api, memory_dir=args.memory_dir).run_forever()


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


class HealthHandler(BaseHTTPRequestHandler):
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

    def log_message(self, format: str, *args: Any) -> None:
        return


def _start_health_server_from_env() -> None:
    port = os.getenv("PORT")
    if not port:
        return

    server = ThreadingHTTPServer(("0.0.0.0", int(port)), HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"xBLAGO health server is listening on port {port}.")


if __name__ == "__main__":
    main()
