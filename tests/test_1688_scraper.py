"""Unit tests for 1688 scraper utilities – no browser required."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from scraper.ali1688_scraper import Ali1688Scraper


def test_parse_price_basic():
    assert Ali1688Scraper._parse_price("¥12.50/件") == 12.50


def test_parse_price_range():
    # Should return first number in a range like "8.50-12.00"
    result = Ali1688Scraper._parse_price("8.50-12.00元")
    assert result == 8.50


def test_parse_price_none():
    assert Ali1688Scraper._parse_price("联系议价") is None


def test_parse_weight_kg_kg():
    assert Ali1688Scraper._parse_weight_kg("0.5kg") == pytest.approx(0.5)


def test_parse_weight_kg_g():
    assert Ali1688Scraper._parse_weight_kg("500g") == pytest.approx(0.5)


def test_parse_weight_kg_chinese():
    assert Ali1688Scraper._parse_weight_kg("1.2千克") == pytest.approx(1.2)


def test_parse_weight_kg_plain():
    assert Ali1688Scraper._parse_weight_kg("0.35") == pytest.approx(0.35)


def test_parse_weight_kg_none():
    assert Ali1688Scraper._parse_weight_kg("不含重量") is None
