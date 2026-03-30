"""Playwright browser lifecycle manager with anti-detection measures."""
from __future__ import annotations
import asyncio
import os
import random
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)
from loguru import logger

import config


def _parse_proxy_env() -> Optional[dict]:
    """
    Read HTTP_PROXY / HTTPS_PROXY from the environment and return a dict
    suitable for Playwright's `proxy=` argument:
      {"server": "http://host:port", "username": "...", "password": "..."}
    Returns None if no proxy is configured.
    """
    raw = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    if not raw:
        return None
    parsed = urlparse(raw)
    proxy: dict = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


class BrowserManager:
    """
    Manages a single Playwright browser instance with a persistent context
    so that cookies (1688 login session) survive across page navigations.

    Usage:
        async with BrowserManager() as bm:
            page = await bm.new_page()
            ...
    """

    def __init__(
        self,
        user_data_dir: Optional[Path] = None,
        headless: bool = config.HEADLESS,
        slow_mo: int = config.SLOW_MO,
    ):
        self.user_data_dir = user_data_dir or (config.DATA_DIR / "browser_profile")
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.slow_mo = slow_mo
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------
    async def __aenter__(self) -> "BrowserManager":
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        proxy = _parse_proxy_env()
        if proxy:
            logger.debug("Using proxy: {}", proxy["server"])
        # Use a persistent context so 1688 login cookies are retained
        launch_kwargs: dict = dict(
            user_data_dir=str(self.user_data_dir),
            headless=self.headless,
            slow_mo=self.slow_mo,
            viewport={"width": 1366, "height": 768},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            ignore_https_errors=bool(proxy),   # proxy does TLS inspection; trust it
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            ignore_default_args=["--enable-automation"],
        )
        if proxy:
            launch_kwargs["proxy"] = proxy
        self._context = await self._playwright.chromium.launch_persistent_context(
            **launch_kwargs
        )
        # Patch navigator.webdriver to evade bot detection
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        logger.info("Browser started (headless={})", self.headless)

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser stopped")

    # ------------------------------------------------------------------
    # Page helpers
    # ------------------------------------------------------------------
    async def new_page(self) -> Page:
        assert self._context, "Call start() first"
        page = await self._context.new_page()
        await self._apply_stealth(page)
        return page

    async def _apply_stealth(self, page: Page) -> None:
        """Apply additional stealth patches to a page."""
        await page.add_init_script("""
            // Overwrite the `plugins` property to use a custom getter.
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });
            // Overwrite the `languages` property to use a custom getter.
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh'],
            });
        """)

    async def goto(
        self,
        page: Page,
        url: str,
        wait_until: str = "domcontentloaded",
        timeout: int = config.BROWSER_TIMEOUT,
    ) -> None:
        """Navigate to a URL with human-like random delay."""
        await asyncio.sleep(random.uniform(0.5, 1.5))
        await page.goto(url, wait_until=wait_until, timeout=timeout)

    async def human_type(self, page: Page, selector: str, text: str) -> None:
        """Type text character-by-character with random delays."""
        await page.click(selector)
        await page.fill(selector, "")
        for char in text:
            await page.type(selector, char, delay=random.randint(50, 150))

    async def screenshot(self, page: Page, name: str) -> Path:
        path = config.DATA_DIR / "screenshots" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=False)
        return path
