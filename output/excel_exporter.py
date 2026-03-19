"""
Excel exporter – produces a multi-sheet workbook for all sourcing results.

Sheet layout
============
Sheet 1 – Summary
  One row per source product:
  源链接 | 产品标题 | 推荐供应商 | 推荐理由
  + 3 supplier columns: 供应商1 URL / 最低价 / 推荐 SKU | 供应商2 … | 供应商3 …

Sheet 2 – SKU Details
  One row per (source product × supplier × SKU):
  源链接 | 产品标题 | 供应商 | 店铺URL | SKU属性 |
  详情页价格(CNY) | 旺旺价格(CNY) | 净重(kg) | 毛重(kg) | 包装尺寸 | 库存 |
  旺旺净重 | 旺旺毛重 | 旺旺包装尺寸 | MOQ | 旺旺询问时间 | 是否已回复

Sheet 3 – Wangwang Raw
  Raw Wangwang reply text for manual review.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side, numbers
)
from openpyxl.utils import get_column_letter
from loguru import logger

from models.supplier import SourcingResult, Supplier, SupplierSKU
import config

# Colour palette
C_HEADER = "1F4E79"        # dark blue  (header text)
C_HEADER_BG = "BDD7EE"    # light blue (header fill)
C_RECOMMENDED = "E2EFDA"  # light green (recommended supplier)
C_ALT_ROW = "F2F2F2"      # light grey (alternating rows)
C_WARN = "FFE699"          # yellow (missing data)
C_ERROR = "FFB3B3"         # red (no data at all)


def _make_border() -> Border:
    thin = Side(border_style="thin", color="AAAAAA")
    return Border(left=thin, right=thin, top=thin, bottom=thin)


def _header_cell(ws, row: int, col: int, value: str, width: Optional[int] = None):
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = Font(bold=True, color=C_HEADER)
    cell.fill = PatternFill("solid", fgColor=C_HEADER_BG)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = _make_border()
    if width:
        ws.column_dimensions[get_column_letter(col)].width = width
    return cell


def _data_cell(ws, row: int, col: int, value, alt: bool = False, warn: bool = False, err: bool = False):
    cell = ws.cell(row=row, column=col, value=value)
    if err:
        cell.fill = PatternFill("solid", fgColor=C_ERROR)
    elif warn:
        cell.fill = PatternFill("solid", fgColor=C_WARN)
    elif alt:
        cell.fill = PatternFill("solid", fgColor=C_ALT_ROW)
    cell.alignment = Alignment(vertical="center", wrap_text=True)
    cell.border = _make_border()
    return cell


class ExcelExporter:
    """Export a list of SourcingResult objects to an Excel workbook."""

    def export(
        self,
        results: list[SourcingResult],
        output_path: Optional[Path] = None,
    ) -> Path:
        if output_path is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = config.OUTPUT_DIR / f"sourcing_{ts}.xlsx"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        wb = openpyxl.Workbook()
        wb.remove(wb.active)  # remove default sheet

        self._build_summary_sheet(wb, results)
        self._build_sku_detail_sheet(wb, results)
        self._build_wangwang_sheet(wb, results)

        wb.save(str(output_path))
        logger.info("Excel exported → {}", output_path)
        return output_path

    # ------------------------------------------------------------------
    # Sheet 1 – Summary
    # ------------------------------------------------------------------
    def _build_summary_sheet(self, wb, results: list[SourcingResult]):
        ws = wb.create_sheet("采购汇总 Summary")
        ws.freeze_panes = "C2"

        headers = [
            ("源链接", 35),
            ("产品标题", 30),
            ("推荐供应商", 25),
            ("推荐理由", 30),
        ]
        for i in range(1, 4):
            headers += [
                (f"供应商{i} URL", 30),
                (f"供应商{i} 店铺名", 20),
                (f"供应商{i} 最低价(CNY)", 15),
                (f"供应商{i} 旺旺价(CNY)", 15),
                (f"供应商{i} 净重(kg)", 12),
                (f"供应商{i} 毛重(kg)", 12),
                (f"供应商{i} 包装尺寸", 18),
                (f"供应商{i} SKU数量", 10),
            ]

        for col, (title, width) in enumerate(headers, start=1):
            _header_cell(ws, 1, col, title, width)
        ws.row_dimensions[1].height = 30

        for row_idx, result in enumerate(results, start=2):
            alt = (row_idx % 2 == 0)
            product = result.product
            rec = result.recommended_supplier

            col = 1
            _data_cell(ws, row_idx, col, product.source_url, alt=alt); col += 1
            _data_cell(ws, row_idx, col, product.title, alt=alt); col += 1
            _data_cell(ws, row_idx, col, rec.shop_name if rec else "—", alt=alt); col += 1
            _data_cell(ws, row_idx, col, self._recommend_reason(rec), alt=alt); col += 1

            for supplier in self._pad_suppliers(result.suppliers):
                if supplier is None:
                    for _ in range(8):
                        _data_cell(ws, row_idx, col, "—", alt=alt); col += 1
                    continue
                is_rec = (supplier is rec)
                fill_green = is_rec

                best_price = supplier.best_price_cny
                ww_price = self._ww_best_price(supplier)
                net_kg = self._best_net_weight(supplier)
                gross_kg = self._best_gross_weight(supplier)
                pkg_size = self._best_pkg_size(supplier)

                c = ws.cell(row=row_idx, column=col, value=supplier.product_url)
                c.hyperlink = supplier.product_url
                c.font = Font(color="0563C1", underline="single")
                c.border = _make_border()
                if fill_green:
                    c.fill = PatternFill("solid", fgColor=C_RECOMMENDED)
                col += 1

                _data_cell(ws, row_idx, col, supplier.shop_name, alt=alt if not fill_green else False)
                if fill_green:
                    ws.cell(row=row_idx, column=col).fill = PatternFill("solid", fgColor=C_RECOMMENDED)
                col += 1

                _data_cell(ws, row_idx, col, best_price, alt=alt, warn=(best_price is None))
                if fill_green:
                    ws.cell(row=row_idx, column=col).fill = PatternFill("solid", fgColor=C_RECOMMENDED)
                col += 1

                _data_cell(ws, row_idx, col, ww_price, alt=alt, warn=(ww_price is None))
                col += 1

                _data_cell(ws, row_idx, col, net_kg, alt=alt, warn=(net_kg is None))
                col += 1

                _data_cell(ws, row_idx, col, gross_kg, alt=alt, warn=(gross_kg is None))
                col += 1

                _data_cell(ws, row_idx, col, pkg_size or "—", alt=alt, warn=(pkg_size is None))
                col += 1

                _data_cell(ws, row_idx, col, len(supplier.skus), alt=alt)
                col += 1

    # ------------------------------------------------------------------
    # Sheet 2 – SKU Details
    # ------------------------------------------------------------------
    def _build_sku_detail_sheet(self, wb, results: list[SourcingResult]):
        ws = wb.create_sheet("SKU明细 Detail")
        ws.freeze_panes = "A2"

        headers = [
            ("源链接", 35),
            ("产品标题", 25),
            ("供应商排名", 10),
            ("店铺名", 20),
            ("产品URL", 30),
            ("SKU ID", 15),
            ("SKU属性", 25),
            # Detail page data
            ("详情页单价(CNY)", 15),
            ("详情页净重(kg)", 13),
            ("详情页毛重(kg)", 13),
            ("详情页包装尺寸", 16),
            ("库存", 8),
            # Wangwang data
            ("旺旺单价(CNY)", 14),
            ("旺旺净重(kg)", 13),
            ("旺旺毛重(kg)", 13),
            ("旺旺包装尺寸", 16),
            ("MOQ", 8),
            ("旺旺已回复", 10),
            ("旺旺询问时间", 16),
            ("数据来源", 12),
        ]

        for col, (title, width) in enumerate(headers, start=1):
            _header_cell(ws, 1, col, title, width)
        ws.row_dimensions[1].height = 30

        row_idx = 2
        for result in results:
            product = result.product
            for supplier in result.suppliers:
                is_rec = (supplier is result.recommended_supplier)
                ww = supplier.wangwang_data

                skus_to_write = supplier.skus or [SupplierSKU(sku_id="—")]
                for sku in skus_to_write:
                    alt = (row_idx % 2 == 0)

                    ww_sku = self._find_ww_sku(ww, sku.sku_id) if ww else None

                    col = 1
                    _data_cell(ws, row_idx, col, product.source_url, alt=alt); col += 1
                    _data_cell(ws, row_idx, col, product.title, alt=alt); col += 1
                    _data_cell(ws, row_idx, col, supplier.rank_position, alt=alt); col += 1
                    _data_cell(ws, row_idx, col, supplier.shop_name, alt=alt); col += 1

                    c = ws.cell(row=row_idx, column=col, value=supplier.product_url)
                    c.hyperlink = supplier.product_url
                    c.font = Font(color="0563C1", underline="single")
                    c.border = _make_border()
                    col += 1

                    _data_cell(ws, row_idx, col, sku.sku_id, alt=alt); col += 1
                    attr_str = "  ".join(f"{k}:{v}" for k, v in sku.attributes.items())
                    _data_cell(ws, row_idx, col, attr_str, alt=alt); col += 1

                    # Detail page
                    _data_cell(ws, row_idx, col, sku.price_cny, alt=alt, warn=(sku.price_cny is None)); col += 1
                    _data_cell(ws, row_idx, col, sku.weight_net_kg, alt=alt, warn=(sku.weight_net_kg is None)); col += 1
                    _data_cell(ws, row_idx, col, sku.weight_gross_kg, alt=alt, warn=(sku.weight_gross_kg is None)); col += 1
                    _data_cell(ws, row_idx, col, sku.package_size_cm or "—", alt=alt, warn=(not sku.package_size_cm)); col += 1
                    _data_cell(ws, row_idx, col, sku.inventory, alt=alt); col += 1

                    # Wangwang
                    ww_price = ww_sku.ww_price_cny if ww_sku else sku.ww_price_cny
                    ww_net = ww_sku.ww_weight_net_kg if ww_sku else sku.ww_weight_net_kg
                    ww_gross = ww_sku.ww_weight_gross_kg if ww_sku else sku.ww_weight_gross_kg
                    ww_size = ww_sku.ww_package_size_cm if ww_sku else sku.ww_package_size_cm
                    ww_moq = (ww.moq if ww else None) or sku.ww_moq

                    _data_cell(ws, row_idx, col, ww_price, alt=alt, warn=(ww_price is None)); col += 1
                    _data_cell(ws, row_idx, col, ww_net, alt=alt, warn=(ww_net is None)); col += 1
                    _data_cell(ws, row_idx, col, ww_gross, alt=alt, warn=(ww_gross is None)); col += 1
                    _data_cell(ws, row_idx, col, ww_size or "—", alt=alt, warn=(not ww_size)); col += 1
                    _data_cell(ws, row_idx, col, ww_moq, alt=alt); col += 1
                    _data_cell(ws, row_idx, col, "是" if (ww and ww.reply_received) else "否", alt=alt); col += 1
                    ww_time = ww.inquiry_time.strftime("%Y-%m-%d %H:%M") if (ww and ww.inquiry_time) else "—"
                    _data_cell(ws, row_idx, col, ww_time, alt=alt); col += 1
                    _data_cell(ws, row_idx, col, supplier.data_source, alt=alt); col += 1

                    if is_rec:
                        for c_idx in range(1, col):
                            cell = ws.cell(row=row_idx, column=c_idx)
                            cell.fill = PatternFill("solid", fgColor=C_RECOMMENDED)

                    row_idx += 1

    # ------------------------------------------------------------------
    # Sheet 3 – Wangwang Raw
    # ------------------------------------------------------------------
    def _build_wangwang_sheet(self, wb, results: list[SourcingResult]):
        ws = wb.create_sheet("旺旺原文 Wangwang")
        ws.freeze_panes = "A2"

        headers = [
            ("源链接", 35),
            ("店铺名", 20),
            ("产品URL", 30),
            ("旺旺ID", 15),
            ("是否发送", 10),
            ("是否回复", 10),
            ("询问时间", 18),
            ("回复时间", 18),
            ("旺旺原文回复", 80),
            ("截图路径", 30),
        ]
        for col, (title, width) in enumerate(headers, start=1):
            _header_cell(ws, 1, col, title, width)
        ws.row_dimensions[1].height = 30

        row_idx = 2
        for result in results:
            product = result.product
            for supplier in result.suppliers:
                ww = supplier.wangwang_data
                if not ww:
                    continue
                alt = (row_idx % 2 == 0)
                col = 1
                _data_cell(ws, row_idx, col, product.source_url, alt=alt); col += 1
                _data_cell(ws, row_idx, col, supplier.shop_name, alt=alt); col += 1
                _data_cell(ws, row_idx, col, supplier.product_url, alt=alt); col += 1
                _data_cell(ws, row_idx, col, supplier.wangwang_id, alt=alt); col += 1
                _data_cell(ws, row_idx, col, "是" if ww.inquiry_sent else "否", alt=alt); col += 1
                _data_cell(ws, row_idx, col, "是" if ww.reply_received else "否", alt=alt); col += 1
                _data_cell(ws, row_idx, col, ww.inquiry_time.strftime("%Y-%m-%d %H:%M") if ww.inquiry_time else "—", alt=alt); col += 1
                _data_cell(ws, row_idx, col, ww.reply_time.strftime("%Y-%m-%d %H:%M") if ww.reply_time else "—", alt=alt); col += 1
                _data_cell(ws, row_idx, col, ww.raw_reply, alt=alt)
                ws.row_dimensions[row_idx].height = min(100, max(15, ww.raw_reply.count("\n") * 15 + 15))
                col += 1
                _data_cell(ws, row_idx, col, ww.screenshot_path, alt=alt); col += 1
                row_idx += 1

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _pad_suppliers(self, suppliers: list[Supplier]) -> list[Optional[Supplier]]:
        result = list(suppliers[:3])
        while len(result) < 3:
            result.append(None)
        return result

    def _recommend_reason(self, supplier: Optional[Supplier]) -> str:
        if not supplier:
            return "—"
        parts = []
        price = supplier.best_price_cny
        if price:
            parts.append(f"价格CNY{price:.2f}")
        if supplier.rating:
            parts.append(f"评分{supplier.rating:.1f}")
        if supplier.years_operating:
            parts.append(f"运营{supplier.years_operating:.0f}年")
        if supplier.wangwang_data and supplier.wangwang_data.reply_received:
            parts.append("旺旺已回复")
        return "；".join(parts) if parts else "综合最优"

    def _ww_best_price(self, supplier: Supplier) -> Optional[float]:
        prices = [
            s.ww_negotiated_price or s.ww_price_cny
            for s in supplier.skus
            if s.ww_negotiated_price or s.ww_price_cny
        ]
        return min(prices) if prices else None

    def _best_net_weight(self, supplier: Supplier) -> Optional[float]:
        vals = [s.ww_weight_net_kg or s.weight_net_kg for s in supplier.skus if s.ww_weight_net_kg or s.weight_net_kg]
        return vals[0] if vals else None

    def _best_gross_weight(self, supplier: Supplier) -> Optional[float]:
        vals = [s.ww_weight_gross_kg or s.weight_gross_kg for s in supplier.skus if s.ww_weight_gross_kg or s.weight_gross_kg]
        return vals[0] if vals else None

    def _best_pkg_size(self, supplier: Supplier) -> Optional[str]:
        for s in supplier.skus:
            if s.ww_package_size_cm:
                return s.ww_package_size_cm
            if s.package_size_cm:
                return s.package_size_cm
        return None

    def _find_ww_sku(self, ww, sku_id: str) -> Optional[SupplierSKU]:
        if not ww:
            return None
        for s in ww.skus:
            if s.sku_id == sku_id:
                return s
        return ww.skus[0] if ww.skus else None
