"""Structured DFM (design-for-manufacturability) analysis.

Turns the geometry/feature signals into a list of graded findings —
{severity, code, title, detail, suggestion} — so the UI can surface
manufacturability risks like a real quoting platform (JLC / Protolabs /
RapidDirect) instead of a flat note list. Pure/​deterministic; consumes the
metrics + features + request the quote already computes.
"""
from __future__ import annotations

from .geometry import MeshMetrics
from .geometry.features import FeatureSet

# severity rank for sorting (higher = more urgent)
_RANK = {"high": 3, "medium": 2, "low": 1, "info": 0}

_THIN_WALL_MM = 1.0
_SLENDER_RATIO = 8.0


def _f(severity: str, code: str, title: str, detail: str, suggestion: str) -> dict:
    return {"severity": severity, "code": code, "title": title,
            "detail": detail, "suggestion": suggestion}


def analyze_dfm(
    metrics: MeshMetrics,
    feat: FeatureSet,
    *,
    tight_tolerance: bool = False,
    requires_5axis: bool = False,
    max_part_mm: float | None = None,
    wall_auto: bool = False,
) -> list[dict]:
    out: list[dict] = []
    dims = sorted(metrics.dims_mm)
    longest, shortest = dims[2], dims[0]
    auto = "（自动检测 auto）" if wall_auto else ""

    # ---- slender / whippy part ----
    if shortest > 0:
        ratio = longest / shortest
        if ratio > 12:
            out.append(_f("high", "slender", "细长比过大 Slender part",
                          f"长短边比 {ratio:.0f}:1，加工振动/挠曲风险高。",
                          "考虑分序、加辅助支撑或降低切深/转速。"))
        elif ratio > _SLENDER_RATIO:
            out.append(_f("medium", "slender", "细长比偏大 Slender part",
                          f"长短边比 {ratio:.0f}:1，易振动。",
                          "建议加支撑或降速精加工。"))

    # ---- thin walls ----
    mw = feat.min_wall_mm
    if mw is not None and mw > 0:
        if mw < 0.5:
            out.append(_f("high", "thin_wall", "壁厚过薄 Very thin wall",
                          f"最小壁厚 {mw:.2f}mm{auto}，极易变形/让刀。",
                          "增厚至 ≥1mm，或预留工艺夹持后再去除。"))
        elif mw < _THIN_WALL_MM:
            out.append(_f("medium", "thin_wall", "薄壁 Thin wall",
                          f"最小壁厚 {mw:.2f}mm < {_THIN_WALL_MM:.0f}mm{auto}，有变形风险。",
                          "尽量增厚或分粗精多刀轻切。"))

    # ---- holes: depth/dia ratio, small dia, small tapped ----
    for h in feat.holes:
        d, depth = h.diameter_mm, h.depth_mm
        if d > 0:
            r = depth / d
            if r > 10:
                out.append(_f("high", "deep_hole", "深径比过大 Deep hole",
                              f"Ø{d:g} 深 {depth:g}mm，深径比 {r:.0f}:1。",
                              "需枪钻/分级啄钻，或确认是否可双面加工。"))
            elif r > 4:
                out.append(_f("medium", "deep_hole", "深孔 Deep hole",
                              f"Ø{d:g} 深 {depth:g}mm，深径比 {r:.0f}:1，需啄钻。",
                              "保证排屑与冷却，工时已上调。"))
        if 0 < d < 1.0:
            out.append(_f("medium", "small_hole", "微孔 Tiny hole",
                          f"Ø{d:g}mm 钻头易断。",
                          "确认是否可放大孔径，或改激光/电火花。"))
        if h.threaded and 0 < d < 2.0:
            out.append(_f("medium", "small_tap", "细牙螺纹孔 Small tapped hole",
                          f"Ø{d:g} 螺纹孔攻丝易断丝。",
                          "确认螺距，必要时改螺纹铣。"))

    # ---- freeform / multi-axis ----
    if metrics.complexity > 0.7 or requires_5axis:
        out.append(_f("info", "multi_axis", "多轴/自由曲面 Multi-axis",
                      "复杂曲面，建议球头刀精铣或五轴联动。",
                      "确认是否需要五轴，影响装夹与编程工时。"))

    # ---- near machine envelope ----
    if max_part_mm and longest > 0.8 * max_part_mm:
        out.append(_f("medium", "envelope", "接近行程上限 Near envelope",
                      f"最大尺寸 {longest:.0f}mm 接近机床行程 {max_part_mm:.0f}mm。",
                      "确认机床行程与装夹可行性，或拆分加工。"))

    # ---- tight tolerance ----
    if tight_tolerance:
        out.append(_f("info", "tight_tol", "精密公差 Tight tolerance",
                      "精密公差需额外检测与慢走刀。",
                      "仅对关键尺寸标注紧公差以控成本。"))

    # ---- universal reminder: inside corners always carry an R ----
    out.append(_f("info", "inner_radius", "内角圆角 Inner radius",
                  "CNC 内壁转角受刀具直径限制必带 R 角。",
                  "如需绝对直角需 EDM 清角，成本另计。"))

    out.sort(key=lambda d: _RANK.get(d["severity"], 0), reverse=True)
    return out
