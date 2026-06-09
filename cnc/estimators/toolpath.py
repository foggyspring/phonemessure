"""High-precision time estimator by *simulating real toolpaths*.

Instead of the analytic V/MRR scalar, this backend reuses the same open-source
geometry kernels a CAM system uses and integrates the resulting toolpath length
against real feed rates (from data/cutting.json):

  * Roughing  — Z-level pocket clearing. At each depth step we section the part
    (trimesh) and subtract it from the stock rectangle (shapely) to get the
    region to clear, then generate concentric offset passes (shapely buffer =
    the core operation of every 2.5D pocketing CAM) and sum their length.
  * Finishing — waterline passes on the walls (part cross-section perimeter at a
    fine Z step) plus a parallel raster over the top footprint.
  * Drilling  — real peck-drill cycle time per declared hole.

cycle_time = Σ path_length / feed + plunge/retract/rapid links.

The result is mapped back onto the same ProcessPlan the analytic backend
produces, so costing/PDF are unchanged. Any per-operation failure (non-
watertight mesh, weird section) falls back to the analytic time for that op, so
a quote is always produced.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from ..engine import capp as _analytic
from ..engine.capp import ProcessPlan
from ..engine.shopdata import Material, ShopData
from ..geometry.features import FeatureSet

_CUTTING_PATH = Path(__file__).resolve().parent.parent / "data" / "cutting.json"

# Hard caps so a pathological/huge mesh can't hang the request.
_MAX_LEVELS = 600
_MAX_FACES = 400_000


class ToolpathUnavailable(RuntimeError):
    """trimesh/shapely missing, or no mesh supplied."""


def _load_cutting() -> dict:
    return json.loads(_CUTTING_PATH.read_text("utf-8"))


def _mat_cut(cutting: dict, key: str) -> dict:
    return cutting["materials"].get(key, cutting["default"])


# --------------------------------------------------------------------------
def _section_polys(mesh, z):
    """Return a shapely geometry of the part's solid cross-section at height z.

    Built from the section's discrete loops in world XY (so it lines up with the
    stock rectangle). Internal loops (holes) are unioned in — i.e. holes read as
    solid, which is correct here: holes are drilled, not roughed.
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    try:
        sec = mesh.section(plane_origin=(0, 0, z), plane_normal=(0, 0, 1))
    except Exception:
        sec = None
    if sec is None:
        return None
    polys = []
    for loop in sec.discrete:               # each loop: Nx3 closed polyline
        xy = loop[:, :2]
        if len(xy) >= 3:
            p = Polygon(xy)
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty and p.area > 1e-6:
                polys.append(p)
    if not polys:
        return None
    return unary_union(polys)


def _offset_pass_length(region, stepover: float) -> float:
    """Total length of concentric inward-offset passes that clear *region*.

    This is exactly how a CAM fills a pocket: repeatedly offset the boundary
    inward by the stepover until nothing remains, summing each ring's perimeter.
    """
    if region is None or region.is_empty or stepover <= 0:
        return 0.0
    total = 0.0
    k = 0.5
    while k < 5000:
        ring = region.buffer(-stepover * k, join_style=2)
        if ring.is_empty:
            break
        total += ring.length
        k += 1.0
    return total


def _stock_rect(bounds, margin: float):
    from shapely.geometry import box
    (x0, y0, _), (x1, y1, _) = bounds[0], bounds[1]
    return box(x0 - margin, y0 - margin, x1 + margin, y1 + margin)


# --------------------------------------------------------------------------
def _simulate_roughing(mesh, bounds, margin, cut, tools) -> tuple[float, dict]:
    stock = _stock_rect(bounds, margin)
    stock_area = stock.area
    stepdown = max(cut["rough_stepdown_mm"], 0.1)
    stepover = max(tools["rough_endmill_d_mm"] * tools["rough_stepover_frac"], 0.5)
    feed = cut["rough_feed_mm_min"]
    plunge_feed = cut["plunge_feed_mm_min"]
    rapid = tools["rapid_mm_min"]
    retract = tools["retract_mm"]

    z_top = bounds[1][2] + margin
    z_bot = bounds[0][2] - margin
    n = min(_MAX_LEVELS, max(1, math.ceil((z_top - z_bot) / stepdown)))
    dz = (z_top - z_bot) / n

    path_len = 0.0
    cut_time = 0.0
    link_time = 0.0
    levels = 0
    for i in range(n):
        zc = z_top - (i + 0.5) * dz
        part = _section_polys(mesh, zc)
        clear = stock if part is None else stock.difference(part)
        if clear.is_empty:
            continue
        L = _offset_pass_length(clear, stepover)
        if L <= 0:
            continue
        path_len += L
        cut_time += L / feed
        # one plunge per level + a rapid reposition allowance
        link_time += (stepdown + retract) / plunge_feed + (2 * retract) / rapid
        levels += 1

    minutes = cut_time + link_time
    detail = {
        "op": "roughing", "strategy": "Z-level pocket (shapely offset)",
        "levels": levels, "path_len_mm": round(path_len, 1),
        "feed_mm_min": feed, "minutes": round(minutes, 2),
    }
    return minutes, detail


def _simulate_finishing(mesh, bounds, cut, tools) -> tuple[float, dict]:
    feed = cut["finish_feed_mm_min"]
    stepdown = max(cut["finish_stepdown_mm"], 0.05)
    stepover = max(tools["finish_stepover_mm"], 0.05)

    z_top = bounds[1][2]
    z_bot = bounds[0][2]
    height = max(z_top - z_bot, 0.0)

    # Waterline walls: perimeter at each fine Z step.
    n = min(_MAX_LEVELS, max(1, math.ceil(height / stepdown)))
    dz = height / n if n else 0.0
    wall_len = 0.0
    footprint_area = 0.0
    for i in range(n):
        zc = z_bot + (i + 0.5) * dz
        part = _section_polys(mesh, zc)
        if part is None or part.is_empty:
            continue
        wall_len += part.length
        footprint_area = max(footprint_area, part.area)

    # Parallel raster over the top footprint (flat/shallow top surfaces).
    raster_len = footprint_area / stepover if stepover > 0 else 0.0

    minutes = (wall_len + raster_len) / feed
    detail = {
        "op": "finishing", "strategy": "waterline walls + parallel raster",
        "levels": n, "wall_len_mm": round(wall_len, 1),
        "raster_len_mm": round(raster_len, 1), "feed_mm_min": feed,
        "minutes": round(minutes, 2),
    }
    return minutes, detail


def _simulate_drilling(feat: FeatureSet, cut: dict) -> tuple[float, float, dict]:
    drill_feed = cut["drill_feed_mm_min"]
    tap_feed = cut["tap_feed_mm_min"]
    drill_min = 0.0
    tap_min = 0.0
    total_depth = 0.0
    for h in feat.holes:
        # peck cycle: drill in at drill_feed, retract on each peck (~3×dia depth).
        peck = max(1, math.ceil(h.depth_mm / max(3.0 * h.diameter_mm, 1e-3)))
        in_time = h.depth_mm / drill_feed
        retract_time = peck * (h.depth_mm / peck) / (cut["plunge_feed_mm_min"]) * 0.4
        per = in_time + retract_time
        drill_min += per * h.count
        total_depth += h.depth_mm * h.count
        if h.threaded:
            # tap in and out at the tapping feed.
            tap_min += 2.0 * (h.depth_mm / tap_feed) * h.count
    detail = {
        "op": "drilling", "holes": feat.total_holes, "threaded": feat.threaded_holes,
        "total_depth_mm": round(total_depth, 1),
        "drill_minutes": round(drill_min, 2), "tap_minutes": round(tap_min, 2),
    }
    return drill_min, tap_min, detail


# --------------------------------------------------------------------------
def load_mesh(stl_bytes: bytes):
    """Load STL bytes into a trimesh.Trimesh (raises ToolpathUnavailable)."""
    try:
        import io
        import trimesh
    except Exception as exc:  # pragma: no cover
        raise ToolpathUnavailable(f"trimesh not installed: {exc}") from exc
    try:
        mesh = trimesh.load(io.BytesIO(stl_bytes), file_type="stl")
    except Exception as exc:
        raise ToolpathUnavailable(f"could not load mesh: {exc}") from exc
    if mesh.is_empty or len(mesh.faces) == 0:
        raise ToolpathUnavailable("empty mesh")
    if len(mesh.faces) > _MAX_FACES:
        raise ToolpathUnavailable(f"mesh too large ({len(mesh.faces)} faces)")
    return mesh


def available() -> bool:
    try:
        import shapely  # noqa: F401
        import trimesh  # noqa: F401
        return True
    except Exception:
        return False


def plan_toolpath(
    feat: FeatureSet,
    material: Material,
    shop: ShopData,
    mesh,
    machine_key: str | None = None,
    cutting: dict | None = None,
) -> ProcessPlan:
    """Produce a ProcessPlan whose cutting times come from toolpath simulation.

    Reuses the analytic backend for everything that isn't a cut (stock, setups,
    tool count, fixturing, programming), then overrides roughing/finishing/
    drilling/tapping with simulated minutes.
    """
    if not available():
        raise ToolpathUnavailable("shapely/trimesh not available")

    base = _analytic.plan(feat, material, shop, machine_key=machine_key)
    cutting = cutting or _load_cutting()
    cut = _mat_cut(cutting, material.key)
    tools = cutting["tools"]
    margin = shop.capp["stock_margin_mm"]
    bounds = mesh.bounds  # ((x0,y0,z0),(x1,y1,z1)) in world coords

    ops: list[dict] = []
    notes = list(base.notes)

    try:
        rough_min, d = _simulate_roughing(mesh, bounds, margin, cut, tools); ops.append(d)
    except Exception as exc:
        rough_min = base.times.roughing_min
        notes.append(f"开粗仿真失败，回退解析值（{exc.__class__.__name__}）")
    try:
        finish_min, d = _simulate_finishing(mesh, bounds, cut, tools); ops.append(d)
    except Exception:
        finish_min = base.times.finishing_min
    drill_min, tap_min, d = _simulate_drilling(feat, cut); ops.append(d)

    # Apply the same risk multipliers the analytic backend uses, for consistency.
    if feat.tight_tolerance:
        f = shop.capp["tight_tolerance_machining_factor"]
        rough_min *= f
        finish_min *= f
    if feat.requires_5axis:
        # 3-axis sim under-models multi-axis surfacing; lift finishing.
        finish_min *= 1.5
        notes.append("五轴曲面：3轴仿真上调精加工 ×1.5（近似）")

    # Small-part floor (load/unload/probe still costs time).
    raw = rough_min + finish_min + drill_min + tap_min
    floor = shop.capp["min_machine_min_per_part"]
    if raw < floor:
        finish_min += floor - raw

    base.times.roughing_min = round(rough_min, 3)
    base.times.finishing_min = round(finish_min, 3)
    base.times.drilling_min = round(drill_min, 3)
    base.times.tapping_min = round(tap_min, 3)
    base.backend = "toolpath"
    base.operations = ops
    notes.append("工时来自刀路仿真（trimesh 分层 + shapely 偏置）")
    base.notes = notes
    return base
