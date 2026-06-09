"""End-to-end funnel tests: geometry -> features -> CAPP -> costing."""
from __future__ import annotations

from cnc.engine import load
from cnc.geometry import Hole, metrics_from_stl_bytes
from cnc.service import QuoteError, QuoteRequest, build_quote
from cnc.tests.fixtures import cube_stl


def _metrics():
    return metrics_from_stl_bytes(cube_stl(50.0))


def test_quote_basic_shape():
    q = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=10))
    assert q["geometry"]["dims_mm"] == [50.0, 50.0, 50.0]
    assert q["quote"]["requested"]["quantity"] == 10
    assert q["quote"]["requested"]["unit_price_cny"] > 0
    # price must exceed cost (margin applied)
    r = q["quote"]["requested"]
    assert r["unit_price_cny"] > r["unit_cost_cny"]


def test_removed_volume_drives_material():
    # Stock = cube + 3mm margin each side = 56^3 mm^3 -> 175.616 cm^3.
    q = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=1))
    # 50mm cube + 3mm/side = 56mm; smallest dim rounds up to the 60mm standard
    # plate, so stock = 60 × 56 × 56 mm.
    assert abs(q["plan"]["stock_volume_cm3"] - (60.0 * 56.0 * 56.0) / 1000.0) < 0.01
    assert abs(q["plan"]["part_volume_cm3"] - 125.0) < 0.01


def test_scrap_credit_reduces_material_cost():
    import trimesh

    from cnc.geometry import metrics_from_stl_bytes
    m = trimesh.creation.cylinder(radius=20, height=40, sections=48)
    m.apply_translation((0, 0, 20))
    sb = m.export(file_type="stl")          # removes a lot of metal → real chips

    ti = build_quote(metrics_from_stl_bytes(sb), QuoteRequest(material="TITANIUM_TC4", quantity=1))
    Q = ti["quote"]
    assert Q["scrap_credit_cny"] > 0
    assert abs(Q["requested"]["material_cny"] - (Q["material_gross_cny"] - Q["scrap_credit_cny"])) < 0.05
    # plastics get no scrap credit
    pom = build_quote(metrics_from_stl_bytes(sb), QuoteRequest(material="POM", quantity=1))
    assert pom["quote"]["scrap_credit_cny"] == 0.0


def test_standard_plate_thickness_rounding():
    # a 17mm-thick plate must be quoted on 20mm stock (next standard plate),
    # and the note should say so.
    import trimesh
    m = trimesh.creation.box(extents=(80, 60, 17)); m.apply_translation((40, 30, 8.5))
    sb = m.export(file_type="stl")
    from cnc.geometry import metrics_from_stl_bytes
    q = build_quote(metrics_from_stl_bytes(sb), QuoteRequest(material="AL6061", quantity=1))
    # thickness with margin = 17+6 = 23 -> rounds up to 25mm plate
    assert abs(q["plan"]["stock"]["height_mm"] - 25.0) < 1e-6 or \
           25.0 in (q["plan"]["stock"]["length_mm"], q["plan"]["stock"]["width_mm"], q["plan"]["stock"]["height_mm"])
    assert any("标准板" in n for n in q["plan"]["notes"])


def test_lead_time_options_and_selection():
    q = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=10))
    opts = {o["key"]: o for o in q["quote"]["lead_time_options"]}
    assert {"economy", "standard", "express", "rush"} <= set(opts)
    # faster delivery costs more; economy (slowest) is cheapest
    assert (opts["economy"]["unit_price_cny"] < opts["standard"]["unit_price_cny"]
            < opts["express"]["unit_price_cny"] < opts["rush"]["unit_price_cny"])
    assert opts["standard"]["selected"] and opts["rush"]["days"] < opts["standard"]["days"]

    # selecting a tier drives the requested price
    qe = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=10, lead_time="express"))
    assert qe["quote"]["lead_time"] == "express"
    assert abs(qe["quote"]["requested"]["unit_price_cny"] - opts["express"]["unit_price_cny"]) < 0.01
    # rush=True remains an alias for the fastest tier (3 days)
    assert build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=10, rush=True))["quote"]["lead_days"] == 3
    # economy discount never sells below unit cost
    for o in q["quote"]["lead_time_options"]:
        assert o["unit_price_cny"] >= q["quote"]["requested"]["unit_cost_cny"] - 1e-6


def test_economy_floor_never_below_cost():
    # force a tiny margin so the economy discount would otherwise dip below cost
    import dataclasses
    from cnc.engine import load
    shop = load()
    shop = dataclasses.replace(shop, business={**shop.business, "margin": 0.02})
    q = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=10), shop)
    eco = next(o for o in q["quote"]["lead_time_options"] if o["key"] == "economy")
    cost = q["quote"]["requested"]["unit_cost_cny"]
    # economy is floored exactly at cost (1.02×0.92 = 0.938 < 1 would breach it)
    assert eco["unit_price_cny"] >= cost - 1e-6
    assert abs(eco["unit_price_cny"] - cost) < 0.01


def test_quantity_breaks_drop_unit_price():
    q = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=1))
    tiers = {t["quantity"]: t["unit_price_cny"] for t in q["quote"]["tiers"]}
    # amortized one-time cost falls -> higher qty cheaper per unit
    assert tiers[1] > tiers[100]


def test_stainless_costs_more_machining_than_aluminum():
    al = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=1))
    ss = build_quote(_metrics(), QuoteRequest(material="SUS304", quantity=1))
    assert ss["quote"]["requested"]["machining_cny"] > al["quote"]["requested"]["machining_cny"]


def test_five_axis_uses_pricier_machine():
    three = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=1))
    five = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=1, requires_5axis=True))
    assert five["quote"]["machine_rate_cny_h"] > three["quote"]["machine_rate_cny_h"]
    assert five["quote"]["requested"]["unit_price_cny"] > three["quote"]["requested"]["unit_price_cny"]


def test_threaded_holes_add_tapping_time():
    plain = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=1))
    tapped = build_quote(
        _metrics(),
        QuoteRequest(
            material="AL6061",
            quantity=1,
            holes=[Hole(diameter_mm=6.0, depth_mm=20.0, count=4, threaded=True)],
        ),
    )
    assert tapped["plan"]["times"]["tapping_min"] > 0
    assert plain["plan"]["times"]["tapping_min"] == 0
    assert tapped["plan"]["times"]["per_part_min"] > plain["plan"]["times"]["per_part_min"]


def test_tight_tolerance_raises_price():
    base = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=5))
    tight = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=5, tight_tolerance=True))
    assert tight["quote"]["requested"]["unit_price_cny"] > base["quote"]["requested"]["unit_price_cny"]


def test_rush_raises_price_and_shortens_lead():
    base = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=5))
    rush = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=5, rush=True))
    assert rush["quote"]["lead_days"] < base["quote"]["lead_days"]
    assert rush["quote"]["requested"]["unit_price_cny"] > base["quote"]["requested"]["unit_price_cny"]


def test_invalid_finish_for_material_rejected():
    # anodizing does not apply to stainless in our finish_ok table.
    try:
        build_quote(_metrics(), QuoteRequest(material="SUS304", quantity=1, finish="anodize_clear"))
    except QuoteError:
        return
    raise AssertionError("expected QuoteError for invalid material/finish combo")


def test_shopdata_loads():
    shop = load()
    assert "AL6061" in shop.materials
    assert shop.machine("mill_3axis").rate_cny_per_hour == 60.0


def test_inch_units_scale_geometry():
    # the same file quoted as inch must be 25.4× larger per axis than as mm.
    stl = cube_stl(2.0)
    mm = build_quote(metrics_from_stl_bytes(stl), QuoteRequest(material="AL6061", quantity=1, units="mm"))
    inch = build_quote(metrics_from_stl_bytes(stl), QuoteRequest(material="AL6061", quantity=1, units="inch"))
    assert abs(inch["geometry"]["dims_mm"][0] - 2.0 * 25.4) < 1e-3
    assert inch["geometry"]["part_weight_g"] > mm["geometry"]["part_weight_g"] * 1000  # 25.4^3 ≈ 16387
    assert inch["quote"]["requested"]["unit_price_cny"] > mm["quote"]["requested"]["unit_price_cny"]


def test_suspicious_tiny_part_warns_units():
    q = build_quote(metrics_from_stl_bytes(cube_stl(1.5)), QuoteRequest(material="AL6061", quantity=1))
    assert any("单位" in w for w in q["warnings"])


def test_tax_and_validity_present():
    q = build_quote(metrics_from_stl_bytes(cube_stl(50.0)), QuoteRequest(material="AL6061", quantity=10))
    Q = q["quote"]
    assert Q["tax_rate"] == 0.13
    # grand total = net × (1 + tax), within rounding
    assert abs(Q["total_incl_tax_cny"] - Q["net_total_cny"] * 1.13) < 0.05
    assert abs(Q["tax_cny"] - Q["net_total_cny"] * 0.13) < 0.05
    assert Q["valid_until"] is not None
    # part weight surfaced
    assert q["geometry"]["part_weight_g"] > 0


def test_tolerance_classes_scale_price_and_inspection():
    m = _metrics()
    std = build_quote(m, QuoteRequest(material="AL6061", quantity=5, tolerance="standard"))
    pre = build_quote(m, QuoteRequest(material="AL6061", quantity=5, tolerance="precision"))
    ult = build_quote(m, QuoteRequest(material="AL6061", quantity=5, tolerance="ultra"))
    su = std["quote"]["requested"]["unit_price_cny"]
    pu = pre["quote"]["requested"]["unit_price_cny"]
    uu = ult["quote"]["requested"]["unit_price_cny"]
    assert su < pu < uu
    assert std["plan"]["times"]["inspection_min"] == 0
    assert ult["plan"]["times"]["inspection_min"] > pre["plan"]["times"]["inspection_min"]
    # legacy tight_tolerance still works (maps to a non-standard class)
    assert build_quote(m, QuoteRequest(material="AL6061", quantity=5, tight_tolerance=True))["quote"]["requested"]["unit_price_cny"] > su


def test_surface_roughness_scales_finishing():
    import trimesh
    from cnc.geometry import metrics_from_stl_bytes
    # a sphere has real finishing area to scale
    sb = trimesh.creation.icosphere(subdivisions=3, radius=25).export(file_type="stl")
    m = metrics_from_stl_bytes(sb)
    std = build_quote(m, QuoteRequest(material="AL6061", quantity=1, surface_finish="standard"), mesh_stl=sb, backend="analytic")
    mir = build_quote(m, QuoteRequest(material="AL6061", quantity=1, surface_finish="mirror"), mesh_stl=sb, backend="analytic")
    assert mir["plan"]["times"]["finishing_min"] > std["plan"]["times"]["finishing_min"]
    assert mir["quote"]["requested"]["unit_price_cny"] > std["quote"]["requested"]["unit_price_cny"]
