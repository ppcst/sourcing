"""1688 Supplier data models."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class SupplierSKU:
    """One SKU from a 1688 supplier's product listing."""
    sku_id: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    # Detail page data
    price_cny: Optional[float] = None          # unit price in CNY
    price_breaks: list[dict] = field(default_factory=list)  # [{"min_qty": 10, "price": 5.5}, ...]
    weight_net_kg: Optional[float] = None
    weight_gross_kg: Optional[float] = None
    package_size_cm: Optional[str] = None      # "L×W×H"
    inventory: Optional[int] = None
    image_url: str = ""
    # Wangwang confirmed data
    ww_price_cny: Optional[float] = None
    ww_weight_net_kg: Optional[float] = None
    ww_weight_gross_kg: Optional[float] = None
    ww_package_size_cm: Optional[str] = None
    ww_moq: Optional[int] = None
    ww_negotiated_price: Optional[float] = None


@dataclass
class WangwangData:
    """Data returned from a Wangwang inquiry conversation."""
    inquiry_sent: bool = False
    inquiry_time: Optional[datetime] = None
    reply_received: bool = False
    reply_time: Optional[datetime] = None
    raw_reply: str = ""
    screenshot_path: str = ""
    # Parsed fields from reply
    skus: list[SupplierSKU] = field(default_factory=list)
    moq: Optional[int] = None
    can_negotiate: bool = False
    has_actual_photos: bool = False
    notes: str = ""


@dataclass
class Supplier:
    """A 1688 supplier with product listing data."""
    shop_name: str = ""
    shop_url: str = ""
    product_url: str = ""          # The specific product URL on 1688
    product_title: str = ""

    # Shop stability metrics
    years_operating: Optional[float] = None
    transaction_level: str = ""    # 1688 transaction badge (e.g. "实力商家")
    rating: Optional[float] = None
    response_rate: Optional[float] = None    # 0-100 %
    repurchase_rate: Optional[float] = None

    # Price (list price from search result)
    list_price_cny: Optional[float] = None
    moq: Optional[int] = None

    # Detailed SKU data scraped from detail page
    skus: list[SupplierSKU] = field(default_factory=list)

    # Wangwang inquiry result
    wangwang_id: str = ""
    wangwang_data: Optional[WangwangData] = None

    # Ranking
    rank_score: float = 0.0
    rank_position: int = 0        # 1 = best

    # Flags
    sku_match_confirmed: bool = False
    data_source: str = "detail_page"  # detail_page | wangwang | both

    @property
    def best_price_cny(self) -> Optional[float]:
        """Return the lowest negotiated or listed price across all SKUs."""
        prices = []
        for sku in self.skus:
            if sku.ww_negotiated_price is not None:
                prices.append(sku.ww_negotiated_price)
            elif sku.ww_price_cny is not None:
                prices.append(sku.ww_price_cny)
            elif sku.price_cny is not None:
                prices.append(sku.price_cny)
        return min(prices) if prices else self.list_price_cny


@dataclass
class SourcingResult:
    """Final output for one source product: source + top 3 suppliers."""
    product: "Product"                          # imported lazily to avoid circular
    suppliers: list[Supplier] = field(default_factory=list)
    recommended_supplier: Optional[Supplier] = None
    processed_at: datetime = field(default_factory=datetime.now)
    notes: str = ""
