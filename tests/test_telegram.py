from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from xblago.integrations.telegram import TelegramApi, TelegramBot, _should_use_webhook, _split_telegram_text


class FakeTelegramApi(TelegramApi):
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class TelegramBotTest(unittest.TestCase):
    def test_start_sends_welcome_message(self) -> None:
        api = FakeTelegramApi()
        with tempfile.TemporaryDirectory() as directory:
            bot = TelegramBot(api=api, memory_dir=Path(directory))
            bot._handle_update(
                {
                    "message": {
                        "chat": {"id": 123},
                        "text": "/start",
                    }
                }
            )

        self.assertEqual(api.sent[0][0], 123)
        self.assertIn("первый агент сети xBLAGO", api.sent[0][1])

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


if __name__ == "__main__":
    unittest.main()
