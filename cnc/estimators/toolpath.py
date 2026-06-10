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
# Above this, sectioning a high-poly (usually organic-scan) mesh at every Z level
# is too slow for an inline request — real validation hit 8–18 s on 50–170 k-face
# scans. Such parts aren't prismatically machinable anyway, so fall back to the
# fast analytic estimate and keep the request bounded.
_MAX_FACES_TOOLPATH = 60_000
_MAX_PART_MM = 1200.0     # above this, inline toolpath sim is too slow -> analytic
_MAX_OFFSET_PASSES = 400  # bound pocket-clearing passes; extrapolate the rest


class ToolpathUnavailable(RuntimeError):
    """trimesh/shapely missing, or no mesh supplied."""


# Per-material feeds/speeds an operator may maintain at runtime.
_CUTTING_OVERRIDABLE = {
    "vc_rough", "fz_rough", "vc_finish", "fz_finish", "rough_stepdown_mm",
    "finish_stepdown_mm", "vc_drill", "fz_drill", "vc_tap",
}
# Global cycle parameters (tools section) — drill/peck/tap cycle knobs that the
# panel maintains under the pseudo-key "tools" (kind="cutting", key="tools").
_CUTTING_GLOBAL_OVERRIDABLE = {
    "rapid_mm_min", "retract_mm", "plunge_feed_frac", "hole_approach_s",
    "peck_trigger_ratio", "peck_depth_ratio", "deep_feed_derate",
    "peck_overhead_s", "point_allowance_ratio", "tap_pitch_fallback_ratio",
}


def _load_cutting(overrides: dict | None = None) -> dict:
    """Handbook feeds/speeds from JSON, with explicit runtime overrides applied.

    Pure of persistence: the caller (API layer) supplies the override dict —
    the estimator no longer reads SQLite behind the planner's back (which was
    both a layering violation and a hidden DB hit on every quote).
    """
    cut = json.loads(_CUTTING_PATH.read_text("utf-8"))
    for mat, fields in (overrides or {}).items():
        if mat == "tools":                       # global cycle params
            for f, v in fields.items():
                if f in _CUTTING_GLOBAL_OVERRIDABLE:
                    try:
                        cut["tools"][f] = float(v)
                    except (TypeError, ValueError):
                        continue
        elif mat in cut.get("materials", {}):
            for f, v in fields.items():
                if f in _CUTTING_OVERRIDABLE:
                    try:
                        cut["materials"][mat][f] = float(v)
                    except (TypeError, ValueError):
                        continue
    return cut


def _mat_cut(cutting: dict, key: str) -> dict:
    return cutting["materials"].get(key, cutting["default"])


def _feed(vc_m_min: float, fz_mm: float, dia_mm: float, teeth: int) -> float:
    """Table feed (mm/min) = fz × teeth × RPM, RPM = Vc·1000/(π·D)."""
    rpm = vc_m_min * 1000.0 / (math.pi * max(dia_mm, 0.1))
    return fz_mm * teeth * rpm


def _drill_feed(raw: dict, hole_dia_mm: float) -> float:
    """Drill feed (mm/min) for a given hole — smaller drills spin faster."""
    rpm = raw["vc_drill"] * 1000.0 / (math.pi * max(hole_dia_mm, 0.5))
    return raw["fz_drill"] * rpm


def _derive(cutting: dict, key: str) -> tuple[dict, dict]:
    """Turn Vc/fz handbook data + tool geometry into the feeds/dims the
    simulators consume. Feeds respond to the actual tool diameter."""
    raw = _mat_cut(cutting, key)
    t = cutting["tools"]
    Dr, zr = t["rough"]["diameter_mm"], t["rough"]["teeth"]
    Df, zf = t["finish"]["diameter_mm"], t["finish"]["teeth"]
    rough_feed = _feed(raw["vc_rough"], raw["fz_rough"], Dr, zr)
    finish_feed = _feed(raw["vc_finish"], raw["fz_finish"], Df, zf)
    cut = {
        "rough_feed_mm_min": rough_feed,
        "finish_feed_mm_min": finish_feed,
        "rough_stepdown_mm": raw["rough_stepdown_mm"],
        "finish_stepdown_mm": raw["finish_stepdown_mm"],
        "plunge_feed_mm_min": rough_feed * t.get("plunge_feed_frac", 0.35),
        "vc_tap": raw.get("vc_tap", 10.0),
        "vc_drill": raw["vc_drill"], "fz_drill": raw["fz_drill"],
    }
    tools = {
        "rough_endmill_d_mm": Dr, "rough_stepover_frac": t["rough"]["stepover_frac"],
        "finish_endmill_d_mm": Df, "finish_stepover_mm": t["finish"]["stepover_mm"],
        "rapid_mm_min": max(float(t["rapid_mm_min"]), 1.0),
        "retract_mm": max(float(t["retract_mm"]), 0.0),
        # Clamp the cycle knobs at the single derivation point: an operator typo
        # (e.g. a negative derate) must never produce NEGATIVE cutting time —
        # same precedent as the scrap_credit_frac clamp in costing.
        "hole_approach_s": max(float(t.get("hole_approach_s", 4.0)), 0.0),
        "peck_trigger_ratio": max(float(t.get("peck_trigger_ratio", 3.0)), 0.0),
        "peck_depth_ratio": max(float(t.get("peck_depth_ratio", 1.0)), 0.01),
        "deep_feed_derate": min(max(float(t.get("deep_feed_derate", 0.75)), 0.05), 1.0),
        "peck_overhead_s": max(float(t.get("peck_overhead_s", 0.4)), 0.0),
        "point_allowance_ratio": min(max(float(t.get("point_allowance_ratio", 0.3)), 0.0), 5.0),
        "tap_pitch_fallback_ratio": min(max(float(t.get("tap_pitch_fallback_ratio", 0.15)), 0.01), 1.0),
    }
    return cut, tools


# --------------------------------------------------------------------------
def _chain_loops(segs, tol: float = 1e-6) -> list:
    """Chain raw 2D section segments into closed loops (vectorized endpoint
    matching). mesh_multiplane hands back unordered (n,2,2) segments; quantize
    endpoints to vertex ids, then walk the degree-2 graph. Open chains (broken
    sections on non-watertight meshes) are dropped, mirroring the old behaviour
    of a failed section at that level."""
    import numpy as np

    pts = segs.reshape(-1, 2)
    q = np.round(pts / tol).astype(np.int64)
    uniq, first_idx, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
    coord = pts[first_idx]                       # representative coordinate per vertex id
    a, b = inv[0::2], inv[1::2]                  # per-segment endpoint vertex ids
    n = len(a)
    deg = np.zeros(len(uniq), dtype=np.int32)
    inc = np.full((len(uniq), 2), -1, dtype=np.int64)
    for i in range(n):
        for v in (a[i], b[i]):
            if deg[v] < 2:
                inc[v, deg[v]] = i
            deg[v] += 1
    used = np.zeros(n, dtype=bool)
    loops = []
    for start in range(n):
        if used[start]:
            continue
        used[start] = True
        ids = [a[start], b[start]]
        while True:
            v = ids[-1]
            nxt = -1
            for s in inc[v]:
                if s >= 0 and not used[s]:
                    nxt = s
                    break
            if nxt < 0:
                break
            used[nxt] = True
            ids.append(b[nxt] if a[nxt] == v else a[nxt])
            if ids[-1] == ids[0]:
                break
        if len(ids) >= 4 and ids[-1] == ids[0]:
            loops.append(coord[ids[:-1]])
    return loops


def _section_polys_batch(mesh, zs) -> list:
    """Solid cross-sections at every height in *zs*, in ONE multiplane pass.

    Replaces per-level mesh.section() calls: trimesh built a full Path3D (graph
    traversals, scipy sparse) per level, ~5 ms each — the dominant cost of the
    whole toolpath sim. mesh_multiplane computes all planes in a single sweep;
    we chain its raw segments ourselves and keep the same even-odd fill
    (chained symmetric difference), so pockets/cavities still read as voids.
    Returns a list aligned with zs; entries are shapely geometry or None.
    """
    import numpy as np
    from shapely.geometry import Polygon

    try:
        import trimesh
        lines, _, _ = trimesh.intersections.mesh_multiplane(
            mesh, plane_origin=(0, 0, 0), plane_normal=(0, 0, 1),
            heights=np.asarray(zs, dtype=float))
    except Exception:
        return [None] * len(zs)
    out = []
    for segs in lines:
        if segs is None or len(segs) == 0:
            out.append(None)
            continue
        acc = None
        for loop in _chain_loops(segs):
            p = Polygon(loop)
            if not p.is_valid:
                p = p.buffer(0)
            if p.is_empty or p.area <= 1e-6:
                continue
            acc = p if acc is None else acc.symmetric_difference(p)
        out.append(acc if (acc is not None and not acc.is_empty) else None)
    return out


def _offset_pass_length(region, stepover: float) -> float:
    """Total length of concentric inward-offset passes that clear *region*.

    This is exactly how a CAM fills a pocket: repeatedly offset the boundary
    inward by the stepover until nothing remains, summing each ring's perimeter.
    """
    if region is None or region.is_empty or stepover <= 0:
        return 0.0
    total = 0.0
    k = 0.5
    while k < _MAX_OFFSET_PASSES:
        ring = region.buffer(-stepover * k, join_style=2)
        if ring.is_empty:
            return total
        total += ring.length
        k += 1.0
    # Hit the pass cap (very large pocket): extrapolate the remaining area as
    # straight passes (area / stepover) instead of buffering thousands of rings.
    rem = region.buffer(-stepover * k, join_style=2)
    if not rem.is_empty:
        total += rem.area / stepover
    return total


def _stock_rect(bounds, margin: float):
    from shapely.geometry import box
    (x0, y0, _), (x1, y1, _) = bounds[0], bounds[1]
    return box(x0 - margin, y0 - margin, x1 + margin, y1 + margin)


# --------------------------------------------------------------------------
def _simulate_roughing(mesh, bounds, margin, cut, tools) -> tuple[float, dict]:
    stock = _stock_rect(bounds, margin)
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
    zcs = [z_top - (i + 0.5) * dz for i in range(n)]
    sections = _section_polys_batch(mesh, zcs)
    # Deep-pocket tracking (skill faq_corner_pocket: cavity depth ≤ 4× width is
    # the DFM guideline): interior rings of the even-odd section ARE the
    # pockets — track the narrowest one across consecutive levels for free.
    pocket_levels = 0
    pocket_min_w = float("inf")
    for part in sections:
        rings = []
        if part is not None and not part.is_empty:
            polys = getattr(part, "geoms", [part])
            for poly in polys:
                rings.extend(getattr(poly, "interiors", []))
        if rings:
            pocket_levels += 1
            for r in rings:
                x0, y0, x1, y1 = r.bounds
                pocket_min_w = min(pocket_min_w, max(min(x1 - x0, y1 - y0), 1e-6))
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
    if pocket_levels and pocket_min_w < float("inf"):
        depth = pocket_levels * dz
        if pocket_min_w > 0 and depth / pocket_min_w > 4.0:
            detail["deep_pocket"] = {"depth_mm": round(depth, 1),
                                     "min_width_mm": round(pocket_min_w, 1),
                                     "ratio": round(depth / pocket_min_w, 1)}
    return minutes, detail


def _simulate_finishing(mesh, bounds, cut, tools) -> tuple[float, dict]:
    feed = cut["finish_feed_mm_min"]
    stepdown = max(cut["finish_stepdown_mm"], 0.05)
    stepover = max(tools["finish_stepover_mm"], 0.05)

    z_top = bounds[1][2]
    z_bot = bounds[0][2]
    height = max(z_top - z_bot, 0.0)

    # Waterline walls: perimeter at each fine Z step (one multiplane pass).
    n = min(_MAX_LEVELS, max(1, math.ceil(height / stepdown)))
    dz = height / n if n else 0.0
    wall_len = 0.0
    footprint_area = 0.0
    zcs = [z_bot + (i + 0.5) * dz for i in range(n)]
    for part in _section_polys_batch(mesh, zcs):
        if part is None or part.is_empty:
            continue
        wall_len += part.length
        footprint_area = max(footprint_area, part.area)

    # Parallel raster over the top footprint (flat projection), then inflate by
    # the real surface slope/curvature measured with opencamlib drop-cutter.
    raster_flat = footprint_area / stepover if stepover > 0 else 0.0
    factor, surf_info = 1.0, {"method": "flat"}
    try:
        from . import surface_ocl
        if surface_ocl.available():
            factor, surf_info = surface_ocl.curvature_factor(
                mesh, tools["finish_endmill_d_mm"], stepover
            )
    except Exception as exc:  # never let surfacing break a quote
        surf_info = {"method": "flat", "reason": f"{exc.__class__.__name__}"}
    surface_len = raster_flat * factor

    minutes = (wall_len + surface_len) / feed
    detail = {
        "op": "finishing",
        "strategy": "waterline walls + drop-cutter surface"
                    if surf_info.get("method") == "drop-cutter"
                    else "waterline walls + parallel raster",
        "levels": n, "wall_len_mm": round(wall_len, 1),
        "raster_len_mm": round(surface_len, 1), "surface_factor": round(factor, 3),
        "surface_method": surf_info.get("method"), "feed_mm_min": feed,
        "minutes": round(minutes, 2),
    }
    return minutes, detail


def _tap_pitch(dia_mm: float, fallback_ratio: float = 0.15) -> float:
    """Coarse metric pitch for a tapped hole — from the standard thread table
    (shared with DFM's tap-drill advice), else a coarse-series approximation."""
    from ..dfm import _nearest_metric_thread
    th = _nearest_metric_thread(dia_mm)
    if th:
        return th[1]
    return min(2.5, max(0.5, fallback_ratio * dia_mm))


def _simulate_drilling(feat: FeatureSet, cut: dict, tools: dict) -> tuple[float, float, dict]:
    approach_min = tools.get("hole_approach_s", 4.0) / 60.0
    drill_min = 0.0
    tap_min = 0.0
    total_depth = 0.0
    for h in feat.holes:
        # diameter-aware drill feed (smaller drills spin faster).
        drill_feed = _drill_feed(cut, h.diameter_mm)
        # 118° point must travel past the nominal depth to cut full diameter:
        # point height = D/2·tan(31°) ≈ point_allowance_ratio·D.
        travel = h.depth_mm + tools["point_allowance_ratio"] * h.diameter_mm
        # G83 practice (Haas/CNCCookbook): peck when depth > 3-4×D, peck depth
        # Q ≈ 1×D. Shallow holes are a single plunge.
        rapid = max(tools.get("rapid_mm_min", 24000.0), 1.0)
        trigger = tools["peck_trigger_ratio"] * h.diameter_mm
        if h.depth_mm > trigger and h.diameter_mm > 0:
            # G83: peck depth Q ≈ peck_depth_ratio×D; each peck retracts fully
            # and re-approaches AT RAPID (air moves), plus a stop/reverse accel
            # allowance per peck. Handbook depth derating beyond the trigger.
            peck_depth = max(tools["peck_depth_ratio"] * h.diameter_mm, 0.1)
            pecks = max(1, math.ceil(travel / peck_depth))
            in_time = (trigger / drill_feed
                       + (travel - trigger) / (tools["deep_feed_derate"] * drill_feed))
            air = 2.0 * peck_depth * pecks * (pecks + 1) / 2.0 / rapid
            peck_overhead = air + pecks * (tools["peck_overhead_s"] / 60.0)
        else:
            # single plunge: no mid-cycle repositioning, just the final retract at rapid
            in_time = travel / drill_feed
            peck_overhead = travel / rapid
        per = approach_min + in_time + peck_overhead    # position+spot, cut, pecks
        drill_min += per * h.count
        total_depth += h.depth_mm * h.count
        if h.threaded:
            # Rigid tapping: feed is geometry-locked to pitch×RPM (not a free
            # parameter) — only the tapping SPEED varies by material.
            pitch = _tap_pitch(h.diameter_mm, tools["tap_pitch_fallback_ratio"])
            rpm = cut.get("vc_tap", 10.0) * 1000.0 / (math.pi * max(h.diameter_mm, 0.5))
            tap_feed = max(rpm * pitch, 1e-6)
            tap_min += (approach_min + 2.0 * (h.depth_mm / tap_feed)) * h.count
    detail = {
        "op": "drilling", "holes": feat.total_holes, "threaded": feat.threaded_holes,
        "total_depth_mm": round(total_depth, 1),
        "drill_minutes": round(drill_min, 2), "tap_minutes": round(tap_min, 2),
    }
    return drill_min, tap_min, detail


# --------------------------------------------------------------------------
def load_mesh(stl_bytes: bytes, scale: float = 1.0):
    """Load STL bytes into a trimesh.Trimesh (raises ToolpathUnavailable).

    *scale* converts the file's units to mm (e.g. 25.4 for an inch STL), so the
    toolpath sim runs in the same mm space as the rest of the pipeline.
    """
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
    if len(mesh.faces) > _MAX_FACES_TOOLPATH:
        # too detailed for inline sectioning → analytic (bounds request latency)
        raise ToolpathUnavailable(f"high-poly mesh ({len(mesh.faces)} faces) → analytic")
    if scale and scale != 1.0:
        mesh.apply_scale(scale)
    ext = mesh.bounds[1] - mesh.bounds[0]
    if float(max(ext)) > _MAX_PART_MM:
        raise ToolpathUnavailable(
            f"part {float(max(ext)):.0f}mm exceeds {_MAX_PART_MM:.0f}mm inline-sim cap"
        )
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
    cut, tools = _derive(cutting, material.key)
    margin = shop.capp["stock_margin_mm"]
    bounds = mesh.bounds  # ((x0,y0,z0),(x1,y1,z1)) in world coords

    ops: list[dict] = []
    notes = list(base.notes)

    try:
        rough_min, d = _simulate_roughing(mesh, bounds, margin, cut, tools); ops.append(d)
        if d.get("deep_pocket"):
            dp = d["deep_pocket"]
            notes.append(f"深腔：腔深 {dp['depth_mm']:g}mm ≈ {dp['ratio']:g}×最窄宽 "
                         f"{dp['min_width_mm']:g}mm（>4:1，标准刀具难达，需加长刀/EDM/双面）")
    except Exception as exc:
        rough_min = base.times.roughing_min
        notes.append(f"开粗仿真失败，回退解析值（{exc.__class__.__name__}）")
    try:
        finish_min, d = _simulate_finishing(mesh, bounds, cut, tools); ops.append(d)
    except Exception:
        finish_min = base.times.finishing_min
    try:
        drill_min, tap_min, d = _simulate_drilling(feat, cut, tools); ops.append(d)
        # Hard materials (machinability ≥2.5) are quoted as THREAD MILLING in
        # the analytic backend; apply the same factor here so the two backends
        # agree on what a Ti/SS thread costs.
        if tap_min and material.machinability >= 2.5:
            tap_min *= shop.capp.get("thread_mill_factor", 1.6)
    except Exception:
        # a zero feed override (vc_drill/vc_tap=0) must not abort the whole plan
        drill_min, tap_min = base.times.drilling_min, base.times.tapping_min

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
