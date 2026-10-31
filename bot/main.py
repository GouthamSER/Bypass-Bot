import asyncio
import html
import os
import re
from urllib.parse import urlparse

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .hosts import host_label
from .resolver import inspect_url

load_dotenv()

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "5"))
TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "20"))
MAX_REDIRECTS = int(os.getenv("MAX_REDIRECTS", "8"))
MAX_LINKS = int(os.getenv("MAX_LINKS", "12"))

app = Client("multihost_link_inspector", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
semaphore = asyncio.Semaphore(MAX_CONCURRENT)
URL_RE = re.compile(r"https?://[^\s<>]+")


def extract_urls(text: str) -> list[str]:
    urls = []
    for match in URL_RE.findall(text or ""):
        url = match.rstrip(".,);]}>")
        if url not in urls:
            urls.append(url)
    return urls[:5]


def build_keyboard(links):
    rows = []
    for label, url in links[:MAX_LINKS]:
        rows.append([InlineKeyboardButton(f"⬇️ {host_label(url)} · {label[:35]}", url=url)])
    return InlineKeyboardMarkup(rows) if rows else None


@app.on_message(filters.command("start"))
async def start(_, message):
    await message.reply_text(
        "👋 **Multi-Host Link Inspector**\n\n"
        "Send a supported HTTP/HTTPS page link. I'll follow ordinary redirects "
        "and show publicly visible download links.\n\n"
        "Use /help for details.",
        disable_web_page_preview=True,
    )


@app.on_message(filters.command("help"))
async def help_cmd(_, message):
    await message.reply_text(
        "**How to use**\n"
        "1. Send one or more links (up to 5).\n"
        "2. Wait while I inspect the public page.\n"
        "3. Tap a discovered mirror button.\n\n"
        "**Supported:** ordinary HTTP redirects and links visible in HTML pages.\n"
        "**Not supported:** bypassing CAPTCHA, login, paywalls, ad gates, DRM, "
        "anti-bot controls, or other access restrictions.\n\n"
        "Use only content you own or are authorized to access and share.",
        disable_web_page_preview=True,
    )


@app.on_message(filters.private & filters.command("b"))
async def bypass_command(_, message):
    # Usage: /b <GDFlix or other supported HTTP(S) link>
    command_text = " ".join(message.command[1:]) if len(message.command) > 1 else ""
    urls = extract_urls(command_text)
    if not urls:
        await message.reply_text(
            "Usage: /b https://gdflix.dev/file/FILE_ID\\n"
            "Send a GDFlix or other supported HTTP(S) page link after /b."
        )
        return

    status = await message.reply_text("🔎 Resolving link and inspecting available page links…")
    output = []
    buttons = []

    for url in urls:
        try:
            async with semaphore:
                result = await inspect_url(url, TIMEOUT, MAX_REDIRECTS, MAX_LINKS)

            output.append(
                f"<b>💥 {html.escape(result.title)}</b>\\n"
                f"<b>Host:</b> {html.escape(host_label(result.final_url))}\\n"
                f"<b>Page:</b> <a href=\\\"{html.escape(result.final_url, quote=True)}\\\">Open resolved page</a>"
                + (f"\\n<b>Size:</b> {html.escape(result.size)}" if result.size else "")
                + (f"\\n<i>{html.escape(result.note)}</i>" if result.note else "")
                + f"\\n<b>Available page links:</b> {len(result.links)}"
            )
            buttons.extend(result.links)
        except Exception as exc:
            output.append(f"<b>Link:</b> {html.escape(url)}\\n<b>Error:</b> {html.escape(str(exc)[:220])}")

    await status.edit_text(
        "💥 <b>Bypass results</b>\\n\\n" + "\\n\\n".join(output),
        reply_markup=build_keyboard(buttons),
        disable_web_page_preview=True,
    )


@app.on_message(filters.private & filters.text & ~filters.command(["start", "help", "b"]))
async def handle_links(_, message):
    urls = extract_urls(message.text)
    if not urls:
        await message.reply_text("Send a valid HTTP or HTTPS link.")
        return

    status = await message.reply_text(f"🔎 Inspecting {len(urls)} link(s)…")
    output = []
    buttons = []

    for url in urls:
        try:
            async with semaphore:
                result = await inspect_url(url, TIMEOUT, MAX_REDIRECTS, MAX_LINKS)

            output.append(
                f"<b>Title:</b> {html.escape(result.title)}\n"
                f"<b>Host:</b> {html.escape(host_label(result.final_url))}\n"
                f'<b>Resolved:</b> <a href="{html.escape(result.final_url, quote=True)}">Open page</a>'
                + (f"\n<b>Size:</b> {html.escape(result.size)}" if result.size else "")
                + (f"\n<i>{html.escape(result.note)}</i>" if result.note else "")
                + f"\n<b>Found links:</b> {len(result.links)}"
            )
            for label, link in result.links:
                buttons.append((label, link))
        except Exception as exc:
            output.append(f"<b>Link:</b> {html.escape(url)}\n<b>Error:</b> {html.escape(str(exc)[:220])}")

    keyboard = build_keyboard(buttons)
    await status.edit_text(
        "💥 <b>Link inspection results</b>\n\n" + "\n\n".join(output),
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


def main():
    app.run()


if __name__ == "__main__":
    main()
