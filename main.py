"""FastAPI wrapper + Telegram Bot for MEW Energy Bot deployment."""

import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI

logger = logging.getLogger("mew-bot")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)


def start_telegram_bot():
    """Start the Telegram bot in a background thread."""
    try:
        import sys
        import asyncio
        if sys.version_info >= (3, 10):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        from tg_bot import main as tg_main
        tg_main()
    except Exception as e:
        logger.error(f"Telegram bot failed: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Telegram bot in background...")
    bot_thread = threading.Thread(target=start_telegram_bot, daemon=True)
    bot_thread.start()
    yield
    logger.info("Shutting down...")


app = FastAPI(title="MEW Energy Bot", lifespan=lifespan)


@app.get("/")
def root():
    return {
        "service": "MEW Energy Bot v3.1",
        "status": "running",
        "now": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
def health():
    return {"status": "ok"}
