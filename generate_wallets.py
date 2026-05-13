"""Generate 200 Ethereum wallets using BIP44 derivation (m/44'/60'/0'/0/0)."""

import json
import os

from eth_account import Account
from mnemonic import Mnemonic

Account.enable_unaudited_hdwallet_features()

WALLET_COUNT = 200
OUTPUT_FILE = "wallets.json"


def generate_wallets(count: int = WALLET_COUNT) -> list[dict]:
    mnemo = Mnemonic("english")
    wallets = []

    for i in range(count):
        phrase = mnemo.generate(strength=128)
        acct = Account.from_mnemonic(phrase, account_path="m/44'/60'/0'/0/0")
        wallet = {
            "index": i,
            "address": acct.address,
            "private_key": acct.key.hex(),
            "mnemonic": phrase,
        }
        wallets.append(wallet)
        if (i + 1) % 50 == 0:
            print(f"Generated {i + 1}/{count} wallets...")

    return wallets


def main():
    if os.path.exists(OUTPUT_FILE):
        print(f"{OUTPUT_FILE} already exists. Skipping generation.")
        print("Delete the file to regenerate wallets.")
        return

    print(f"Generating {WALLET_COUNT} wallets...")
    wallets = generate_wallets()

    with open(OUTPUT_FILE, "w") as f:
        json.dump(wallets, f, indent=2)

    print(f"Saved {len(wallets)} wallets to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
