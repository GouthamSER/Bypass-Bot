# Multi-host Telegram Link Inspector (Pyrogram)

A modular Telegram bot that follows ordinary HTTP redirects and extracts publicly
visible download/mirror links from pages you are authorized to access. It does
**not** defeat paywalls, CAPTCHA, login requirements, ad gates, DRM, anti-bot
controls, or provider access restrictions, and it does not download copyrighted
content for users.

## Features

- Pyrogram bot with `/start`, `/help`, and `/b <link>`
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

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather).
2. Get `API_ID` and `API_HASH` from [my.telegram.org](https://my.telegram.org).
3. Copy `.env.example` to `.env` and fill in your credentials.
4. Install and run:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python -m bot
   ```

On Windows PowerShell, activate with `.venv\\Scripts\\Activate.ps1`.

## Docker: build and run manually

Run these commands from the project root (the directory containing `Dockerfile`,
`requirements.txt`, and the `bot/` folder).

1. Create and edit the environment file:

   ```bash
   cp .env.example .env
   nano .env
   ```

   Add your Telegram `API_ID`, `API_HASH`, and `BOT_TOKEN` values. Keep `.env`
   private; do not upload it to Git or share it.

2. Build the image:

   ```bash
   docker build -t gdflix-bot .
   ```

3. Remove an older container with the same name, if one exists:

   ```bash
   docker rm -f gdflix-bot 2>/dev/null || true
   ```

4. Create and start the container:

   ```bash
   docker run -d \\
     --name gdflix-bot \\
     --restart unless-stopped \\
     --env-file .env \\
     gdflix-bot:latest
   ```

5. Check that it is running and view its logs:

   ```bash
   docker ps
   docker logs -f gdflix-bot
   ```

   Press `Ctrl+C` to stop following the logs; this does not stop the container.

### Docker management commands

```bash
# Stop the bot
docker stop gdflix-bot

# Start it again
docker start gdflix-bot

# Restart it
docker restart gdflix-bot

# View the latest 100 log lines
docker logs --tail 100 gdflix-bot

# Follow logs continuously
docker logs -f gdflix-bot

# Stop and remove the container (keeps the image and project files)
docker rm -f gdflix-bot
```

### Rebuild after changing code

```bash
docker rm -f gdflix-bot 2>/dev/null || true
docker build -t gdflix-bot .
docker run -d \\
  --name gdflix-bot \\
  --restart unless-stopped \\
  --env-file .env \\
  gdflix-bot:latest
```

The bot uses Telegram polling, so you normally do not need to publish a host port
with `-p`. The Docker image must have a suitable `CMD`, such as
`CMD ["python", "-m", "bot"]`, and the project must include `bot/__main__.py`.

## Docker Compose

If you prefer Docker Compose, create `.env` as described above, then run:

```bash
docker compose up -d --build
docker compose logs -f
```

To stop the Compose service:

```bash
docker compose down
```

Use either the manual `docker run` method or Docker Compose for the same bot
instance; do not start both at the same time unless you intentionally want two
instances.

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
