import re
import asyncio

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import API_ID, API_HASH, BOT_TOKEN
from resolvers.registry import get_resolver
from utils.formatter import format_result

app = Client(
    "link_resolver_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.I)


@app.on_message(filters.command("start") & filters.private)
async def start(_, message):
    await message.reply_text(
        "💥 <b>Link Resolver Bot</b>\n\n"
        "Send one or more supported URLs and I will inspect the public page.\n\n"
        "Supported in this build:\n"
        "• Direct HTTP/HTTPS files\n"
        "• HubCloud public-page metadata\n\n"
        "Protected/anti-bot flows are not bypassed."
    )


@app.on_message(filters.private & filters.text)
async def resolve_links(_, message):
    text = message.text or ""
    urls = URL_PATTERN.findall(text)

    if not urls:
        await message.reply_text(
            "🔗 <b>No URL found.</b>\n\n"
            "Send a http:// or https:// link."
        )
        return

    status = await message.reply_text("⏳ <b>Processing...</b>")

    results = []
    buttons = []

    async def one(url):
        resolver = get_resolver(url)
        if resolver is None:
            return {
                "success": False,
                "error": "Unsupported URL",
                "url": url,
            }

        try:
            result = await resolver.resolve(url)
            return result
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "url": url,
            }

    resolved = await asyncio.gather(*(one(u) for u in urls))

    for result in resolved:
        results.append(format_result(result))

        if result.get("success") and result.get("direct_url"):
            buttons.append([
                InlineKeyboardButton(
                    "⬇️ Download",
                    url=result["direct_url"]
                )
            ])

    keyboard = InlineKeyboardMarkup(buttons) if buttons else None

    await status.edit_text(
        "\n\n──────────────\n\n".join(results),
        reply_markup=keyboard
    )


if __name__ == "__main__":
    print("Link Resolver Bot started...")
    app.run()
