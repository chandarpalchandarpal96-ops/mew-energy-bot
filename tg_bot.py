"""MEW Energy Telegram Bot - Control bot via Telegram commands."""

import asyncio
import json
import logging
import os
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests as http_requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from bot import (
    PROXY_LIST,
    api_request,
    authenticate_wallet,
    build_composite_id,
    check_balance,
    claim_action,
    claim_energy,
    get_available_actions,
    get_or_create_uuid,
    register_profile,
)
from generate_wallets import generate_wallets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("mew-tg-bot")

BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    os.environ.get("ARC_BOT_TOKEN", ""),
)
OWNER_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID",
    os.environ.get("ARC_BOT_CHAT_ID", ""),
)
WALLETS_FILE = "wallets.json"
MNEMONIC_FILE = "mnemonic_backup.txt"
LOGS_DIR = "logs"

claim_running = False
auto_claim_timer = None
IST = ZoneInfo("Asia/Kolkata")
AUTO_CLAIM_HOUR = 8
AUTO_CLAIM_MINUTE = 30


def load_wallets() -> list[dict]:
    if not os.path.exists(WALLETS_FILE):
        return []
    with open(WALLETS_FILE) as f:
        return json.load(f)


def save_wallets(wallets: list[dict]):
    with open(WALLETS_FILE, "w") as f:
        json.dump(wallets, f, indent=2)


def save_mnemonic_backup(wallets: list[dict]):
    """Save all mnemonic phrases to a backup text file."""
    with open(MNEMONIC_FILE, "w") as f:
        f.write(f"MEW Energy Bot - Mnemonic Backup\n")
        f.write(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
        f.write(f"Total Wallets: {len(wallets)}\n")
        f.write("=" * 60 + "\n\n")
        for w in wallets:
            f.write(f"#{w.get('index', '?')} | {w['address']}\n")
            f.write(f"Mnemonic: {w['mnemonic']}\n")
            f.write(f"Private Key: {w['private_key']}\n")
            f.write("-" * 60 + "\n")
    logger.info(f"Mnemonic backup saved to {MNEMONIC_FILE}")


def is_owner(update: Update) -> bool:
    if not OWNER_CHAT_ID:
        return True
    return str(update.effective_chat.id) == str(OWNER_CHAT_ID)


def _send_tg_message(chat_id: int, text: str, parse_mode: str | None = None):
    """Send Telegram message synchronously from background thread."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", os.environ.get("ARC_BOT_TOKEN", ""))
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        logger.warning(f"Failed to send TG message: {e}")


def _send_tg_document(chat_id: int, file_path: str, caption: str = ""):
    """Send a file via Telegram synchronously."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", os.environ.get("ARC_BOT_TOKEN", ""))
    try:
        with open(file_path, "rb") as f:
            http_requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat_id, "caption": caption},
                files={"document": (os.path.basename(file_path), f)},
                timeout=30,
            )
    except Exception as e:
        logger.warning(f"Failed to send TG document: {e}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.message.reply_text(
        "<b>MEW Energy Bot v3.1</b>\n\n"
        "Commands:\n"
        "/genr [N] - Generate N wallets + auto claim\n"
        "/claim - Start claim cycle for all wallets\n"
        "/balance - Check balance of all wallets\n"
        "/status - Show bot status\n"
        "/wallets - Show wallet count & summary\n"
        "/export - Export wallet details\n"
        "/delete - Delete all wallets\n"
        "/proxy - Show proxy status\n"
        "/help - Show this help\n\n"
        "<i>Auto-claim: Daily 8:30 AM IST (10 sec delay)</i>",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_genr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global claim_running
    if not is_owner(update):
        return

    count = 200
    if context.args:
        try:
            count = int(context.args[0])
            if count < 1 or count > 1000:
                await update.message.reply_text("1 se 1000 ke beech mein number do.")
                return
        except ValueError:
            await update.message.reply_text("Valid number do. Example: /genr 200")
            return

    existing = load_wallets()
    if existing:
        await update.message.reply_text(
            f"Pehle se {len(existing)} wallets hain.\n"
            f"Naye {count} wallets ADD honge (total: {len(existing) + count}).\n\n"
            f"Generating..."
        )
    else:
        await update.message.reply_text(f"Generating {count} wallets...")

    loop = asyncio.get_running_loop()
    new_wallets = await loop.run_in_executor(None, generate_wallets, count)

    for i, w in enumerate(new_wallets):
        w["index"] = len(existing) + i

    all_wallets = existing + new_wallets
    save_wallets(all_wallets)
    save_mnemonic_backup(all_wallets)

    await update.message.reply_text(
        f"<b>{count} wallets generated!</b>\n"
        f"Total wallets: {len(all_wallets)}\n"
        f"First: {new_wallets[0]['address'][:10]}...\n"
        f"Last: {new_wallets[-1]['address'][:10]}...\n\n"
        f"Mnemonic backup saved to {MNEMONIC_FILE}\n"
        f"<b>Auto-claiming started...</b>",
        parse_mode="HTML",
    )

    # Send mnemonic backup file via Telegram
    chat_id = update.effective_chat.id
    loop.run_in_executor(None, _send_tg_document, chat_id, MNEMONIC_FILE, f"{len(all_wallets)} wallets ka mnemonic backup")

    # Auto-start claim after generating
    if not claim_running:
        claim_running = True
        threading.Thread(
            target=run_claim_thread,
            args=(all_wallets, chat_id),
            daemon=True,
        ).start()


async def cmd_claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global claim_running
    if not is_owner(update):
        return

    wallets = load_wallets()
    if not wallets:
        await update.message.reply_text("Koi wallet nahi hai. Pehle /genr karo.")
        return

    if claim_running:
        await update.message.reply_text("Claim cycle already running hai. Wait karo.")
        return

    claim_running = True
    await update.message.reply_text(
        f"Claim cycle starting for {len(wallets)} wallets...\n"
        f"Proxies: {len(PROXY_LIST) or 'None'}\n"
        f"Delay: ~5.1-5.9 min per wallet\n"
        f"Estimated time: ~{len(wallets) * 5.5:.0f} min"
    )

    chat_id = update.effective_chat.id
    threading.Thread(
        target=run_claim_thread,
        args=(wallets, chat_id),
        daemon=True,
    ).start()


def run_claim_thread(wallets: list[dict], chat_id: int, fast_mode: bool = False):
    """Process all wallets with per-wallet XP updates.
    
    fast_mode=True: 10 sec delay (for scheduled auto-claim at 8:30)
    fast_mode=False: 5.1-5.9 min delay (for manual /genr or /claim)
    """
    global claim_running

    total = len(wallets)
    success = 0
    already = 0
    failed = 0
    total_reward = 0
    total_action = 0
    start = time.time()
    delay_info = "10 sec" if fast_mode else "~1 min"

    _send_tg_message(chat_id, f"Claim cycle started for {total} wallets...\nDelay: {delay_info} per wallet")

    for i, wallet in enumerate(wallets):
        address = wallet["address"]
        private_key = wallet["private_key"]
        device_uuid = get_or_create_uuid(address)
        composite_id = build_composite_id(address, device_uuid)
        short = f"{address[:6]}...{address[-4:]}"

        logger.info(f"[{i+1}/{total}] Processing {short}")

        register_profile(composite_id)
        authenticate_wallet(composite_id, address, private_key)

        bal = check_balance(composite_id)
        bal_amount = bal.get("balance", 0) if bal else 0

        wallet_reward = 0
        wallet_action = 0
        status = ""

        result = claim_energy(composite_id)
        if result:
            if result.get("already_claimed"):
                already += 1
                status = "Already Claimed"
            else:
                success += 1
                wallet_reward = result.get("reward", 0)
                total_reward += wallet_reward
                status = "Claimed!"
        else:
            failed += 1
            status = "Failed"

        actions = get_available_actions(composite_id)
        for action in actions:
            aid = action.get("id")
            amount = action.get("amount", 0)
            ar = claim_action(composite_id, aid)
            if ar and not ar.get("already_claimed"):
                action_reward = ar.get("reward", amount)
                wallet_action += action_reward
                total_action += action_reward

        # Update status if actions succeeded but daily failed
        if status == "Failed" and wallet_action > 0:
            status = "Action Claimed"

        # Per wallet XP message
        total_xp = wallet_reward + wallet_action
        msg = (
            f"[{i+1}/{total}] {short}\n"
            f"Status: {status}\n"
            f"XP: +{total_xp}"
        )
        if wallet_reward > 0 and wallet_action > 0:
            msg += f" ({wallet_reward} daily + {wallet_action} actions)"
        elif wallet_reward > 0:
            msg += " (daily)"
        elif wallet_action > 0:
            msg += " (actions)"
        msg += f"\nBalance: {bal_amount + total_xp} energy"
        _send_tg_message(chat_id, msg)

        # Delay between wallets
        if i < total - 1:
            if fast_mode:
                delay = 10  # 10 seconds for scheduled auto-claim
            else:
                delay = random.uniform(1.0 * 60, 1.2 * 60)  # 60-72 seconds
            logger.info(f"  Waiting {delay:.0f}s before next wallet...")
            time.sleep(delay)

    elapsed = time.time() - start
    claim_running = False

    os.makedirs(LOGS_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.join(LOGS_DIR, f"run_{ts}.json")
    with open(log_file, "w") as f:
        json.dump({"success": success, "already": already, "failed": failed,
                    "reward": total_reward, "action": total_action,
                    "timestamp": ts}, f)

    summary = (
        f"<b>Claim Cycle Complete</b>\n"
        f"{'=' * 30}\n"
        f"Total Wallets: {total}\n"
        f"Successful: {success}\n"
        f"Already Claimed: {already}\n"
        f"Failed: {failed}\n"
        f"Daily Reward: {total_reward} XP\n"
        f"Action Reward: {total_action} XP\n"
        f"<b>Total: {total_reward + total_action} XP</b>\n"
        f"Proxies: {len(PROXY_LIST) or 'None'}\n"
        f"Time: {elapsed:.0f}s ({elapsed/60:.1f} min)\n\n"
        f"<i>Next auto-claim at 8:30 AM IST</i>"
    )
    _send_tg_message(chat_id, summary, parse_mode="HTML")

    # Schedule next auto-claim at 8:30 AM IST
    schedule_daily_claim(chat_id)


def _seconds_until_next_claim() -> float:
    """Calculate seconds until next 8:30 AM IST."""
    now = datetime.now(IST)
    target = now.replace(hour=AUTO_CLAIM_HOUR, minute=AUTO_CLAIM_MINUTE, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def schedule_daily_claim(chat_id: int):
    """Schedule next claim cycle at 8:30 AM IST."""
    global auto_claim_timer

    if auto_claim_timer:
        auto_claim_timer.cancel()

    wait_seconds = _seconds_until_next_claim()
    next_time = datetime.now(IST) + timedelta(seconds=wait_seconds)

    def auto_claim():
        global claim_running
        if claim_running:
            logger.info("Auto-claim skipped — claim already running.")
            schedule_daily_claim(chat_id)
            return

        logger.info("8:30 AM IST — Auto-claim triggered!")
        _send_tg_message(
            chat_id,
            f"<b>8:30 AM Auto-Claim Started!</b>\n"
            f"Daily reset — claiming all wallets (10 sec delay)...",
            parse_mode="HTML",
        )

        current_wallets = load_wallets()
        if not current_wallets:
            _send_tg_message(chat_id, "Auto-claim: Koi wallet nahi hai.")
            schedule_daily_claim(chat_id)
            return

        claim_running = True
        run_claim_thread(current_wallets, chat_id, fast_mode=True)

    auto_claim_timer = threading.Timer(wait_seconds, auto_claim)
    auto_claim_timer.daemon = True
    auto_claim_timer.start()
    logger.info(f"Auto-claim scheduled at {next_time.strftime('%Y-%m-%d %H:%M IST')} ({wait_seconds/3600:.1f}h from now)")


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    wallets = load_wallets()
    if not wallets:
        await update.message.reply_text("Koi wallet nahi hai. Pehle /genr karo.")
        return

    await update.message.reply_text(f"Checking balance for {len(wallets)} wallets...")

    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, check_all_balances, wallets)

    total_bal = sum(r.get("balance", 0) for r in results)
    checked = len([r for r in results if r.get("balance") is not None])

    msg = (
        f"<b>Balance Summary</b>\n"
        f"Wallets Checked: {checked}/{len(wallets)}\n"
        f"<b>Total Balance: {total_bal} energy</b>\n"
    )

    top5 = sorted(results, key=lambda x: x.get("balance", 0), reverse=True)[:5]
    if top5 and top5[0].get("balance", 0) > 0:
        msg += "\nTop 5:\n"
        for r in top5:
            msg += f"  {r['address'][:10]}... = {r.get('balance', 0)}\n"

    await update.message.reply_text(msg, parse_mode="HTML")


def check_all_balances(wallets: list[dict]) -> list[dict]:
    results = []
    for w in wallets:
        address = w["address"]
        device_uuid = get_or_create_uuid(address)
        composite_id = build_composite_id(address, device_uuid)
        bal = check_balance(composite_id)
        results.append({
            "address": address,
            "balance": bal.get("balance", 0) if bal else 0,
            "next_claim": bal.get("next_claim_date") if bal else None,
        })
        time.sleep(1)
    return results


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    wallets = load_wallets()
    logs = []
    if os.path.exists(LOGS_DIR):
        logs = sorted(os.listdir(LOGS_DIR))

    if auto_claim_timer and auto_claim_timer.is_alive():
        next_claim = datetime.now(IST) + timedelta(seconds=_seconds_until_next_claim())
        auto_status = f"Active (next: {next_claim.strftime('%H:%M IST')})"
    else:
        auto_status = "Not scheduled"

    msg = (
        f"<b>Bot Status</b>\n"
        f"Wallets: {len(wallets)}\n"
        f"Proxies: {len(PROXY_LIST) or 'None'}\n"
        f"Claim Running: {'Yes' if claim_running else 'No'}\n"
        f"Auto-Claim: {auto_status}\n"
        f"Total Runs: {len(logs)}\n"
    )

    if logs:
        msg += f"Last Run: {logs[-1].replace('run_', '').replace('.json', '')}\n"

    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    wallets = load_wallets()
    if not wallets:
        await update.message.reply_text("Koi wallet nahi hai. /genr se banao.")
        return

    msg = (
        f"<b>Wallets: {len(wallets)}</b>\n\n"
        f"First 5:\n"
    )
    for w in wallets[:5]:
        msg += f"  {w['index']}: {w['address'][:16]}...\n"

    if len(wallets) > 5:
        msg += f"\n...aur {len(wallets) - 5} wallets\n"

    msg += f"\nLast: {wallets[-1]['address'][:16]}..."

    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    wallets = load_wallets()
    if not wallets:
        await update.message.reply_text("Koi wallet nahi hai.")
        return

    # Save fresh mnemonic backup
    save_mnemonic_backup(wallets)

    await update.message.reply_document(
        document=open(MNEMONIC_FILE, "rb"),
        filename="mnemonic_backup.txt",
        caption=f"{len(wallets)} wallets ka mnemonic backup.",
    )


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    wallets = load_wallets()
    if not wallets:
        await update.message.reply_text("Koi wallet nahi hai already.")
        return

    if claim_running:
        await update.message.reply_text("Claim chal raha hai. Pehle complete hone do.")
        return

    count = len(wallets)
    os.remove(WALLETS_FILE)
    await update.message.reply_text(
        f"{count} wallets deleted.\n"
        f"UUIDs intact hain (same wallets dobara banoge toh kaam aayenge).\n"
        f"Naye wallets ke liye /genr karo."
    )


async def cmd_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    if PROXY_LIST:
        msg = f"<b>Proxies: {len(PROXY_LIST)}</b>\n\n"
        for i, p in enumerate(PROXY_LIST):
            url = p.get("http", "")
            host = url.split("@")[-1] if "@" in url else url
            msg += f"  {i+1}. {host}\n"
    else:
        msg = "No proxies configured.\nproxies.txt file mein add karo."

    await update.message.reply_text(msg, parse_mode="HTML")


def main():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        return

    logger.info("Starting MEW Energy Telegram Bot v3.1...")
    logger.info(f"Owner Chat ID: {OWNER_CHAT_ID or 'Not set (anyone can use)'}")
    logger.info(f"Proxies loaded: {len(PROXY_LIST)}")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("genr", cmd_genr))
    app.add_handler(CommandHandler("claim", cmd_claim))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("wallets", cmd_wallets))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("proxy", cmd_proxy))

    # Schedule daily auto-claim at 8:30 AM IST on startup
    if OWNER_CHAT_ID:
        schedule_daily_claim(int(OWNER_CHAT_ID))
        next_claim = datetime.now(IST) + timedelta(seconds=_seconds_until_next_claim())
        logger.info(f"Daily auto-claim scheduled at 8:30 AM IST (next: {next_claim.strftime('%Y-%m-%d %H:%M IST')})")

    logger.info("Bot polling started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import sys
    if sys.version_info >= (3, 10):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    main()
