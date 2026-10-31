import asyncio
import html
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from pyrogram import Client, filters

try:
    from .hosts import host_label
    from .resolver import inspect_url, parse_html
except ImportError:  # run as a plain script (python main.py)
    from hosts import host_label
    from resolver import inspect_url, parse_html

load_dotenv()
import logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s: %(message)s")

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


FRIENDLY = (  # (substring in lowercase label, shown name)
    ("telegram", "Telegram"),
    ("instant", "Instant Download"),
    ("fast download", "Fast Cloud Download"),
    ("fast cloud", "Fast Cloud Download"),
    ("zipdisk", "Fast Cloud Download"),
    ("resume", "Download"),
    ("cloud download", "Cloud Download"),
    ("gofile", "GoFile"),
    ("pixel", "PixelDrain"),
)


def friendly_name(label: str, url: str) -> str:
    low = label.lower()
    for key, name in FRIENDLY:
        if key in low:
            return name
    return label[:40] or host_label(url)


def clean_title(title: str) -> str:
    return re.sub(r"^\s*GDFlix\s*\|\s*", "", title).strip()


def format_result(result, elapsed: float) -> str:
    """Clean card: Title / Size / Download Links / Elapsed (no buttons)."""
    e = html.escape
    parts = [
        "▸ <b>Title ➙</b>\n<blockquote>" + e(clean_title(result.title)) + "</blockquote>",
    ]
    if result.size:
        parts.append(f"▸ <b>Size ➙</b> {e(result.size)}")
    links, seen = [], set()
    for label, url in result.links:
        if "login" in label.lower():  # needs an account, not a download
            continue
        name = friendly_name(label, url)
        if name in seen:
            name = f"{name} 2"
        seen.add(name)
        links.append(f'• <a href="{e(url, quote=True)}">{e(name)}</a>')
    if links:
        parts.append("▸ <b>Download Links ➙</b>\n\n" + "\n".join(links))
    if result.note:
        parts.append(f"<i>{e(result.note)}</i>")
    parts.append(f"<blockquote>⧖ <b>Elapsed ➙</b> {elapsed:.1f}s</blockquote>")
    return "\n\n".join(parts)


async def run_inspection(urls):
    """Inspect each URL -> list of formatted blocks."""
    blocks = []
    for url in urls:
        t0 = time.monotonic()
        try:
            async with semaphore:
                result = await inspect_url(url, TIMEOUT, MAX_REDIRECTS, MAX_LINKS)
            blocks.append(format_result(result, time.monotonic() - t0))
        except Exception as exc:
            blocks.append(f"<b>Link:</b> {html.escape(url)}\n<b>Error:</b> {html.escape(str(exc)[:220])}")
    return blocks


async def reply_card(status, blocks):
    text = "\n\n➖➖➖\n\n".join(blocks)
    await status.edit_text(text[:4090], disable_web_page_preview=True)


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
        "3. Tap a link in the result.\n\n"
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

    status = await message.reply_text("🔎 Resolving link…")
    await reply_card(status, await run_inspection(urls))


@app.on_message(filters.private & filters.text & ~filters.command(["start", "help", "b"]))
async def handle_links(_, message):
    urls = extract_urls(message.text)
    if not urls:
        await message.reply_text("Send a valid HTTP or HTTPS link.")
        return

    status = await message.reply_text(f"🔎 Inspecting {len(urls)} link(s)…")
    await reply_card(status, await run_inspection(urls))


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
    t0 = time.monotonic()
    try:
        data = await message.download(in_memory=True)
        raw = bytes(data.getbuffer())
        head = raw[:200_000].decode("utf-8", "ignore")
        m = re.search(r'(?:property=["\']og:url["\']\s+content|rel=["\']canonical["\']\s+href|<base\s+href)=["\']([^"\']+)', head, re.I)
        base = m.group(1) if m else "https://new8.gdflix.io/"
        result = parse_html(base, raw, base, MAX_LINKS)
        await reply_card(status, [format_result(result, time.monotonic() - t0)])
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
