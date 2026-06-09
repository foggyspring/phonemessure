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
