"""Automatic wall-thickness detection (shot-ray) + quote integration."""
from __future__ import annotations

import time

import pytest

trimesh = pytest.importorskip("trimesh")

from cnc.geometry import metrics_from_stl_bytes  # noqa: E402
from cnc.geometry.thickness import estimate_min_wall_mm  # noqa: E402
from cnc.service import QuoteRequest, build_quote  # noqa: E402


def _stl(m) -> bytes:
    return m.export(file_type="stl")


def test_solid_block_is_full_thickness():
    w = estimate_min_wall_mm(_stl(trimesh.creation.box(extents=(50, 50, 50))))
    assert w is not None and 45.0 < w <= 50.5


def test_thin_plate_detected():
    w = estimate_min_wall_mm(_stl(trimesh.creation.box(extents=(80, 60, 1.5))))
    assert w is not None and abs(w - 1.5) < 0.3


def test_hollow_box_wall_detected():
    h = (trimesh.creation.box(extents=(50, 50, 50))
         .difference(trimesh.creation.box(extents=(46, 46, 46))))
    w = estimate_min_wall_mm(_stl(h))
    assert w is not None and abs(w - 2.0) < 0.5


def test_garbage_and_tiny_return_none():
    assert estimate_min_wall_mm(b"not an stl at all") is None


def test_large_mesh_is_bounded():
    s = trimesh.creation.icosphere(subdivisions=4, radius=30)   # ~5k faces
    t0 = time.time()
    w = estimate_min_wall_mm(_stl(s))
    assert time.time() - t0 < 6.0
    assert w is not None and w > 40            # solid sphere ≈ diameter


def test_face_cap_skips_huge_mesh():
    s = trimesh.creation.icosphere(subdivisions=4, radius=30)
    assert estimate_min_wall_mm(_stl(s), max_faces=100) is None   # over cap → skip


# ---- quote integration ----
def test_auto_wall_flags_thin_wall_in_quote():
    h = (trimesh.creation.box(extents=(50, 50, 50))
         .difference(trimesh.creation.box(extents=(48.4, 48.4, 48.4))))   # 0.8mm wall
    sb = _stl(h)
    q = build_quote(metrics_from_stl_bytes(sb), QuoteRequest(material="AL6061", quantity=1),
                    mesh_stl=sb, backend="analytic")
    assert q["geometry"]["min_wall_auto"] is True
    assert q["geometry"]["min_wall_mm"] < 1.0
    assert any(d["code"] == "thin_wall" for d in q["dfm"])


def test_solid_part_no_false_thin_wall():
    sb = _stl(trimesh.creation.box(extents=(50, 50, 50)))
    q = build_quote(metrics_from_stl_bytes(sb), QuoteRequest(material="AL6061", quantity=1),
                    mesh_stl=sb, backend="analytic")
    assert not any(d["code"] == "thin_wall" for d in q["dfm"])


def test_declared_wall_takes_precedence_over_auto():
    sb = _stl(trimesh.creation.box(extents=(50, 50, 50)))
    q = build_quote(metrics_from_stl_bytes(sb),
                    QuoteRequest(material="AL6061", quantity=1, min_wall_mm=0.4),
                    mesh_stl=sb, backend="analytic")
    assert q["geometry"]["min_wall_auto"] is False
    assert q["geometry"]["min_wall_mm"] == pytest.approx(0.4)


def test_inch_units_scale_auto_wall():
    # a 1.5-unit-thick plate read as inches → ~38mm wall (scaled), not thin
    sb = _stl(trimesh.creation.box(extents=(40, 30, 1.5)))
    q = build_quote(metrics_from_stl_bytes(sb),
                    QuoteRequest(material="AL6061", quantity=1, units="inch"),
                    mesh_stl=sb, backend="analytic")
    assert q["geometry"]["min_wall_mm"] == pytest.approx(1.5 * 25.4, abs=8.0)
    assert not any(d["code"] == "thin_wall" for d in q["dfm"])
