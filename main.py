"""FastAPI wrapper for MEW Energy Bot with scheduled daily claims."""

import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from bot import run_claim_cycle
from generate_wallets import main as generate_wallets

logger = logging.getLogger("mew-bot")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)

scheduler = BackgroundScheduler()
last_run_result: dict = {"status": "not_started", "timestamp": None}
run_lock = threading.Lock()


def scheduled_claim():
    global last_run_result
    if not run_lock.acquire(blocking=False):
        logger.info("Claim cycle already running. Skipping.")
        return
    try:
        logger.info("Scheduled claim cycle triggered.")
        run_claim_cycle()
        last_run_result = {
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Scheduled claim failed: {e}", exc_info=True)
        last_run_result = {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        run_lock.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    generate_wallets()
    scheduler.add_job(scheduled_claim, "interval", hours=24, id="daily_claim")
    scheduler.start()
    logger.info("Scheduler started. Running initial claim cycle...")
    threading.Thread(target=scheduled_claim, daemon=True).start()
    yield
    scheduler.shutdown()


app = FastAPI(title="MEW Energy Bot", lifespan=lifespan)


@app.get("/")
def root():
    return {
        "service": "MEW Energy Bot",
        "status": "running",
        "last_run": last_run_result,
        "now": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/trigger")
def trigger_claim():
    if not run_lock.acquire(blocking=False):
        return {"status": "already_running"}
    run_lock.release()
    threading.Thread(target=scheduled_claim, daemon=True).start()
    return {"status": "triggered"}
