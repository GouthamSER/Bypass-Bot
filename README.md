# Link Resolver Bot — Pyrogram

A modular Telegram bot for processing public/direct download pages.

## Included

- Pyrogram Telegram bot
- Multiple URL detection
- Direct HTTP/HTTPS link handling
- HubCloud public-page metadata extraction
- Title and file-size extraction
- Inline Download button when a public download URL is exposed
- Screenshot-style HTML response
- Async HTTP requests
- Resolver registry for adding other authorized/public resolvers

## Important

This project intentionally does NOT implement CAPTCHA bypass, anti-bot bypass,
authentication bypass, token theft, paywall bypass, or other access-control
circumvention.

For a service that requires an API, authenticated integration, or permission,
use its documented/authorized interface.

## Setup

1. Create a Telegram bot with BotFather.
2. Get `API_ID` and `API_HASH` from Telegram.
3. Copy `.env.example` to `.env`.
4. Fill in the credentials.
5. Install dependencies:

   pip install -r requirements.txt

6. Start:

   python bot.py

## Adding a resolver

Create a class in `resolvers/`, subclass `BaseResolver`, implement:
- `can_handle(url)`
- `resolve(url)`

Then add an instance to `RESOLVERS` in `resolvers/registry.py`.
