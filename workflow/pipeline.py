"""
Main sourcing pipeline orchestrator.

For each source product URL:
  1. Scrape the source product (title, image, SKUs)
  2. 1688 image search → collect candidate suppliers
  3. Scrape detail page for each candidate
  4. Rank suppliers → keep top-3
  5. Optionally send Wangwang inquiries for missing data
  6. Return SourcingResult

Designed to process a batch of URLs from a CSV / list file.
Concurrency controlled by MAX_CONCURRENT_PRODUCTS.
"""
from __future__ import annotations
import asyncio
import csv
import time
from pathlib import Path
from typing import Optional

from loguru import logger
from tqdm.asyncio import tqdm

from models.product import Product
from models.supplier import Supplier, SourcingResult
from scraper.browser import BrowserManager
from scraper.product_scraper import ProductScraper
from scraper.ali1688_scraper import Ali1688Scraper
from scraper.wangwang import WangwangClient
from workflow.supplier_ranker import SupplierRanker
from output.excel_exporter import ExcelExporter
import config


class SourcingPipeline:
    """
    End-to-end sourcing pipeline.

    Parameters
    ----------
    use_wangwang : bool
        Whether to send Wangwang inquiries (requires browser login).
    wangwang_quantity : int
        The inquiry order quantity used in the Wangwang message template.
    max_candidates : int
        How many supplier search results to evaluate before ranking.
    """

    def __init__(
        self,
        use_wangwang: bool = True,
        wangwang_quantity: int = 100,
        max_candidates: int = 20,
        output_path: Optional[Path] = None,
    ):
        self.use_wangwang = use_wangwang
        self.wangwang_quantity = wangwang_quantity
        self.max_candidates = max_candidates
        self.output_path = output_path
        self.ranker = SupplierRanker()
        self.exporter = ExcelExporter()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------
    async def run_from_file(self, input_path: Path) -> Path:
        """
        Read source URLs from a plain-text file (one URL per line) or CSV
        (first column = URL), process all, and write the Excel report.
        """
        urls = self._load_urls(input_path)
        logger.info("Loaded {} URLs from {}", len(urls), input_path)
        return await self.run(urls)

    async def run(self, urls: list[str]) -> Path:
        """Process a list of source product URLs and export results to Excel."""
        async with BrowserManager() as browser:
            self._browser = browser
            self._product_scraper = ProductScraper(browser)
            self._ali_scraper = Ali1688Scraper(browser)
            self._ww_client = WangwangClient(browser)

            sem = asyncio.Semaphore(config.MAX_CONCURRENT_PRODUCTS)
            tasks = [self._process_with_sem(url, sem) for url in urls]

            results: list[SourcingResult] = []
            for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Sourcing"):
                result = await coro
                if result:
                    results.append(result)

        output_path = self.exporter.export(results, self.output_path)
        logger.success("Done! Report saved to {}", output_path)
        return output_path

    # ------------------------------------------------------------------
    # Per-product flow
    # ------------------------------------------------------------------
    async def _process_with_sem(self, url: str, sem: asyncio.Semaphore) -> Optional[SourcingResult]:
        async with sem:
            return await self._process_one(url)

    async def _process_one(self, url: str) -> Optional[SourcingResult]:
        try:
            t0 = time.perf_counter()

            # Step 1: Scrape source product
            product = await self._scrape_product(url)
            if not product:
                return None

            # Step 2: 1688 image search
            candidates = await self._ali_scraper.find_suppliers(product, self.max_candidates)
            if not candidates:
                logger.warning("No 1688 suppliers found for: {}", url)
                return SourcingResult(product=product, notes="1688图搜无结果")

            # Step 3: Scrape detail pages (concurrently)
            detail_sem = asyncio.Semaphore(config.MAX_CONCURRENT_SUPPLIERS)
            detail_tasks = [
                self._scrape_detail_with_sem(s, detail_sem) for s in candidates
            ]
            detailed = await asyncio.gather(*detail_tasks)
            candidates = [s for s in detailed if s is not None]

            # Step 4: Rank → top 3
            top3 = self.ranker.rank(candidates, product)

            # Step 5: Wangwang inquiries for missing data
            if self.use_wangwang:
                ww_tasks = [
                    self._wangwang_if_needed(s) for s in top3
                ]
                top3 = list(await asyncio.gather(*ww_tasks))

            # Step 6: Re-rank after Wangwang data (scores may have changed)
            if self.use_wangwang:
                top3 = self.ranker.rank(top3, product)

            recommended = top3[0] if top3 else None
            result = SourcingResult(
                product=product,
                suppliers=top3,
                recommended_supplier=recommended,
            )
            elapsed = time.perf_counter() - t0
            logger.info("Completed {} in {:.1f}s", url, elapsed)
            return result

        except Exception as e:
            logger.error("Pipeline error for {}: {}", url, e, exc_info=True)
            return None

    async def _scrape_product(self, url: str) -> Optional[Product]:
        for attempt in range(config.MAX_RETRIES):
            try:
                return await self._product_scraper.scrape(url)
            except Exception as e:
                if attempt == config.MAX_RETRIES - 1:
                    logger.error("Failed to scrape source product {}: {}", url, e)
                    return None
                await asyncio.sleep(config.RETRY_DELAY * (2 ** attempt))
        return None

    async def _scrape_detail_with_sem(
        self, supplier: Supplier, sem: asyncio.Semaphore
    ) -> Optional[Supplier]:
        async with sem:
            for attempt in range(config.MAX_RETRIES):
                try:
                    return await self._ali_scraper.scrape_supplier_detail(supplier)
                except Exception as e:
                    if attempt == config.MAX_RETRIES - 1:
                        logger.warning("Detail scrape failed for {}: {}", supplier.product_url, e)
                        return supplier  # return partially-filled supplier
                    await asyncio.sleep(config.RETRY_DELAY * (2 ** attempt))
        return supplier

    async def _wangwang_if_needed(self, supplier: Supplier) -> Supplier:
        """Send Wangwang inquiry only when detail page data is missing/incomplete."""
        missing = self._has_missing_data(supplier)
        if not missing:
            supplier.data_source = "detail_page"
            return supplier

        logger.info("Sending Wangwang inquiry to {} (missing: {})", supplier.shop_name, missing)
        ww_data = await self._ww_client.inquire(supplier, quantity=self.wangwang_quantity)
        supplier.wangwang_data = ww_data
        supplier.data_source = "both" if ww_data.reply_received else "detail_page"
        return supplier

    def _has_missing_data(self, supplier: Supplier) -> list[str]:
        """Return list of missing data field names."""
        missing = []
        if not supplier.skus:
            missing.append("skus")
            return missing
        for sku in supplier.skus:
            if sku.price_cny is None:
                missing.append("price")
            if sku.weight_net_kg is None:
                missing.append("net_weight")
            if sku.weight_gross_kg is None:
                missing.append("gross_weight")
            if not sku.package_size_cm:
                missing.append("package_size")
        return list(set(missing))

    # ------------------------------------------------------------------
    # URL loading
    # ------------------------------------------------------------------
    @staticmethod
    def _load_urls(path: Path) -> list[str]:
        urls: list[str] = []
        text = path.read_text(encoding="utf-8").strip()
        if path.suffix.lower() == ".csv":
            reader = csv.reader(text.splitlines())
            for row in reader:
                if row and row[0].startswith("http"):
                    urls.append(row[0].strip())
        else:
            for line in text.splitlines():
                line = line.strip()
                if line and line.startswith("http"):
                    urls.append(line)
        return urls
