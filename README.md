# MEW Energy Bot v2.0

Telegram-controlled bot that automates daily energy claims for Ethereum wallets on MyEtherWallet.

## Features

- **Telegram Commands** — Control everything via Telegram bot
- **Wallet Generation** — Generate wallets on demand via `/genr`
- **Daily Energy Claims** — Claim energy for all wallets via `/claim`
- **Proxy Support** — Rotating proxy to bypass IP restrictions
- **Action Claims** — Auto-claim Twitter/X share tasks (+5 energy each)
- **Auth Signing** — Ethereum wallet signing for `/energy/auth`

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Start bot & show help |
| `/genr [N]` | Generate N wallets (default 200) |
| `/claim` | Start claim cycle for all wallets |
| `/balance` | Check balance of all wallets |
| `/status` | Show bot status |
| `/wallets` | Show wallet count & summary |
| `/export` | Export wallet details as file |
| `/delete` | Delete all wallets |
| `/proxy` | Show proxy status |
| `/help` | Show help |

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Environment Variables

```bash
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"
```

### 3. Configure Proxy (Optional)

Create `proxies.txt` with one proxy per line:
```
http://user:pass@host:port
socks5://user:pass@host:port
```

### 4. Run

```bash
python tg_bot.py
```

### Docker

```bash
docker build -t mew-bot .
docker run -d \
  -e TELEGRAM_BOT_TOKEN="your_token" \
  -e TELEGRAM_CHAT_ID="your_chat_id" \
  mew-bot
```

## File Structure

```
├── tg_bot.py          # Telegram bot (main entry point)
├── bot.py             # Core claim engine (proxy, auth, actions)
├── generate_wallets.py # BIP44 wallet generator
├── telegram_notify.py  # Legacy notification helper
├── proxies.txt        # Proxy configuration (git-ignored)
├── wallets.json       # Generated wallets (git-ignored)
├── uuids/             # Device UUIDs per wallet
├── logs/              # Claim cycle logs
├── Dockerfile         # Docker deployment
└── requirements.txt   # Python dependencies
```

## How It Works

1. `/genr 200` → Generates 200 HD wallets (BIP44 m/44'/60'/0'/0/0)
2. `/claim` → For each wallet:
   - Register profile on MEW (`PATCH /v2/profile`)
   - Attempt auth signing (`POST /energy/auth`)
   - Check balance (`GET /energy/balance`)
   - Claim daily energy (`GET /energy/claim`) → +15-20 energy
   - Claim bonus actions (`GET /energy/claim?action_id=2,3`) → +5 each
3. Progress updates every 50 wallets via Telegram
4. Summary sent on completion
