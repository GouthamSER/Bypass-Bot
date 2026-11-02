import asyncio
import ipaddress
import logging
import os
import re
import socket
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlparse, urlsplit

import httpx
from bs4 import BeautifulSoup

from .hosts import host_label, hostname, is_domain
from . import clicker
from .models import Result, fmt_bytes
from .providers import is_shortener, resolve_provider

__all__ = ["Result", "inspect_url", "parse_html", "fetch_with_redirects"]

log = logging.getLogger("resolver")
SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)\b", re.I)
MAX_BODY = 2_000_000
REDIRECT_CODES = (301, 302, 303, 307, 308)
RETRY_STATUS = (429, 502, 503, 504)
CACHE_TTL = float(os.getenv("CACHE_TTL", "600"))
CACHE_MAX = 256
DOMAIN_TTL = 3600  # how long a "working" GDFlix domain is trusted

# Honest default UA. Override with USER_AGENT env var if a host needs something else.
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (compatible; MultiHostLinkInspector/1.0)")
# GDFlix rotates domains; a file ID may only exist on one of them.
GDFLIX_DOMAINS = [d.strip() for d in os.getenv(
    "GDFLIX_DOMAINS", "new8.gdflix.io,gdflix.dev,gdflix.io,gdflix.net").split(",") if d.strip()]
GDFLIX_SUFFIXES = ("gdflix.io", "gdflix.dev", "gdflix.net")

_META_RE = re.compile(r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\'][^"\'>]*?url=([^"\'>\s]+)', re.I)
_JS_RE = re.compile(r'(?:window\.|document\.|top\.)?location(?:\.href)?\s*(?:=|\.replace\(|\.assign\()\s*["\']([^"\']+)["\']', re.I)

_working_domain: tuple[str, float] | None = None  # (domain, monotonic time)
_cache: dict[str, tuple[float, Result]] = {}


# ── GDFlix host helpers ──────────────────────────────────────────────────────
def _is_gdflix_host(host: str | None) -> bool:
    host = (host or "").rstrip(".").lower()
    configured = tuple(d.strip().rstrip(".").lower() for d in GDFLIX_DOMAINS if d.strip())
    return any(host == suffix or host.endswith("." + suffix)
               for suffix in (*GDFLIX_SUFFIXES, *configured))


def _valid_gdflix_domain(domain: str) -> bool:
    domain = domain.strip().rstrip(".").lower()
    return bool(re.fullmatch(r"[a-z0-9.-]+", domain) and _is_gdflix_host(domain))


def _is_gdflix_file(url: str) -> bool:
    p = urlparse(url)
    return _is_gdflix_host(p.hostname) and p.path.startswith("/file/")


def _is_gdflix_cloud(url: str) -> bool:
    p = urlparse(url)
    return _is_gdflix_host(p.hostname) and p.path.startswith("/cloud/")


def _get_working_domain() -> str | None:
    if _working_domain and time.monotonic() - _working_domain[1] < DOMAIN_TTL:
        return _working_domain[0]
    return None


# ── URL safety ───────────────────────────────────────────────────────────────
def _validate_public_http_url(url: str) -> None:
    """Reject unsafe schemes, credentials, nonstandard ports and non-public DNS."""
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError("The URL is invalid or too long.")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("The URL is malformed.") from exc
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Only http:// and https:// links are supported.")
    if not parsed.hostname:
        raise ValueError("The URL has no hostname.")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing embedded credentials are not accepted.")
    if port not in (None, 80, 443):
        raise ValueError("Only standard HTTP/HTTPS ports are accepted.")

    host = parsed.hostname.rstrip(".").lower()
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            records = socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80),
                                         type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError("The hostname could not be resolved.") from exc
        addresses = [ipaddress.ip_address(item[4][0].split("%")[0]) for item in records]
    if not addresses:
        raise ValueError("The hostname did not resolve to an IP address.")
    # Reject the whole hostname if any answer is unsafe (including mixed public/private DNS).
    if any(not ip.is_global for ip in addresses):
        raise ValueError("This link resolves to a non-public network address.")


# ── HTTP ─────────────────────────────────────────────────────────────────────
def _retry_after(headers: dict) -> float:
    try:
        return max(0.5, min(float(headers.get("retry-after", "1")), 3.0))
    except ValueError:
        return 1.0


async def _read_capped(response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        remaining = MAX_BODY - len(body)
        if remaining <= 0:
            break
        body.extend(chunk[:remaining])
        if len(chunk) > remaining:
            break
    return bytes(body)


async def _request(client: httpx.AsyncClient, url: str, extra_headers: dict | None = None):
    """One GET hop (no redirect following). Retries once on transient failures."""
    for attempt in range(2):
        try:
            async with client.stream("GET", url, headers=extra_headers, follow_redirects=False) as response:
                status, headers, final = response.status_code, dict(response.headers), str(response.url)
                body = b"" if status in REDIRECT_CODES else await _read_capped(response)
        except httpx.TransportError:
            if attempt:
                raise
            await asyncio.sleep(1)
            continue
        if status in RETRY_STATUS and not attempt:
            await asyncio.sleep(_retry_after(headers))
            continue
        return status, headers, body, final
    raise RuntimeError("unreachable")


async def fetch_with_redirects(client: httpx.AsyncClient, url: str, max_redirects: int,
                               extra_headers: dict | None = None):
    """Follow redirects manually so every hop is validated before it is requested."""
    current = url
    for _ in range(max_redirects + 1):
        await asyncio.to_thread(_validate_public_http_url, current)
        status, headers, body, final = await _request(client, current, extra_headers)
        if status in REDIRECT_CODES:
            location = headers.get("location")
            if not location:
                return status, headers, b"", final
            current = urljoin(final, location)
            continue
        return status, headers, body, final
    raise ValueError(f"Too many redirects (limit: {max_redirects}).")


def _client_redirect(raw: bytes, base: str) -> str | None:
    """Meta-refresh / JS redirect found in a tiny page body."""
    if len(raw) > 20_000:
        return None
    text = raw.decode("utf-8", "ignore")
    m = _META_RE.search(text) or _JS_RE.search(text)
    if not m:
        return None
    target = urljoin(base, m.group(1).strip())
    return target if target != base else None


async def _follow_client_redirects(client, state, max_redirects, hops: int = 3):
    status, headers, raw, final_url = state
    for _ in range(hops):
        nxt = _client_redirect(raw, final_url)
        if not nxt:
            break
        status, headers, raw, final_url = await fetch_with_redirects(client, nxt, max_redirects)
    return status, headers, raw, final_url


# ── GDFlix domain rotation ───────────────────────────────────────────────────
async def _resolve_gdflix(client, url, max_redirects, first):
    """GDFlix rotates domains. Follow JS/meta redirects, ask the old domain's
    root where it now lives, then try known domains. Remembers the winner."""
    global _working_domain
    state = await _follow_client_redirects(client, first, max_redirects)
    status, headers, raw, final_url = state
    if 200 <= status < 300 or not _is_gdflix_file(url):
        return state

    parsed = urlparse(url)
    old = (parsed.hostname or "").lower()
    candidates: list[str] = []
    working = _get_working_domain()
    if working and _valid_gdflix_domain(working):
        candidates.append(working)
    # where does the old domain's root redirect now?
    try:
        _, _, root_raw, root_final = await fetch_with_redirects(client, f"{parsed.scheme}://{old}/", max_redirects)
        host = urlparse(root_final).hostname
        if not host or host == old:
            nxt = _client_redirect(root_raw, root_final)
            host = urlparse(nxt).hostname if nxt else None
        if host and _valid_gdflix_domain(host):
            candidates.append(host.lower())
    except Exception as exc:
        log.debug("gdflix root lookup failed for %s: %r", old, exc)
    candidates += [d.lower() for d in GDFLIX_DOMAINS if _valid_gdflix_domain(d)]

    tried = {old}
    for domain in candidates:
        if domain in tried:
            continue
        tried.add(domain)
        try:
            first_try = await fetch_with_redirects(client, parsed._replace(netloc=domain).geturl(), max_redirects)
            st, hd, bd, fu = await _follow_client_redirects(client, first_try, max_redirects)
        except Exception as exc:
            log.debug("gdflix candidate %s failed: %r", domain, exc)
            continue
        if 200 <= st < 300:
            _working_domain = (urlparse(fu).hostname or domain, time.monotonic())
            return st, hd, bd, fu
    return state


async def _expand_one(client, page_url: str, url: str, max_redirects: int, max_links: int):
    """Open one GDFlix /cloud/ page; return its external links, or [] if none."""
    st, _, raw, final = await fetch_with_redirects(client, url, max_redirects, {"Referer": page_url})
    if not 200 <= st < 300:
        raise ValueError(f"cloud page HTTP {st} at {final}")
    sub = parse_html(url, raw, final, max_links)
    own = (urlparse(final).hostname or "").lower()
    real = [(l, u) for l, u in sub.links
            if (urlparse(u).hostname or "").lower() != own and "login" not in l.lower()]
    if not real:
        log.warning("cloud page %s: no external link. anchors seen: %s", final,
                    [(l, u[:60]) for l, u in sub.links])
    return real


async def expand_cloud_links(client, result: Result, max_redirects, max_links) -> Result:
    """GDFlix 'FAST CLOUD / ZIPDISK' goes to a /cloud/ page that holds the real
    download anchor. Open it (an ordinary public page) and swap in its links."""
    # Real cloud URLs look like /cloud/<unix-time>/<file-id> and are generated per click,
    # so they cannot be guessed from the file URL. Only links actually on the page are followed.
    targets = [(l, u) for l, u in result.links if _is_gdflix_cloud(u)]
    if not targets:
        return result

    async def run(item):
        try:
            return await _expand_one(client, result.final_url, item[1], max_redirects, max_links)
        except Exception as exc:
            log.warning("cloud expand failed for %s: %r", item[1], exc)
            return []

    expanded = dict(zip(targets, await asyncio.gather(*(run(t) for t in targets))))
    out: list[tuple[str, str]] = []
    for item in result.links:
        if item not in expanded or not expanded[item]:  # keep original when expansion found nothing
            out.append(item)
        else:
            out.extend((item[0], u) for _, u in expanded[item])

    seen, uniq = set(), []
    for l, u in out:
        if u not in seen:
            seen.add(u)
            uniq.append((l, u))
    result.links = uniq[:max_links]
    return result


# ── Orchestration ────────────────────────────────────────────────────────────
def _cache_get(url: str) -> Result | None:
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    _cache.pop(url, None)
    return None


def _cache_put(url: str, result: Result) -> None:
    if CACHE_TTL <= 0:
        return
    if len(_cache) >= CACHE_MAX:
        _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
    _cache[url] = (time.monotonic(), result)


async def _inspect(client, url, max_redirects, max_links) -> Result:
    async def fetch(u):
        return await fetch_with_redirects(client, u, max_redirects)

    await asyncio.to_thread(_validate_public_http_url, url)
    try:
        provider_result = await resolve_provider(url, fetch, max_links)
    except ValueError:
        raise
    except Exception as exc:  # provider hiccup: fall back to the generic path
        log.warning("provider adapter failed for %s: %r", url, exc)
        provider_result = None
    if provider_result is not None:
        return provider_result

    state = await fetch_with_redirects(client, url, max_redirects)
    status, response_headers, raw, final_url = state
    if not 200 <= status < 300 or _client_redirect(raw, final_url):
        state = await _resolve_gdflix(client, url, max_redirects, state)
        status, response_headers, raw, final_url = state

    content_type = response_headers.get("content-type", "").lower()
    if not 200 <= status < 300:
        hint = ""
        body = raw[:4000].decode("utf-8", "ignore").lower()
        if status in (403, 429, 503) and ("cloudflare" in response_headers.get("server", "").lower()
                                         or "cf-mitigated" in response_headers
                                         or "just a moment" in body
                                         or "cf-ray" in response_headers):
            hint = " (Cloudflare bot check blocked this server's request; the bot does not bypass it)"
        raise ValueError(f"Page returned HTTP {status} at {final_url}{hint}")

    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        # A direct file URL is still useful as a single download link.
        name = urlparse(final_url).path.rsplit("/", 1)[-1] or "Direct download"
        result = Result(url, final_url, name, fmt_bytes(response_headers.get("content-length")),
                        [(name, final_url)], "The destination is not an HTML page.")
    else:
        result = parse_html(url, raw, final_url, max_links)
        result = await expand_cloud_links(client, result, max_redirects, max_links)

    if result.needs_click and clicker.ENABLED:
        result = await _auto_click(result, url, max_links)

    if is_shortener(url) and final_url != url:
        extra = f"Expanded from {urlparse(url).hostname}."
        result.note = f"{result.note} {extra}" if result.note else extra
    return result


async def _auto_click(result: Result, url: str, max_links: int) -> Result:
    """Press GDFlix's Generate button in a headless browser and read the generated links."""
    def done(html: bytes, page_url: str) -> bool:
        page = parse_html(url, html, page_url, max_links)
        return bool(_external_links(page.links))

    try:
        html, final = await clicker.click_generate(
            result.final_url, _validate_public_http_url, done, USER_AGENT)
    except clicker.ClickError as exc:
        log.warning("auto-click failed for %s: %s", result.final_url, exc)
        result.note = f"{result.note} Auto-click failed: {exc}"
        return result
    clicked = parse_html(url, html, final, max_links)
    merged, seen = [], set()
    for label, link in [*clicked.links, *_external_links(result.links)]:
        if link not in seen and label != "Open resolved page":
            seen.add(link)
            merged.append((label, link))
    result.links = merged[:max_links] or result.links
    result.final_url = final
    result.note = "Generated by pressing the page's button (session link, may expire soon)."
    result.needs_click = False
    return result


async def inspect_url(url: str, timeout: float = 20, max_redirects: int = 8,
                      max_links: int = 12) -> Result:
    cached = _cache_get(url)
    if cached is not None:
        return cached
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/json"}
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    async with httpx.AsyncClient(timeout=timeout, headers=headers, limits=limits, http2=True) as client:
        result = await _inspect(client, url, max_redirects, max_links)
    _cache_put(url, result)
    return result


# ── HTML parsing ─────────────────────────────────────────────────────────────
def _extract_size(text: str) -> str | None:
    match = SIZE_RE.search(text)
    return f"{match.group(1)} {match.group(2).upper()}" if match else None


def _clean_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:70]


def _countdown_gate(soup) -> bool:
    """A "wait for the countdown, then Continue" interstitial (monetized link gate)."""
    return soup.select_one("a#cta") is not None and (
        soup.select_one("#timer, .timer") is not None or "countdown" in soup.get_text(" ").lower())


def _expiry_note(links) -> str | None:
    """Signed links often carry exp=<unix time>; tell the user when they stop working."""
    for _, link in links:
        try:
            exp = int(parse_qs(urlsplit(link).query).get("exp", [""])[0])
            when = datetime.fromtimestamp(exp, timezone.utc)
        except (ValueError, OverflowError, OSError):
            continue
        if 2020 <= when.year <= 2100:
            return f"Link expires {when:%Y-%m-%d %H:%M} UTC."
    return None


def parse_html(url: str, raw: bytes, final_url: str, max_links: int = 12,
               offline: bool = False) -> Result:
    """Extract title/size/links from a page. ``offline`` means the user saved the page
    themselves (after doing any waiting in their own browser)."""
    max_links = max(1, min(int(max_links), 50))
    soup = BeautifulSoup(raw, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else "Untitled page"
    if not offline and _countdown_gate(soup):
        # Do not read past a countdown/ad gate on a live fetch: the visitor must wait it out.
        return Result(url, final_url, title[:180], None, [("Open page", final_url)],
                      "This is a countdown/ad gate page and the bot does not skip it. Open it in your "
                      "browser, let it finish, then send the final page URL (or the saved finished page).")
    # Only the top of the page: file sizes live there, footers hold unrelated "5 MB" noise.
    size = _extract_size(soup.get_text(" ", strip=True)[:4000])

    found: list[tuple[str, str]] = []
    seen = {final_url}
    # Many download pages render their primary action as a button with a data URL
    # rather than an anchor. Read only explicit URL-bearing attributes; never run JS.
    anchors = list(soup.select("a[href]"))
    for element in soup.select("[data-url], [data-href], [data-link], [formaction]"):
        target = (element.get("data-url") or element.get("data-href") or
                  element.get("data-link") or element.get("formaction"))
        if target:
            label = element.get_text(" ", strip=True) or element.get("aria-label") or "Download"
            synthetic = soup.new_tag("a", href=target)
            synthetic.string = str(label)
            anchors.append(synthetic)

    # MessyCloud file pages: the server buttons live in .download-grid, the heading is the filename.
    if is_domain(hostname(final_url), "messycloud.ink"):
        grid = soup.select(".download-grid a[href]")
        if grid:
            anchors = grid
        heading = soup.select_one("h1")
        if heading and heading.get_text(strip=True):
            title = heading.get_text(" ", strip=True)

    # GDFlix pages expose their mirror actions as ordinary anchors. Prioritize
    # those actions so the header/footer navigation does not crowd them out.
    if _is_gdflix_host(urlparse(final_url).hostname):
        preferred_terms = (
            "instant dl", "cloud download", "fast cloud", "zipdisk", "zip-disk",
            "multiup", "gofile", "mirror", "telegram generate", "pixel",
            "drivebot", "download",
        )
        text_of = {id(a): a.get_text(" ", strip=True).lower() for a in anchors}
        preferred = [a for a in anchors if any(t in text_of[id(a)] for t in preferred_terms)]
        chosen = {id(a) for a in preferred}
        # Keep login actions visible but clearly distinguish them from mirrors.
        anchors = preferred + [a for a in anchors if "login" in text_of[id(a)] and id(a) not in chosen]

    for anchor in anchors:
        href = urljoin(final_url, anchor.get("href", "").strip())
        try:
            parsed = urlsplit(href)
        except ValueError:
            continue
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            continue
        if href in seen:
            continue
        seen.add(href)
        label = _clean_label(anchor.get_text(" ", strip=True)) or host_label(href)
        found.append((label, href))
        if len(found) >= max_links:
            break

    # If the page contains no visible anchors, keep the resolved page as a usable result.
    if not found:
        found.append(("Open resolved page", final_url))

    note, needs_click = _gdflix_state(soup, final_url, found)
    expiry = _expiry_note(found)
    if expiry:
        note = f"{note} {expiry}" if note else expiry
    return Result(url, final_url, title[:180], size, found, note, needs_click)


def _external_links(found) -> list[tuple[str, str]]:
    """Links that leave GDFlix (real mirrors), ignoring login/telegram/placeholder entries."""
    return [(label, u) for label, u in found
            if label != "Open resolved page" and "login" not in label.lower()
            and not _is_gdflix_host(urlparse(u).hostname) and urlparse(u).hostname != "t.me"]


def _gdflix_state(soup, final_url: str, found) -> tuple[str | None, bool]:
    """Explain GDFlix pages that hold no download link yet. Returns (note, needs_click)."""
    if not _is_gdflix_host(urlparse(final_url).hostname) or _external_links(found):
        return None, False
    bar = soup.select_one("#progress-bar, .progress-bar")
    if bar is not None:
        try:
            pct = f" ({float(bar.get('aria-valuenow')):.0f}%)"
        except (TypeError, ValueError):
            pct = ""
        return f"GDFlix is still processing this file{pct}. No link exists yet; retry later.", False
    buttons = " ".join(b.get_text(" ", strip=True).lower() for b in soup.select("button, input[type=button]"))
    if "generate" in buttons:
        return ("Links on this page are created by a button click (browser script/anti-bot check). "
                "The bot cannot press it; open the page in your browser."), True
    return None, False
    external = [u for label, u in found
                if label != "Open resolved page" and "login" not in label.lower()
                and not _is_gdflix_host(urlparse(u).hostname) and urlparse(u).hostname != "t.me"]
    if external:
        return None  # a real external mirror is present, nothing to explain
    bar = soup.select_one("#progress-bar, .progress-bar")
    if bar is not None:
        try:
            pct = f" ({float(bar.get('aria-valuenow')):.0f}%)"
        except (TypeError, ValueError):
            pct = ""
        return f"GDFlix is still processing this file{pct}. No link exists yet; retry later."
    buttons = " ".join(b.get_text(" ", strip=True).lower() for b in soup.select("button, input[type=button]"))
    if "generate" in buttons:
        return ("Links on this page are created by a button click (browser script/anti-bot check). "
                "The bot cannot press it; open the page in your browser.")
    return None
