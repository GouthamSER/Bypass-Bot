from urllib.parse import urlparse, unquote
import aiohttp

from .base import BaseResolver


class DirectResolver(BaseResolver):
    name = "Direct"

    @classmethod
    def can_handle(cls, url: str) -> bool:
        p = urlparse(url)
        return p.scheme.lower() in ("http", "https")

    async def resolve(self, url: str) -> dict:
        timeout = aiohttp.ClientTimeout(total=20)
        headers = {"User-Agent": "Mozilla/5.0 LinkResolverBot/1.0"}

        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers=headers,
            ) as session:
                async with session.head(
                    url,
                    allow_redirects=True,
                ) as r:
                    final_url = str(r.url)
                    content_type = (r.headers.get("Content-Type") or "").lower()

                    # A generic web page should be handled by a more
                    # specific resolver when one exists.
                    if "text/html" in content_type:
                        return {
                            "success": False,
                            "error": "This URL is a web page, not a direct file.",
                            "url": url,
                            "source": self.name,
                        }

                    filename = unquote(
                        urlparse(final_url).path.rsplit("/", 1)[-1]
                    ) or "Unknown File"

                    length = r.headers.get("Content-Length")
                    size = format_size(int(length)) if length and length.isdigit() else "Unknown"

                    if r.status >= 400:
                        return {
                            "success": False,
                            "error": f"HTTP {r.status}",
                            "url": url,
                            "source": self.name,
                        }

                    return {
                        "success": True,
                        "title": filename,
                        "size": size,
                        "direct_url": final_url,
                        "source": self.name,
                        "url": url,
                    }

        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "url": url,
                "source": self.name,
            }


def format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"
