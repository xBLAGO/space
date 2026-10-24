from __future__ import annotations

import json
import urllib.error
from email.message import Message
from io import BytesIO
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from xblago.core.storage import SQLiteBlagoStore, TelegramIdentity
from xblago.integrations.telegram import (
    TelegramApi,
    TelegramBot,
    TelegramWebhookHandler,
    _should_use_webhook,
    _split_telegram_text,
)
from xblago.integrations.web import WebApp


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


class FailingHistoryStore(SQLiteBlagoStore):
    def append_history(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("history write failed")

    def append_exchange(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("history write failed")


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

    def test_automatic_memory_survives_bot_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            first_bot = TelegramBot(api=FakeTelegramApi(), memory_dir=data_dir)
            first_bot.replies_for_update(_telegram_update(1, 101, 1001, "Меня зовут Анна"))

            restarted_bot = TelegramBot(api=FakeTelegramApi(), memory_dir=data_dir)
            memory = restarted_bot.replies_for_update(_telegram_update(2, 101, 1001, "/memory"))[0]["text"]

        self.assertIn("name: Анна", str(memory))

    def test_duplicate_update_reuses_cached_reply_without_repeating_operation(self) -> None:
        update = _telegram_update(77, 101, 1001, "remember my timezone is Europe/Moscow")
        with tempfile.TemporaryDirectory() as directory:
            bot = TelegramBot(api=FakeTelegramApi(), memory_dir=Path(directory))

            first_reply = bot.replies_for_update(update)
            duplicate_reply = bot.replies_for_update(update)
            memory = bot.replies_for_update(_telegram_update(78, 101, 1001, "/memory"))[0]["text"]

        self.assertEqual(len(first_reply), 1)
        self.assertEqual(duplicate_reply, first_reply)
        self.assertEqual(str(memory).count("timezone: Europe/Moscow"), 1)

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

    def test_missing_hf_token_returns_fallback_through_webhook(self) -> None:
        body = json.dumps(_telegram_update(1, 101, 1001, "Меня зовут Анна")).encode("utf-8")
        headers = Message()
        headers["Content-Length"] = str(len(body))
        headers["X-Telegram-Bot-Api-Secret-Token"] = "secret"
        with tempfile.TemporaryDirectory() as directory, unittest.mock.patch.dict(
            "os.environ",
            {
                "XBLAGO_LLM_PROVIDER": "hf",
                "XBLAGO_LLM_MODEL": "Qwen/Qwen3-14B:cheapest",
                "TELEGRAM_RETURN_WEBHOOK_REPLIES": "true",
            },
            clear=True,
        ):
            data_dir = Path(directory)
            bot = TelegramBot(api=FakeTelegramApi(), memory_dir=data_dir)
            handler = TelegramWebhookHandler.__new__(TelegramWebhookHandler)
            handler.path = "/telegram/secret"
            handler.headers = headers
            handler.rfile = BytesIO(body)
            handler.wfile = BytesIO()
            handler.server = SimpleNamespace(
                telegram_bot=bot,
                telegram_webhook_path="/telegram/secret",
                telegram_webhook_secret="secret",
            )
            handler.status_code = None
            handler.response_headers = []
            handler.send_response = lambda status, message=None: setattr(handler, "status_code", status)
            handler.send_header = lambda name, value: handler.response_headers.append((name, value))
            handler.end_headers = lambda: None

            handler.do_POST()

            self.assertEqual(handler.status_code, 200)
            payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
            self.assertIn("не получается связаться", payload["replies"][0]["text"])
            uid = bot.store.resolve_telegram_user(TelegramIdentity(telegram_user_id=101, chat_id=1001, chat_type="private"))
            self.assertEqual(bot.store.all_memory(uid), {})
            self.assertEqual(len(bot.store.recent_history(uid, 12)), 2)

    def test_llm_fallback_does_not_mask_history_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory, unittest.mock.patch.dict(
            "os.environ", {"XBLAGO_LLM_PROVIDER": "hf"}, clear=True
        ):
            data_dir = Path(directory)
            store = FailingHistoryStore(data_dir / "blago.sqlite3", legacy_memory_dir=data_dir)
            bot = TelegramBot(api=FakeTelegramApi(), memory_dir=data_dir, store=store)

            with self.assertRaisesRegex(RuntimeError, "history write failed"):
                bot.replies_for_update(_telegram_update(1, 101, 1001, "Привет"))

    def test_invalid_hf_token_returns_fallback_through_webhook_once(self) -> None:
        body = json.dumps(_telegram_update(1, 101, 1001, "Составь план")).encode("utf-8")
        headers = Message()
        headers["Content-Length"] = str(len(body))
        headers["X-Telegram-Bot-Api-Secret-Token"] = "secret"
        provider_error = urllib.error.HTTPError("https://router.huggingface.co/v1/chat/completions", 401, "token", {}, None)
        with tempfile.TemporaryDirectory() as directory, unittest.mock.patch.dict(
            "os.environ",
            {
                "XBLAGO_LLM_PROVIDER": "hf",
                "HF_TOKEN": "hf_invalid_token",
                "TELEGRAM_RETURN_WEBHOOK_REPLIES": "true",
            },
            clear=True,
        ), unittest.mock.patch(
            "xblago.core.llm.urllib.request.urlopen", side_effect=provider_error
        ) as urlopen:
            bot = TelegramBot(api=FakeTelegramApi(), memory_dir=Path(directory))
            handler = TelegramWebhookHandler.__new__(TelegramWebhookHandler)
            handler.path = "/telegram/secret"
            handler.headers = headers
            handler.rfile = BytesIO(body)
            handler.wfile = BytesIO()
            handler.server = SimpleNamespace(
                telegram_bot=bot,
                telegram_webhook_path="/telegram/secret",
                telegram_webhook_secret="secret",
            )
            handler.status_code = None
            handler.response_headers = []
            handler.send_response = lambda status, message=None: setattr(handler, "status_code", status)
            handler.send_header = lambda name, value: handler.response_headers.append((name, value))
            handler.end_headers = lambda: None

            handler.do_POST()

        self.assertEqual(handler.status_code, 200)
        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertIn("не получается связаться", payload["replies"][0]["text"])
        self.assertEqual(urlopen.call_count, 1)

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

    def test_telegram_link_command_merges_with_web_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            store = SQLiteBlagoStore(data_dir / "blago.sqlite3", legacy_memory_dir=data_dir)
            web = WebApp(store)
            with unittest.mock.patch.dict("os.environ", {"XBLAGO_WEB_INVITE_CODE": "invite"}):
                token, _ = web.register("web_person", "a-long-password", "invite")
            web_uid = web.session(token)[0]
            bot = TelegramBot(api=FakeTelegramApi(), memory_dir=data_dir, store=store)
            bot.replies_for_update(_telegram_update(1, 777, 777, "remember my city is Telegram"))
            code = web.new_link_code(web_uid)

            reply = bot.replies_for_update(_telegram_update(2, 777, 777, f"/link {code}"))[0]["text"]
            memory = web.store.all_memory(web_uid)
            telegram_memory = bot.replies_for_update(_telegram_update(3, 777, 777, "/memory"))[0]["text"]

        self.assertIn("общую память", str(reply))
        self.assertEqual(memory["city"], "Telegram")
        self.assertIn("city: Telegram", str(telegram_memory))

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

    def test_startup_check_can_be_skipped_for_local_web_checks(self) -> None:
        api = FakeTelegramApi()
        with tempfile.TemporaryDirectory() as directory:
            bot = TelegramBot(api=api, memory_dir=Path(directory))
            with unittest.mock.patch.dict("os.environ", {"TELEGRAM_SKIP_STARTUP_CHECK": "true"}):
                bot._print_startup_identity()

        self.assertEqual(api.sent, [])


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
