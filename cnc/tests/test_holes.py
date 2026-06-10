"""Automatic cylindrical-hole recognition + quote integration."""
from __future__ import annotations

import pytest

trimesh = pytest.importorskip("trimesh")

from cnc.geometry import Hole, metrics_from_stl_bytes  # noqa: E402
from cnc.geometry.holes import detect_holes  # noqa: E402
from cnc.service import QuoteRequest, build_quote  # noqa: E402


def _block_with_holes(specs) -> bytes:
    part = trimesh.creation.box(extents=(80, 60, 20))
    part.apply_translation((40, 30, 10))
    for (x, y, d) in specs:
        c = trimesh.creation.cylinder(radius=d / 2, height=24, sections=48)
        c.apply_translation((x, y, 10))
        part = part.difference(c)
    return part.export(file_type="stl")


def test_detects_through_holes_with_diameters():
    holes = detect_holes(_block_with_holes([(20, 30, 6), (40, 30, 10), (60, 30, 4)]))
    dias = sorted(round(h["diameter_mm"]) for h in holes)
    assert dias == [4, 6, 10]
    assert all(h["through"] for h in holes)
    assert all(abs(h["depth_mm"] - 20) < 1 for h in holes)


def test_blind_hole_not_through():
    part = trimesh.creation.box(extents=(60, 60, 20)); part.apply_translation((30, 30, 10))
    bore = trimesh.creation.cylinder(radius=4, height=16, sections=48)
    bore.apply_translation((30, 30, 16))           # blind from the top
    part = part.difference(bore)
    holes = detect_holes(part.export(file_type="stl"))
    assert len(holes) == 1 and not holes[0]["through"]
    assert round(holes[0]["diameter_mm"]) == 8


def test_solid_block_has_no_holes():
    assert detect_holes(trimesh.creation.box(extents=(50, 50, 50)).export(file_type="stl")) == []


def test_boss_is_not_a_hole():
    # a convex stud must not be mistaken for a bore
    base = trimesh.creation.box(extents=(50, 50, 10)); base.apply_translation((25, 25, 5))
    stud = trimesh.creation.cylinder(radius=8, height=20, sections=48); stud.apply_translation((25, 25, 10))
    assert detect_holes(base.union(stud).export(file_type="stl")) == []


def test_garbage_returns_empty():
    assert detect_holes(b"not an stl") == []


def test_grouping_counts_identical_holes():
    holes = detect_holes(_block_with_holes([(20, 30, 6), (60, 30, 6)]))
    assert len(holes) == 1 and holes[0]["count"] == 2     # two Ø6 → one group, count 2


# ---- quote integration ----
def test_auto_holes_drive_drilling_and_dfm():
    sb = _block_with_holes([(20, 30, 4), (40, 30, 4), (60, 30, 4)])
    q = build_quote(metrics_from_stl_bytes(sb), QuoteRequest(material="AL6061", quantity=1),
                    mesh_stl=sb, backend="analytic")
    assert q["geometry"]["holes_auto"] is True
    assert q["plan"]["times"]["drilling_min"] > 0
    assert any(d["code"] == "holes_auto" for d in q["dfm"])


def test_declared_holes_take_precedence():
    sb = _block_with_holes([(40, 30, 10)])
    q = build_quote(metrics_from_stl_bytes(sb),
                    QuoteRequest(material="AL6061", quantity=1, holes=[Hole(6.0, 12.0, 2)]),
                    mesh_stl=sb, backend="analytic")
    assert q["geometry"]["holes_auto"] is False


def test_scale_gate_applies_to_physical_size_for_inch_parts():
    # a Ø6mm hole modeled in inches (r=0.118 native) is below the 0.3mm radius
    # gate unless scale maps native→mm; with scale=25.4 it is detected.
    box = trimesh.creation.box((1.0, 0.75, 0.4)); box.apply_translation((0.5, 0.375, 0.2))
    c = trimesh.creation.cylinder(radius=0.118, height=0.6, sections=32)
    c.apply_translation((0.5, 0.375, 0.2))
    sb = box.difference(c).export(file_type="stl")
    assert detect_holes(sb, scale=1.0) == []          # treated as mm → sub-gate, dropped
    inch = detect_holes(sb, scale=25.4)               # treated as inch → passes physical gate
    # diameter is reported in native units (the caller scales it); ~0.236" here
    assert inch and 0.2 < inch[0]["diameter_mm"] < 0.27
