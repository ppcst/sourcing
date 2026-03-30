#!/usr/bin/env python3
"""
E-Commerce Supplier Sourcing Tool
===================================
Automates 1688 supplier discovery for Shopify / AliExpress products.

Usage examples
--------------
# Process a list of URLs from a file (one per line or CSV)
python main.py run --input urls.txt

# Process a single URL directly
python main.py run --url "https://www.aliexpress.com/item/12345.html"

# Disable Wangwang (faster, detail-page only)
python main.py run --input urls.txt --no-wangwang

# Run headless (no browser window)
HEADLESS=true python main.py run --input urls.txt

# Output to a specific file
python main.py run --input urls.txt --output data/report_2024.xlsx
"""
import asyncio
import sys
from pathlib import Path

import argparse
from loguru import logger

import config


def _setup_logging(log_level: str = config.LOG_LEVEL) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> — <level>{message}</level>",
    )
    logger.add(
        str(config.LOG_FILE),
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="E-Commerce 1688 Supplier Sourcing Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── run ───────────────────────────────────────────────────────────
    run_p = sub.add_parser("run", help="Run the sourcing pipeline")
    group = run_p.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", "-i", type=Path, help="Path to URL list file (.txt or .csv)")
    group.add_argument("--url", "-u", type=str, help="Single source product URL to process")
    run_p.add_argument("--output", "-o", type=Path, default=None, help="Output Excel path")
    run_p.add_argument(
        "--no-wangwang", action="store_true",
        help="Skip Wangwang inquiries (faster, detail-page data only)"
    )
    run_p.add_argument(
        "--quantity", "-q", type=int, default=100,
        help="Inquiry quantity used in Wangwang message (default: 100)"
    )
    run_p.add_argument(
        "--max-candidates", type=int, default=20,
        help="Max 1688 search results to evaluate per product (default: 20)"
    )
    run_p.add_argument("--log-level", default=config.LOG_LEVEL, help="Logging level")

    # ── login ─────────────────────────────────────────────────────────
    login_p = sub.add_parser(
        "login",
        help="Open a browser window so you can log in to 1688 manually. "
             "Cookies are saved in the persistent browser profile."
    )
    login_p.add_argument("--log-level", default="INFO")
    login_p.add_argument(
        "--import-cookies", type=Path, default=None, metavar="COOKIES_JSON",
        help="Import cookies from a JSON file exported by a browser extension "
             "(e.g. EditThisCookie) instead of opening the browser."
    )
    login_p.add_argument(
        "--timeout", type=int, default=300,
        help="Seconds to keep the browser open (default: 300)"
    )

    # ── demo ──────────────────────────────────────────────────────────
    demo_p = sub.add_parser("demo", help="Run a quick demo with sample data (no browser needed)")
    demo_p.add_argument("--output", "-o", type=Path, default=None)

    return parser


async def cmd_run(args: argparse.Namespace) -> None:
    from workflow.pipeline import SourcingPipeline

    pipeline = SourcingPipeline(
        use_wangwang=not args.no_wangwang,
        wangwang_quantity=args.quantity,
        max_candidates=args.max_candidates,
        output_path=args.output,
    )

    if args.url:
        output = await pipeline.run([args.url])
    else:
        if not args.input.exists():
            logger.error("Input file not found: {}", args.input)
            sys.exit(1)
        output = await pipeline.run_from_file(args.input)

    print(f"\nReport saved to: {output}")


async def cmd_login(args: argparse.Namespace) -> None:
    """Open a persistent browser session for the user to log in to 1688."""
    import json
    import os
    import shutil
    from scraper.browser import BrowserManager

    # ── Option A: import cookies from a JSON file ─────────────────────
    if getattr(args, "import_cookies", None):
        cookie_path: Path = args.import_cookies
        if not cookie_path.exists():
            print(f"[ERROR] Cookie file not found: {cookie_path}")
            sys.exit(1)
        raw = json.loads(cookie_path.read_text(encoding="utf-8"))
        # Normalize EditThisCookie / Cookie-Editor format → Playwright format
        sameSite_map = {
            "no_restriction": "None",
            "unspecified": "None",
            "lax": "Lax",
            "strict": "Strict",
            "none": "None",
        }
        cookies = []
        for c in raw:
            pw = {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path", "/"),
                "secure": c.get("secure", False),
                "httpOnly": c.get("httpOnly", False),
                "sameSite": sameSite_map.get(
                    str(c.get("sameSite", "None")).lower(), "None"
                ),
            }
            # Only set expires for persistent cookies (skip session cookies)
            exp = c.get("expirationDate")
            if exp and not c.get("session", False):
                pw["expires"] = int(exp)
            cookies.append(pw)

        print(f"Importing {len(cookies)} cookies into the browser profile…")
        async with BrowserManager(headless=True) as bm:
            page = await bm.new_page()
            await bm.goto(page, "https://www.1688.com")
            await bm._context.add_cookies(cookies)
            await page.reload()
            await asyncio.sleep(3)
            # Verify login by checking for username in page
            html = await page.content()
            nick = next((c["value"] for c in cookies if c["name"] == "_nk_"), "")
            logged_in = nick and nick in html
            if logged_in:
                print(f"✓ Logged in as: {nick}")
            else:
                title = await page.title()
                print(f"Page title: {title}")
                print("⚠ Could not confirm login — cookies may be expired.")
        print("Session saved. Run `python main.py run …` to start sourcing.")
        return

    # ── Option B: headed browser (requires a display / xvfb-run) ──────
    has_display = bool(os.environ.get("DISPLAY"))
    has_xvfb = bool(shutil.which("xvfb-run"))

    if not has_display:
        if has_xvfb:
            # Re-launch ourselves under xvfb-run so the browser gets a virtual display
            import subprocess
            print("No $DISPLAY found. Relaunching under xvfb-run (virtual display)…")
            # Strip DISPLAY from env so xvfb-run sets it correctly for its child
            clean_env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
            cmd = ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1366x768x24",
                   sys.executable] + sys.argv
            result = subprocess.run(cmd, env=clean_env)
            sys.exit(result.returncode)
        else:
            print(
                "\n[ERROR] No display server found ($DISPLAY is empty) and xvfb-run is not installed.\n"
                "\nOptions:\n"
                "  1. Install xvfb:  sudo apt-get install -y xvfb\n"
                "     Then re-run:   xvfb-run python main.py login\n\n"
                "  2. Export cookies from your local browser using the\n"
                "     'EditThisCookie' or 'Cookie-Editor' extension on 1688.com,\n"
                "     save them as cookies.json, then run:\n"
                "     python main.py login --import-cookies cookies.json\n\n"
                "  3. Run on your local machine (Mac/Windows) where a display is available.\n"
            )
            sys.exit(1)

    timeout = getattr(args, "timeout", 300)
    print(f"Opening browser… Please log in to 1688.com (you have {timeout}s).")
    print("Press Ctrl+C when you are done to save the session.\n")
    async with BrowserManager(headless=False) as bm:
        page = await bm.new_page()
        await bm.goto(page, "https://login.1688.com/member/signin.htm")
        print("Browser open. Log in now…")
        try:
            await asyncio.sleep(timeout)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
    print("\nSession saved to:", config.DATA_DIR / "browser_profile")
    print("You can now run the sourcing pipeline.")


async def cmd_demo(args: argparse.Namespace) -> None:
    """Generate a demo Excel report using synthetic data (no browser required)."""
    from models.product import Product, SKUVariant, ProductFeature
    from models.supplier import Supplier, SupplierSKU, WangwangData, SourcingResult
    from output.excel_exporter import ExcelExporter
    from datetime import datetime

    # --- Synthetic source product ---
    product = Product(
        source_url="https://example-shop.myshopify.com/products/sample-widget",
        platform="shopify",
        title="Premium Widget Pro — 3 Colours",
        main_image_url="https://cdn.shopify.com/s/files/1/sample.jpg",
        category="Electronics",
    )
    product.skus = [
        SKUVariant(sku_id="101", attributes={"Color": "Red"}, price=29.99, currency="USD"),
        SKUVariant(sku_id="102", attributes={"Color": "Blue"}, price=29.99, currency="USD"),
        SKUVariant(sku_id="103", attributes={"Color": "Black"}, price=27.99, currency="USD"),
    ]
    product.features = [
        ProductFeature("Material", "ABS Plastic"),
        ProductFeature("Weight", "0.35 kg"),
    ]

    # --- Synthetic suppliers ---
    def make_supplier(rank, shop, url, price, net_kg, gross_kg, pkg, ww_price, rating, years):
        s = Supplier(
            shop_name=shop,
            shop_url=f"https://shop.1688.com/{shop}",
            product_url=url,
            rank_position=rank,
            rank_score=1.0 - rank * 0.1,
            rating=rating,
            years_operating=years,
            transaction_level="实力商家",
            response_rate=92.0,
        )
        s.skus = [
            SupplierSKU(
                sku_id=f"s{rank}_{i}",
                attributes={"颜色": c},
                price_cny=price + i * 0.5,
                weight_net_kg=net_kg,
                weight_gross_kg=gross_kg,
                package_size_cm=pkg,
                inventory=500 + i * 100,
                ww_price_cny=ww_price + i * 0.3,
                ww_weight_net_kg=net_kg + 0.01,
                ww_weight_gross_kg=gross_kg + 0.05,
                ww_package_size_cm=pkg,
                ww_moq=50,
            )
            for i, c in enumerate(["红色", "蓝色", "黑色"])
        ]
        s.wangwang_id = f"ww_{shop}"
        s.wangwang_data = WangwangData(
            inquiry_sent=True,
            inquiry_time=datetime(2024, 3, 1, 10, 0),
            reply_received=True,
            reply_time=datetime(2024, 3, 1, 10, 5),
            raw_reply=(
                f"您好！感谢询价。\n红色 单价¥{ww_price:.1f}，蓝色 单价¥{ww_price+0.3:.1f}，"
                f"黑色 单价¥{ww_price+0.6:.1f}\n净重：{net_kg}kg  毛重：{gross_kg}kg\n"
                f"包装尺寸：{pkg}\nMOQ：50件\n可以议价，欢迎合作！"
            ),
            moq=50,
            can_negotiate=True,
            has_actual_photos=True,
        )
        s.data_source = "both"
        return s

    sup1 = make_supplier(1, "优质五金厂", "https://detail.1688.com/offer/111.html", 12.5, 0.32, 0.40, "15×10×8cm", 12.0, 4.8, 6)
    sup2 = make_supplier(2, "蓝海电子商行", "https://detail.1688.com/offer/222.html", 13.0, 0.35, 0.44, "16×11×9cm", 12.5, 4.6, 4)
    sup3 = make_supplier(3, "新锐制造工厂", "https://detail.1688.com/offer/333.html", 11.8, 0.30, 0.38, "14×9×7cm", 11.5, 4.3, 2)

    result = SourcingResult(
        product=product,
        suppliers=[sup1, sup2, sup3],
        recommended_supplier=sup1,
    )

    exporter = ExcelExporter()
    output = exporter.export([result], args.output)
    print(f"\nDemo report saved to: {output}")
    print("Open it to see the full Excel layout with all 3 sheets.")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _setup_logging(getattr(args, "log_level", config.LOG_LEVEL))

    command_map = {
        "run": cmd_run,
        "login": cmd_login,
        "demo": cmd_demo,
    }
    coro = command_map[args.command](args)
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
