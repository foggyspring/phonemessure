"""Toolpath-simulation backend tests (skip cleanly if libs absent)."""
from __future__ import annotations

import pytest

from cnc.engine import load
from cnc.geometry import Hole, analyze, metrics_from_stl_bytes
from cnc.tests.fixtures import cube_stl

tp = pytest.importorskip("cnc.estimators.toolpath")
trimesh = pytest.importorskip("trimesh")
pytest.importorskip("shapely")


def _mesh(size=50.0):
    return tp.load_mesh(cube_stl(size))


def test_available():
    assert tp.available() is True


def test_load_mesh_dims():
    m = _mesh(50.0)
    ext = m.bounds[1] - m.bounds[0]
    assert all(abs(e - 50.0) < 1e-3 for e in ext)


def test_toolpath_plan_runs_and_is_tagged():
    shop = load()
    feat = analyze(metrics_from_stl_bytes(cube_stl(50.0)))
    plan = tp.plan_toolpath(feat, shop.material("AL6061"), shop, _mesh(50.0))
    assert plan.backend == "toolpath"
    assert plan.times.per_part_min > 0
    # operations detail captured
    ops = {o["op"] for o in plan.operations}
    assert {"roughing", "finishing", "drilling"} <= ops
    # roughing recorded real path length and Z levels
    rough = next(o for o in plan.operations if o["op"] == "roughing")
    assert rough["levels"] >= 1
    assert rough["path_len_mm"] > 0


def test_stainless_slower_than_aluminum_toolpath():
    shop = load()
    feat = analyze(metrics_from_stl_bytes(cube_stl(50.0)))
    al = tp.plan_toolpath(feat, shop.material("AL6061"), shop, _mesh(50.0))
    ss = tp.plan_toolpath(feat, shop.material("SUS304"), shop, _mesh(50.0))
    assert ss.times.per_part_min > al.times.per_part_min


def test_threaded_holes_add_tapping_toolpath():
    shop = load()
    feat = analyze(
        metrics_from_stl_bytes(cube_stl(50.0)),
        holes=[Hole(diameter_mm=6.0, depth_mm=20.0, count=4, threaded=True)],
    )
    plan = tp.plan_toolpath(feat, shop.material("AL6061"), shop, _mesh(50.0))
    assert plan.times.tapping_min > 0
    assert plan.times.drilling_min > 0


def test_make_plan_auto_uses_toolpath_with_mesh():
    from cnc import estimators
    shop = load()
    feat = analyze(metrics_from_stl_bytes(cube_stl(40.0)))
    plan, info = estimators.make_plan(
        feat, shop.material("AL6061"), shop, backend="auto", mesh_stl=cube_stl(40.0)
    )
    assert info["used"] == "toolpath"
    assert plan.backend == "toolpath"


def test_make_plan_falls_back_without_mesh():
    from cnc import estimators
    shop = load()
    feat = analyze(metrics_from_stl_bytes(cube_stl(40.0)))
    plan, info = estimators.make_plan(feat, shop.material("AL6061"), shop, backend="auto")
    assert info["used"] == "analytic"


def test_oversized_part_falls_back_fast():
    """Perf guard: a part bigger than the inline-sim cap must fall back to
    analytic (found by fuzzing — a 10m inch-misread part took 25s otherwise)."""
    import time
    from cnc import estimators
    shop = load()
    big = trimesh.creation.box(extents=(1600, 900, 500))
    big.apply_translation((800, 450, 250))
    sb = big.export(file_type="stl")
    feat = analyze(metrics_from_stl_bytes(sb))
    t0 = time.time()
    plan, info = estimators.make_plan(feat, shop.material("AL6061"), shop,
                                      backend="toolpath", mesh_stl=sb)
    assert time.time() - t0 < 3.0          # must not grind through 600 levels
    assert info["used"] == "analytic"
    assert "cap" in info.get("fallback_reason", "")


def test_pocket_roughs_more_than_solid():
    """Regression: a milled cavity must add roughing vs the same solid block.

    Earlier the cross-section unioned internal loops (filled the pocket), so a
    pocketed part reported identical roughing to the solid — this guards it.
    """
    shop = load()
    mat = shop.material("AL6061")

    solid = trimesh.creation.box(extents=(100, 60, 30))
    solid.apply_translation((50, 30, 15))
    base = trimesh.creation.box(extents=(100, 60, 30))
    base.apply_translation((50, 30, 15))
    cav = trimesh.creation.box(extents=(80, 40, 22))
    cav.apply_translation((50, 30, 19))
    pocketed = base.difference(cav)

    fs = analyze(metrics_from_stl_bytes(solid.export(file_type="stl")))
    fp = analyze(metrics_from_stl_bytes(pocketed.export(file_type="stl")))
    ps = tp.plan_toolpath(fs, mat, shop, tp.load_mesh(solid.export(file_type="stl")))
    pp = tp.plan_toolpath(fp, mat, shop, tp.load_mesh(pocketed.export(file_type="stl")))

    # pocket removes ~70 cm^3 more -> materially more roughing time + path.
    assert pp.removed_volume_cm3 > ps.removed_volume_cm3 + 30
    assert pp.times.roughing_min > ps.times.roughing_min * 1.5
    rough = next(o for o in pp.operations if o["op"] == "roughing")
    assert rough["levels"] >= 5      # cavity cleared across multiple depths
