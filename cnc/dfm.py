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

# Standard coarse-pitch metric threads → recommended tap-drill (mm).
# (nominal Ø, pitch, tap-drill) — drill ≈ nominal − pitch.
_METRIC_TAP = [
    (2.0, 0.4, 1.6), (2.5, 0.45, 2.05), (3.0, 0.5, 2.5), (4.0, 0.7, 3.3),
    (5.0, 0.8, 4.2), (6.0, 1.0, 5.0), (8.0, 1.25, 6.8), (10.0, 1.5, 8.5),
    (12.0, 1.75, 10.2), (16.0, 2.0, 14.0),
]


def _nearest_metric_thread(d: float):
    """Closest standard metric thread to a nominal diameter (within 0.6mm)."""
    best = min(_METRIC_TAP, key=lambda t: abs(t[0] - d))
    return best if abs(best[0] - d) <= 0.6 else None


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
    holes_auto: bool = False,
    watertight: bool = True,
    tol_mm: float | None = None,
    machinability: float = 1.0,
    finish_key: str = "none",
) -> list[dict]:
    out: list[dict] = []
    dims = sorted(metrics.dims_mm)
    longest, shortest = dims[2], dims[0]
    auto = "（自动检测 auto）" if wall_auto else ""

    # ---- dimension sanity: a sub-3mm whole part is almost always a unit error ----
    # (real validation: web models in inches/metres/normalised units quoted a
    # 0.16mm 'bunny' confidently). Flag prominently with a rescale hint.
    if 0 < longest < 3.0:
        out.append(_f("high", "unit_suspect", "尺寸异常·单位疑似有误 Unit suspect",
                      f"最大尺寸仅 {longest:.2f}mm，远小于常规加工件。"
                      f"若图纸为英寸，×25.4 后约 {longest*25.4:.0f}mm。",
                      "请确认上传单位：英寸请在单位选择器选 inch，或重新导出为 mm 后再报价。"))

    if holes_auto and feat.holes:
        n = sum(h.count for h in feat.holes)
        out.append(_f("info", "holes_auto", "自动识别孔 Auto-detected holes",
                      f"从模型识别到 {n} 个孔（{len(feat.holes)} 种规格），用于钻孔工时与提示。",
                      "请核对孔数/孔径，并补充螺纹要求（无法从网格判断）。"))

    # ---- buy-to-fly: a sparse part milled from a solid block wastes metal ----
    # (machinist insight: a thin L-bracket fills ~13% of its envelope → ~87% of
    # the block becomes chips; roughing is slow and the stock cost is mostly
    # scrapped). Plate-shaped parts fill their bbox ~100%, so they don't flag.
    bbox = metrics.bbox_volume_mm3
    if bbox > 0:
        fill = metrics.volume_mm3 / bbox
        if 0 < fill < 0.18:
            out.append(_f("low", "material_removal", "高去料比 High material removal",
                          f"零件仅填充包络的 {fill*100:.0f}%，约 {(1-fill)*100:.0f}% 毛坯被切除成屑，"
                          "开粗工时与料耗偏高。",
                          "可考虑近净成形毛坯（型材/锻件/焊接件）或调整毛坯朝向以减少去料。"))

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
        if d > 0 and depth > 0:
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
        # tap-drill guidance: map a threaded hole's nominal Ø to the standard
        # metric thread and its底孔(tap-drill) — a concrete shop instruction.
        if h.threaded and d > 0:
            th = _nearest_metric_thread(d)
            if th:
                nom, pitch, drill = th
                out.append(_f("info", "tap_drill", "螺纹底孔 Tap-drill",
                              f"Ø{d:g} 螺纹孔按 M{nom:g}×{pitch:g} 处理，"
                              f"底孔钻 Ø{drill:g}mm（=公称−螺距）。",
                              "如为细牙/英制螺纹请在备注注明，底孔随之调整。"))
        # thread engagement depth: AL/软材料推荐 1.5–2×D，<1×D 螺纹强度不足
        if h.threaded and d > 0 and depth > 0:
            eng = depth / d
            if eng < 1.0:
                out.append(_f("medium", "thread_engage", "螺纹啮合深度不足 Thread engagement",
                              f"Ø{d:g} 螺纹有效深度 {depth:g}mm（约 {eng:.1f}×D），"
                              "铝/塑料推荐 1.5–2×D 以保证强度。",
                              "加深螺纹孔，或改用螺纹护套(钢丝螺套)提升承载。"))

    # ---- hard material + tapping: a broken tap scraps the part ----
    # (skill faq_thread_mill, sourced: tap torque rises steeply with material
    # hardness; thread milling is the safe choice on Ti/SS — a broken thread
    # mill doesn't scrap the part, a broken tap does.)
    n_tapped = sum(h.count for h in feat.holes if h.threaded)
    if n_tapped and machinability >= 2.5:
        out.append(_f("medium", "hard_tap", "难加工材料攻丝风险 Hard-material tapping",
                      f"该材料可加工性 {machinability:g}（钛/不锈钢级），{n_tapped} 个螺纹孔"
                      "攻丝扭矩大、断锥即报废零件。",
                      "建议改螺纹铣（断刀不报废件、可控深度），或确认允许丝锥加工并接受风险。"))

    # ---- undercut / faces unreachable from ±axis (3-axis can't reach) ----
    if not requires_5axis and feat.undercut_frac > 0.2:
        out.append(_f("medium", "undercut", "可能存在倒扣 Undercut",
                      f"约 {feat.undercut_frac*100:.0f}% 面不朝向任一主轴向，三轴难达。",
                      "考虑五轴/电极，或拆分零件、增加工序。"))

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

    # ---- standard tolerance vs ISO 2768-m for large parts ----
    # ISO 2768-m widens with size (30-120: ±0.3, 120-400: ±0.5); our flat
    # "standard ±0.1" is effectively TIGHTER than 2768-m on large parts — tell
    # the customer they may be paying for precision they didn't intend.
    if tol_mm and abs(tol_mm - 0.1) < 1e-9 and longest > 120:
        iso_m = 0.5 if longest > 120 else 0.3
        out.append(_f("info", "tol_vs_iso2768", "标准公差对大件偏紧 Tolerance vs ISO 2768",
                      f"最长边 {longest:.0f}mm 按 ISO 2768-m 一般公差为 ±{iso_m:g}，"
                      f"当前'标准 ±0.1'实际紧了 {iso_m/0.1:.0f} 倍。",
                      "若非配合面，可在备注注明按 ISO 2768-m 验收，加工与检验更宽松。"))

    # ---- tolerance feasibility vs part size ----
    # Achievable 3-axis accuracy widens with size (thermal growth, fixturing,
    # tool deflection): roughly ±(0.02 + 0.00008·L) mm. A request tighter than
    # that on a large part is hard to hold and to gauge.
    if tol_mm and tol_mm > 0 and longest > 0:
        achievable = 0.01 + 0.00008 * longest
        if tol_mm < achievable:
            out.append(_f("medium", "tol_feasibility", "公差相对尺寸偏紧 Tolerance vs size",
                          f"在最长边 {longest:.0f}mm 上要求 ±{tol_mm:g}mm，"
                          f"常规精密三轴可达约 ±{achievable:.3f}mm，难稳定保证。",
                          "放宽非关键尺寸公差，或改恒温间/精密机床并预留检测成本。"))

    # ---- anodize coating thickness vs tight tolerance (MIL-A-8625) ----
    # Type II grows ~5µm/side (often ignorable), hardcoat ~25µm/side — against a
    # ±0.02 band (40µm total) even Type II eats a quarter of it. The drawing
    # must say whether dimensions apply BEFORE or AFTER coating.
    if tol_mm and tol_mm <= 0.05 and ("anodize" in (finish_key or "")):
        grow = 25 if "hard" in finish_key else 5
        out.append(_f("medium", "finish_dim", "膜厚挤占公差带 Coating vs tolerance",
                      f"阳极膜单边生长约 {grow}µm，而 ±{tol_mm:g} 公差带总宽仅 {tol_mm*2000:g}µm，"
                      "配合面尺寸可能被膜厚吃掉。",
                      "图纸注明按'阳极前'还是'阳极后'尺寸验收；配合面/螺纹孔考虑保护(堵孔)或预留。"))

    # ---- non-watertight mesh: volume (→ material cost) less reliable ----
    if not watertight:
        out.append(_f("medium", "open_mesh", "网格非水密 Non-watertight mesh",
                      "模型非封闭，体积/重量估算可能偏差，料费仅供参考。",
                      "建议导出封闭实体（修复法线/补面）后重新报价。"))

    # ---- universal reminder: inside corners always carry an R ----
    out.append(_f("info", "inner_radius", "内角圆角 Inner radius",
                  "CNC 内壁转角受刀具直径限制必带 R 角。",
                  "如需绝对直角需 EDM 清角，成本另计。"))

    out.sort(key=lambda d: _RANK.get(d["severity"], 0), reverse=True)
    return out
