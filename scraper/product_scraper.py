"""
Scrape source product pages from Shopify, AliExpress, Amazon, and generic stores.
Extracts: title, main image URL, SKU attributes, key features.
"""
from __future__ import annotations
import re
import json
import asyncio
from urllib.parse import urlparse
from typing import Optional

from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_fixed

from models.product import Product, SKUVariant, ProductFeature
from scraper.browser import BrowserManager
import config


def detect_platform(url: str) -> str:
    """Detect the e-commerce platform from a URL."""
    hostname = urlparse(url).hostname or ""
    if "aliexpress.com" in hostname:
        return "aliexpress"
    if "amazon.com" in hostname or "amazon.co" in hostname:
        return "amazon"
    # Shopify stores often have myshopify.com or a custom domain
    # We flag as shopify if the page has Shopify JSON-LD
    return "shopify"   # fall through – generic Shopify scraper handles most


class ProductScraper:
    """Scrape source product listings from various platforms."""

    def __init__(self, browser: BrowserManager):
        self.browser = browser

    async def scrape(self, url: str) -> Product:
        platform = detect_platform(url)
        logger.info("Scraping {} product: {}", platform, url)
        page = await self.browser.new_page()
        try:
            await self.browser.goto(page, url)
            await page.wait_for_load_state("networkidle", timeout=20000)
            html = await page.content()

            if platform == "aliexpress":
                return await self._scrape_aliexpress(url, html, page)
            else:
                return await self._scrape_shopify_generic(url, html, page)
        finally:
            await page.close()

    # ------------------------------------------------------------------
    # AliExpress
    # ------------------------------------------------------------------
    async def _scrape_aliexpress(self, url: str, html: str, page) -> Product:
        product = Product(source_url=url, platform="aliexpress")

        # AliExpress embeds product data as window.runParams JSON
        match = re.search(r'window\.runParams\s*=\s*(\{.+?\});\s*var', html, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                detail = data.get("data", {}).get("productInfoComponent", {})
                sku_info = data.get("data", {}).get("skuComponent", {})

                product.title = detail.get("subject", "")
                product.category = data.get("data", {}).get("crossLinkComponent", {}).get("breadCrumbPathList", [{}])[-1].get("name", "")

                # Main image
                imgs = data.get("data", {}).get("imageComponent", {}).get("imagePathList", [])
                if imgs:
                    product.main_image_url = "https:" + imgs[0] if imgs[0].startswith("//") else imgs[0]
                    product.additional_image_urls = [
                        "https:" + i if i.startswith("//") else i for i in imgs[1:]
                    ]

                # SKUs
                sku_props = sku_info.get("productSKUPropertyList", [])
                sku_price_list = sku_info.get("skuPriceList", [])
                for item in sku_price_list:
                    sku = SKUVariant()
                    sku.sku_id = str(item.get("skuId", ""))
                    price_info = item.get("skuVal", {})
                    sku.price = float(price_info.get("skuAmount", {}).get("value", 0) or 0)
                    sku.currency = price_info.get("skuAmount", {}).get("currency", "USD")
                    sku.inventory = item.get("skuVal", {}).get("availQuantity")

                    # Attribute names
                    prop_path = item.get("skuPropIds", "").split(",")
                    for prop in sku_props:
                        for val in prop.get("skuPropertyValues", []):
                            if str(val.get("propertyValueId")) in prop_path:
                                sku.attributes[prop.get("skuPropertyName", "")] = val.get("propertyValueDisplayName", "")
                                if val.get("skuPropertyImagePath"):
                                    path = val["skuPropertyImagePath"]
                                    sku.image_url = "https:" + path if path.startswith("//") else path

                    product.skus.append(sku)

                # Features from props
                props = data.get("data", {}).get("specsProps", {}).get("props", [])
                for p in props:
                    product.features.append(ProductFeature(
                        key=p.get("attrName", ""),
                        value=p.get("attrValue", ""),
                    ))
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("AliExpress JSON parse error: {}", e)

        # Fallback: scrape visible elements
        if not product.title:
            product.title = await page.title()
        if not product.main_image_url:
            product.main_image_url = await self._fallback_main_image(page)

        return product

    # ------------------------------------------------------------------
    # Shopify / generic
    # ------------------------------------------------------------------
    async def _scrape_shopify_generic(self, url: str, html: str, page) -> Product:
        product = Product(source_url=url, platform="shopify")
        soup = BeautifulSoup(html, "lxml")

        # Try Shopify product JSON endpoint first
        parsed = urlparse(url)
        json_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}.json"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(json_url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    data = resp.json().get("product", {})
                    product.title = data.get("title", "")
                    product.description = BeautifulSoup(data.get("body_html", ""), "lxml").get_text(" ")
                    product.category = data.get("product_type", "")
                    product.brand = data.get("vendor", "")

                    # Images
                    images = data.get("images", [])
                    if images:
                        product.main_image_url = images[0].get("src", "")
                        product.additional_image_urls = [i.get("src", "") for i in images[1:]]

                    # SKUs from variants
                    for v in data.get("variants", []):
                        sku = SKUVariant()
                        sku.sku_id = str(v.get("id", ""))
                        sku.price = float(v.get("price", 0) or 0)
                        sku.inventory = v.get("inventory_quantity")
                        sku.attributes = {
                            k: v.get(k, "")
                            for k in ("option1", "option2", "option3")
                            if v.get(k)
                        }
                        # Map option names
                        opts = data.get("options", [])
                        named_attrs = {}
                        for i, opt in enumerate(opts):
                            key = f"option{i+1}"
                            if v.get(key):
                                named_attrs[opt.get("name", key)] = v[key]
                        sku.attributes = named_attrs

                        # Match variant image
                        vid = v.get("image_id")
                        for img in images:
                            if img.get("id") == vid:
                                sku.image_url = img.get("src", "")
                        product.skus.append(sku)

                    # Features from options
                    for opt in data.get("options", []):
                        product.features.append(ProductFeature(
                            key=opt.get("name", ""),
                            value=", ".join(opt.get("values", [])),
                        ))
                    return product
        except Exception as e:
            logger.debug("Shopify JSON endpoint failed ({}), falling back to HTML", e)

        # HTML fallback
        product.title = (
            soup.find("meta", property="og:title") or {}
        ).get("content") or soup.title.string if soup.title else ""

        # Main image from og:image
        og_img = soup.find("meta", property="og:image")
        if og_img:
            product.main_image_url = og_img.get("content", "")

        if not product.main_image_url:
            product.main_image_url = await self._fallback_main_image(page)

        # Try JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    data = data[0]
                if data.get("@type") in ("Product",):
                    product.title = product.title or data.get("name", "")
                    offers = data.get("offers", [])
                    if isinstance(offers, dict):
                        offers = [offers]
                    for offer in offers:
                        sku = SKUVariant()
                        sku.price = float(offer.get("price", 0) or 0)
                        sku.currency = offer.get("priceCurrency", "USD")
                        sku.sku_id = offer.get("sku", "")
                        product.skus.append(sku)
            except (json.JSONDecodeError, TypeError):
                pass

        return product

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _fallback_main_image(self, page) -> str:
        """Get first large image visible on the page."""
        try:
            img = await page.query_selector("img[src*='cdn'], img[src*='image'], .product-image img, #product-img img")
            if img:
                return await img.get_attribute("src") or ""
        except Exception:
            pass
        return ""
