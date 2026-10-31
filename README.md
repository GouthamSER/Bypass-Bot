# Multi-host Telegram Link Inspector (Pyrogram)

A modular Telegram bot that follows ordinary HTTP redirects and extracts publicly
visible download/mirror links from pages you are authorized to access. It does
**not** defeat paywalls, CAPTCHA, login requirements, ad gates, DRM, anti-bot
controls, or provider access restrictions, and it does not download copyrighted
content for users.

## Features
- Pyrogram bot with `/start` and `/help`
- Accepts one or more HTTP(S) URLs in a message
- Follows a limited number of normal HTTP redirects
- Extracts visible links from the final HTML page
- Labels common hosts (GoFile, Pixeldrain, Google Drive, MediaFire, etc.)
- Prioritizes GDFlix mirror actions (Instant DL, Cloud Download, Fast Cloud/ZipDisk, Multiup/GoFile, Telegram Generate)
- Sends URL buttons for discovered links
- Modular host registry in `bot/hosts.py`
- Basic SSRF protections: only HTTP/HTTPS on ports 80/443; blocks localhost,
  private, link-local, multicast, reserved and loopback IP addresses
- Concurrent processing limit and configurable timeouts

## Setup

1. Create a Telegram bot with @BotFather.
2. Get `API_ID` and `API_HASH` from https://my.telegram.org.
3. Copy `.env.example` to `.env` and fill in credentials.
4. Install and run:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python -m bot
   ```

On Windows PowerShell, activate with `.venv\\Scripts\\Activate.ps1`.

## Docker

```bash
cp .env.example .env
# Edit .env
docker compose up -d --build
docker compose logs -f
```

## Adding a host label

Edit `HOST_LABELS` in `bot/hosts.py`. Add a lowercase domain fragment and a
friendly label. The bot only identifies and presents links; provider-specific
API integrations should use that provider's documented public API and terms.

## Notes
- Some sites render links with JavaScript; this lightweight parser cannot see
  those links. Add an official API adapter when a host documents one.
- File size is shown only when the source page exposes it in ordinary HTML.
- Never put your bot token or API hash in source control.
- Run this only for files you own or are authorized to distribute.
