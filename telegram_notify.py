"""Telegram notification helper for MEW Energy Bot."""

import logging
import os

import requests

logger = logging.getLogger("mew-bot")

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    os.environ.get("ARC_BOT_TOKEN", ""),
)
TELEGRAM_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID",
    os.environ.get("ARC_BOT_CHAT_ID", ""),
)
TELEGRAM_API = "https://api.telegram.org"


def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not set. Skipping notification.")
        return False

    url = f"{TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info("Telegram notification sent.")
        return True
    except requests.RequestException as e:
        logger.warning(f"Telegram send failed: {e}")
        return False
