"""
1688.com scraper:
  1. Image-search using a product's main image URL
  2. Collect top supplier listings from search results
  3. Scrape each supplier's product detail page for SKU, price, weight, inventory, packaging
"""
from __future__ import annotations
import asyncio
import re
import json
import random
from typing import Optional
from urllib.parse import urlencode, quote_plus

from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from models.product import Product
from models.supplier import Supplier, SupplierSKU
from scraper.browser import BrowserManager
import config


# CSS selectors – 1688 changes these periodically; update as needed
SEL = {
    # Image search entry on 1688 search bar
    "img_search_icon": "span.img-search-icon, .search-bar-icon, [class*='img-search']",
    "img_url_input": "input[placeholder*='图片'], input.img-url-input, [class*='imgSearch'] input",
    "img_search_btn": "button[class*='search'], .img-search-btn, [class*='imgSearch'] button",

    # Search result cards
    "result_card": ".offer-list-row .offer-list-item, [class*='offerList'] [class*='offerItem']",
    "card_title": "[class*='title']",
    "card_price": "[class*='price']",
    "card_link": "a[href*='detail.1688.com'], a[href*='offer']",
    "card_shop": "[class*='company'], [class*='shop']",

    # Detail page
    "detail_title": "h1[class*='title'], .title-text, [data-component*='title']",
    "detail_price": "[class*='price'] [class*='num'], .price .num, [data-price]",
    "detail_price_range": "[class*='priceRange'], [class*='price-range']",
    "detail_sku_item": "[class*='skuItem'], [class*='sku-item'], .sku-list li",
    "detail_shop_name": "[class*='company-name'], .company-name a, [data-company]",
    "detail_attrs": "[class*='attributes'] tr, .attributes-list li",
    "detail_weight": "td:contains('重量'), td:contains('净重'), [class*='weight']",

    # Wangwang button on detail page
    "wangwang_btn": "[class*='wangwang'], [class*='im-btn'], a[href*='wangwang']",
}


class Ali1688Scraper:
    """
    Orchestrates 1688 image search and supplier detail scraping.
    The browser must already be logged in to 1688 for Wangwang to work.
    """

    def __init__(self, browser: BrowserManager):
        self.browser = browser

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def find_suppliers(
        self,
        product: Product,
        max_results: int = 20,
    ) -> list[Supplier]:
        """
        Use the product's main image URL to image-search on 1688,
        then return a list of Supplier objects (un-ranked, un-detailed).
        """
        if not product.main_image_url:
            logger.warning("No main image URL for {}", product.source_url)
            return []

        logger.info("1688 image search for: {}", product.main_image_url)
        suppliers = await self._image_search(product.main_image_url, max_results)
        logger.info("Found {} suppliers from image search", len(suppliers))
        return suppliers

    async def scrape_supplier_detail(self, supplier: Supplier) -> Supplier:
        """Scrape SKU / price / weight / inventory from a supplier's product detail page."""
        logger.info("Scraping detail: {}", supplier.product_url)
        page = await self.browser.new_page()
        try:
            await self.browser.goto(page, supplier.product_url)
            await page.wait_for_load_state("networkidle", timeout=25000)
            html = await page.content()

            supplier = await self._parse_detail_page(supplier, html, page)
        except Exception as e:
            logger.error("Detail scrape failed for {}: {}", supplier.product_url, e)
        finally:
            await page.close()
        return supplier

    # ------------------------------------------------------------------
    # Image search
    # ------------------------------------------------------------------
    async def _image_search(self, image_url: str, max_results: int) -> list[Supplier]:
        """Navigate to 1688, trigger image search, and collect result cards."""
        page = await self.browser.new_page()
        suppliers: list[Supplier] = []
        try:
            # Approach 1: Use the direct image search URL (most reliable)
            search_url = (
                "https://s.1688.com/youyuan/index.htm?"
                + urlencode({"imageAddress": image_url})
            )
            await self.browser.goto(page, search_url, wait_until="networkidle")
            await asyncio.sleep(random.uniform(2, 4))

            # Wait for results to load
            try:
                await page.wait_for_selector(
                    "[class*='offerItem'], [class*='offer-item'], .offer-list-item",
                    timeout=15000,
                )
            except Exception:
                logger.warning("Image search results selector timed out, trying fallback")
                # Approach 2: Manually input image URL in the search bar
                await self._manual_image_search(page, image_url)

            html = await page.content()
            suppliers = self._parse_search_results(html)

            # Paginate if needed
            page_num = 1
            while len(suppliers) < max_results and page_num < 5:
                next_btn = await page.query_selector("a.next-page, [class*='nextPage'], li.next a")
                if not next_btn:
                    break
                await next_btn.click()
                await page.wait_for_load_state("networkidle", timeout=15000)
                await asyncio.sleep(random.uniform(1, 2))
                html = await page.content()
                new = self._parse_search_results(html)
                if not new:
                    break
                suppliers.extend(new)
                page_num += 1

            await self.browser.screenshot(page, "1688_image_search_results")
        except Exception as e:
            logger.error("Image search failed: {}", e)
        finally:
            await page.close()

        return suppliers[:max_results]

    async def _manual_image_search(self, page, image_url: str) -> None:
        """Fallback: manually interact with the 1688 search bar image icon."""
        await self.browser.goto(page, "https://www.1688.com", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # Click camera icon
        for sel in ["[class*='imgSearch']", ".search-img-icon", "[title*='图片']"]:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    await asyncio.sleep(1)
                    break
            except Exception:
                continue

        # Paste image URL
        for sel in ["input[class*='imgUrl']", "input[placeholder*='图片链接']", "input[type='text']"]:
            try:
                inp = await page.query_selector(sel)
                if inp:
                    await inp.fill(image_url)
                    await asyncio.sleep(0.5)
                    break
            except Exception:
                continue

        # Click search
        for sel in ["[class*='imgSearch'] button", "button[class*='search']"]:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    await page.wait_for_load_state("networkidle", timeout=15000)
                    break
            except Exception:
                continue

    # ------------------------------------------------------------------
    # Parse search results HTML
    # ------------------------------------------------------------------
    def _parse_search_results(self, html: str) -> list[Supplier]:
        soup = BeautifulSoup(html, "lxml")
        suppliers: list[Supplier] = []

        # Try multiple card selectors
        cards = (
            soup.select("[class*='offerItem']")
            or soup.select(".offer-list-item")
            or soup.select("[class*='offer-list'] li")
        )

        for card in cards:
            try:
                supplier = Supplier()

                # Product URL
                link = card.select_one("a[href*='detail.1688.com'], a[href*='/offer/']")
                if not link:
                    continue
                href = link.get("href", "")
                supplier.product_url = "https:" + href if href.startswith("//") else href

                # Title
                title_el = card.select_one("[class*='title']")
                supplier.product_title = title_el.get_text(strip=True) if title_el else ""

                # List price
                price_el = card.select_one("[class*='price'] [class*='num'], [class*='price']")
                if price_el:
                    price_text = price_el.get_text(strip=True)
                    supplier.list_price_cny = self._parse_price(price_text)

                # Shop name
                shop_el = card.select_one("[class*='company'], [class*='shop']")
                supplier.shop_name = shop_el.get_text(strip=True) if shop_el else ""

                # Shop URL
                shop_link = card.select_one("a[href*='shop.1688.com']")
                if shop_link:
                    href = shop_link.get("href", "")
                    supplier.shop_url = "https:" + href if href.startswith("//") else href

                # MOQ
                moq_el = card.select_one("[class*='moq'], [class*='minOrder']")
                if moq_el:
                    moq_text = moq_el.get_text(strip=True)
                    m = re.search(r'(\d+)', moq_text)
                    supplier.moq = int(m.group(1)) if m else None

                suppliers.append(supplier)
            except Exception as e:
                logger.debug("Card parse error: {}", e)

        return suppliers

    # ------------------------------------------------------------------
    # Detail page parsing
    # ------------------------------------------------------------------
    async def _parse_detail_page(self, supplier: Supplier, html: str, page) -> Supplier:
        soup = BeautifulSoup(html, "lxml")

        # ── Shop name ──────────────────────────────────────────────────
        shop_el = soup.select_one(
            "[class*='company-name'] a, [class*='companyName'] a, .company-info a"
        )
        if shop_el:
            supplier.shop_name = shop_el.get_text(strip=True)
            href = shop_el.get("href", "")
            if href:
                supplier.shop_url = "https:" + href if href.startswith("//") else href

        # ── Wangwang ID ────────────────────────────────────────────────
        ww_link = soup.select_one("a[href*='wangwang'], [class*='wangwang'] a")
        if ww_link:
            href = ww_link.get("href", "")
            m = re.search(r'ww=([^&]+)', href)
            if m:
                supplier.wangwang_id = m.group(1)

        # ── Shop stability metrics ─────────────────────────────────────
        rating_el = soup.select_one("[class*='score'], [class*='rating']")
        if rating_el:
            try:
                supplier.rating = float(re.search(r'[\d.]+', rating_el.get_text()).group())
            except Exception:
                pass

        # Years operating
        year_el = soup.select_one("[class*='year'], [class*='operate']")
        if year_el:
            m = re.search(r'(\d+)', year_el.get_text())
            if m:
                supplier.years_operating = float(m.group(1))

        # Transaction level badge
        badge = soup.select_one("[class*='creditLevel'], [class*='credit-level'], [class*='transLevel']")
        if badge:
            supplier.transaction_level = badge.get_text(strip=True)

        # ── Title ──────────────────────────────────────────────────────
        title_el = soup.select_one("h1[class*='title'], .title-text, [class*='subject']")
        if title_el:
            supplier.product_title = title_el.get_text(strip=True)

        # ── Try to extract SKU data from embedded JS ───────────────────
        supplier.skus = self._extract_skus_from_js(html) or await self._extract_skus_from_dom(soup, page)

        # ── Attributes table (weight, packaging, etc.) ─────────────────
        await self._parse_attributes(supplier, soup)

        return supplier

    def _extract_skus_from_js(self, html: str) -> list[SupplierSKU]:
        """Extract SKU data from embedded JavaScript variables."""
        skus: list[SupplierSKU] = []

        # 1688 detail pages embed skuMap / offerDetail in window.__INIT_DATA__
        patterns = [
            r'window\.__INIT_DATA__\s*=\s*(\{.+?\})\s*;',
            r'offerDetail\s*:\s*(\{.+?\})\s*[,}]',
            r'skuMap\s*:\s*(\{.+?\})\s*[,}]',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if not match:
                continue
            try:
                data = json.loads(match.group(1))
                sku_map = (
                    data.get("skuMap")
                    or data.get("offerDetail", {}).get("skuMap")
                    or {}
                )
                sku_props = (
                    data.get("skuProps")
                    or data.get("offerDetail", {}).get("skuProps")
                    or []
                )
                # Build prop id -> name mapping
                prop_names: dict[str, tuple[str, str]] = {}
                for prop in sku_props:
                    for val in prop.get("values", []):
                        prop_names[str(val.get("id"))] = (
                            prop.get("name", ""),
                            val.get("name", ""),
                        )

                for sku_key, sku_val in sku_map.items():
                    sku = SupplierSKU()
                    sku.sku_id = sku_key
                    # Parse attributes from key like "1234:5678;9012:3456"
                    for pair in sku_key.split(";"):
                        if ":" in pair:
                            pid, vid = pair.split(":", 1)
                            if vid in prop_names:
                                attr_name, attr_val = prop_names[vid]
                                sku.attributes[attr_name] = attr_val

                    price_info = sku_val.get("price", {})
                    if isinstance(price_info, dict):
                        sku.price_cny = float(price_info.get("price", 0) or 0)
                    elif isinstance(price_info, (int, float)):
                        sku.price_cny = float(price_info)

                    sku.inventory = sku_val.get("quantity") or sku_val.get("stock")
                    skus.append(sku)

                if skus:
                    return skus
            except (json.JSONDecodeError, TypeError, ValueError):
                continue

        return skus

    async def _extract_skus_from_dom(self, soup: BeautifulSoup, page) -> list[SupplierSKU]:
        """Fallback: extract SKU info from DOM elements."""
        skus: list[SupplierSKU] = []
        sku_items = soup.select("[class*='skuItem'], .sku-list li, [class*='sku-prop-item']")

        for item in sku_items:
            sku = SupplierSKU()
            sku.sku_id = item.get("data-sku-id", "") or item.get("data-id", "")
            name_el = item.select_one("[class*='name'], [class*='text']")
            if name_el:
                sku.attributes["规格"] = name_el.get_text(strip=True)
            price_el = item.select_one("[class*='price']")
            if price_el:
                sku.price_cny = self._parse_price(price_el.get_text(strip=True))
            skus.append(sku)

        # If no individual SKUs found, create one from the listed price
        if not skus:
            price_el = soup.select_one(
                "[class*='price'] [class*='num'], [class*='priceText'], .price-common-text"
            )
            if price_el:
                sku = SupplierSKU()
                sku.sku_id = "default"
                sku.price_cny = self._parse_price(price_el.get_text(strip=True))
                skus.append(sku)

        return skus

    async def _parse_attributes(self, supplier: Supplier, soup: BeautifulSoup) -> None:
        """Parse the product attributes table for weight, packaging size, etc."""
        # Attribute table rows
        rows = (
            soup.select("[class*='attributes-list'] li")
            or soup.select("[class*='detailAttr'] tr")
            or soup.select("table.table-basic tr")
        )

        attr_map: dict[str, str] = {}
        for row in rows:
            cells = row.select("td, span, li > *")
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True).rstrip("：:")
                val = cells[1].get_text(strip=True)
                attr_map[key] = val
            else:
                text = row.get_text(strip=True)
                if "：" in text or ":" in text:
                    parts = re.split(r'[：:]', text, maxsplit=1)
                    if len(parts) == 2:
                        attr_map[parts[0].strip()] = parts[1].strip()

        # Apply to first/default SKU or all SKUs
        weight_keys = ["净重", "重量", "单件重量", "净重量"]
        gross_keys = ["毛重", "包装重量"]
        size_keys = ["包装尺寸", "箱规", "装箱尺寸", "外箱尺寸"]

        for sku in supplier.skus:
            for k in weight_keys:
                if k in attr_map:
                    sku.weight_net_kg = self._parse_weight_kg(attr_map[k])
                    break
            for k in gross_keys:
                if k in attr_map:
                    sku.weight_gross_kg = self._parse_weight_kg(attr_map[k])
                    break
            for k in size_keys:
                if k in attr_map:
                    sku.package_size_cm = attr_map[k]
                    break

        # Also try to find weight in the page text directly
        if not any(s.weight_net_kg for s in supplier.skus):
            weight_text = soup.find(string=re.compile(r'净重[：:]\s*[\d.]+'))
            if weight_text:
                m = re.search(r'[\d.]+', str(weight_text))
                if m and supplier.skus:
                    supplier.skus[0].weight_net_kg = float(m.group())

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_price(text: str) -> Optional[float]:
        """Extract first numeric price from text."""
        m = re.search(r'[\d,]+\.?\d*', text.replace(",", ""))
        if m:
            try:
                return float(m.group().replace(",", ""))
            except ValueError:
                pass
        return None

    @staticmethod
    def _parse_weight_kg(text: str) -> Optional[float]:
        """Parse weight string like '0.5kg', '500g', '1.2' -> float kg."""
        text = text.strip().lower()
        m = re.search(r'([\d.]+)\s*(kg|g|千克|克)?', text)
        if not m:
            return None
        val = float(m.group(1))
        unit = m.group(2) or ""
        if unit in ("g", "克"):
            val /= 1000
        return val
