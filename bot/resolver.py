import asyncio
import os
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

try:
    from .hosts import host_label
except ImportError:
    from hosts import host_label

SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)\b", re.I)
MAX_BODY = 2_000_000
# Honest default UA. Override with USER_AGENT env var if a host needs something else.
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (compatible; MultiHostLinkInspector/1.0)")
# GDFlix rotates domains; a file ID may only exist on one of them.
GDFLIX_DOMAINS = [d.strip() for d in os.getenv(
    "GDFLIX_DOMAINS", "new8.gdflix.io,gdflix.dev,gdflix.io,gdflix.net").split(",") if d.strip()]


@dataclass
class Result:
    submitted_url: str
    final_url: str
    title: str
    size: str | None
    links: list[tuple[str, str]]
    note: str | None = None


def _validate_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http:// and https:// links are supported.")
    if not parsed.hostname:
        raise ValueError("The URL has no hostname.")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing embedded credentials are not accepted.")
    if parsed.port not in (None, 80, 443):
        raise ValueError("Only standard HTTP/HTTPS ports are accepted.")

    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError("The hostname could not be resolved.") from exc

    for item in addresses:
        ip = ipaddress.ip_address(item[4][0].split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            raise ValueError("This link resolves to a non-public network address.")


async def fetch_with_redirects(client: httpx.AsyncClient, url: str, max_redirects: int):
    current = url
    for _ in range(max_redirects + 1):
        await asyncio.get_running_loop().run_in_executor(None, _validate_public_http_url, current)
        async with client.stream("GET", current, follow_redirects=False) as response:
            status = response.status_code
            headers = dict(response.headers)
            final_url = str(response.url)
            if status in (301, 302, 303, 307, 308):
                location = headers.get("location")
                if not location:
                    return status, headers, b"", final_url
                current = urljoin(final_url, location)
                continue
            body = bytearray()
            async for chunk in response.aiter_bytes():
                remaining = MAX_BODY - len(body)
                if remaining <= 0:
                    break
                body.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    break
            return status, headers, bytes(body), final_url
    raise ValueError(f"Too many redirects (limit: {max_redirects}).")


def _extract_size(text: str) -> str | None:
    match = SIZE_RE.search(text)
    return f"{match.group(1)} {match.group(2).upper()}" if match else None


def _fmt_bytes(value: str | None) -> str | None:
    if not value or not value.isdigit():
        return None
    n = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


def _clean_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:70]


_META_RE = re.compile(r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\'][^"\'>]*?url=([^"\'>\s]+)', re.I)
_JS_RE = re.compile(r'(?:window\.|document\.|top\.)?location(?:\.href)?\s*(?:=|\.replace\(|\.assign\()\s*["\']([^"\']+)["\']', re.I)
_working_domain: str | None = None  # last domain that served a file page


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


def _is_gdflix_file(url: str) -> bool:
    p = urlparse(url)
    return "gdflix" in (p.hostname or "").lower() and p.path.startswith("/file/")


async def _resolve_gdflix(client, url, max_redirects, first):
    """GDFlix rotates domains. Follow JS/meta redirects, ask the old domain's
    root where it now lives, then try known domains. Remembers the winner."""
    global _working_domain
    status, headers, raw, final_url = first

    # client-side redirect (meta refresh / window.location)
    for _ in range(3):
        nxt = _client_redirect(raw, final_url)
        if not nxt:
            break
        status, headers, raw, final_url = await fetch_with_redirects(client, nxt, max_redirects)
    if 200 <= status < 300:
        return status, headers, raw, final_url

    if not _is_gdflix_file(url):
        return status, headers, raw, final_url

    parsed = urlparse(url)
    old = (parsed.hostname or "").lower()
    candidates: list[str] = []
    if _working_domain:
        candidates.append(_working_domain)
    # where does the old domain's root redirect now?
    try:
        _, _, root_raw, root_final = await fetch_with_redirects(client, f"{parsed.scheme}://{old}/", max_redirects)
        host = urlparse(root_final).hostname
        if not host or host == old:
            nxt = _client_redirect(root_raw, root_final)
            host = urlparse(nxt).hostname if nxt else None
        if host:
            candidates.append(host.lower())
    except Exception:
        pass
    candidates += GDFLIX_DOMAINS

    tried = {old}
    for domain in candidates:
        if domain in tried:
            continue
        tried.add(domain)
        try:
            st, hd, bd, fu = await fetch_with_redirects(
                client, parsed._replace(netloc=domain).geturl(), max_redirects)
            for _ in range(3):
                nxt = _client_redirect(bd, fu)
                if not nxt:
                    break
                st, hd, bd, fu = await fetch_with_redirects(client, nxt, max_redirects)
        except Exception:
            continue
        if 200 <= st < 300:
            _working_domain = urlparse(fu).hostname
            return st, hd, bd, fu
    return status, headers, raw, final_url


async def expand_cloud_links(result: Result, timeout, max_redirects, max_links, headers, limits) -> Result:
    """GDFlix 'FAST CLOUD / ZIPDISK' goes to a /cloud/ page that holds the real
    download anchor. Open it (an ordinary public page) and swap in its links."""
    targets = [(l, u) for l, u in result.links if _is_gdflix_cloud(u)]
    if not targets:
        return result
    out: list[tuple[str, str]] = []
    async with httpx.AsyncClient(timeout=timeout, headers=headers, limits=limits, http2=True) as client:
        for label, url in result.links:
            if (label, url) not in targets:
                out.append((label, url))
                continue
            try:
                st, hd, raw, final = await fetch_with_redirects(client, url, max_redirects)
                if not 200 <= st < 300:
                    raise ValueError(st)
                sub = parse_html(url, raw, final, max_links)
                own = (urlparse(final).hostname or "").lower()
                real = [(l, u) for l, u in sub.links
                        if (urlparse(u).hostname or "").lower() != own and "login" not in l.lower()]
                out.extend([(label, u) for _, u in real] or [(label, url)])
            except Exception:
                out.append((label, url))
    seen, uniq = set(), []
    for l, u in out:
        if u not in seen:
            seen.add(u)
            uniq.append((l, u))
    result.links = uniq[:max_links]
    return result


def _is_gdflix_cloud(url: str) -> bool:
    p = urlparse(url)
    return "gdflix" in (p.hostname or "").lower() and p.path.startswith("/cloud/")


async def inspect_url(url: str, timeout: float = 20, max_redirects: int = 8,
                      max_links: int = 12) -> Result:
    headers = {"User-Agent": USER_AGENT,
               "Accept": "text/html,application/xhtml+xml"}
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    async with httpx.AsyncClient(timeout=timeout, headers=headers, limits=limits, http2=True) as client:
        status, response_headers, raw, final_url = await fetch_with_redirects(client, url, max_redirects)
        if not 200 <= status < 300 or _client_redirect(raw, final_url):
            status, response_headers, raw, final_url = await _resolve_gdflix(
                client, url, max_redirects, (status, response_headers, raw, final_url))

    content_type = response_headers.get("content-type", "").lower()
    if not 200 <= status < 300:
        hint = ""
        low = {k.lower(): v for k, v in response_headers.items()}
        body = raw[:4000].decode("utf-8", "ignore").lower()
        if status in (403, 429, 503) and ("cloudflare" in low.get("server", "").lower()
                                         or "cf-mitigated" in low or "just a moment" in body
                                         or "cf-ray" in low):
            hint = " (Cloudflare bot check blocked this server's request; the bot does not bypass it)"
        raise ValueError(f"Page returned HTTP {status} at {final_url}{hint}")
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        # A direct file URL is still useful as a single download link.
        name = urlparse(final_url).path.rsplit("/", 1)[-1] or "Direct download"
        return Result(url, final_url, name, _fmt_bytes(response_headers.get("content-length")),
                      [(name, final_url)], "The destination is not an HTML page.")

    result = parse_html(url, raw, final_url, max_links)
    return await expand_cloud_links(result, timeout, max_redirects, max_links, headers, limits)


def parse_html(url: str, raw: bytes, final_url: str, max_links: int = 12) -> Result:
    soup = BeautifulSoup(raw, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else "Untitled page"
    page_text = soup.get_text(" ", strip=True)
    size = _extract_size(page_text)

    found: list[tuple[str, str]] = []
    seen = {final_url}
    anchors = soup.select("a[href]")

    # GDFlix pages expose their mirror actions as ordinary anchors. Prioritize
    # those actions so the header/footer navigation does not crowd them out.
    is_gdflix = any(token in (urlparse(final_url).hostname or "").lower()
                    for token in ("gdflix",))
    if is_gdflix:
        preferred_terms = (
            "instant dl", "cloud download", "fast cloud", "zipdisk", "zip-disk",
            "multiup", "gofile", "mirror", "telegram generate", "pixel",
            "drivebot", "download",
        )
        preferred = [
            a for a in anchors
            if any(term in a.get_text(" ", strip=True).lower()
                   for term in preferred_terms)
        ]
        # Keep login actions visible but clearly distinguish them from mirrors.
        login_actions = [
            a for a in anchors
            if "login" in a.get_text(" ", strip=True).lower()
        ]
        anchors = preferred + [a for a in login_actions if a not in preferred]

    for anchor in anchors:
        href = urljoin(final_url, anchor.get("href", "").strip())
        parsed = urlparse(href)
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

    return Result(url, final_url, title[:180], size, found)
