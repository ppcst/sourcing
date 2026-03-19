"""
Supplier ranking and filtering logic.

Scoring criteria (weights in config.RANK_WEIGHTS):
  price_score       – inverse of unit price (lower price = higher score)
  stability_score   – shop age + transaction level + rating + response rate
  sku_match_score   – fraction of source-product attributes found in supplier SKUs
  response_score    – response rate / Wangwang activity
"""
from __future__ import annotations
import math
from typing import Optional

from loguru import logger

from models.product import Product
from models.supplier import Supplier
import config


class SupplierRanker:
    """Scores and ranks a list of suppliers for a given source product."""

    def __init__(self, weights: Optional[dict] = None):
        self.weights = weights or config.RANK_WEIGHTS

    def rank(self, suppliers: list[Supplier], product: Product) -> list[Supplier]:
        """Score all suppliers and return top-N sorted by score (best first)."""
        if not suppliers:
            return []

        # Compute raw scores
        for s in suppliers:
            s.rank_score = self._score(s, product)

        # Sort descending
        ranked = sorted(suppliers, key=lambda s: s.rank_score, reverse=True)

        # Assign rank position
        for i, s in enumerate(ranked):
            s.rank_position = i + 1

        top = ranked[: config.TOP_N_SUPPLIERS]
        logger.info(
            "Ranked {} suppliers → top {}: {}",
            len(suppliers),
            config.TOP_N_SUPPLIERS,
            [s.shop_name or s.product_url for s in top],
        )
        return top

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def _score(self, supplier: Supplier, product: Product) -> float:
        price_s = self._price_score(supplier)
        stability_s = self._stability_score(supplier)
        sku_s = self._sku_match_score(supplier, product)
        response_s = self._response_score(supplier)

        score = (
            self.weights["price_score"] * price_s
            + self.weights["stability_score"] * stability_s
            + self.weights["sku_match_score"] * sku_s
            + self.weights["response_score"] * response_s
        )
        logger.debug(
            "{} → price={:.2f} stability={:.2f} sku={:.2f} resp={:.2f} total={:.2f}",
            supplier.shop_name or supplier.product_url[:40],
            price_s, stability_s, sku_s, response_s, score,
        )
        return score

    def _price_score(self, supplier: Supplier) -> float:
        """Inverse normalised price (higher score = cheaper)."""
        price = supplier.best_price_cny
        if price is None or price <= 0:
            return 0.5  # neutral when unknown
        # Use 1 / log(price+1) so the score is bounded and doesn't blow up
        return 1.0 / (1.0 + math.log1p(price))

    def _stability_score(self, supplier: Supplier) -> float:
        """0-1 score based on shop maturity signals."""
        score = 0.0
        components = 0

        # Years operating (max credit at 5+ years)
        if supplier.years_operating is not None:
            score += min(supplier.years_operating / 5.0, 1.0)
            components += 1

        # Rating (out of 5)
        if supplier.rating is not None:
            score += supplier.rating / 5.0
            components += 1

        # Transaction level badge
        level_map = {
            "实力商家": 1.0,
            "诚信通": 0.8,
            "旺铺": 0.6,
        }
        for badge, val in level_map.items():
            if badge in (supplier.transaction_level or ""):
                score += val
                components += 1
                break

        # Response rate (0-100)
        if supplier.response_rate is not None:
            score += supplier.response_rate / 100.0
            components += 1

        return (score / components) if components else 0.3

    def _sku_match_score(self, supplier: Supplier, product: Product) -> float:
        """How many source SKU attribute dimensions are present in supplier SKUs."""
        source_attrs = set(product.sku_attribute_names)
        if not source_attrs:
            return 0.7  # No attributes to compare → neutral

        supplier_attrs: set[str] = set()
        for sku in supplier.skus:
            supplier_attrs.update(sku.attributes.keys())

        if not supplier_attrs:
            return 0.3  # Supplier has no SKUs extracted yet

        # Check coverage using a loose key similarity (contains match)
        matched = 0
        for sa in source_attrs:
            for pa in supplier_attrs:
                if sa in pa or pa in sa:
                    matched += 1
                    break

        return matched / len(source_attrs)

    def _response_score(self, supplier: Supplier) -> float:
        """Score based on response rate and Wangwang data availability."""
        score = 0.5  # neutral default

        if supplier.response_rate is not None:
            score = supplier.response_rate / 100.0

        # Bonus if we got a Wangwang reply
        if supplier.wangwang_data and supplier.wangwang_data.reply_received:
            score = min(score + 0.2, 1.0)

        return score
