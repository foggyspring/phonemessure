"""Mesh-derived fixturing setups + undercut detection."""
from __future__ import annotations

import pytest

trimesh = pytest.importorskip("trimesh")

from cnc.geometry import metrics_from_stl_bytes  # noqa: E402
from cnc.geometry.setups import analyze_setups, estimate_setups_3axis  # noqa: E402
from cnc.service import QuoteRequest, build_quote  # noqa: E402


def _stl(m):
    return m.export(file_type="stl")


def test_plain_block_two_setups():
    s = estimate_setups_3axis(analyze_setups(_stl(trimesh.creation.box(extents=(40, 40, 40)))))
    assert s == 2                                   # top + flip, not 3


def test_thin_plate_setups_small():
    s = estimate_setups_3axis(analyze_setups(_stl(trimesh.creation.box(extents=(100, 80, 6)))))
    assert s <= 2


def test_sphere_has_undercut_and_more_setups():
    info = analyze_setups(_stl(trimesh.creation.icosphere(subdivisions=3, radius=25)))
    assert info["undercut_frac"] > 0.2
    assert estimate_setups_3axis(info) >= 3


def test_garbage_returns_none():
    assert analyze_setups(b"not stl") is None
    assert estimate_setups_3axis(None) is None


def test_quote_uses_mesh_setups_and_flags_undercut():
    sb = _stl(trimesh.creation.icosphere(subdivisions=3, radius=25))
    q = build_quote(metrics_from_stl_bytes(sb), QuoteRequest(material="AL6061", quantity=1),
                    mesh_stl=sb, backend="analytic")
    assert q["plan"]["setups"] >= 3
    assert any(d["code"] in ("undercut", "multi_axis") for d in q["dfm"])
