"""Unit tests for data models – no browser required."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from models.product import Product, SKUVariant, ProductFeature
from models.supplier import Supplier, SupplierSKU, WangwangData, SourcingResult


def test_product_sku_attribute_names():
    product = Product(source_url="https://example.com")
    product.skus = [
        SKUVariant(sku_id="1", attributes={"Color": "Red", "Size": "M"}),
        SKUVariant(sku_id="2", attributes={"Color": "Blue", "Size": "L"}),
    ]
    assert product.sku_attribute_names == ["Color", "Size"]


def test_product_price_range():
    product = Product(source_url="https://example.com")
    product.skus = [
        SKUVariant(sku_id="1", price=10.0),
        SKUVariant(sku_id="2", price=20.0),
        SKUVariant(sku_id="3", price=15.0),
    ]
    assert product.price_range == (10.0, 20.0)


def test_product_price_range_empty():
    product = Product(source_url="https://example.com")
    assert product.price_range == (None, None)


def test_supplier_best_price_ww_negotiated():
    s = Supplier()
    s.skus = [
        SupplierSKU(price_cny=20.0, ww_price_cny=18.0, ww_negotiated_price=15.0),
        SupplierSKU(price_cny=22.0, ww_negotiated_price=17.0),
    ]
    assert s.best_price_cny == 15.0


def test_supplier_best_price_ww_only():
    s = Supplier()
    s.skus = [
        SupplierSKU(price_cny=20.0, ww_price_cny=18.0),
        SupplierSKU(price_cny=22.0, ww_price_cny=19.0),
    ]
    assert s.best_price_cny == 18.0


def test_supplier_best_price_detail_fallback():
    s = Supplier(list_price_cny=25.0)
    assert s.best_price_cny == 25.0


def test_sourcing_result_creation():
    product = Product(source_url="https://test.com")
    sup = Supplier(shop_name="Test Shop", rank_position=1)
    result = SourcingResult(product=product, suppliers=[sup], recommended_supplier=sup)
    assert result.recommended_supplier.shop_name == "Test Shop"
    assert len(result.suppliers) == 1
