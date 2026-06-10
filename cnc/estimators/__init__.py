"""Pluggable time-estimation backends.

Three precision tiers share one contract (they all return an engine.ProcessPlan,
so costing/PDF never change):

  * "analytic"  — engine.capp.plan: fast V/MRR model, no deps, ~70% accuracy.
  * "toolpath"  — simulate real toolpaths with trimesh + shapely (needs a mesh).

`make_plan` picks the best available backend for the inputs and falls back
cleanly, returning (plan, info) where info records what actually ran.
"""
from __future__ import annotations

from ..engine import capp as _analytic
from ..engine.capp import ProcessPlan
from ..engine.shopdata import Material, ShopData
from ..geometry.features import FeatureSet
from . import toolpath as _toolpath

BACKENDS = ("auto", "analytic", "toolpath")


def backend_status() -> dict:
    from . import surface_ocl
    return {
        "analytic": True,
        "toolpath": _toolpath.available(),
        "surface_dropcutter": surface_ocl.available(),  # opencamlib refinement
    }


def make_plan(
    feat: FeatureSet,
    material: Material,
    shop: ShopData,
    *,
    backend: str = "auto",
    machine_key: str | None = None,
    mesh_stl: bytes | None = None,
    unit_scale: float = 1.0,
    cutting: dict | None = None,
) -> tuple[ProcessPlan, dict]:
    """Return (ProcessPlan, info). info = {requested, used, fallback_reason?}."""
    info = {"requested": backend, "used": "analytic"}

    want_toolpath = backend in ("auto", "toolpath")
    if want_toolpath and mesh_stl and _toolpath.available():
        try:
            mesh = _toolpath.load_mesh(mesh_stl, scale=unit_scale)
            plan = _toolpath.plan_toolpath(feat, material, shop, mesh, machine_key=machine_key,
                                           cutting=cutting)
            info["used"] = "toolpath"
            return plan, info
        except Exception as exc:
            info["fallback_reason"] = f"toolpath: {exc.__class__.__name__}: {exc}"
            if backend == "toolpath":
                # explicit request failed → still give analytic, but say why
                pass

    if backend == "toolpath" and not mesh_stl:
        info["fallback_reason"] = "toolpath needs a mesh (STL); none available"

    plan = _analytic.plan(feat, material, shop, machine_key=machine_key)
    info["used"] = "analytic"
    return plan, info


__all__ = ["make_plan", "backend_status", "BACKENDS", "ProcessPlan"]
