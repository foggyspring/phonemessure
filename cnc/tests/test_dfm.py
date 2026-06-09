"""Structured DFM analysis."""
from __future__ import annotations

from cnc.dfm import analyze_dfm
from cnc.geometry import Hole, metrics_from_stl_bytes
from cnc.geometry.features import analyze
from cnc.service import QuoteRequest, build_quote
from cnc.tests.fixtures import cube_stl

_RANK = {"high": 3, "medium": 2, "low": 1, "info": 0}


def test_flags_thin_wall_deep_and_small_holes():
    m = metrics_from_stl_bytes(cube_stl(50.0))
    feat = analyze(m, holes=[Hole(diameter_mm=0.8, depth_mm=20.0, count=1, threaded=True)],
                   min_wall_mm=0.4)
    dfm = analyze_dfm(m, feat, tight_tolerance=True)
    codes = {d["code"] for d in dfm}
    assert {"thin_wall", "deep_hole", "small_hole", "small_tap", "tight_tol", "inner_radius"} <= codes
    # every finding carries an actionable suggestion
    assert all(d["suggestion"] for d in dfm)


def test_findings_sorted_by_severity():
    m = metrics_from_stl_bytes(cube_stl(50.0))
    feat = analyze(m, holes=[Hole(diameter_mm=0.5, depth_mm=18.0, count=1)], min_wall_mm=0.3)
    dfm = analyze_dfm(m, feat)
    ranks = [_RANK[d["severity"]] for d in dfm]
    assert ranks == sorted(ranks, reverse=True)
    assert dfm[0]["severity"] == "high"


def test_clean_cube_only_info():
    m = metrics_from_stl_bytes(cube_stl(50.0))
    dfm = analyze_dfm(m, analyze(m))
    assert dfm and all(d["severity"] == "info" for d in dfm)
    assert any(d["code"] == "inner_radius" for d in dfm)


def test_thin_wall_high_vs_medium():
    m = metrics_from_stl_bytes(cube_stl(50.0))
    high = analyze_dfm(m, analyze(m, min_wall_mm=0.3))
    med = analyze_dfm(m, analyze(m, min_wall_mm=0.8))
    assert next(d for d in high if d["code"] == "thin_wall")["severity"] == "high"
    assert next(d for d in med if d["code"] == "thin_wall")["severity"] == "medium"


def test_dfm_present_in_quote_payload():
    q = build_quote(metrics_from_stl_bytes(cube_stl(50.0)),
                    QuoteRequest(material="AL6061", quantity=1, min_wall_mm=0.4))
    assert isinstance(q["dfm"], list) and q["dfm"]
    assert any(d["code"] == "thin_wall" for d in q["dfm"])
