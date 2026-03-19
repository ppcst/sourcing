"""
Wangwang (旺旺) inquiry automation via Playwright.

Flow:
1. On the supplier's 1688 product detail page, click the Wangwang chat button.
2. A Wangwang chat popup / new window opens.
3. Send an inquiry message templated from config.WANGWANG_INQUIRY_TEMPLATE.
4. Wait up to config.WANGWANG_WAIT_REPLY_SEC seconds for a reply.
5. Parse the reply to extract SKU prices, net/gross weight, package size, MOQ.
"""
from __future__ import annotations
import asyncio
import re
import json
from datetime import datetime
from typing import Optional

from loguru import logger
from playwright.async_api import Page

from models.supplier import Supplier, SupplierSKU, WangwangData
from scraper.browser import BrowserManager
import config


# Regex patterns to parse Wangwang replies (supports Chinese text)
_PRICE_RE = re.compile(
    r'(?:单价|价格|报价|¥|￥|元/?件|元/?个)[\s：:]*'
    r'([\d.]+)',
    re.IGNORECASE,
)
_NET_WEIGHT_RE = re.compile(
    r'净重[\s：:]*([\d.]+)\s*(kg|g|克|千克)?',
    re.IGNORECASE,
)
_GROSS_WEIGHT_RE = re.compile(
    r'毛重[\s：:]*([\d.]+)\s*(kg|g|克|千克)?',
    re.IGNORECASE,
)
_SIZE_RE = re.compile(
    r'(?:包装尺寸|外箱|尺寸)[\s：:]*'
    r'([\d.]+\s*[×xX*]\s*[\d.]+\s*[×xX*]\s*[\d.]+\s*(?:cm|CM|厘米)?)',
)
_MOQ_RE = re.compile(
    r'(?:最小起订|MOQ|起订量)[\s：:]*(\d+)',
    re.IGNORECASE,
)


class WangwangClient:
    """Automates sending Wangwang inquiries and capturing replies."""

    def __init__(self, browser: BrowserManager):
        self.browser = browser

    async def inquire(
        self,
        supplier: Supplier,
        quantity: int = 100,
    ) -> WangwangData:
        """
        Open Wangwang chat with the supplier and send the inquiry.
        Returns a WangwangData object with parsed results.
        """
        ww_data = WangwangData()

        if not supplier.product_url:
            logger.warning("No product URL for supplier {}", supplier.shop_name)
            return ww_data

        page = await self.browser.new_page()
        try:
            # Navigate to the product page first
            await self.browser.goto(page, supplier.product_url)
            await page.wait_for_load_state("networkidle", timeout=20000)

            # Click the Wangwang / 联系卖家 button
            chat_opened = await self._open_chat(page)
            if not chat_opened:
                logger.warning("Could not open Wangwang chat for {}", supplier.shop_name)
                return ww_data

            # Build the inquiry message
            message = config.WANGWANG_INQUIRY_TEMPLATE.format(
                quantity=quantity,
                product_url=supplier.product_url,
            )

            # Send the message
            sent = await self._send_message(page, message)
            ww_data.inquiry_sent = sent
            ww_data.inquiry_time = datetime.now()

            if not sent:
                return ww_data

            # Wait for and capture reply
            reply = await self._wait_for_reply(page)
            if reply:
                ww_data.reply_received = True
                ww_data.reply_time = datetime.now()
                ww_data.raw_reply = reply
                ww_data.skus = self._parse_reply(reply, supplier)
                ww_data.moq = self._extract_moq(reply)
                ww_data.can_negotiate = any(
                    kw in reply for kw in ["可以", "能谈", "议价", "商量", "优惠"]
                )
                ww_data.has_actual_photos = any(
                    kw in reply for kw in ["实物图", "样品图", "可以发", "图片"]
                )

            # Screenshot the chat window
            screenshot_path = await self.browser.screenshot(
                page, f"wangwang_{supplier.shop_name}_{datetime.now().strftime('%H%M%S')}"
            )
            ww_data.screenshot_path = str(screenshot_path)

        except Exception as e:
            logger.error("Wangwang inquiry error for {}: {}", supplier.shop_name, e)
        finally:
            await page.close()

        return ww_data

    # ------------------------------------------------------------------
    # Open chat
    # ------------------------------------------------------------------
    async def _open_chat(self, page: Page) -> bool:
        """Click the Wangwang / contact seller button on the product detail page."""
        selectors = [
            # Text-based
            "text=联系卖家",
            "text=立即咨询",
            "text=旺旺",
            # Class-based
            "[class*='wangwang']",
            "[class*='im-chat']",
            "[class*='contact-btn']",
            "a[href*='wangwang']",
            "a[href*='amos://']",
            "[class*='chat'] button",
        ]

        for sel in selectors:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    # Some shops open a new tab/popup
                    async with page.expect_popup(timeout=5000) as popup_info:
                        await btn.click()
                    popup = await popup_info.value
                    await popup.wait_for_load_state("domcontentloaded", timeout=10000)
                    # Switch focus to the popup (Wangwang chat window)
                    page._ww_popup = popup
                    return True
            except Exception:
                # No popup – Wangwang might be embedded in page
                try:
                    btn = await page.query_selector(sel)
                    if btn:
                        await btn.click()
                        await asyncio.sleep(2)
                        return True
                except Exception:
                    continue

        return False

    # ------------------------------------------------------------------
    # Send message
    # ------------------------------------------------------------------
    async def _send_message(self, page: Page, message: str) -> bool:
        """Type and send a message in the chat input."""
        # Use popup if opened, otherwise current page
        target = getattr(page, "_ww_popup", page)

        input_selectors = [
            "[class*='chat-input'] textarea",
            "[class*='message-input'] textarea",
            ".im-msg-send textarea",
            "textarea[placeholder*='消息']",
            "[contenteditable='true']",
        ]
        send_selectors = [
            "button[class*='send']",
            "button:has-text('发送')",
            "[class*='send-btn']",
        ]

        for inp_sel in input_selectors:
            try:
                inp = await target.query_selector(inp_sel)
                if inp:
                    await inp.click()
                    await inp.fill(message)
                    await asyncio.sleep(0.5)

                    # Press Enter or click Send button
                    for send_sel in send_selectors:
                        send_btn = await target.query_selector(send_sel)
                        if send_btn:
                            await send_btn.click()
                            logger.info("Wangwang message sent via button")
                            return True
                    # Fallback: Ctrl+Enter
                    await inp.press("Control+Enter")
                    logger.info("Wangwang message sent via keyboard")
                    return True
            except Exception:
                continue

        return False

    # ------------------------------------------------------------------
    # Wait for reply
    # ------------------------------------------------------------------
    async def _wait_for_reply(self, page: Page) -> Optional[str]:
        """Poll for a reply message, up to WANGWANG_WAIT_REPLY_SEC seconds."""
        target = getattr(page, "_ww_popup", page)
        deadline = asyncio.get_event_loop().time() + config.WANGWANG_WAIT_REPLY_SEC

        msg_selectors = [
            "[class*='msg-item']:last-child",
            "[class*='message-item']:last-child",
            ".im-msg-list li:last-child",
        ]

        last_msg = ""
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(5)
            for sel in msg_selectors:
                try:
                    el = await target.query_selector(sel)
                    if el:
                        text = await el.inner_text()
                        if text and text != last_msg:
                            last_msg = text
                            # Check if it looks like a seller reply (not our own message)
                            if not any(kw in text for kw in ["我已发送", "消息已发送"]):
                                return text
                except Exception:
                    continue

        logger.info("Wangwang reply wait timed out")
        return None

    # ------------------------------------------------------------------
    # Reply parsing
    # ------------------------------------------------------------------
    def _parse_reply(self, reply: str, supplier: Supplier) -> list[SupplierSKU]:
        """Parse structured data from a Wangwang reply."""
        # If there are existing SKUs, try to match reply data to them
        if supplier.skus:
            skus = []
            for src_sku in supplier.skus:
                sku = SupplierSKU(
                    sku_id=src_sku.sku_id,
                    attributes=src_sku.attributes.copy(),
                    price_cny=src_sku.price_cny,
                    inventory=src_sku.inventory,
                )
                # Try to find price for this SKU in reply
                # Look for attribute value followed by price
                for attr_val in src_sku.attributes.values():
                    pattern = re.compile(
                        rf'{re.escape(attr_val)}[^¥￥\d]*([¥￥]?\s*[\d.]+)',
                        re.IGNORECASE,
                    )
                    m = pattern.search(reply)
                    if m:
                        try:
                            sku.ww_price_cny = float(m.group(1).replace("¥", "").replace("￥", "").strip())
                        except ValueError:
                            pass

                # Fallback: use the first price found
                if sku.ww_price_cny is None:
                    m = _PRICE_RE.search(reply)
                    if m:
                        try:
                            sku.ww_price_cny = float(m.group(1))
                        except ValueError:
                            pass

                # Weight
                net_m = _NET_WEIGHT_RE.search(reply)
                if net_m:
                    sku.ww_weight_net_kg = self._to_kg(net_m.group(1), net_m.group(2))

                gross_m = _GROSS_WEIGHT_RE.search(reply)
                if gross_m:
                    sku.ww_weight_gross_kg = self._to_kg(gross_m.group(1), gross_m.group(2))

                # Package size
                size_m = _SIZE_RE.search(reply)
                if size_m:
                    sku.ww_package_size_cm = size_m.group(1).strip()

                skus.append(sku)
            return skus

        # No existing SKUs – create one generic record
        sku = SupplierSKU(sku_id="ww_default")
        m = _PRICE_RE.search(reply)
        if m:
            try:
                sku.ww_price_cny = float(m.group(1))
            except ValueError:
                pass

        net_m = _NET_WEIGHT_RE.search(reply)
        if net_m:
            sku.ww_weight_net_kg = self._to_kg(net_m.group(1), net_m.group(2))

        gross_m = _GROSS_WEIGHT_RE.search(reply)
        if gross_m:
            sku.ww_weight_gross_kg = self._to_kg(gross_m.group(1), gross_m.group(2))

        size_m = _SIZE_RE.search(reply)
        if size_m:
            sku.ww_package_size_cm = size_m.group(1).strip()

        return [sku]

    def _extract_moq(self, reply: str) -> Optional[int]:
        m = _MOQ_RE.search(reply)
        return int(m.group(1)) if m else None

    @staticmethod
    def _to_kg(value_str: str, unit: Optional[str]) -> float:
        val = float(value_str)
        if unit and unit.lower() in ("g", "克"):
            val /= 1000
        return val
