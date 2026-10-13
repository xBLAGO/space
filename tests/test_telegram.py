from __future__ import annotations

import json
from email.message import Message
from io import BytesIO
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from xblago.core.storage import SQLiteBlagoStore
from xblago.integrations.telegram import (
    TelegramApi,
    TelegramBot,
    TelegramWebhookHandler,
    _should_use_webhook,
    _split_telegram_text,
)


class FakeTelegramApi(TelegramApi):
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class FailingBot:
    def replies_for_update(self, update: dict[str, object]) -> list[dict[str, str | int]]:
        raise RuntimeError("boom")

    def handle_update(self, update: dict[str, object]) -> None:
        raise RuntimeError("boom")


class TelegramBotTest(unittest.TestCase):
    def test_start_sends_welcome_message(self) -> None:
        api = FakeTelegramApi()
        with tempfile.TemporaryDirectory() as directory:
            bot = TelegramBot(api=api, memory_dir=Path(directory))
            bot._handle_update(
                {
                    "update_id": 1,
                    "message": {
                        "from": {"id": 123, "first_name": "Roman"},
                        "chat": {"id": 123},
                        "text": "/start",
                    }
                }
            )

        self.assertEqual(api.sent[0][0], 123)
        self.assertIn("первый агент сети xBLAGO", api.sent[0][1])

    def test_two_users_do_not_see_each_other_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bot = TelegramBot(api=FakeTelegramApi(), memory_dir=Path(directory))

            bot.replies_for_update(_telegram_update(1, 101, 1001, "remember my city is Moscow"))
            bot.replies_for_update(_telegram_update(2, 202, 2002, "remember my city is Berlin"))

            first_memory = bot.replies_for_update(_telegram_update(3, 101, 1001, "/memory"))[0]["text"]
            second_memory = bot.replies_for_update(_telegram_update(4, 202, 2002, "/memory"))[0]["text"]

        self.assertIn("city: Moscow", str(first_memory))
        self.assertNotIn("Berlin", str(first_memory))
        self.assertIn("city: Berlin", str(second_memory))
        self.assertNotIn("city: Moscow", str(second_memory))

    def test_two_users_in_same_chat_are_still_isolated_by_from_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bot = TelegramBot(api=FakeTelegramApi(), memory_dir=Path(directory))

            bot.replies_for_update(_telegram_update(1, 101, -999, "remember my city is Moscow", chat_type="group"))
            second_memory = bot.replies_for_update(_telegram_update(2, 202, -999, "/memory", chat_type="group"))[0][
                "text"
            ]

        self.assertNotIn("city: Moscow", str(second_memory))

    def test_history_survives_bot_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            store = SQLiteBlagoStore(data_dir / "blago.sqlite3", legacy_memory_dir=data_dir)
            first_bot = TelegramBot(api=FakeTelegramApi(), memory_dir=data_dir, store=store)
            first_bot.replies_for_update(_telegram_update(1, 101, 1001, "Собери план недели"))

            restarted_store = SQLiteBlagoStore(data_dir / "blago.sqlite3", legacy_memory_dir=data_dir)
            restarted_bot = TelegramBot(api=FakeTelegramApi(), memory_dir=data_dir, store=restarted_store)
            history = restarted_bot.replies_for_update(_telegram_update(2, 101, 1001, "/history"))[0]["text"]

        self.assertIn("Собери план недели", str(history))

    def test_duplicate_update_does_not_repeat_operation(self) -> None:
        update = _telegram_update(77, 101, 1001, "remember my timezone is Europe/Moscow")
        with tempfile.TemporaryDirectory() as directory:
            bot = TelegramBot(api=FakeTelegramApi(), memory_dir=Path(directory))

            first_reply = bot.replies_for_update(update)
            duplicate_reply = bot.replies_for_update(update)

        self.assertEqual(len(first_reply), 1)
        self.assertEqual(duplicate_reply, [])

    def test_webhook_processing_error_returns_failure(self) -> None:
        body = json.dumps(_telegram_update(1, 101, 1001, "/help")).encode("utf-8")
        headers = Message()
        headers["Content-Length"] = str(len(body))
        headers["X-Telegram-Bot-Api-Secret-Token"] = "secret"
        handler = TelegramWebhookHandler.__new__(TelegramWebhookHandler)
        handler.path = "/telegram/secret"
        handler.headers = headers
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        handler.server = SimpleNamespace(
            telegram_bot=FailingBot(),
            telegram_webhook_path="/telegram/secret",
            telegram_webhook_secret="secret",
        )
        handler.status_code = None
        handler.response_headers = []
        handler.send_response = lambda status, message=None: setattr(handler, "status_code", status)
        handler.send_header = lambda name, value: handler.response_headers.append((name, value))
        handler.end_headers = lambda: None

        with unittest.mock.patch("builtins.print"):
            handler.do_POST()

        self.assertEqual(handler.status_code, 500)

    def test_legacy_json_memory_is_migrated_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "telegram_1001.json").write_text(
                json.dumps({"timezone": "Europe/Moscow"}),
                encoding="utf-8",
            )
            bot = TelegramBot(api=FakeTelegramApi(), memory_dir=data_dir)

            memory = bot.replies_for_update(_telegram_update(1, 1001, 1001, "/memory"))[0]["text"]
            backups = list((data_dir / "backups").glob("*/telegram_1001.json"))

        self.assertIn("timezone: Europe/Moscow", str(memory))
        self.assertEqual(len(backups), 1)

    def test_existing_help_command_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bot = TelegramBot(api=FakeTelegramApi(), memory_dir=Path(directory))
            reply = bot.replies_for_update(_telegram_update(1, 101, 1001, "/help"))[0]["text"]

        self.assertIn("/memory", str(reply))
        self.assertIn("/profile", str(reply))

    def test_splits_long_telegram_messages(self) -> None:
        chunks = _split_telegram_text("a" * 4100, limit=4096)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(len(chunks[0]), 4096)
        self.assertEqual(len(chunks[1]), 4)

    def test_auto_delivery_uses_webhook_on_spaces(self) -> None:
        with unittest.mock.patch.dict("os.environ", {"SPACE_HOST": "romanmaloev-space.hf.space"}):
            self.assertTrue(_should_use_webhook("auto"))

    def test_polling_delivery_overrides_space_detection(self) -> None:
        with unittest.mock.patch.dict("os.environ", {"SPACE_HOST": "romanmaloev-space.hf.space"}):
            self.assertFalse(_should_use_webhook("polling"))


def _telegram_update(
    update_id: int,
    user_id: int,
    chat_id: int,
    text: str,
    chat_type: str = "private",
) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "from": {"id": user_id, "first_name": f"User {user_id}"},
            "chat": {"id": chat_id, "type": chat_type},
            "text": text,
        },
    }


if __name__ == "__main__":
    unittest.main()
