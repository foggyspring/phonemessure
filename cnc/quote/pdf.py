"""Render a quotation to PDF with reportlab.

Self-contained: takes the structured quote result (the same dict the API
returns) plus a few header fields and produces a one-page, customer-ready
quotation with the cost breakdown, the quantity price-break table, and the
manufacturability notes/warnings.
"""
from __future__ import annotations

import io
import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_ACCENT = colors.HexColor("#1f6feb")
_GREY = colors.HexColor("#57606a")

# Bilingual (中文 + Latin) quote rendering needs an *embedded* CJK font;
# the Adobe CID font STSong-Light is not embedded and tofus in most viewers.
# We search common CJK TrueType locations across Linux/macOS/Windows, embed the
# first we find, and fall back to the (non-embedded) CID font only as a last
# resort. Override with the CNC_CJK_FONT env var to point at any .ttf/.ttc.
_CJK_FONT_CANDIDATES = [
    os.environ.get("CNC_CJK_FONT"),
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.ttf",
    "/etc/alternatives/fonts-japanese-gothic.ttf",
    "/System/Library/Fonts/PingFang.ttc",          # macOS
    "/System/Library/Fonts/STHeiti Light.ttc",     # macOS
    "C:/Windows/Fonts/msyh.ttc",                    # Windows (Microsoft YaHei)
    "C:/Windows/Fonts/simsun.ttc",                  # Windows (SimSun)
]

_FONT = "STSong-Light"  # set to the embedded TTF name by _ensure_font() if found
_font_ready = False


def _ensure_font() -> None:
    """Register a CJK-capable font exactly once; record its name in _FONT."""
    global _FONT, _font_ready
    if _font_ready:
        return
    for path in _CJK_FONT_CANDIDATES:
        if not path or not os.path.exists(path):
            continue
        try:
            # TrueType/OpenType collections need a subfont index.
            if path.lower().endswith((".ttc", ".otc")):
                pdfmetrics.registerFont(TTFont("CNCCJK", path, subfontIndex=0))
            else:
                pdfmetrics.registerFont(TTFont("CNCCJK", path))
            _FONT = "CNCCJK"
            _font_ready = True
            return
        except Exception:
            continue
    # Last resort: relies on the viewer supplying Adobe Asian fonts.
    if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    _FONT = "STSong-Light"
    _font_ready = True


def _money(v: float, cur: str = "CNY") -> str:
    sym = "¥" if cur == "CNY" else cur + " "
    return f"{sym}{v:,.2f}"


def build_quote_pdf(payload: dict, *, quote_no: str | None = None) -> bytes:
    """payload is the dict produced by service.build_quote()."""
    _ensure_font()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=f"CNC Quotation {quote_no or ''}".strip(),
    )
    styles = getSampleStyleSheet()
    h1 = styles["Title"]
    h1.textColor = _ACCENT
    h1.fontName = _FONT
    body = styles["BodyText"]
    body.fontName = _FONT
    small = styles["BodyText"].clone("small")
    small.fontName = _FONT
    small.fontSize = 8
    small.textColor = _GREY

    cur = payload["quote"]["currency"]
    inp = payload["input"]
    geo = payload["geometry"]
    plan = payload["plan"]
    quote = payload["quote"]
    req = quote["requested"]

    flow = []
    flow.append(Paragraph("CNC 加工报价单 · CNC Machining Quotation", h1))
    quote_no = quote_no or f"Q{date.today():%Y%m%d}-{abs(hash(str(payload))) % 10000:04d}"
    valid_until = quote.get("valid_until")
    meta = Table(
        [
            ["报价单号 Quote No.", quote_no, "日期 Date", f"{date.today():%Y-%m-%d}"],
            ["零件 Part", inp.get("part_name", "—"), "交期 Lead", f"{quote['lead_days']} 天"],
            ["客户 Customer", inp.get("customer") or "—", "有效期 Valid until",
             valid_until or "—"],
        ],
        colWidths=[32 * mm, 56 * mm, 28 * mm, 48 * mm],
    )
    meta.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), _FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), _GREY),
                ("TEXTCOLOR", (2, 0), (2, -1), _GREY),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    flow.append(meta)
    flow.append(Spacer(1, 6 * mm))

    # ---- Specification ----
    flow.append(Paragraph("<b>规格 Specification</b>", body))
    dims = geo["dims_mm"]
    spec_rows = [
        ["材料 Material", inp["material_label"]],
        ["料价 Material price",
         f"¥{inp.get('material_price_cny_per_kg', 0):.2f}/kg · {inp.get('price_source', 'static')}"],
        ["数量 Quantity", str(req["quantity"])],
        ["表面处理 Finish", inp["finish_label"]],
        ["公差 Tolerance", "精密 Tight" if inp["tight_tolerance"] else "标准 Standard"],
        ["外形 Bounding box", f"{dims[0]:.1f} × {dims[1]:.1f} × {dims[2]:.1f} mm"],
        ["体积 Volume", f"{geo['volume_cm3']:.2f} cm³"],
        ["重量 Weight", f"{geo.get('part_weight_g', 0)/1000:.3f} kg / 件"],
        ["毛坯 Stock", f"{plan['stock']['length_mm']:.1f} × {plan['stock']['width_mm']:.1f} × {plan['stock']['height_mm']:.1f} mm"],
        ["机床 Machine", plan["machine_label"]],
        ["装夹/刀具 Setups/Tools", f"{plan['setups']} setups · {plan['tools']} tools"],
        ["单件机时 Cycle", f"{plan['times']['per_part_min']:.1f} min"],
    ]
    spec = Table(spec_rows, colWidths=[45 * mm, 119 * mm])
    spec.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), _FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), _GREY),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#e1e4e8")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    flow.append(spec)
    flow.append(Spacer(1, 5 * mm))

    # ---- Cost breakdown (per unit) ----
    flow.append(Paragraph("<b>成本构成 Cost breakdown (单件 per unit)</b>", body))
    cb_rows = [["项目 Item", "金额 Amount"]]
    if quote.get("scrap_credit_cny", 0) > 0:
        cb_rows += [
            ["材料毛重 Material gross", _money(quote["material_gross_cny"], cur)],
            ["废料抵扣 Scrap credit", "−" + _money(quote["scrap_credit_cny"], cur)],
            ["材料净费 Material net", _money(req["material_cny"], cur)],
        ]
    else:
        cb_rows.append(["材料费 Material", _money(req["material_cny"], cur)])
    cb_rows += [
        ["加工费 Machining", _money(req["machining_cny"], cur)],
        ["表面处理 Finishing", _money(req["finish_variable_cny"], cur)],
        ["编程/准备摊销 Setup (amortized)", _money(req["amortized_one_time_cny"], cur)],
        ["单件成本 Unit cost", _money(req["unit_cost_cny"], cur)],
        [f"利润率 Margin ({req['margin']*100:.0f}%) 后单价 Unit price", _money(req["unit_price_cny"], cur)],
    ]
    cb = Table(cb_rows, colWidths=[120 * mm, 44 * mm])
    cb.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), _FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), _ACCENT),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEABOVE", (0, -2), (-1, -2), 0.5, _GREY),
                ("FONTNAME", (0, -1), (-1, -1), _FONT),
                ("TEXTCOLOR", (0, -1), (-1, -1), _ACCENT),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    flow.append(cb)
    if quote.get("addons"):
        names = "、".join(a["label"] for a in quote["addons"])
        flow.append(Spacer(1, 2 * mm))
        flow.append(Paragraph(f"含质检/认证增项 QA add-ons: {names}", small))
    flow.append(Spacer(1, 3 * mm))
    flow.append(
        Paragraph(
            f"一次性费用 One-time (programming + first article + finish setup): "
            f"<b>{_money(quote['one_time_cny'], cur)}</b> — 已按数量摊销 amortized over qty.",
            small,
        )
    )
    flow.append(Spacer(1, 5 * mm))

    # ---- Delivery (lead-time) options ----
    lead_opts = quote.get("lead_time_options") or []
    if len(lead_opts) > 1:
        flow.append(Paragraph("<b>交期选项 Delivery options</b>（按所选数量 at requested qty）", body))
        lt_rows = [["交期 Lead time", "工期 Days", f"单价 Unit ({cur})", f"总价 Total ({cur})"]]
        sel_idx = 0
        for i, o in enumerate(lead_opts):
            if o.get("selected"):
                sel_idx = i + 1
            lt_rows.append([o["label"], f"{o['days']} 天",
                            _money(o["unit_price_cny"], cur), _money(o["total_cny"], cur)])
        lt = Table(lt_rows, colWidths=[58 * mm, 30 * mm, 38 * mm, 38 * mm])
        lt.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _FONT), ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), _ACCENT), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("BACKGROUND", (0, sel_idx), (-1, sel_idx), colors.Color(0.85, 0.92, 1.0)),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("GRID", (0, 0), (-1, -1), 0.4, _GREY),
        ]))
        flow.append(lt)
        flow.append(Spacer(1, 5 * mm))

    # ---- Quantity price breaks ----
    flow.append(Paragraph("<b>阶梯报价 Quantity price breaks</b>", body))
    tier_header = ["数量 Qty", "单价 Unit", "总价 Total"]
    tier_rows = [tier_header]
    for t in quote["tiers"]:
        tier_rows.append(
            [str(t["quantity"]), _money(t["unit_price_cny"], cur), _money(t["line_total_cny"], cur)]
        )
    tiers = Table(tier_rows, colWidths=[54 * mm, 55 * mm, 55 * mm])
    style = [
        ("FONTNAME", (0, 0), (-1, -1), _FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f3f6")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, _GREY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfc")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    # highlight the requested quantity row
    for i, t in enumerate(quote["tiers"], start=1):
        if t["quantity"] == req["quantity"]:
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fff8c5")))
            style.append(("FONTNAME", (0, i), (-1, i), _FONT))
    tiers.setStyle(TableStyle(style))
    flow.append(tiers)
    flow.append(Spacer(1, 5 * mm))

    # ---- Order totals for the requested quantity (incl. tax) ----
    tax_pct = quote.get("tax_rate", 0) * 100
    tot_rows = [
        [f"订单合计 Order total (×{req['quantity']})", _money(quote.get("net_total_cny", req["line_total_cny"]), cur)],
        [f"{quote.get('tax_label','税')} ({tax_pct:.0f}%)", _money(quote.get("tax_cny", 0), cur)],
        ["含税总计 Grand total", _money(quote.get("total_incl_tax_cny", req["line_total_cny"]), cur)],
    ]
    tot = Table(tot_rows, colWidths=[120 * mm, 44 * mm])
    tot.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), _FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.5, _GREY),
                ("TEXTCOLOR", (0, -1), (-1, -1), _ACCENT),
                ("FONTSIZE", (0, -1), (-1, -1), 11),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    flow.append(tot)
    flow.append(Spacer(1, 5 * mm))

    # ---- DFM findings (graded) ----
    dfm = [d for d in payload.get("dfm", []) if d.get("severity") in ("high", "medium")]
    if dfm:
        flow.append(Paragraph("<b>可加工性提示 DFM findings</b>", body))
        _sev = {"high": ("⛔ 高", colors.Color(0.85, 0.33, 0.31)),
                "medium": ("⚠ 中", colors.Color(0.88, 0.57, 0.18))}
        for d in dfm:
            tag, col = _sev[d["severity"]]
            flow.append(Paragraph(
                f'<font color="#{int(col.red*255):02x}{int(col.green*255):02x}{int(col.blue*255):02x}">'
                f'[{tag}]</font> <b>{d["title"]}</b> — {d["detail"]} <i>建议：{d["suggestion"]}</i>', small))
        flow.append(Spacer(1, 2 * mm))

    # ---- Notes ----
    notes = list(quote.get("notes", [])) + list(plan.get("notes", []))
    if notes:
        flow.append(Paragraph("<b>工艺/成本说明 Notes</b>", body))
        for n in notes:
            flow.append(Paragraph(f"• {n}", small))
    flow.append(Spacer(1, 4 * mm))
    flow.append(
        Paragraph(
            "本报价由自动报价引擎生成，仅供参考；最终以工程评审为准。"
            "Auto-generated estimate; subject to engineering review.",
            small,
        )
    )

    doc.build(flow)
    return buf.getvalue()
