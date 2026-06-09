"""Orchestration layer: geometry metrics + user params -> full quote payload.

This is the single seam the API and the PDF renderer both call, so the quote a
customer sees on screen is byte-for-byte the one in the PDF. It runs the full
funnel:

    metrics -> features.analyze -> capp.plan -> costing.price -> payload dict
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .engine import ShopData, load
from .engine import capp as capp_mod
from .engine import costing as costing_mod
from .geometry import MeshMetrics, analyze
from .geometry.features import Hole


@dataclass
class QuoteRequest:
    material: str
    quantity: int = 1
    finish: str = "none"
    tight_tolerance: bool = False
    requires_5axis: bool = False
    min_wall_mm: float | None = None
    rush: bool = False
    machine: str | None = None
    holes: list[Hole] = field(default_factory=list)
    part_name: str = ""

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
            requires_5axis=bool(p.get("requires_5axis", False)),
            min_wall_mm=(float(p["min_wall_mm"]) if p.get("min_wall_mm") else None),
            rush=bool(p.get("rush", False)),
            machine=(str(p["machine"]) if p.get("machine") else None),
            holes=holes,
            part_name=str(p.get("part_name", "")),
        )


class QuoteError(ValueError):
    """User-facing validation problem (bad material, finish, etc.)."""


def build_quote(
    metrics: MeshMetrics,
    req: QuoteRequest,
    shop: ShopData | None = None,
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

    feat = analyze(
        metrics,
        holes=req.holes,
        tight_tolerance=req.tight_tolerance,
        requires_5axis=req.requires_5axis,
        min_wall_mm=req.min_wall_mm,
    )

    plan = capp_mod.plan(feat, material, shop, machine_key=req.machine)
    quote = costing_mod.price(
        plan,
        material,
        finish,
        shop,
        quantity=req.quantity,
        tight_tolerance=req.tight_tolerance,
        rush=req.rush,
    )

    dims = metrics.dims_mm
    plan_d = plan.to_dict()
    plan_d["machine_label"] = plan.machine.label

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
        },
        "plan": plan_d,
        "quote": quote.to_dict(),
        "warnings": feat.warnings,
    }
