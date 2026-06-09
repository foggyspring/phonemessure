"""Orchestration layer: geometry metrics + user params -> full quote payload.

This is the single seam the API and the PDF renderer both call, so the quote a
customer sees on screen is byte-for-byte the one in the PDF. It runs the full
funnel:

    metrics -> features.analyze -> capp.plan -> costing.price -> payload dict
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import estimators
from .calibration import factor_for
from .dfm import analyze_dfm
from .engine import ShopData, load
from .engine import costing as costing_mod
from .geometry import MeshMetrics, analyze
from .geometry.features import Hole


@dataclass
class QuoteRequest:
    material: str
    quantity: int = 1
    finish: str = "none"
    tight_tolerance: bool = False
    tolerance: str | None = None     # 标准/精密/超精 tolerance class key
    surface_finish: str | None = None  # 表面粗糙度 Ra class key
    requires_5axis: bool = False
    min_wall_mm: float | None = None
    rush: bool = False
    lead_time: str | None = None     # 经济/标准/加急/特急 tier key
    machine: str | None = None
    holes: list[Hole] = field(default_factory=list)
    part_name: str = ""
    units: str = "mm"          # "mm" | "inch" — unit of the uploaded geometry
    customer: str = ""         # optional customer / project for the quote header

    @classmethod
    def from_payload(cls, p: dict) -> "QuoteRequest":
        holes = [
            Hole(
                diameter_mm=float(h["diameter_mm"]),
                depth_mm=float(h["depth_mm"]),
                count=int(h.get("count", 1)),
                threaded=bool(h.get("threaded", False)),
            )
            for h in p.get("holes", [])
            if float(h.get("diameter_mm", 0)) > 0
        ]
        return cls(
            material=str(p["material"]),
            quantity=max(1, int(p.get("quantity", 1))),
            finish=str(p.get("finish", "none")),
            tight_tolerance=bool(p.get("tight_tolerance", False)),
            tolerance=(str(p["tolerance"]) if p.get("tolerance") else None),
            surface_finish=(str(p["surface_finish"]) if p.get("surface_finish") else None),
            requires_5axis=bool(p.get("requires_5axis", False)),
            min_wall_mm=(float(p["min_wall_mm"]) if p.get("min_wall_mm") else None),
            rush=bool(p.get("rush", False)),
            lead_time=(str(p["lead_time"]) if p.get("lead_time") else None),
            machine=(str(p["machine"]) if p.get("machine") else None),
            holes=holes,
            part_name=str(p.get("part_name", "")),
            units=("inch" if str(p.get("units", "mm")).lower() in ("inch", "in") else "mm"),
            customer=str(p.get("customer", "")),
        )


class QuoteError(ValueError):
    """User-facing validation problem (bad material, finish, etc.)."""


def build_quote(
    metrics: MeshMetrics,
    req: QuoteRequest,
    shop: ShopData | None = None,
    *,
    mesh_stl: bytes | None = None,
    backend: str = "auto",
    price_sources: dict | None = None,
    calibration_factors: dict | None = None,
) -> dict:
    shop = shop or load()

    try:
        material = shop.material(req.material)
    except KeyError as exc:
        raise QuoteError(str(exc)) from exc
    try:
        finish = shop.finish(req.finish)
    except KeyError as exc:
        raise QuoteError(str(exc)) from exc

    if req.finish not in material.finish_ok:
        raise QuoteError(
            f"表面处理 '{finish.label}' 不适用于材料 '{material.label}'。"
            f"可选: {', '.join(material.finish_ok)}"
        )

    # Units: STL/IGES carry no unit, so honour the user's choice (mm | inch) and
    # scale the geometry to mm. Catches the classic 25.4x inch-as-mm blunder.
    unit_scale = 25.4 if req.units == "inch" else 1.0
    if unit_scale != 1.0:
        metrics = metrics.scaled(unit_scale)

    # Reject parts beyond any real machining envelope — almost always a wrong
    # unit / scale (e.g. an inch file read as mm becomes metres), and avoids
    # emitting an absurd million-minute quote.
    max_dim = max(metrics.dims_mm)
    max_part = float(shop.business.get("max_part_mm", 0) or 0)
    if max_part and max_dim > max_part:
        raise QuoteError(
            f"零件最大尺寸 {max_dim:.0f}mm 超出可加工范围（{max_part:.0f}mm），"
            f"请确认图纸单位或缩放。Part exceeds machinable envelope."
        )

    # Min wall: use the declared value, else auto-detect from the mesh (shot-ray
    # thickness) so DFM/cost flag thin walls even when nothing was declared.
    min_wall = req.min_wall_mm
    auto_wall = None
    if min_wall is None and mesh_stl is not None:
        from .geometry.thickness import estimate_min_wall_mm
        raw = estimate_min_wall_mm(mesh_stl)          # in the mesh's native units
        auto_wall = round(raw * unit_scale, 3) if raw is not None else None
        min_wall = auto_wall

    # Holes: use declared, else auto-recognise cylindrical bores from the mesh
    # so drilling cost + deep/small-hole DFM work without a manual declaration.
    holes = req.holes
    holes_auto = False
    detected_holes: list[dict] = []
    if not holes and mesh_stl is not None:
        from .geometry.holes import detect_holes
        detected_holes = detect_holes(mesh_stl)
        if detected_holes:
            holes = [Hole(diameter_mm=h["diameter_mm"] * unit_scale,
                          depth_mm=h["depth_mm"] * unit_scale,
                          count=h["count"], threaded=False) for h in detected_holes]
            holes_auto = True

    # Resolve the tolerance class (标准/精密/超精). tight_tolerance is the legacy
    # alias for the first non-standard class.
    tol_classes = shop.business.get("tolerance_classes") or []
    tol_key = req.tolerance or (
        next((t["key"] for t in tol_classes if t["margin_bonus"] > 0), "precision")
        if req.tight_tolerance else shop.business.get("default_tolerance", "standard"))
    tol = next((t for t in tol_classes if t["key"] == tol_key),
               tol_classes[0] if tol_classes else None)

    feat = analyze(
        metrics,
        holes=holes,
        tight_tolerance=bool(tol and tol["margin_bonus"] > 0) or req.tight_tolerance,
        requires_5axis=req.requires_5axis,
        min_wall_mm=min_wall,
    )
    feat.tolerance = tol
    surf_classes = shop.business.get("surface_classes") or []
    surf_key = req.surface_finish or shop.business.get("default_surface", "standard")
    feat.surface = next((s for s in surf_classes if s["key"] == surf_key),
                        surf_classes[0] if surf_classes else None)

    # Mesh-derived fixturing setups + undercut fraction (refines the bbox guess).
    if mesh_stl is not None:
        from .geometry.setups import analyze_setups, estimate_setups_3axis
        si = analyze_setups(mesh_stl)
        if si:
            feat.setup_dirs = si["setup_dirs"]
            feat.setup_count = estimate_setups_3axis(si)
            feat.undercut_frac = si["undercut_frac"]

    # Suspiciously tiny part — likely an inch drawing read as mm.
    if max_dim < 3.0:
        feat.warnings.insert(0, f"零件最大尺寸仅 {max_dim:.2f}mm，疑似单位有误（英寸图纸？），请确认单位。")

    plan, backend_info = estimators.make_plan(
        feat, material, shop,
        backend=backend, machine_key=req.machine, mesh_stl=mesh_stl,
        unit_scale=unit_scale,
    )

    # Calibration: scale the estimate by the factor learned from real cycle
    # times for this material (own factor → global → 1.0). Applied before
    # costing so both the shown time and the price reflect it.
    cal_factor, cal_n = factor_for(calibration_factors, material.key, backend_info.get("used"))
    if cal_factor != 1.0:
        t = plan.times
        for attr in ("roughing_min", "finishing_min", "drilling_min", "tapping_min",
                     "toolchange_min", "fixturing_min", "inspection_min"):
            setattr(t, attr, round(getattr(t, attr) * cal_factor, 3))
        plan.notes.append(f"工时按历史实测校准 ×{cal_factor} (n={cal_n})")

    quote = costing_mod.price(
        plan,
        material,
        finish,
        shop,
        quantity=req.quantity,
        tight_tolerance=req.tight_tolerance,
        rush=req.rush,
        lead_time=req.lead_time,
        tolerance_margin_bonus=(float(tol["margin_bonus"]) if tol else None),
        tolerance_label=(tol["label"] if tol else None),
    )

    dims = metrics.dims_mm
    plan_d = plan.to_dict()
    plan_d["machine_label"] = plan.machine.label
    plan_d["calibration"] = {"factor": cal_factor, "n": cal_n}

    return {
        "input": {
            "part_name": req.part_name or "part",
            "material": material.key,
            "material_label": material.label,
            "finish": finish.key,
            "finish_label": finish.label,
            "quantity": req.quantity,
            "tight_tolerance": req.tight_tolerance,
            "requires_5axis": feat.requires_5axis,
            "rush": req.rush,
            "units": req.units,
            "customer": req.customer,
            "material_price_cny_per_kg": material.price_cny_per_kg,
            "price_source": (price_sources or {}).get(material.key, "static"),
            "tolerance": tol["label"] if tol else None,
            "surface_finish": feat.surface["label"] if feat.surface else None,
            "holes": [
                {
                    "diameter_mm": h.diameter_mm,
                    "depth_mm": h.depth_mm,
                    "count": h.count,
                    "threaded": h.threaded,
                }
                for h in req.holes
            ],
        },
        "geometry": {
            "dims_mm": [round(dims[0], 3), round(dims[1], 3), round(dims[2], 3)],
            "volume_cm3": round(metrics.volume_mm3 / 1000.0, 3),
            "area_cm2": round(metrics.area_mm2 / 100.0, 3),
            "bbox_fill_pct": (
                round(100.0 * metrics.volume_mm3 / metrics.bbox_volume_mm3, 1)
                if metrics.bbox_volume_mm3 > 0
                else None
            ),
            "complexity": round(metrics.complexity, 3),
            "triangles": metrics.triangles,
            # Finished-part weight (clamped part volume × density) for logistics.
            "part_weight_g": round(plan.part_volume_cm3 * material.density_g_cm3, 1),
            "stock_weight_g": round(plan.stock_volume_cm3 * material.density_g_cm3, 1),
            "min_wall_mm": round(min_wall, 3) if min_wall is not None else None,
            "min_wall_auto": auto_wall is not None,
            "holes_auto": holes_auto,
            "holes_detected": [
                {**h, "diameter_mm": round(h["diameter_mm"] * unit_scale, 2),
                 "depth_mm": round(h["depth_mm"] * unit_scale, 2)} for h in detected_holes
            ],
        },
        "plan": plan_d,
        "quote": quote.to_dict(),
        "warnings": feat.warnings,
        "dfm": analyze_dfm(metrics, feat, tight_tolerance=req.tight_tolerance,
                           requires_5axis=req.requires_5axis, max_part_mm=max_part or None,
                           wall_auto=auto_wall is not None, holes_auto=holes_auto),
        "estimator": backend_info,
    }
