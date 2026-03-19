"""Unit tests for supplier ranking logic – no browser required."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from models.product import Product, SKUVariant
from models.supplier import Supplier, SupplierSKU, WangwangData
from workflow.supplier_ranker import SupplierRanker


def _make_supplier(price, rating=4.0, years=3.0, skus=None, has_ww_reply=False):
    s = Supplier(
        shop_name=f"Shop_{price}",
        list_price_cny=price,
        rating=rating,
        years_operating=years,
        transaction_level="实力商家",
        response_rate=90.0,
    )
    s.skus = skus or [SupplierSKU(sku_id="1", price_cny=price, attributes={"颜色": "红色"})]
    if has_ww_reply:
        s.wangwang_data = WangwangData(reply_received=True)
    return s


def _make_product():
    p = Product(source_url="https://example.com")
    p.skus = [SKUVariant(sku_id="1", attributes={"Color": "Red"})]
    return p


def test_rank_returns_top_n():
    ranker = SupplierRanker()
    product = _make_product()
    suppliers = [_make_supplier(p) for p in [10, 12, 8, 15, 11]]
    ranked = ranker.rank(suppliers, product)
    assert len(ranked) == 3


def test_rank_assigns_positions():
    ranker = SupplierRanker()
    product = _make_product()
    suppliers = [_make_supplier(p) for p in [10, 8, 12]]
    ranked = ranker.rank(suppliers, product)
    positions = [s.rank_position for s in ranked]
    assert positions == [1, 2, 3]


def test_rank_cheaper_scores_higher():
    ranker = SupplierRanker()
    product = _make_product()
    cheap = _make_supplier(5.0, rating=4.0, years=3.0)
    expensive = _make_supplier(50.0, rating=4.0, years=3.0)
    ranked = ranker.rank([cheap, expensive], product)
    assert ranked[0].list_price_cny == 5.0


def test_rank_empty():
    ranker = SupplierRanker()
    product = _make_product()
    assert ranker.rank([], product) == []


def test_sku_match_score_full_match():
    ranker = SupplierRanker()
    product = _make_product()  # has "Color" attribute
    supplier = _make_supplier(10.0, skus=[
        SupplierSKU(sku_id="1", price_cny=10.0, attributes={"Color": "Red"}),
    ])
    score = ranker._sku_match_score(supplier, product)
    assert score == 1.0


def test_sku_match_score_no_skus():
    ranker = SupplierRanker()
    product = _make_product()
    supplier = Supplier(list_price_cny=10.0)  # no SKUs
    score = ranker._sku_match_score(supplier, product)
    assert score == 0.3


def test_ww_reply_boosts_response_score():
    ranker = SupplierRanker()
    s_with_reply = _make_supplier(10.0, has_ww_reply=True)
    s_no_reply = _make_supplier(10.0, has_ww_reply=False)
    assert ranker._response_score(s_with_reply) > ranker._response_score(s_no_reply)
