"""CAPP — Computer-Aided Process Planning.

Turns geometry + declared features into a simulated process plan and a time
budget, following the brief's chain:

    开粗 Roughing -> 精加工 Finishing -> 钻孔 Drilling -> 攻丝 Tapping

Plus the overheads that dominate small-batch CNC: fixturing per setup, tool
changes, and a one-time programming + first-article cost amortised over the
batch. Times are in minutes; money is added later by the costing engine.

The numbers are intentionally transparent (every line is traceable to a shop
constant in machines.json) rather than a black box — that is what lets a quote
be defended to a customer or tuned by an estimator.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..geometry.features import FeatureSet
from .shopdata import Machine, Material, ShopData


@dataclass
class Stock:
    length_mm: float
    width_mm: float
    height_mm: float
    margin_mm: float

    @property
    def volume_mm3(self) -> float:
        return self.length_mm * self.width_mm * self.height_mm


@dataclass
class TimeBreakdown:
    roughing_min: float
    finishing_min: float
    drilling_min: float
    tapping_min: float
    toolchange_min: float
    fixturing_min: float
    inspection_min: float

    @property
    def per_part_min(self) -> float:
        return (
            self.roughing_min
            + self.finishing_min
            + self.drilling_min
            + self.tapping_min
            + self.toolchange_min
            + self.fixturing_min
            + self.inspection_min
        )


@dataclass
class ProcessPlan:
    machine: Machine
    stock: Stock
    stock_volume_cm3: float
    part_volume_cm3: float
    part_area_cm2: float
    removed_volume_cm3: float
    effective_mrr_cm3_min: float
    setups: int
    tools: int
    times: TimeBreakdown
    one_time_min: float          # programming + first article, per batch
    complexity_factor: float
    notes: list[str]
    backend: str = "analytic"    # which estimator produced the cutting times
    operations: list[dict] = field(default_factory=list)  # per-op toolpath detail

    def to_dict(self) -> dict:
        d = asdict(self)
        d["machine"] = self.machine.key
        d["times"]["per_part_min"] = round(self.times.per_part_min, 2)
        d["process_steps"] = self.process_steps()
        return d

    def process_steps(self) -> list[dict]:
        """Human-readable routing card (工序卡): ordered工序 with per-part minutes.

        Lets the customer see the plan as a real shop traveller —
        下料→编程→装夹→粗铣→精铣→钻孔→攻丝→检验 — instead of a flat time blob.
        Only steps with real time are listed; one-time工序 are marked per-batch.
        """
        t = self.times
        steps: list[dict] = []

        def add(name: str, detail: str, minutes: float, *, per_batch: bool = False):
            if minutes <= 0.005:
                return
            steps.append({"step": len(steps) + 1, "name": name, "detail": detail,
                          "minutes": round(minutes, 2),
                          "scope": "每批 per-batch" if per_batch else "每件 per-part"})

        # stock prep time is bundled into material/fixturing; list it informationally
        steps.append({"step": 1, "name": "下料/备料 Stock prep",
                      "detail": f"标准板锯切至毛坯 {self.stock.length_mm:g}×"
                                f"{self.stock.width_mm:g}×{self.stock.height_mm:g}mm",
                      "minutes": 0.0, "scope": "每批 per-batch"})
        add("编程/首件 Programming & FAI",
            f"CAM 编程 + 首件检验（共 {self.tools} 把刀，{self.setups} 次装夹）",
            self.one_time_min, per_batch=True)
        add("装夹 Fixturing", f"{self.setups} 次装夹/找正", t.fixturing_min)
        add("粗铣 Roughing", f"去除余量 {self.removed_volume_cm3:g}cm³", t.roughing_min)
        add("精铣 Finishing",
            f"精加工面积 {self.part_area_cm2:g}cm² (复杂度系数 {self.complexity_factor:.2f})",
            t.finishing_min)
        add("钻孔 Drilling", "按特征孔位钻孔", t.drilling_min)
        add("攻丝 Tapping", "螺纹孔攻丝", t.tapping_min)
        add("换刀 Tool change", f"{self.tools} 把刀换刀", t.toolchange_min)
        add("检验 Inspection", "尺寸/公差检验", t.inspection_min)
        # renumber after conditional drops
        for i, s in enumerate(steps, 1):
            s["step"] = i
        return steps


def _estimate_setups(feat: FeatureSet, machine: Machine) -> int:
    """How many times the part must be re-fixtured.

    A 5-axis machine reaches most faces in one setup. On 3-axis, a roughly
    cubic / multi-face part needs a flip (top + bottom), and very non-planar
    parts may need a third orientation.
    """
    if machine.max_axes >= 5:
        return 2 if feat.metrics.complexity > 0.6 else 1
    # Prefer the mesh-derived count (faces featured per direction) when present.
    if feat.setup_count is not None:
        return feat.setup_count
    dims = sorted(feat.metrics.dims_mm)
    plate_like = dims[0] > 0 and dims[2] / dims[0] > 4.0  # thin -> likely 1-2 setups
    setups = 2
    if feat.metrics.complexity > 0.5 and not plate_like:
        setups = 3
    return setups


def _count_tools(feat: FeatureSet) -> int:
    """Distinct cutting tools -> drives tool-change time."""
    tools = 2  # a roughing endmill + a finishing endmill, always.
    tools += feat.distinct_drill_sizes          # one drill per distinct hole size
    if feat.threaded_holes:
        tools += 1                               # a tap
    if feat.metrics.complexity > _FREEFORM:
        tools += 1                               # a ball-nose for surfacing
    return tools


_FREEFORM = 0.45


def _round_up_to(x: float, sizes: list) -> float:
    """Smallest standard size >= x; if larger than all, keep x (custom stock)."""
    for s in sorted(sizes):
        if float(s) >= x:
            return float(s)
    return float(x)


def plan(
    feat: FeatureSet,
    material: Material,
    shop: ShopData,
    machine_key: str | None = None,
) -> ProcessPlan:
    capp = shop.capp
    notes: list[str] = []

    machine_key = machine_key or ("mill_5axis" if feat.requires_5axis else "mill_3axis")
    machine = shop.machine(machine_key)

    # ---- Stock (raw material block) ----
    # Real stock is bought in standard plate thicknesses, so the smallest
    # dimension (thickness) is rounded up to the next stocked plate — you pay
    # for a 60mm plate even if the part is 50mm thick. Length/width are sawn
    # from the plate, so they only carry the machining margin.
    margin = capp["stock_margin_mm"]
    dims = [d + 2 * margin for d in feat.metrics.dims_mm]
    plate = capp.get("stock_plate_mm")
    if plate:
        ti = min(range(3), key=lambda j: dims[j])      # thickness = smallest dim
        std = _round_up_to(dims[ti], plate)
        if std > dims[ti] + 1e-6:
            notes.append(f"毛坯厚度按标准板 {std:g}mm（净厚 {feat.metrics.dims_mm[ti]:.1f}mm）")
            dims[ti] = std
    stock = Stock(dims[0], dims[1], dims[2], margin)
    stock_cm3 = stock.volume_mm3 / 1000.0
    part_cm3 = feat.metrics.volume_mm3 / 1000.0
    # A part cannot out-volume its own stock; if it does, the mesh is bad
    # (non-watertight / duplicated faces / wrong winding inflate the volume).
    if part_cm3 > stock_cm3:
        notes.append("零件体积≥毛坯：图纸可能非封闭或含重复面，体积已按毛坯封顶，请核对模型")
        part_cm3 = stock_cm3
    removed_cm3 = max(stock_cm3 - part_cm3, 0.0)

    # ---- Complexity multiplier (freeform / 5-axis / thin wall / tolerance) ----
    complexity_factor = 1.0 + feat.metrics.complexity  # 1.0 .. 2.0
    if feat.requires_5axis:
        complexity_factor *= 1.5
        notes.append("五轴/复杂曲面：精加工系数 ×1.5")
    if feat.min_wall_mm is not None and 0 < feat.min_wall_mm < 1.0:
        complexity_factor *= 1.25
        notes.append("薄壁(<1mm)：难度系数 ×1.25")

    # ---- Roughing: bulk metal removal at the material-adjusted MRR ----
    eff_mrr = machine.base_mrr_cm3_min / material.machinability
    roughing_min = removed_cm3 / eff_mrr if eff_mrr > 0 else 0.0

    # ---- Finishing: scales with surface area and complexity ----
    area_cm2 = feat.metrics.area_mm2 / 100.0
    finishing_min = (
        area_cm2
        * capp["finish_pass_min_per_cm2"]
        * complexity_factor
        * material.machinability ** 0.5
    )

    # ---- Drilling & tapping ----
    drilling_min = 0.0
    tapping_min = 0.0
    for h in feat.holes:
        per_hole = (
            capp["drill_min_per_hole_base"]
            + capp["drill_min_per_mm_depth"] * h.depth_mm
        ) * material.machinability
        if h.is_deep:
            per_hole *= 1.5  # peck-drilling penalty
        drilling_min += per_hole * h.count
        if h.threaded:
            tapping_min += capp["tap_min_per_hole"] * material.machinability * h.count

    # ---- Setups, tool changes, inspection ----
    setups = _estimate_setups(feat, machine)
    tools = _count_tools(feat)
    fixturing_min = capp["fixture_min_per_setup"] * setups
    toolchange_min = capp["toolchange_min_per_tool"] * tools
    # Tolerance class drives inspection time + a cutting slowdown (the resolved
    # class is attached to the feature set; fall back to the tight-tol constants).
    tol = feat.tolerance
    if tol is not None:
        inspection_min = float(tol["inspection_min"])
        tol_factor = float(tol["machining_factor"])
    else:
        inspection_min = capp["tight_tolerance_inspection_min_per_part"] if feat.tight_tolerance else 0.0
        tol_factor = capp["tight_tolerance_machining_factor"] if feat.tight_tolerance else 1.0
    # Inspection scales with the number of controlled features (holes/bores):
    # a CMM probes each feature, so a 30-hole precision part inspects far longer
    # than a 2-hole one at the same class. Only applies when the class actually
    # demands inspection (baseline > 0); standard parts aren't fully gauged.
    if inspection_min > 0:
        per_feature = capp.get("inspection_min_per_feature", 0.3)
        n_features = sum(h.count for h in feat.holes)
        if n_features:
            extra = per_feature * n_features
            inspection_min += extra
            notes.append(f"检测随特征数叠加：{n_features} 处 × {per_feature:g}min = {extra:.1f}min")
    if tol_factor != 1.0:
        roughing_min *= tol_factor
        finishing_min *= tol_factor
        notes.append(f"{tol['label'] if tol else '精密公差'}：切削系数 ×{tol_factor}")

    # ---- Surface roughness (Ra): finer finish ⇒ slower finishing passes ----
    if feat.surface is not None and float(feat.surface["finish_factor"]) != 1.0:
        sf = float(feat.surface["finish_factor"])
        finishing_min *= sf
        notes.append(f"{feat.surface['label']}：精加工系数 ×{sf}")

    # Floor: tiny parts still cost real cycle time (load/unload/probe).
    raw_cut = roughing_min + finishing_min + drilling_min + tapping_min
    floor = capp["min_machine_min_per_part"]
    if raw_cut < floor:
        finishing_min += floor - raw_cut
        notes.append(f"小件保底机时 {floor:g}min")

    times = TimeBreakdown(
        roughing_min=roughing_min,
        finishing_min=finishing_min,
        drilling_min=drilling_min,
        tapping_min=tapping_min,
        toolchange_min=toolchange_min,
        fixturing_min=fixturing_min,
        inspection_min=inspection_min,
    )

    # ---- One-time batch overhead: programming scales with complexity ----
    one_time_min = (
        capp["programming_min_base"]
        + capp["programming_min_per_complexity"] * feat.metrics.complexity
        + capp["first_article_min"]
    )
    if feat.requires_5axis:
        one_time_min += capp["programming_min_per_complexity"]  # 5-axis CAM is slower
        notes.append("五轴编程：一次性编程工时增加")

    return ProcessPlan(
        machine=machine,
        stock=stock,
        stock_volume_cm3=round(stock_cm3, 3),
        part_volume_cm3=round(part_cm3, 3),
        part_area_cm2=round(area_cm2, 3),
        removed_volume_cm3=round(removed_cm3, 3),
        effective_mrr_cm3_min=round(eff_mrr, 2),
        setups=setups,
        tools=tools,
        times=times,
        one_time_min=round(one_time_min, 2),
        complexity_factor=round(complexity_factor, 3),
        notes=notes,
    )
