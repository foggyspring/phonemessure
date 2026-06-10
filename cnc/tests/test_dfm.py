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


def test_unit_suspect_on_sub_3mm_part():
    # real-world: web models in inch/metre/normalised units → a sub-3mm "part".
    m = metrics_from_stl_bytes(cube_stl(0.16))
    dfm = analyze_dfm(m, analyze(m))
    f = next((d for d in dfm if d["code"] == "unit_suspect"), None)
    assert f and f["severity"] == "high" and "25.4" in f["detail"]
    # a normal part has no unit-suspect flag
    assert not any(d["code"] == "unit_suspect" for d in analyze_dfm(
        metrics_from_stl_bytes(cube_stl(50.0)), analyze(metrics_from_stl_bytes(cube_stl(50.0)))))


def test_tolerance_feasibility_vs_part_size():
    import trimesh
    def codes(ext, tol):
        b = trimesh.creation.box(ext); b.apply_translation([e / 2 for e in ext])
        sb = b.export(file_type="stl")
        return {d["code"] for d in build_quote(metrics_from_stl_bytes(sb),
                QuoteRequest(material="AL6061", quantity=5, tolerance=tol),
                mesh_stl=sb, backend="analytic")["dfm"]}
    # ±0.02 on a 600mm part is beyond reliable 3-axis capability → flag
    assert "tol_feasibility" in codes((600, 200, 40), "ultra")
    # ±0.02 on a 40mm precision part is achievable → no flag
    assert "tol_feasibility" not in codes((40, 30, 20), "ultra")
    # loose standard tolerance never flags
    assert "tol_feasibility" not in codes((600, 200, 40), "standard")


def test_high_material_removal_flagged_for_sparse_part():
    import trimesh
    p1 = trimesh.creation.box((100, 80, 5)); p1.apply_translation((50, 40, 2.5))
    p2 = trimesh.creation.box((5, 80, 60)); p2.apply_translation((2.5, 40, 30))
    sb = p1.union(p2).export(file_type="stl")
    m = metrics_from_stl_bytes(sb)
    assert "material_removal" in {d["code"] for d in analyze_dfm(m, analyze(m))}
    # a solid block fills its envelope → no high-removal flag
    assert "material_removal" not in {d["code"] for d in analyze_dfm(
        metrics_from_stl_bytes(cube_stl(50.0)), analyze(metrics_from_stl_bytes(cube_stl(50.0))))}


def test_tap_drill_recommendation_for_metric_thread():
    m = metrics_from_stl_bytes(cube_stl(50.0))
    feat = analyze(m, holes=[Hole(diameter_mm=6.0, depth_mm=15.0, count=4, threaded=True)])
    td = [d for d in analyze_dfm(m, feat) if d["code"] == "tap_drill"]
    assert td and "5" in td[0]["detail"]            # M6 → Ø5.0 tap-drill
    # plain (non-threaded) hole gets no tap-drill note
    feat2 = analyze(m, holes=[Hole(diameter_mm=6.0, depth_mm=15.0, count=4)])
    assert not any(d["code"] == "tap_drill" for d in analyze_dfm(m, feat2))


def test_dfm_summary_headline_and_counts():
    # clean cube → ok verdict, no high/medium
    clean = build_quote(metrics_from_stl_bytes(cube_stl(50.0)),
                        QuoteRequest(material="AL6061", quantity=10))["dfm_summary"]
    assert clean["level"] == "ok" and clean["counts"]["high"] == 0
    # a thin tapped-wall part raises real flags → not ok
    risky = build_quote(metrics_from_stl_bytes(cube_stl(50.0)),
                        QuoteRequest(material="AL6061", quantity=10, min_wall_mm=0.3,
                                     holes=[Hole(diameter_mm=0.8, depth_mm=20.0, count=1, threaded=True)]))["dfm_summary"]
    assert risky["level"] in ("high", "medium")
    assert risky["counts"]["high"] + risky["counts"]["medium"] > 0


def test_thread_engagement_too_shallow():
    # blind tapped hole with < 1×D engagement → strength warning
    m = metrics_from_stl_bytes(cube_stl(50.0))
    feat = analyze(m, holes=[Hole(diameter_mm=6.0, depth_mm=4.0, count=4, threaded=True)])
    codes = {d["code"] for d in analyze_dfm(m, feat)}
    assert "thread_engage" in codes
    # adequate engagement (≥1×D) does not flag
    feat2 = analyze(m, holes=[Hole(diameter_mm=6.0, depth_mm=10.0, count=4, threaded=True)])
    assert "thread_engage" not in {d["code"] for d in analyze_dfm(m, feat2)}


def test_non_watertight_mesh_flagged():
    # ~30% of real web meshes are non-watertight → volume/material cost less
    # reliable. Drop two faces from a box to make an open mesh.
    import trimesh
    from cnc.service import QuoteRequest, build_quote
    box = trimesh.creation.box((40, 40, 40)); box.apply_translation((20, 20, 20))
    open_mesh = trimesh.Trimesh(vertices=box.vertices, faces=box.faces[:-2], process=False)
    assert not open_mesh.is_watertight
    sb = open_mesh.export(file_type="stl")
    q = build_quote(metrics_from_stl_bytes(sb), QuoteRequest(material="AL6061", quantity=10),
                    mesh_stl=sb, backend="analytic")
    assert any(d["code"] == "open_mesh" for d in q["dfm"])
    assert any("非水密" in r for r in q["confidence"]["reasons"])


def test_standard_tolerance_flagged_as_tight_for_large_parts():
    # per ISO 2768-m a 300mm part's general tolerance is ±0.5 — our flat
    # "standard ±0.1" is 5x tighter, so the customer deserves a heads-up.
    import trimesh
    big = trimesh.creation.box((300, 100, 30)); big.apply_translation((150, 50, 15))
    sb = big.export(file_type="stl")
    q = build_quote(metrics_from_stl_bytes(sb),
                    QuoteRequest(material="AL6061", quantity=5, tolerance="standard"),
                    mesh_stl=sb, backend="analytic")
    assert any(d["code"] == "tol_vs_iso2768" for d in q["dfm"])
    # small parts (where ±0.1 ≈ 2768-m) must NOT nag
    small = build_quote(metrics_from_stl_bytes(cube_stl(50.0)),
                        QuoteRequest(material="AL6061", quantity=5, tolerance="standard"))
    assert not any(d["code"] == "tol_vs_iso2768" for d in small["dfm"])


def test_hard_material_tapping_warns_thread_milling():
    holes = [Hole(diameter_mm=6.0, depth_mm=12.0, count=8, threaded=True)]
    ti = build_quote(metrics_from_stl_bytes(cube_stl(50.0)),
                     QuoteRequest(material="TITANIUM_TC4", quantity=2, holes=holes))
    assert any(d["code"] == "hard_tap" for d in ti["dfm"])
    # aluminium tapping is routine — no warning
    al = build_quote(metrics_from_stl_bytes(cube_stl(50.0)),
                     QuoteRequest(material="AL6061", quantity=2, holes=holes))
    assert not any(d["code"] == "hard_tap" for d in al["dfm"])
    # hard material WITHOUT threads — no warning either
    ti2 = build_quote(metrics_from_stl_bytes(cube_stl(50.0)),
                      QuoteRequest(material="TITANIUM_TC4", quantity=2))
    assert not any(d["code"] == "hard_tap" for d in ti2["dfm"])
