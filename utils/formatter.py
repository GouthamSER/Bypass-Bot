from html import escape


def format_result(result: dict) -> str:
    if not result.get("success"):
        return (
            "💥 <b>Link Resolver Bot</b>\n\n"
            "❌ <b>Unable to resolve</b>\n"
            f"➤ <code>{escape(str(result.get('url', '')))}</code>\n"
            f"➤ <b>Error:</b> {escape(str(result.get('error', 'Unknown error')))}"
        )

    title = escape(str(result.get("title") or "Unknown"))
    size = escape(str(result.get("size") or "Unknown"))
    source = escape(str(result.get("source") or "Unknown"))

    text = (
        "💥 <b>Link Resolver Bot</b>\n\n"
        f"▸ <b>Source ➜</b> {source}\n\n"
        f"▸ <b>Title ➜</b>\n"
        f"<code>{title}</code>\n\n"
        f"▸ <b>Size ➜</b> <code>{size}</code>\n\n"
        "▸ <b>Download Links ➜</b>"
    )

    if result.get("note"):
        text += (
            "\n\n⚠️ <i>"
            + escape(str(result["note"]))
            + "</i>"
        )

    if result.get("direct_url"):
        text += "\n• Download button below"

    return text
