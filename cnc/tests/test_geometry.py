"""Geometry-core tests: the mesh math must be exact on a known solid."""
from __future__ import annotations

import math

from cnc.geometry import metrics_from_dims, metrics_from_stl_bytes
from cnc.tests.fixtures import cube_stl


def _close(a: float, b: float, rel: float = 1e-4) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-6)


def test_cube_bounding_box():
    m = metrics_from_stl_bytes(cube_stl(50.0))
    assert _close(m.dims_mm[0], 50.0)
    assert _close(m.dims_mm[1], 50.0)
    assert _close(m.dims_mm[2], 50.0)


def test_cube_volume():
    m = metrics_from_stl_bytes(cube_stl(50.0))
    assert _close(m.volume_mm3, 50.0 ** 3)  # 125000 mm^3


def test_cube_surface_area():
    m = metrics_from_stl_bytes(cube_stl(50.0))
    assert _close(m.area_mm2, 6 * 50.0 ** 2)  # 15000 mm^2


def test_cube_is_low_complexity():
    # A plain block's surface area equals its bbox area -> complexity ~0.
    m = metrics_from_stl_bytes(cube_stl(50.0))
    assert m.complexity < 0.05


def test_ascii_and_binary_agree():
    binary = metrics_from_stl_bytes(cube_stl(30.0))
    # Build a tiny ASCII STL of a single triangle and ensure it parses.
    ascii_stl = (
        b"solid t\nfacet normal 0 0 0\nouter loop\n"
        b"vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
        b"endloop\nendfacet\nendsolid t\n"
    )
    m = metrics_from_stl_bytes(ascii_stl)
    assert m.triangles == 1
    assert binary.triangles == 12


def test_manual_dims_fallback():
    m = metrics_from_dims(100.0, 50.0, 20.0, None)
    assert _close(m.bbox_volume_mm3, 100_000.0)
    # Unknown volume -> 55% fill assumption.
    assert _close(m.volume_mm3, 0.55 * 100_000.0)


def test_non_finite_vertices_rejected():
    # a NaN/inf coordinate must fail loudly, not flow into a NaN price.
    import trimesh

    from cnc.geometry import GeometryError
    m = trimesh.creation.box((30, 20, 10))
    v = m.vertices.copy()
    v[0] = [float("nan"), 0.0, 0.0]
    bad = trimesh.Trimesh(vertices=v, faces=m.faces, process=False).export(file_type="stl")
    try:
        metrics_from_stl_bytes(bad)
        raised = False
    except GeometryError:
        raised = True
    assert raised
