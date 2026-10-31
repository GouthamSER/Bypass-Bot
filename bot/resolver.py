import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .hosts import host_label

SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)\b", re.I)
MAX_BODY = 2_000_000
USER_AGENT = "Mozilla/5.0 (compatible; MultiHostLinkInspector/1.0)"


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
        _validate_public_http_url(current)
        async with client.stream(current, follow_redirects=False) as response:
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


def _clean_label(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value[:70] or "Download"


async def inspect_url(url: str, timeout: float = 20, max_redirects: int = 8,
                      max_links: int = 12) -> Result:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    async with httpx.AsyncClient(timeout=timeout, headers=headers, limits=limits) as client:
        status, response_headers, raw, final_url = await fetch_with_redirects(client, url, max_redirects)

    content_type = response_headers.get("content-type", "").lower()
    if not 200 <= status < 300:
        raise ValueError(f"Page returned HTTP {status}.")
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        # A direct file URL is still useful as a single download link.
        name = urlparse(final_url).path.rsplit("/", 1)[-1] or "Direct download"
        return Result(url, final_url, name, _extract_size(response_headers.get("content-length", "")),
                      [(name, final_url)], "The destination is not an HTML page.")

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
            "instant dl", "cloud download", "fast cloud", "zipdisk",
            "multiup", "gofile", "mirror", "telegram generate",
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
