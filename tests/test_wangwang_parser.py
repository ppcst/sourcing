"""Unit tests for Wangwang reply parsing – no browser required."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from models.supplier import Supplier, SupplierSKU
from scraper.wangwang import WangwangClient, _PRICE_RE, _NET_WEIGHT_RE, _GROSS_WEIGHT_RE, _SIZE_RE, _MOQ_RE


SAMPLE_REPLY = """
您好！感谢您的询价。
红色 单价¥12.5/件，蓝色 单价¥13.0，黑色 单价¥11.8
净重：0.32kg  毛重：0.40kg
包装尺寸：15×10×8cm
最小起订量MOQ：50件
大量采购可以议价，欢迎合作！
"""

class _DummyBrowser:
    pass


def _make_client():
    return WangwangClient(_DummyBrowser())


def test_price_regex():
    m = _PRICE_RE.search(SAMPLE_REPLY)
    assert m is not None
    assert float(m.group(1)) == pytest.approx(12.5)


def test_net_weight_regex():
    m = _NET_WEIGHT_RE.search(SAMPLE_REPLY)
    assert m is not None
    assert float(m.group(1)) == pytest.approx(0.32)


def test_gross_weight_regex():
    m = _GROSS_WEIGHT_RE.search(SAMPLE_REPLY)
    assert m is not None
    assert float(m.group(1)) == pytest.approx(0.40)


def test_size_regex():
    m = _SIZE_RE.search(SAMPLE_REPLY)
    assert m is not None
    assert "15" in m.group(1)


def test_moq_regex():
    m = _MOQ_RE.search(SAMPLE_REPLY)
    assert m is not None
    assert int(m.group(1)) == 50


def test_parse_reply_no_skus():
    client = _make_client()
    supplier = Supplier()
    skus = client._parse_reply(SAMPLE_REPLY, supplier)
    assert len(skus) == 1
    assert skus[0].ww_price_cny == pytest.approx(12.5)
    assert skus[0].ww_weight_net_kg == pytest.approx(0.32)
    assert skus[0].ww_weight_gross_kg == pytest.approx(0.40)
    assert skus[0].ww_package_size_cm == "15×10×8cm"


def test_parse_reply_with_existing_skus():
    client = _make_client()
    supplier = Supplier()
    supplier.skus = [
        SupplierSKU(sku_id="1", attributes={"颜色": "红色"}, price_cny=15.0),
        SupplierSKU(sku_id="2", attributes={"颜色": "蓝色"}, price_cny=16.0),
    ]
    skus = client._parse_reply(SAMPLE_REPLY, supplier)
    assert len(skus) == 2
    # Red should be matched to 12.5
    red_sku = next(s for s in skus if s.attributes.get("颜色") == "红色")
    assert red_sku.ww_price_cny == pytest.approx(12.5)


def test_extract_moq():
    client = _make_client()
    assert client._extract_moq(SAMPLE_REPLY) == 50


def test_to_kg_grams():
    assert WangwangClient._to_kg("500", "g") == pytest.approx(0.5)


def test_to_kg_kg():
    assert WangwangClient._to_kg("1.5", "kg") == pytest.approx(1.5)
