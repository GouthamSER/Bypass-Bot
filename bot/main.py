import asyncio
import html
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.enums import MessageEntityType, ParseMode

from .hosts import host_label
from .mhtml import extract_html
from .resolver import inspect_url, parse_html

load_dotenv()
import logging
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
MAX_CONCURRENT = max(1, min(int(os.getenv("MAX_CONCURRENT", "5")), 20))
TIMEOUT = max(3.0, min(float(os.getenv("REQUEST_TIMEOUT", "20")), 90.0))
MAX_REDIRECTS = max(0, min(int(os.getenv("MAX_REDIRECTS", "8")), 15))
MAX_LINKS = max(1, min(int(os.getenv("MAX_LINKS", "12")), 50))
MAX_MESSAGE_LENGTH = 3900
# Optional access control: comma-separated Telegram user IDs. Empty = everyone.
ALLOWED_USERS = {int(x) for x in os.getenv("ALLOWED_USERS", "").replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}
COOLDOWN = max(0.0, float(os.getenv("USER_COOLDOWN", "3")))
_last_request: dict[int, float] = {}

# Keep the Pyrogram SQLite session on writable ephemeral storage (also works in read-only Docker).
app = Client("/tmp/multihost_link_inspector", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
semaphore = asyncio.Semaphore(MAX_CONCURRENT)
URL_RE = re.compile(r"https?://[^\s<>]+")


def extract_urls(text: str, extra: list[str] | None = None) -> list[str]:
    """URLs from plain text plus any extra (e.g. hidden text-link entities), max 5."""
    urls: list[str] = []
    candidates = [*URL_RE.findall(text or ""), *(extra or [])]
    for match in candidates:
        url = match.rstrip(".,);]}>")
        if url.lower().startswith(("http://", "https://")) and url not in urls:
            urls.append(url)
    return urls[:5]


def entity_urls(message) -> list[str]:
    """Hidden-text links (text_link entities) and URL entities, incl. in captions."""
    out = []
    for ent in [*(message.entities or []), *(message.caption_entities or [])]:
        if ent.type == MessageEntityType.TEXT_LINK and ent.url:
            out.append(ent.url)
    return out


def message_urls(message, command_text: str | None = None) -> list[str]:
    text = command_text if command_text is not None else (message.text or message.caption or "")
    urls = extract_urls(text, entity_urls(message))
    reply = getattr(message, "reply_to_message", None)
    if not urls and reply is not None:  # "/b" as a reply to a message holding the link
        urls = extract_urls(reply.text or reply.caption or "", entity_urls(reply))
    return urls


def allowed(message) -> bool:
    """Allow-list + per-user cooldown. Returns False when the request should be dropped."""
    user = message.from_user.id if message.from_user else 0
    if ALLOWED_USERS and user not in ALLOWED_USERS:
        return False
    now = time.monotonic()
    if now - _last_request.get(user, -1e9) < COOLDOWN:
        return False
    if len(_last_request) > 5000:
        _last_request.clear()
    _last_request[user] = now
    return True


FRIENDLY = (  # (substring in lowercase label, shown name)
    ("telegram", "Telegram"),
    ("instant", "Instant Download"),
    ("fast download", "Fast Cloud Download"),
    ("fast cloud", "Fast Cloud Download"),
    ("zipdisk", "Fast Cloud Download"),
    ("cloud resume", "Cloud Resume Download"),
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
    """Build HTML from individually closed lines so splitting never breaks tags."""
    e = html.escape
    lines = [f"▸ <b>Title ➙</b> {e(clean_title(result.title))}"]
    if result.size:
        lines.append(f"▸ <b>Size ➙</b> {e(result.size)}")
    links, seen = [], set()
    for label, url in result.links:
        if "login" in label.lower():
            continue
        name = friendly_name(label, url)
        if name in seen:
            name = f"{name} 2"
        seen.add(name)
        links.append(f'• <a href="{e(url, quote=True)}">{e(name)}</a>')
    if links:
        lines.extend(["▸ <b>Download Links ➙</b>", *links])
    if result.note:
        lines.append(f"<i>{e(result.note)}</i>")
    lines.append(f"⧖ <b>Elapsed ➙</b> {elapsed:.1f}s")
    return "\n".join(lines)


def split_html_lines(blocks: list[str], limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Pack complete, closed HTML lines into Telegram-safe messages.

    Blocks (one per submitted URL) are separated by a blank line. Splitting only
    ever happens between lines, so an <a> tag is never cut in half.
    """
    lines: list[str] = []
    for i, block in enumerate(blocks):
        if i:
            lines.append("")
        lines.extend(block.splitlines())

    chunks, current = [], ""
    for line in lines:
        if len(line) > limit:
            # Single unusually long line: drop its markup rather than emit broken HTML.
            line = html.escape(re.sub(r"<[^>]+>", "", line)[: limit - 40]) + " …"
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return chunks or ["No results."]


async def run_inspection(urls):
    """Inspect submitted URLs concurrently, bounded by the shared semaphore."""
    async def inspect_one(url):
        started = time.monotonic()
        try:
            async with semaphore:
                result = await inspect_url(url, TIMEOUT, MAX_REDIRECTS, MAX_LINKS)
            return format_result(result, time.monotonic() - started)
        except ValueError as exc:  # expected user-facing failures (bad/blocked link, HTTP error)
            log.warning("Inspection rejected for %s: %s", url, exc)
            return (f"<b>Link:</b> {html.escape(url)}\n"
                    f"<b>Error:</b> {html.escape(str(exc)[:220])}")
        except Exception as exc:
            log.exception("Inspection failed for %s", url)
            return (f"<b>Link:</b> {html.escape(url)}\n"
                    f"<b>Error:</b> {html.escape(str(exc)[:220])}")
    return await asyncio.gather(*(inspect_one(url) for url in urls))


async def reply_card(status, blocks):
    chunks = split_html_lines(blocks)
    await status.edit_text(chunks[0], parse_mode=ParseMode.HTML,
                           disable_web_page_preview=True)
    for chunk in chunks[1:]:
        await status.reply_text(chunk, parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True)


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


@app.on_message(filters.command("b"))
async def bypass_command(_, message):
    # Usage: /b <link>  (works in private chats and groups; also as a reply to a link)
    if not allowed(message):
        return
    command_text = " ".join(message.command[1:]) if len(message.command) > 1 else ""
    urls = message_urls(message, command_text) if command_text else message_urls(message, "")
    if not urls:
        await message.reply_text(
            "Usage: /b https://gdflix.dev/file/FILE_ID\n"
            "Send a link after /b, or reply to a message that contains one."
        )
        return

    status = await message.reply_text("🔎 Resolving link…")
    await reply_card(status, await run_inspection(urls))


@app.on_message(filters.private & filters.text & ~filters.command(["start", "help", "b"]))
async def handle_links(_, message):
    if not allowed(message):
        return
    urls = message_urls(message)
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
    if not allowed(message):
        return
    doc = message.document
    name = (doc.file_name or "").lower()
    if not name.endswith((".html", ".htm", ".mhtml", ".mht")):
        await message.reply_text("Send a saved .html/.mhtml page, or a link.")
        return
    if doc.file_size and doc.file_size > 5_000_000:
        await message.reply_text("File too big (max 5 MB).")
        return
    status = await message.reply_text("🔎 Reading saved page…")
    t0 = time.monotonic()
    try:
        data = await message.download(in_memory=True)
        raw, saved_url = extract_html(bytes(data.getbuffer()))
        head = raw[:200_000].decode("utf-8", "ignore")
        m = re.search(r'(?:property=["\']og:url["\']\s+content|rel=["\']canonical["\']\s+href|<base\s+href)=["\']([^"\']+)', head, re.I)
        base = saved_url or (m.group(1) if m else "https://new8.gdflix.io/")
        result = parse_html(base, raw, base, MAX_LINKS, offline=True)
        await reply_card(status, [format_result(result, time.monotonic() - t0)])
    except Exception as exc:
        await status.edit_text(f"<b>Error:</b> {html.escape(str(exc)[:220])}")


def _start_health_server():
    """Tiny HTTP server so hosts that need an open port (Koyeb, Render, Docker -p) stay healthy."""
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), _Health)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()


def main():
    _start_health_server()
    app.run()


if __name__ == "__main__":
    main()
