"""Global configuration for the sourcing pipeline."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output" / "reports"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Browser settings
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
BROWSER_TIMEOUT = int(os.getenv("BROWSER_TIMEOUT", "30000"))  # ms
SLOW_MO = int(os.getenv("SLOW_MO", "500"))  # ms between actions (human-like)

# 1688 settings
ALI1688_BASE = "https://www.1688.com"
ALI1688_IMAGE_SEARCH = "https://s.1688.com/youyuan/index.htm"
ALI1688_SEARCH_URL = "https://s.1688.com/selloffer/offer_search.htm"

# Supplier ranking weights
RANK_WEIGHTS = {
    "price_score": 0.40,       # lowest landed cost
    "stability_score": 0.30,   # shop age, transaction volume, rating
    "sku_match_score": 0.20,   # how well SKUs match source product
    "response_score": 0.10,    # response rate / Wangwang activity
}

# Top N suppliers to keep
TOP_N_SUPPLIERS = 3

# Concurrency
MAX_CONCURRENT_PRODUCTS = int(os.getenv("MAX_CONCURRENT_PRODUCTS", "5"))
MAX_CONCURRENT_SUPPLIERS = int(os.getenv("MAX_CONCURRENT_SUPPLIERS", "3"))

# Wangwang
WANGWANG_WAIT_REPLY_SEC = int(os.getenv("WANGWANG_WAIT_REPLY_SEC", "120"))
WANGWANG_INQUIRY_TEMPLATE = """您好！我是一位跨境电商卖家，对贵店的商品非常感兴趣。
请问能否提供以下信息：
1. 各SKU的最新报价（数量：{quantity}件）
2. 各SKU的净重（kg）
3. 各SKU的毛重（kg）
4. 包装尺寸（长×宽×高 cm）
5. 最小起订量（MOQ）
6. 可否提供实物图/白底图
7. 大量采购是否可以议价

商品链接：{product_url}
感谢您的配合！"""

# Retry settings
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = BASE_DIR / "data" / "sourcing.log"
