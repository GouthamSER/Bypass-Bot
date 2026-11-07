# Multi-host Telegram Link Inspector

A lightweight Pyrogram bot that follows ordinary HTTP redirects and extracts publicly visible links from HTML pages you are authorized to inspect. It does not bypass CAPTCHA, login requirements, paywalls, DRM, ad gates, or anti-bot controls, and it does not download files.

## Provider adapters (`bot/providers.py`)

Documented public endpoints or plain URL rewriting only:

- **Pixeldrain** `/u/ID`, `/l/ID`: name/size via the public file/list info API, direct download link.
- **Internet Archive** `/details/ID`: file list from the metadata API.
- **Google Drive**: `/file/d/ID`, `open?id=`, `uc?id=` normalized to one download link (quota/confirm pages are not bypassed).
- **Dropbox**: share links rewritten to `dl=1`.
- **MediaFire**: direct download extraction from public file pages.
- **GitHub**: releases metadata API for release assets and blob URLs normalized to `raw.githubusercontent.com`.
- **GitLab**: blob URLs rewritten to `/-/raw/` direct download.
- **Bitbucket**: file view URLs rewritten to `/raw/` direct download.
- **BuzzHeavier**: direct download URL normalizer.
- **FuckingFast**: direct download `/dl/` link normalizer.
- **Catbox / Litterbox**: direct public file links (`files.catbox.moe`, `litterbox.catbox.moe`).
- **Hugging Face**: model/dataset file blob URLs rewritten to `/resolve/` direct download.
- **SourceForge**: project file links normalized to direct download mirrors.
- **OneDrive**: public share links rewritten with `download=1`.
- **Shorteners** (bit.ly, tinyurl, t.co, is.gd, spoo.me, clck.ru, git.io, ...): plain redirects expanded and noted.

Not implemented on purpose: ad-gate/countdown shorteners, CAPTCHA, login/paywall workarounds, and GoFile (its API needs a reverse-engineered anti-bot token).

## Auto-click (optional)

Some GDFlix pages build their link only after a "Generate Cloud/Quick Link" click. With `AUTO_CLICK=1` the bot opens the page in headless Chromium (Playwright), presses the button like a visitor would, waits (up to `AUTO_CLICK_TIMEOUT`) for the generated link, and merges it with the mirror links already on the page. It uses an honest User-Agent, applies the same public-address checks to every browser request, and **stops with a message if an anti-bot challenge (Cloudflare/Turnstile/reCAPTCHA/hCaptcha) appears** - it never solves or evades one. Generated links are session-bound and can expire. Docker: `docker build --build-arg WITH_BROWSER=1 .` (the compose file's read-only root needs a larger `/tmp` tmpfs, e.g. 256m without `noexec`, for Chromium).

## What's improved

- Results are cached (`CACHE_TTL`), transient 429/5xx/transport errors retry once, cloud pages expand in parallel.
- `/b` works in groups and as a reply; hidden text-link entities are read.
- Optional `ALLOWED_USERS` allow-list and per-user `USER_COOLDOWN`.

- GDFlix detection uses exact domain/subdomain boundaries rather than substring matching.
- GDFlix domain rotation candidates are restricted to recognized/configured GDFlix domains.
- GDFlix `/file/` pages prioritize mirror actions; `/cloud/` pages are expanded to publicly visible external links.
- HTML extraction also recognizes explicit `data-url`, `data-href`, `data-link`, and `formaction` attributes without executing JavaScript.
- Generic HTML and redirect inspection works for any reachable public HTTP(S) host.
- Host labels cover common services including GoFile, Pixeldrain, Google Drive, MediaFire, MEGA, TeraBox, Send.cm, KrakenFiles, BuzzHeavier, Rapidgator, NitroFlare, Uptobox, MultiUp, Dropbox, OneDrive, and others. Labels are identification only, not provider-specific integrations.
- Multiple submitted URLs are inspected concurrently under a global semaphore.
- Telegram HTML output is escaped and split into complete message chunks rather than blindly truncated.
- Request limits, timeouts, redirect limits, response-body caps, and URL validation are enforced.
- Docker uses a separate wheel-building stage and runs as a non-root user.

## Important support distinction

The resolver can inspect ordinary redirects and HTML links on arbitrary supported public websites. A host label only identifies the destination service; it does not mean the bot has a special integration for that service. JavaScript-only links, authenticated content, CAPTCHA, anti-bot challenges, and provider-specific APIs are not handled. Use an official public API adapter when a provider documents one.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather).
2. Obtain `API_ID` and `API_HASH` from [my.telegram.org](https://my.telegram.org).
3. Copy `.env.example` to `.env` and fill in the credentials.
4. Install and run:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python -m bot
   ```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

## Commands

- `/start` — introduction
- `/help` — usage and limitations
- `/b https://example.com/file` — inspect a link

You can also send one or more HTTP(S) URLs in a private chat. Up to five URLs are accepted per message.

## Configuration

| Variable | Default | Meaning |
|---|---:|---|
| `API_ID` | required | Telegram API ID |
| `API_HASH` | required | Telegram API hash |
| `BOT_TOKEN` | required | Bot token |
| `MAX_CONCURRENT` | `5` | Global concurrent inspection limit (clamped to 1–20) |
| `REQUEST_TIMEOUT` | `20` | HTTP timeout in seconds (clamped to 3–90) |
| `MAX_REDIRECTS` | `8` | Maximum HTTP redirects (clamped to 0–15) |
| `MAX_LINKS` | `12` | Maximum extracted links per page (clamped to 1–50) |
| `PORT` | `8080` | Health server port |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `USER_AGENT` | built-in | Optional HTTP User-Agent override |
| `GDFLIX_DOMAINS` | built-in list | Comma-separated additional/known GDFlix hostnames |
| `ALLOWED_USERS` | empty (everyone) | Comma-separated Telegram user IDs allowed to use the bot |
| `USER_COOLDOWN` | `3` | Seconds between requests per user |
| `CACHE_TTL` | `600` | Seconds to cache results (`0` disables) |

Never commit `.env` or share your bot token/API hash.

## Cloud Deployment

The bot includes built-in HTTP health check support (binding to `$PORT` or `8080`) so cloud platforms can verify the application is active and healthy.

### Deploying to Koyeb
1. Fork or push this repository to GitHub.
2. In the Koyeb dashboard, click **Create App** and connect your GitHub repository.
3. Koyeb will detect either the `Dockerfile` or use the provided `koyeb.yaml`.
4. Set the following environment variables:
   - `API_ID`
   - `API_HASH`
   - `BOT_TOKEN`
5. Koyeb routes health checks to `/health` on port `8080` (or your configured `PORT`).

### Deploying to Heroku
1. Install the Heroku CLI and login: `heroku login`.
2. Create an app: `heroku create your-app-name`.
3. Set the required config vars:
   ```bash
   heroku config:set API_ID=123456 API_HASH=your_hash BOT_TOKEN=your_token
   ```
4. Deploy:
   ```bash
   git push heroku main
   ```
5. Heroku reads `Procfile` (`web: python run.py` or `worker: python run.py`) and `runtime.txt`. You can also deploy via the Heroku dashboard using `app.json`.

### Deploying to Render
1. In Render, select **New Blueprint Instance** and connect this repository (Render reads `render.yaml`).
2. Alternatively, create a **Web Service** or **Background Worker**:
   - **Environment**: `Python`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python run.py`
   - **Health Check Path**: `/health`
3. Add environment variables: `API_ID`, `API_HASH`, and `BOT_TOKEN`.

## Running Tests

Run the built-in test suite:

```bash
python -m unittest discover tests
```

## Docker

```bash
cp .env.example .env
# Edit .env and add Telegram credentials
docker compose up -d --build
docker compose logs -f
```

The container runs as an unprivileged user, drops Linux capabilities, uses a read-only root filesystem, stores the Pyrogram session in writable temporary storage, and exposes a simple health endpoint on port 8080. For manual Docker usage:

```bash
docker build -t multihost-link-inspector .
docker run -d --name link-inspector --restart unless-stopped \
  --env-file .env -p 8080:8080 multihost-link-inspector
```

## Security notes

- Only HTTP/HTTPS URLs on standard ports are accepted.
- DNS answers are checked and any non-global address causes rejection; each redirect is validated again.
- Response bodies are capped at 2 MB and redirect depth is limited.
- This DNS validation is defense-in-depth, not a complete defense against DNS rebinding between validation and the HTTP client's connection. For hostile multi-tenant deployments, run outbound requests behind an egress firewall/proxy that blocks private, loopback, link-local, metadata-service, and reserved networks at connection time.
- Extracted external links are displayed, not fetched automatically (except the GDFlix cloud-page expansion flow).

## Adding a provider adapter

Keep provider-specific logic in a dedicated module under `bot/hosts/` (or a future `bot/providers/` package). Use documented public endpoints or publicly visible page markup, apply the same URL validation and response-size limits, and add fixture-based tests. Do not implement CAPTCHA, login, paywall, DRM, or anti-bot circumvention.
