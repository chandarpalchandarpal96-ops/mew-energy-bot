"""MEW Energy Bot - Automates daily energy claims for Ethereum wallets."""

import json
import logging
import os
import random
import time
import uuid
from datetime import datetime, timezone

import requests
from eth_account import Account
from eth_account.messages import encode_defunct

from telegram_notify import send_telegram_message

Account.enable_unaudited_hdwallet_features()

BASE_URL = "https://mainnet.mewwallet.dev"
USER_AGENT = "okhttp/4.12.0"
WALLETS_FILE = "wallets.json"
UUIDS_DIR = "uuids"
LOGS_DIR = "logs"
CLAIM_INTERVAL_HOURS = 24
MIN_DELAY = 3
MAX_DELAY = 8
MAX_RETRIES = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("mew-bot")


def load_proxies() -> list[dict]:
    proxy_file = os.environ.get("PROXY_FILE", "proxies.txt")
    if not os.path.exists(proxy_file):
        return []
    proxies = []
    with open(proxy_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "://" not in line:
                line = f"http://{line}"
            proxies.append({"http": line, "https": line})
    if proxies:
        logger.info(f"Loaded {len(proxies)} proxies from {proxy_file}")
    return proxies


PROXY_LIST = load_proxies()


_proxy_index = 0


def get_proxy(rotate: bool = False) -> dict | None:
    global _proxy_index
    if not PROXY_LIST:
        return None
    if rotate or len(PROXY_LIST) > 1:
        _proxy_index = (_proxy_index + 1) % len(PROXY_LIST)
    return PROXY_LIST[_proxy_index]


def get_or_create_uuid(address: str) -> str:
    os.makedirs(UUIDS_DIR, exist_ok=True)
    uuid_file = os.path.join(UUIDS_DIR, f"{address}.txt")
    if os.path.exists(uuid_file):
        with open(uuid_file) as f:
            return f.read().strip()
    device_id = str(uuid.uuid4())
    with open(uuid_file, "w") as f:
        f.write(device_id)
    return device_id


def build_composite_id(address: str, device_uuid: str) -> str:
    return f"{address}|{device_uuid}"


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
    )
    proxy = get_proxy()
    if proxy:
        session.proxies.update(proxy)
        proxy_host = proxy.get('http', '').split('@')[-1] if '@' in proxy.get('http', '') else proxy.get('http', '')
        logger.debug(f"  Using proxy: {proxy_host}")
    return session


def api_request(method: str, url: str, **kwargs) -> requests.Response | None:
    """Make an API request with retry logic. Switches proxy on each retry."""
    for attempt in range(MAX_RETRIES):
        session = create_session()
        try:
            resp = getattr(session, method)(url, timeout=45, **kwargs)
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            proxy_info = f" (switching proxy)" if len(PROXY_LIST) > 1 else ""
            logger.warning(f"  Request failed (attempt {attempt + 1}/{MAX_RETRIES}){proxy_info}: {e}")
            if attempt < MAX_RETRIES - 1:
                # Rotate to next proxy for retry
                get_proxy(rotate=True)
                time.sleep(3 * (attempt + 1))
    return None


def register_profile(composite_id: str) -> dict | None:
    url = f"{BASE_URL}/v2/profile"
    resp = api_request("patch", url, params={"id": composite_id}, json=[])
    if resp and resp.status_code == 200:
        return resp.json()
    logger.warning("Profile registration failed.")
    return None


def authenticate_wallet(
    composite_id: str,
    address: str,
    private_key: str,
) -> dict | None:
    """Authenticate wallet via /energy/auth using signed message."""
    auth_url = f"{BASE_URL}/energy/auth"

    resp = api_request("get", auth_url, params={"id": composite_id})
    if not resp:
        return None

    auth_status = resp.json()
    is_known = auth_status.get("is_known_wallet_to_device", False)
    if is_known:
        logger.info("  Wallet already authenticated.")
        return auth_status

    logger.info("  Wallet not authenticated. Attempting auth signing...")

    timestamp = str(int(time.time()))
    msg = encode_defunct(text=timestamp)
    signed = Account.sign_message(msg, private_key=private_key)
    signature = "0x" + signed.signature.hex()

    payload = [{"signature": signature, "address": address}]
    auth_resp = api_request(
        "post", auth_url, params={"id": composite_id}, json=payload
    )

    if auth_resp and auth_resp.status_code == 200:
        logger.info("  Auth signing successful!")
        return auth_resp.json()

    tx = {
        "to": "0x0000000000000000000000000000000000000001",
        "value": 0,
        "gas": 21000,
        "maxFeePerGas": 20000000000,
        "maxPriorityFeePerGas": 1000000000,
        "nonce": 0,
        "chainId": 1,
        "type": 2,
    }
    signed_tx = Account.sign_transaction(tx, private_key=private_key)
    raw_tx = "0x" + signed_tx.raw_transaction.hex()

    payload_tx = [{"transaction": raw_tx}]
    auth_resp2 = api_request(
        "post", auth_url, params={"id": composite_id}, json=payload_tx
    )

    if auth_resp2 and auth_resp2.status_code == 200:
        logger.info("  Auth via signed transaction successful!")
        return auth_resp2.json()

    if auth_resp2:
        logger.warning(
            f"  Auth failed: {auth_resp2.status_code} {auth_resp2.text[:150]}"
        )
    return auth_status


def check_balance(composite_id: str) -> dict | None:
    url = f"{BASE_URL}/energy/balance"
    resp = api_request("get", url, params={"id": composite_id})
    if resp and resp.status_code == 200:
        return resp.json()
    logger.warning("Balance check failed.")
    return None


def claim_energy(composite_id: str) -> dict | None:
    url = f"{BASE_URL}/energy/claim"
    resp = api_request("get", url, params={"id": composite_id})
    if not resp:
        return None
    if resp.status_code == 200:
        return resp.json()
    if resp.status_code == 406:
        logger.info("  Already claimed today (406). Skipping.")
        return {"reward": 0, "balance": 0, "already_claimed": True}
    if resp.status_code == 424:
        logger.warning("  Claim returned 424 (auth required or dependency failed).")
        return None
    logger.warning(f"  Claim failed: {resp.status_code} {resp.text[:150]}")
    return None


def get_available_actions(composite_id: str) -> list[dict]:
    url = f"{BASE_URL}/energy/action"
    resp = api_request("get", url, params={"id": composite_id})
    if resp and resp.status_code == 200:
        data = resp.json()
        return data.get("available", [])
    return []


def claim_action(composite_id: str, action_id: int) -> dict | None:
    """Claim a bonus action (e.g. Twitter/X share) via action_id."""
    url = f"{BASE_URL}/energy/claim"
    resp = api_request(
        "get", url, params={"id": composite_id, "action_id": action_id}
    )
    if not resp:
        return None
    if resp.status_code == 200:
        logger.info(f"  Action {action_id} claimed! {resp.text[:100]}")
        return resp.json()
    if resp.status_code == 406:
        logger.info(f"  Action {action_id} not available yet (406).")
        return {"reward": 0, "already_claimed": True}
    logger.warning(
        f"  Action {action_id} claim failed: {resp.status_code} {resp.text[:100]}"
    )
    return None


def load_wallets() -> list[dict]:
    if not os.path.exists(WALLETS_FILE):
        logger.error(f"{WALLETS_FILE} not found. Run generate_wallets.py first.")
        return []
    with open(WALLETS_FILE) as f:
        return json.load(f)


def save_daily_log(results: list[dict]):
    os.makedirs(LOGS_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.join(LOGS_DIR, f"run_{timestamp}.json")
    with open(log_file, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"Daily log saved to {log_file}")


def process_wallet(wallet: dict, index: int, total: int) -> dict:
    address = wallet["address"]
    private_key = wallet["private_key"]
    device_uuid = get_or_create_uuid(address)
    composite_id = build_composite_id(address, device_uuid)
    short_addr = f"{address[:6]}...{address[-4:]}"

    result = {
        "index": index,
        "address": address,
        "status": "unknown",
        "reward": 0,
        "action_reward": 0,
        "balance": 0,
        "error": None,
    }

    logger.info(f"[{index + 1}/{total}] Processing {short_addr}")

    register_profile(composite_id)

    authenticate_wallet(composite_id, address, private_key)

    balance_data = check_balance(composite_id)
    if balance_data:
        current_balance = balance_data.get("balance", 0)
        next_claim = balance_data.get("next_claim_date", "unknown")
        logger.info(f"  Balance: {current_balance}, Next claim: {next_claim}")

    claim_data = claim_energy(composite_id)
    if claim_data:
        if claim_data.get("already_claimed"):
            result["status"] = "already_claimed"
            result["balance"] = balance_data.get("balance", 0) if balance_data else 0
        else:
            result["status"] = "claimed"
            result["reward"] = claim_data.get("reward", 0)
            result["balance"] = claim_data.get("balance", 0)
            logger.info(
                f"  Claimed! Reward: {result['reward']}, Balance: {result['balance']}"
            )
    else:
        result["status"] = "failed"
        result["error"] = "Claim request failed"
        logger.warning(f"  Claim failed for {short_addr}")

    actions = get_available_actions(composite_id)
    for action in actions:
        action_id = action.get("id")
        amount = action.get("amount", 0)
        logger.info(f"  Attempting action {action_id} (+{amount} energy)...")
        action_result = claim_action(composite_id, action_id)
        if action_result and not action_result.get("already_claimed"):
            result["action_reward"] += action_result.get("reward", amount)

    return result


def run_claim_cycle():
    wallets = load_wallets()
    if not wallets:
        return

    total = len(wallets)
    results = []
    success_count = 0
    already_claimed_count = 0
    fail_count = 0
    total_reward = 0
    total_action_reward = 0

    logger.info(f"Starting claim cycle for {total} wallets...")
    start_time = time.time()

    for i, wallet in enumerate(wallets):
        result = process_wallet(wallet, i, total)
        results.append(result)

        if result["status"] == "claimed":
            success_count += 1
            total_reward += result["reward"]
        elif result["status"] == "already_claimed":
            already_claimed_count += 1
        else:
            fail_count += 1

        total_action_reward += result.get("action_reward", 0)

        if i < total - 1:
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            time.sleep(delay)

    elapsed = time.time() - start_time
    save_daily_log(results)

    summary = (
        f"MEW Energy Bot - Claim Cycle Complete\n"
        f"{'=' * 40}\n"
        f"Total Wallets: {total}\n"
        f"Successful Claims: {success_count}\n"
        f"Already Claimed: {already_claimed_count}\n"
        f"Failed Claims: {fail_count}\n"
        f"Daily Reward: {total_reward}\n"
        f"Action Reward: {total_action_reward}\n"
        f"Total Reward: {total_reward + total_action_reward}\n"
        f"Proxies: {len(PROXY_LIST) or 'None'}\n"
        f"Time Elapsed: {elapsed:.1f}s\n"
        f"Timestamp: {datetime.now(timezone.utc).isoformat()}"
    )
    logger.info(f"\n{summary}")

    try:
        send_telegram_message(summary)
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")


def main():
    logger.info("MEW Energy Bot started.")
    while True:
        try:
            run_claim_cycle()
        except Exception as e:
            logger.error(f"Claim cycle error: {e}", exc_info=True)
            try:
                send_telegram_message(f"MEW Bot Error: {e}")
            except Exception:
                pass

        next_run = CLAIM_INTERVAL_HOURS * 3600
        logger.info(f"Next cycle in {CLAIM_INTERVAL_HOURS} hours. Sleeping...")
        time.sleep(next_run)


if __name__ == "__main__":
    main()
