import asyncio
import html
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.errors import ButtonUrlInvalid
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from .hosts import host_label
    from .resolver import inspect_url, parse_html
except ImportError:  # run as a plain script (python main.py)
    from hosts import host_label
    from resolver import inspect_url, parse_html

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


BUTTON_URL_MAX = 512  # Telegram rejects long button URLs (BUTTON_URL_INVALID)


def _button_ok(url: str) -> bool:
    return len(url) <= BUTTON_URL_MAX and url.isascii() and " " not in url


def _links_as_text(links) -> str:
    return "\n".join(
        f'• <a href="{html.escape(u, quote=True)}">{html.escape(l[:60])}</a>' for l, u in links
    )


async def send_results(status, text: str, links):
    """Short links -> buttons. Long/odd links -> clickable text. Never crash on bad button URLs."""
    links = list(links)[:MAX_LINKS]
    short = [(l, u) for l, u in links if _button_ok(u)]
    long_ = [(l, u) for l, u in links if not _button_ok(u)]
    body = text + (("\n\n<b>More links:</b>\n" + _links_as_text(long_)) if long_ else "")
    try:
        await status.edit_text(body, reply_markup=build_keyboard(short),
                               disable_web_page_preview=True)
    except ButtonUrlInvalid:
        await status.edit_text(text + "\n\n<b>Links:</b>\n" + _links_as_text(links),
                               disable_web_page_preview=True)


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
            "Usage: /b https://gdflix.dev/file/FILE_ID\n"
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
                f"<b>💥 {html.escape(result.title)}</b>\n"
                f"<b>Host:</b> {html.escape(host_label(result.final_url))}\n"
                f'<b>Page:</b> <a href="{html.escape(result.final_url, quote=True)}">Open resolved page</a>'
                + (f"\n<b>Size:</b> {html.escape(result.size)}" if result.size else "")
                + (f"\n<i>{html.escape(result.note)}</i>" if result.note else "")
                + f"\n<b>Available page links:</b> {len(result.links)}"
            )
            buttons.extend(result.links)
        except Exception as exc:
            output.append(f"<b>Link:</b> {html.escape(url)}\n<b>Error:</b> {html.escape(str(exc)[:220])}")

    await send_results(status, "💥 <b>Bypass results</b>\n\n" + "\n\n".join(output), buttons)


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

    await send_results(status, "💥 <b>Link inspection results</b>\n\n" + "\n\n".join(output), buttons)


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@app.on_message(filters.private & filters.document)
async def handle_html_file(_, message):
    """Offline mode: user saves the page in their browser and sends the .html file."""
    doc = message.document
    name = (doc.file_name or "").lower()
    if not name.endswith((".html", ".htm")):
        await message.reply_text("Send a saved .html page, or a link.")
        return
    if doc.file_size and doc.file_size > 5_000_000:
        await message.reply_text("File too big (max 5 MB).")
        return
    status = await message.reply_text("🔎 Reading saved page…")
    try:
        data = await message.download(in_memory=True)
        raw = bytes(data.getbuffer())
        soup_text = raw[:200_000].decode("utf-8", "ignore")
        m = re.search(r'(?:property=["\']og:url["\']\s+content|rel=["\']canonical["\']\s+href|<base\s+href)=["\']([^"\']+)', soup_text, re.I)
        base = m.group(1) if m else "https://new8.gdflix.io/"
        result = parse_html(base, raw, base, MAX_LINKS)
        text = (
            f"<b>Title:</b> {html.escape(result.title)}\n"
            f"<b>Host:</b> {html.escape(host_label(result.final_url))}"
            + (f"\n<b>Size:</b> {html.escape(result.size)}" if result.size else "")
            + f"\n<b>Found links:</b> {len(result.links)}"
        )
        await send_results(status, "💥 <b>Saved page results</b>\n\n" + text, result.links)
    except Exception as exc:
        await status.edit_text(f"<b>Error:</b> {html.escape(str(exc)[:220])}")


def _start_health_server():
    """Tiny HTTP server so hosts that need an open port (Koyeb, Render, Docker -p) stay healthy."""
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), _Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def main():
    _start_health_server()
    app.run()


if __name__ == "__main__":
    main()
