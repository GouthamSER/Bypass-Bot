import re
from urllib.parse import urljoin
import aiohttp
from bs4 import BeautifulSoup

from .base import BaseResolver


class HubCloudResolver(BaseResolver):
    name = "HubCloud"

    DOMAINS = (
        "hubcloud.",
        "hubcloud.ist",
        "hubcloud.lol",
        "hubcloud.foo",
        "hubcloud.icu",
        "hubcloud.co",
    )

    @classmethod
    def can_handle(cls, url: str) -> bool:
        value = url.lower()
        return "hubcloud." in value

    async def resolve(self, url: str) -> dict:
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10) "
                          "AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36"
        }
        timeout = aiohttp.ClientTimeout(total=20)

        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers=headers,
            ) as session:
                async with session.get(
                    url,
                    allow_redirects=True,
                ) as r:
                    html = await r.text(errors="ignore")
                    final_page = str(r.url)

            soup = BeautifulSoup(html, "html.parser")
            title = self._title(soup)
            size = self._size(soup, html)

            # Only use a download URL if the public page itself exposes one.
            direct_url = self._public_download_url(soup, final_page)

            return {
                "success": True,
                "title": title or "HubCloud File",
                "size": size or "Unknown",
                "direct_url": direct_url,
                "source": self.name,
                "url": url,
                "note": (
                    None if direct_url else
                    "The public page did not expose a direct download URL."
                ),
            }

        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "url": url,
                "source": self.name,
            }

    @staticmethod
    def _title(soup,):
        # Prefer obvious title/file-name elements.
        for selector in (
            "h1", ".card-title", ".file-name", ".filename",
            "[class*='filename']", "[class*='file-name']",
        ):
            node = soup.select_one(selector)
            if node and node.get_text(strip=True):
                return node.get_text(" ", strip=True)

        if soup.title and soup.title.get_text(strip=True):
            return soup.title.get_text(" ", strip=True)

        return None

    @staticmethod
    def _size(soup, html):
        text = soup.get_text(" ", strip=True)

        match = re.search(
            r"(?i)\b(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)\b",
            text
        )
        if match:
            return f"{match.group(1)} {match.group(2).upper()}"

        match = re.search(
            r"(?i)(?:size|file size)\s*[:\-]?\s*"
            r"(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)",
            html
        )
        if match:
            return f"{match.group(1)} {match.group(2).upper()}"

        return None

    @staticmethod
    def _public_download_url(soup, base_url):
        candidates = []

        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            label = a.get_text(" ", strip=True).lower()

            if not href:
                continue

            absolute = urljoin(base_url, href)

            if (
                "download" in label
                or "direct" in label
                or "download" in href.lower()
            ):
                candidates.append(absolute)

        return candidates[0] if candidates else None
