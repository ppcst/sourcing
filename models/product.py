"""Source product data models (Shopify / AliExpress / generic e-commerce)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProductFeature:
    """A single key-value feature extracted from a product page."""
    key: str
    value: str


@dataclass
class SKUVariant:
    """One SKU/variant of the source product."""
    sku_id: str = ""
    attributes: dict[str, str] = field(default_factory=dict)  # e.g. {"Color": "Red", "Size": "M"}
    price: Optional[float] = None
    currency: str = "USD"
    inventory: Optional[int] = None
    image_url: str = ""


@dataclass
class Product:
    """Represents a source e-commerce product to be sourced on 1688."""
    source_url: str
    platform: str = ""          # shopify | aliexpress | amazon | generic
    title: str = ""
    main_image_url: str = ""
    additional_image_urls: list[str] = field(default_factory=list)
    description: str = ""
    features: list[ProductFeature] = field(default_factory=list)
    skus: list[SKUVariant] = field(default_factory=list)
    category: str = ""
    brand: str = ""
    weight_kg: Optional[float] = None
    # Raw scraped data for debugging
    raw_html: str = ""

    @property
    def sku_attribute_names(self) -> list[str]:
        """Return unique attribute dimension names across all SKUs."""
        names: set[str] = set()
        for sku in self.skus:
            names.update(sku.attributes.keys())
        return sorted(names)

    @property
    def price_range(self) -> tuple[Optional[float], Optional[float]]:
        prices = [s.price for s in self.skus if s.price is not None]
        if not prices:
            return None, None
        return min(prices), max(prices)
