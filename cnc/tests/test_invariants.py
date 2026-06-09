"""Regression + property invariants surfaced by the fuzz harness.

Two specific bugs the 250-case fuzz run found and we fixed:
  1. displayed total must equal rounded unit price × quantity (no sub-cent drift)
  2. a part can never out-volume its own stock (degenerate/duplicated meshes)
Plus a small seeded property loop over random shapes (skips without trimesh).
"""
from __future__ import annotations

import math

import pytest

from cnc.engine import load
from cnc.geometry import analyze, metrics_from_stl_bytes
from cnc.geometry.mesh import MeshMetrics
from cnc.service import QuoteRequest, build_quote
from cnc.tests.fixtures import cube_stl


# ---- regression 1: quote arithmetic reconciles at every quantity ----------
def test_line_total_equals_unit_times_qty():
    for qty in (1, 7, 25, 250, 999):
        q = build_quote(metrics_from_stl_bytes(cube_stl(50.0)),
                        QuoteRequest(material="AL6061", quantity=qty))
        r = q["quote"]["requested"]
        assert abs(r["line_total_cny"] - round(r["unit_price_cny"], 2) * qty) < 0.011
        for t in q["quote"]["tiers"]:
            assert abs(t["line_total_cny"] - round(t["unit_price_cny"], 2) * t["quantity"]) < 0.011


# ---- regression 2: part volume can't exceed stock (bad mesh) ---------------
def test_part_volume_clamped_to_stock():
    # craft metrics whose volume is physically impossible for its bbox
    # (a 50mm cube's stock ~175.6 cm³, but claim 300 cm³ of material).
    bad = MeshMetrics(
        triangles=0, bbox_min=(0, 0, 0), bbox_max=(50, 50, 50),
        volume_mm3=300_000.0, area_mm2=15_000.0,
    )
    q = build_quote(bad, QuoteRequest(material="AL6061", quantity=1))
    pl = q["plan"]
    assert pl["part_volume_cm3"] <= pl["stock_volume_cm3"] + 1e-6
    assert pl["removed_volume_cm3"] >= 0.0
    assert any("毛坯" in n for n in pl["notes"])   # warned about the bad model


# ---- property loop over random shapes (needs trimesh) ---------------------
trimesh = pytest.importorskip("trimesh")


def _shapes(rng):
    import trimesh as tm
    k = rng.choice(["box", "cyl", "sphere", "pocket", "plate"])
    if k == "box":
        m = tm.creation.box(extents=(rng.uniform(20, 150), rng.uniform(20, 120), rng.uniform(8, 80)))
    elif k == "cyl":
        m = tm.creation.cylinder(radius=rng.uniform(8, 50), height=rng.uniform(10, 100), sections=32)
    elif k == "sphere":
        m = tm.creation.icosphere(subdivisions=2, radius=rng.uniform(10, 45))
    elif k == "plate":
        m = tm.creation.box(extents=(rng.uniform(60, 160), rng.uniform(60, 160), rng.uniform(2, 5)))
    else:
        b = tm.creation.box(extents=(100, 70, 40))
        p = tm.creation.box(extents=(70, 45, 26)); p.apply_translation((0, 0, 8))
        try:
            m = b.difference(p)
        except Exception:
            m = b
    m.apply_translation(-m.bounds[0] + 1.0)
    return m


def test_property_invariants_random_shapes():
    import random
    shop = load()
    mats = list(shop.materials)
    rng = random.Random(20240607)
    for i in range(24):
        m = _shapes(rng)
        stl = m.export(file_type="stl")
        mat = rng.choice(mats)
        fin = rng.choice(shop.materials[mat].finish_ok)
        qty = rng.choice([1, 5, 25, 100])
        backend = rng.choice(["auto", "analytic"])
        q = build_quote(
            metrics_from_stl_bytes(stl),
            QuoteRequest(material=mat, quantity=qty, finish=fin),
            shop, mesh_stl=stl, backend=backend,
        )
        r = q["quote"]["requested"]
        pl = q["plan"]
        assert math.isfinite(r["unit_price_cny"]) and r["unit_price_cny"] > 0
        assert r["unit_price_cny"] >= r["unit_cost_cny"] - 1e-6
        assert pl["times"]["per_part_min"] > 0
        assert pl["part_volume_cm3"] <= pl["stock_volume_cm3"] + 1e-3
        ups = [t["unit_price_cny"] for t in q["quote"]["tiers"]]
        assert all(ups[j] <= ups[j - 1] + 1e-6 for j in range(1, len(ups)))
