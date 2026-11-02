"""Provider adapters that use documented public endpoints or plain URL rewriting.

Each adapter returns a Result, or None when the URL is not one it understands.
Network access goes through the injected ``fetch`` coroutine, so every request
gets the same URL validation, redirect limits and body cap as the generic path.

Deliberately NOT here: CAPTCHA/ad-gate/countdown shorteners, login or paywall
workarounds, and sites whose APIs require reverse-engineered anti-bot tokens.
"""
import json
import re
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from .hosts import hostname, is_domain
from .models import Result, fmt_bytes

# Plain-redirect URL shorteners. The generic redirect follower expands them.
SHORTENER_DOMAINS = (
    "bit.ly", "tinyurl.com", "t.co", "is.gd", "v.gd", "cutt.ly", "rb.gy",
    "goo.gl", "ow.ly", "buff.ly", "t.ly", "shorturl.at", "lnkd.in",
)

_ID = r"[A-Za-z0-9_-]+"
_IA_SKIP_SUFFIX = ("_meta.xml", "_files.xml", "_meta.sqlite", "_reviews.xml", "_archive.torrent")


def is_shortener(url: str) -> bool:
    host = hostname(url)
    return any(is_domain(host, d) for d in SHORTENER_DOMAINS)


def _loads(body: bytes, what: str) -> dict:
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError as exc:
        raise ValueError(f"{what}: unexpected response (not JSON).") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{what}: unexpected response.")
    return data


# ── Pixeldrain ───────────────────────────────────────────────────────────────
async def _pixeldrain(url, fetch, max_links):
    parts = urlsplit(url)
    if not is_domain(hostname(url), "pixeldrain.com"):
        return None
    m = re.match(rf"^/(u|l|api/file|api/list)/({_ID})", parts.path)
    if not m:
        return None
    kind, ident = m.group(1), m.group(2)

    if kind in ("u", "api/file"):
        status, _, body, _ = await fetch(f"https://pixeldrain.com/api/file/{ident}/info")
        data = _loads(body, "Pixeldrain")
        if status == 404 or data.get("success") is False:
            raise ValueError(f"Pixeldrain: {data.get('message') or 'file not found'}.")
        if not 200 <= status < 300:
            raise ValueError(f"Pixeldrain API returned HTTP {status}.")
        name = str(data.get("name") or ident)
        return Result(url, url, name, fmt_bytes(data.get("size")),
                      [("Pixeldrain Download", f"https://pixeldrain.com/api/file/{ident}?download"),
                       ("Pixeldrain Page", f"https://pixeldrain.com/u/{ident}")])

    status, _, body, _ = await fetch(f"https://pixeldrain.com/api/list/{ident}")
    data = _loads(body, "Pixeldrain")
    if status == 404 or data.get("success") is False:
        raise ValueError(f"Pixeldrain: {data.get('message') or 'list not found'}.")
    if not 200 <= status < 300:
        raise ValueError(f"Pixeldrain API returned HTTP {status}.")
    files = [f for f in data.get("files") or [] if isinstance(f, dict) and f.get("id")]
    links = [(str(f.get("name") or f["id"])[:70], f"https://pixeldrain.com/api/file/{f['id']}?download")
             for f in files[:max_links]]
    total = sum(int(f.get("size") or 0) for f in files)
    note = f"{len(files)} files in list" + (f" (showing {max_links})" if len(files) > max_links else "")
    return Result(url, url, str(data.get("title") or f"Pixeldrain list {ident}"),
                  fmt_bytes(total) if total else None,
                  links or [("Open list", f"https://pixeldrain.com/l/{ident}")], note)


# ── Internet Archive ─────────────────────────────────────────────────────────
async def _archive_org(url, fetch, max_links):
    parts = urlsplit(url)
    if not is_domain(hostname(url), "archive.org"):
        return None
    m = re.match(r"^/(?:details|download)/([^/?#]+)", parts.path)
    if not m:
        return None
    ident = unquote(m.group(1))
    status, _, body, _ = await fetch(f"https://archive.org/metadata/{quote(ident, safe='')}")
    if not 200 <= status < 300:
        raise ValueError(f"Internet Archive API returned HTTP {status}.")
    data = _loads(body, "Internet Archive")
    if not data.get("files"):
        raise ValueError("Internet Archive: item not found or has no files.")
    files = [f for f in data["files"] if isinstance(f, dict) and f.get("name")]
    originals = [f for f in files if f.get("source") == "original"]
    chosen = [f for f in (originals or files)
              if not str(f["name"]).endswith(_IA_SKIP_SUFFIX) and not str(f["name"]).startswith("__ia_")]
    links = [(str(f["name"]).rsplit("/", 1)[-1][:70],
              f"https://archive.org/download/{quote(ident, safe='')}/{quote(str(f['name']))}")
             for f in chosen[:max_links]]
    total = 0
    for f in chosen:
        try:
            total += int(f.get("size") or 0)
        except (TypeError, ValueError):
            pass
    title = (data.get("metadata") or {}).get("title") or ident
    if isinstance(title, list):
        title = title[0] if title else ident
    note = f"{len(chosen)} files" + (f" (showing {max_links})" if len(chosen) > max_links else "")
    return Result(url, url, str(title)[:180], fmt_bytes(total) if total else None,
                  links or [("Open item", f"https://archive.org/details/{quote(ident, safe='')}")], note)


# ── Google Drive (URL normalisation only) ────────────────────────────────────
async def _gdrive(url, fetch, max_links):
    host = hostname(url)
    if not (is_domain(host, "drive.google.com") or host == "docs.google.com"):
        return None
    parts = urlsplit(url)
    m = re.search(rf"/file/d/({_ID})", parts.path)
    ident = m.group(1) if m else dict(parse_qsl(parts.query)).get("id")
    if not ident or not re.fullmatch(_ID, ident) or "/folders/" in parts.path:
        return None
    return Result(url, url, f"Google Drive file {ident}", None,
                  [("Google Drive Download", f"https://drive.google.com/uc?export=download&id={ident}"),
                   ("Google Drive View", f"https://drive.google.com/file/d/{ident}/view")],
                  "Normalized link only. Large files may show Google's own confirmation page; "
                  "sharing/quota limits are not bypassed.")


# ── Dropbox (dl=1 rewrite) ───────────────────────────────────────────────────
async def _dropbox(url, fetch, max_links):
    if not is_domain(hostname(url), "dropbox.com"):
        return None
    parts = urlsplit(url)
    if not re.match(r"^/(s|scl/fi|scl/fo|sh)/", parts.path):
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "dl"]
    direct = parts._replace(query=urlencode(query + [("dl", "1")]), fragment="").geturl()
    name = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1]) or "Dropbox file"
    return Result(url, url, name, None, [("Dropbox Direct Download", direct)],
                  "Folder links download as a zip." if "/scl/fo/" in parts.path else None)


_ADAPTERS = (_pixeldrain, _archive_org, _gdrive, _dropbox)


async def resolve_provider(url: str, fetch, max_links: int = 12) -> Result | None:
    """Try each adapter in turn; the first that recognises the URL answers."""
    for adapter in _ADAPTERS:
        result = await adapter(url, fetch, max_links)
        if result is not None:
            return result
    return None
