"""Knowledge ↔ engine consistency lock (会议5 决议).

The skill library carries sourced professional facts; the engine carries rules
and parameters. Meetings 1-4 aligned them — this suite keeps them aligned:
if either side drifts (someone edits an engine threshold or a skill's numeric
fact), a test goes red and forces the two to be reconciled deliberately.
"""
from __future__ import annotations

import re
from pathlib import Path

from cnc.ai.skills import load_skills
from cnc.dfm import _METRIC_TAP
from cnc.engine import load
from cnc.geometry.features import Hole

_DOC = Path(__file__).resolve().parent.parent.parent / "doc" / "skill-knowledge-sources.md"


def _skill(key: str) -> dict:
    return next(s for s in load_skills() if s["key"] == key)


def test_tap_drill_table_matches_knowledge_skill():
    # dfm's metric tap table and the faq_tap_drill narrative must agree:
    # drill = nominal − pitch for the common coarse threads.
    resp = _skill("faq_tap_drill")["response"]
    table = {nom: drill for nom, _pitch, drill in _METRIC_TAP}
    for nom, drill in ((3.0, 2.5), (4.0, 3.3), (5.0, 4.2), (6.0, 5.0)):
        assert abs(table[nom] - drill) < 1e-9          # engine table
        assert f"Ø{drill:g}" in resp                   # skill narrative
    for nom, pitch, drill in _METRIC_TAP:
        assert abs((nom - pitch) - drill) < 0.26       # drill ≈ nominal − pitch


def test_deep_hole_thresholds_match_knowledge():
    # faq_deep_hole: >3-4×D needs pecking, >10:1 is gun-drill territory.
    resp = _skill("faq_deep_hole")["response"]
    assert ">3-4" in resp and "10:1" in resp
    # engine: Hole.is_deep flips above 4×D (peck surcharge)
    assert not Hole(diameter_mm=6, depth_mm=24).is_deep      # = 4× exactly
    assert Hole(diameter_mm=6, depth_mm=25).is_deep          # > 4×
    # dfm escalates to HIGH (gun drill) above 10:1 — assert via the rule source
    dfm_src = (Path(__file__).resolve().parent.parent / "dfm.py").read_text()
    assert "r > 10" in dfm_src and "r > 4" in dfm_src


def test_reaming_parameter_exists_for_precision_holes():
    # faq_ream: H7 needs a reamer pass — the engine must carry a maintainable
    # per-hole reaming time and it must be a sane magnitude (0.2..10 min).
    capp = load().capp
    assert 0.2 <= float(capp["ream_min_per_hole"]) <= 10.0
    from cnc.engine.shopdata import _CAPP_OVERRIDABLE
    assert "ream_min_per_hole" in _CAPP_OVERRIDABLE     # operator-maintainable


def test_anodize_finishes_match_coating_knowledge():
    # faq_anodize_dim: hardcoat exists, is outsourced (lead days), and costs
    # more than Type II; both anodize kinds carry outsourcing lead time
    # (faq_finish says外协 adds days).
    fins = load().finishes
    assert "hard_anodize" in fins
    assert fins["hard_anodize"].lead_days >= fins["anodize_clear"].lead_days > 0
    assert fins["hard_anodize"].per_dm2_cny > fins["anodize_clear"].per_dm2_cny
    # hardcoat is aluminum-territory: allowed on AL, not on plastics
    mats = load().materials
    assert "hard_anodize" in mats["AL6061"].finish_ok
    assert "hard_anodize" not in mats["POM"].finish_ok


def test_thin_wall_threshold_matches_knowledge():
    # faq_thin_wall advises ≥1mm (steel) / 0.5mm floor — dfm warns below 1.0
    # and escalates below 0.5.
    from cnc.dfm import _THIN_WALL_MM
    assert _THIN_WALL_MM == 1.0
    resp = _skill("faq_thin_wall")["response"]
    assert "1mm" in resp and "0.5mm" in resp


def test_tolerance_class_labels_match_iso2768_narrative():
    # the standard class the engine prices (±0.1) is the figure the tolerance
    # FAQ talks about; assumptions reference ISO 2768-m (meeting 1).
    classes = {t["key"]: t for t in load().business["tolerance_classes"]}
    assert abs(classes["standard"]["tol_mm"] - 0.1) < 1e-9
    assert "±0.1" in _skill("faq_tolerance")["response"]
    assert "ISO 2768" in _skill("faq_iso2768")["response"]


def test_every_knowledge_skill_is_traceable_in_sources_doc():
    # no "unsourced knowledge" may slip into the default library: every
    # builtin knowledge skill key must appear in the sources doc.
    doc = _DOC.read_text("utf-8")
    missing = [s["key"] for s in load_skills()
               if s["kind"] == "knowledge" and s["builtin"] and s["key"] not in doc]
    assert not missing, f"知识技能缺溯源记录: {missing}"


def test_knowledge_numeric_facts_are_internally_consistent():
    # spot-check sourced numbers that also appear in engine behavior notes
    resp = _skill("faq_metrology")["response"]
    assert "10:1" in resp and "千分尺" in resp
    resp = _skill("faq_fits")["response"]
    assert "H7/g6" in resp
    # counterbore quick table: cb dia ≈ 1.5× nominal for the quoted sizes
    resp = _skill("faq_counterbore")["response"]
    for nom, cb in ((3, 6.5), (4, 8), (5, 9.5), (6, 11), (8, 14)):
        assert f"Ø{cb:g}" in resp, (nom, cb)
        assert cb >= 1.5 * nom - 0.6


def test_tapping_feed_is_geometry_locked_to_pitch():
    # Rigid-tapping physics: feed MUST equal pitch × RPM — it is not a free
    # parameter. The sim's M6 tap time must match the closed-form exactly.
    import math

    from cnc.estimators.toolpath import (
        _derive,
        _load_cutting,
        _simulate_drilling,
        _tap_pitch,
    )
    from cnc.geometry import metrics_from_stl_bytes
    from cnc.geometry.features import analyze
    from cnc.tests.fixtures import cube_stl
    cut, tools = _derive(_load_cutting(), "AL6061")
    m = metrics_from_stl_bytes(cube_stl(50.0))
    dia, depth = 6.0, 12.0
    pitch = _tap_pitch(dia)
    assert pitch == 1.0                                  # M6 coarse
    rpm = cut["vc_tap"] * 1000.0 / (math.pi * dia)
    expect = tools["hole_approach_s"] / 60.0 + 2.0 * depth / (rpm * pitch)
    _, tap, _ = _simulate_drilling(
        analyze(m, holes=[Hole(diameter_mm=dia, depth_mm=depth, count=1, threaded=True)]),
        cut, tools)
    assert abs(tap - expect) < 1e-9
    # and the material speeds follow the tap charts' ordering: Al ≫ SS ≫ Ti
    mats = _load_cutting()["materials"]
    assert mats["AL6061"]["vc_tap"] > mats["SUS304"]["vc_tap"] > mats["TITANIUM_TC4"]["vc_tap"]


def test_both_backends_agree_hard_threads_are_thread_milled():
    # the analytic backend prices Ti threads with thread_mill_factor; the
    # toolpath backend must apply the same factor (they previously disagreed).
    from cnc.engine import load as _load
    from cnc.estimators import toolpath as tp
    from cnc.geometry import metrics_from_stl_bytes
    from cnc.geometry.features import analyze
    from cnc.tests.fixtures import cube_stl
    shop = _load()
    holes = [Hole(diameter_mm=6.0, depth_mm=12.0, count=4, threaded=True)]
    feat = analyze(metrics_from_stl_bytes(cube_stl(50.0)), holes=holes)
    mesh = tp.load_mesh(cube_stl(50.0))
    ti = tp.plan_toolpath(feat, shop.material("TITANIUM_TC4"), shop, mesh)
    # strip the factor → must be smaller than the quoted tapping time
    factor = shop.capp["thread_mill_factor"]
    assert factor > 1.0
    al = tp.plan_toolpath(feat, shop.material("AL6061"), shop, mesh)
    # Ti tapping must exceed Al by far more than the speed ratio alone would
    # give without the factor (vc 20/3 ≈ 6.7×, plus ×1.6 thread mill)
    assert ti.times.tapping_min > al.times.tapping_min * 2.0
