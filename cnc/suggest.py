"""Cheaper-material suggestions (à la Xometry material recommendations).

Re-prices the same geometry with other materials in the same category (that
support the chosen finish) using the fast analytic estimator, and returns the
ones meaningfully cheaper than the current pick, sorted by saving. Comparison is
apples-to-apples (all analytic) so the % saving is robust regardless of the
backend shown in the headline.
"""
from __future__ import annotations

from .engine import capp
from .engine import costing as costing_mod


def suggest_materials(feat, material, finish, shop, qty, *, max_suggestions=3,
                      min_saving=0.03) -> list[dict]:
    def unit_price(mat) -> float:
        plan = capp.plan(feat, mat, shop)
        return costing_mod.price(plan, mat, finish, shop, quantity=qty).requested.unit_price_cny

    try:
        base = unit_price(material)
    except Exception:
        return []
    if base <= 0:
        return []

    finish_key = getattr(finish, "key", "none")
    out: list[dict] = []
    for key, mat in shop.materials.items():
        if key == material.key or mat.category != material.category:
            continue
        if finish_key != "none" and finish_key not in mat.finish_ok:
            continue
        try:
            u = unit_price(mat)
        except Exception:
            continue
        if u < base * (1 - min_saving):
            out.append({"key": key, "label": mat.label, "unit_price_cny": round(u, 2),
                        "savings_pct": round((base - u) / base * 100, 1)})
    out.sort(key=lambda d: -d["savings_pct"])
    return out[:max_suggestions]


def compare_all_materials(feat, finish, shop, qty) -> list[dict]:
    """Full what-if table: every material that supports the finish, priced
    (analytic) with its key properties, cheapest first."""
    finish_key = getattr(finish, "key", "none")
    rows: list[dict] = []
    for key, mat in shop.materials.items():
        if finish_key != "none" and finish_key not in mat.finish_ok:
            continue
        try:
            plan = capp.plan(feat, mat, shop)
            u = costing_mod.price(plan, mat, finish, shop, quantity=qty).requested.unit_price_cny
        except Exception:
            continue
        rows.append({"key": key, "label": mat.label, "category": mat.category,
                     "unit_price_cny": round(u, 2), "density_g_cm3": mat.density_g_cm3,
                     "machinability": mat.machinability})
    rows.sort(key=lambda d: d["unit_price_cny"])
    return rows
