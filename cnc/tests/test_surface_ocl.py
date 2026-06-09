"""opencamlib drop-cutter surface refinement (skips if OCL/trimesh absent)."""
from __future__ import annotations

import pytest

trimesh = pytest.importorskip("trimesh")
surface_ocl = pytest.importorskip("cnc.estimators.surface_ocl")

pytestmark = pytest.mark.skipif(
    not surface_ocl.available(), reason="opencamlib not installed"
)


def test_flat_top_factor_is_one():
    box = trimesh.creation.box(extents=(60, 60, 20))
    box.apply_translation((30, 30, 10))
    factor, info = surface_ocl.curvature_factor(box, 6.0, 0.35)
    assert info["method"] == "drop-cutter"
    assert 0.99 <= factor <= 1.05         # a flat lid needs no inflation


def test_curved_top_factor_exceeds_one():
    sph = trimesh.creation.icosphere(subdivisions=3, radius=30)
    dome = sph.slice_plane(plane_origin=(0, 0, 0), plane_normal=(0, 0, 1))
    factor, info = surface_ocl.curvature_factor(dome, 6.0, 0.35)
    assert factor > 1.15                   # a dome's real path is meaningfully longer


def test_finishing_uses_dropcutter_when_available():
    from cnc.engine import load
    from cnc.geometry import analyze, metrics_from_stl_bytes
    from cnc.estimators import toolpath as tp

    shop = load()
    box = trimesh.creation.box(extents=(60, 60, 20))
    box.apply_translation((30, 30, 10))
    sb = box.export(file_type="stl")
    plan = tp.plan_toolpath(analyze(metrics_from_stl_bytes(sb)),
                            shop.material("AL6061"), shop, tp.load_mesh(sb))
    fin = next(o for o in plan.operations if o["op"] == "finishing")
    assert fin["surface_method"] == "drop-cutter"
    assert "surface_factor" in fin
