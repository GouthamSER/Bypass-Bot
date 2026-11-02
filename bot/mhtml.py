"""Read pages saved as MHTML (Chrome/Edge "Save as... Webpage, Single File")."""
import email
from email import policy


def extract_html(raw: bytes) -> tuple[bytes, str | None]:
    """Return (html_bytes, original_page_url). Plain HTML passes through unchanged."""
    head = raw[:4096].lower()
    if b"multipart/related" not in head or b"mime-version" not in head:
        return raw, None
    try:
        msg = email.message_from_bytes(raw, policy=policy.default)
    except Exception:
        return raw, None
    base = (msg.get("Snapshot-Content-Location") or "").strip() or None
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)  # honours quoted-printable / base64
            if payload:
                return payload, base or (part.get("Content-Location") or "").strip() or None
    return raw, base
