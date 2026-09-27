from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config import Config, ConfigError


BASE_ENV = {
    "OKDESK_LOGIN": "login",
    "OKDESK_PASSWORD": "password",
    "TELEGRAM_BOT_TOKEN": "token",
    "TELEGRAM_CHAT_ID": "77",
}


class ConfigTests(unittest.TestCase):
    def test_numeric_channel_id_is_kept_for_telegram(self) -> None:
        with patch.dict(os.environ, BASE_ENV, clear=True):
            config = Config.from_env()

        self.assertEqual(config.telegram_chat_id, "77")

    def test_public_channel_username_is_supported(self) -> None:
        environment = {**BASE_ENV, "TELEGRAM_CHAT_ID": "@support_alerts"}
        with patch.dict(os.environ, environment, clear=True):
            config = Config.from_env()

        self.assertEqual(config.telegram_chat_id, "@support_alerts")

    def test_empty_channel_id_is_rejected(self) -> None:
        environment = {**BASE_ENV, "TELEGRAM_CHAT_ID": "   "}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ConfigError, "TELEGRAM_CHAT_ID"):
                Config.from_env()


if __name__ == "__main__":
    unittest.main()
