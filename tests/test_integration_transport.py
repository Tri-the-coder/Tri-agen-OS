import os

import pytest

from telegram_client import TelegramClient


@pytest.mark.integration
def test_send_message_integration_smoke():
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        pytest.skip("Telegram credentials are not configured")

    client = TelegramClient()
    response = client.send_message(os.getenv("TELEGRAM_CHAT_ID", ""), "Hello from integration test")

    assert response["ok"] is True
