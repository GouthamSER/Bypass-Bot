"""Optional "press the Generate button" step for pages that build their link on click.

Runs a real headless Chromium (Playwright) the way a visitor's browser would:
honest User-Agent, no stealth patches, no CAPTCHA solving. If the page shows an
anti-bot challenge (Cloudflare/Turnstile/reCAPTCHA/hCaptcha) it stops and says so.
Every request the browser makes is checked against the same public-address rules
as the HTTP client, so redirects/scripts cannot reach private networks.

Off by default. Enable with AUTO_CLICK=1 and `pip install playwright` +
`playwright install chromium`.
"""
import asyncio
import logging
import os
import time
from collections.abc import Callable
from urllib.parse import urlsplit

log = logging.getLogger("clicker")

ENABLED = os.getenv("AUTO_CLICK", "0") == "1"
TIMEOUT = max(10.0, min(float(os.getenv("AUTO_CLICK_TIMEOUT", "60")), 180.0))
NO_SANDBOX = os.getenv("AUTO_CLICK_NO_SANDBOX", "1") == "1"  # needed in most containers
BUTTONS = ("#cloud", "#quick")  # Generate Cloud Link first, then Generate Quick Link
_SKIP_TYPES = {"image", "font", "media"}
_LOCAL_SCHEMES = {"data", "blob", "about"}
_CHALLENGE_SELECTORS = (
    'iframe[src*="challenges.cloudflare.com"]', ".cf-turnstile", "#challenge-form",
    "#cf-challenge-running", ".g-recaptcha", ".h-captcha", 'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
)
_semaphore = asyncio.Semaphore(max(1, int(os.getenv("AUTO_CLICK_MAX", "1"))))


class ClickError(ValueError):
    """Expected, user-presentable failure (challenge, timeout, missing browser)."""


async def _challenge_present(page) -> bool:
    try:
        title = (await page.title()).lower()
        if "just a moment" in title or "attention required" in title:
            return True
        for sel in _CHALLENGE_SELECTORS:
            if await page.locator(sel).count():
                return True
    except Exception:  # page mid-navigation; check again next tick
        return False
    return False


async def click_generate(url: str, validate: Callable[[str], None],
                         is_done: Callable[[bytes, str], bool], user_agent: str,
                         timeout: float = TIMEOUT) -> tuple[bytes, str]:
    """Open ``url``, press the first available Generate button, wait until
    ``is_done(html, url)`` is true. Returns (html_bytes, final_url)."""
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise ClickError("Auto-click needs Playwright (pip install playwright && "
                         "playwright install chromium).") from exc

    verdicts: dict[str, bool] = {}

    async def guard(route):
        req = route.request
        scheme = urlsplit(req.url).scheme
        if scheme in _LOCAL_SCHEMES:
            return await route.continue_()
        if req.resource_type in _SKIP_TYPES:
            return await route.abort()
        host = urlsplit(req.url).hostname or ""
        if host not in verdicts:
            try:
                await asyncio.to_thread(validate, req.url)
                verdicts[host] = True
            except Exception:
                verdicts[host] = False
        return await (route.continue_() if verdicts[host] else route.abort())

    async with _semaphore:
        deadline = time.monotonic() + timeout
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(
                    headless=True, args=["--no-sandbox"] if NO_SANDBOX else [])
            except PlaywrightError as exc:
                raise ClickError(f"Could not start the browser: {str(exc)[:120]}") from exc
            try:
                context = await browser.new_context(user_agent=user_agent, java_script_enabled=True)
                page = await context.new_page()
                await page.route("**/*", guard)
                try:
                    await page.goto(url, wait_until="domcontentloaded",
                                    timeout=int(min(30, timeout) * 1000))
                except PlaywrightError as exc:
                    raise ClickError(f"Page did not load: {str(exc)[:120]}") from exc

                clicked = False
                while time.monotonic() < deadline:
                    if await _challenge_present(page):
                        raise ClickError("The page shows an anti-bot challenge. The bot does not "
                                         "solve or bypass it; open the page in your browser.")
                    try:
                        html = (await page.content()).encode("utf-8", "replace")
                        if is_done(html, page.url):
                            return html, page.url
                        if not clicked:
                            for selector in BUTTONS:
                                button = page.locator(selector)
                                if await button.count() and await button.first.is_enabled():
                                    await button.first.click(timeout=5000)
                                    clicked = True
                                    log.info("clicked %s on %s", selector, page.url)
                                    break
                    except PlaywrightError as exc:  # navigation in flight; retry next tick
                        log.debug("click loop retry: %s", exc)
                    await page.wait_for_timeout(1500)
                raise ClickError("Timed out waiting for the link to be generated "
                                 + ("(button was pressed)." if clicked else "(no Generate button found)."))
            finally:
                await browser.close()
