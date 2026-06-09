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


def test_price_drivers_summary():
    # qty1 should be setup-dominated; qty50 should be machining/material-dominated
    one = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=1))["price_drivers"]
    fifty = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=50))["price_drivers"]
    assert one["summary"] and fifty["summary"]
    assert sum(d["pct"] for d in one["top"]) <= 100
    assert "Setup" in one["top"][0]["label"]              # one-time dominates at qty1
    assert "Setup" not in fifty["top"][0]["label"]        # amortized away at qty50


def test_cost_lines_reconcile_with_deburr_exposed():
    # metal parts carry a deburring/post-process line; the breakdown lines must
    # sum to unit_cost so a customer can verify it by hand.
    r = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=10))["quote"]["requested"]
    assert r["addon_per_part_cny"] > 0          # deburring present for metal
    s = (r["material_cny"] + r["machining_cny"] + r["finish_variable_cny"]
         + r["addon_per_part_cny"] + r["amortized_one_time_cny"])
    assert abs(s - r["unit_cost_cny"]) < 0.05


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


def test_process_steps_routing_card():
    q = build_quote(
        _metrics(),
        QuoteRequest(material="AL6061", quantity=10,
                     holes=[Hole(diameter_mm=6.0, depth_mm=20.0, count=4, threaded=True)]),
    )
    steps = q["plan"]["process_steps"]
    names = [s["name"] for s in steps]
    # ordered, numbered routing card with the core machining工序 present
    assert [s["step"] for s in steps] == list(range(1, len(steps) + 1))
    assert any("下料" in n for n in names) and any("精铣" in n for n in names)
    assert any("攻丝" in n for n in names)        # tapping appears for threaded holes
    assert all("scope" in s and s["detail"] for s in steps)


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


def test_tool_wear_charged_for_hard_materials_not_aluminium():
    import trimesh
    sb = trimesh.creation.box((80, 60, 30)).export(file_type="stl")
    m = metrics_from_stl_bytes(sb)
    al = build_quote(m, QuoteRequest(material="AL6061", quantity=10), mesh_stl=sb, backend="analytic")
    ti = build_quote(m, QuoteRequest(material="TITANIUM_TC4", quantity=10), mesh_stl=sb, backend="analytic")
    # aluminium (machinability 1.0) pays no tool-wear consumable; titanium does
    assert not any("刀具消耗" in n for n in al["quote"]["notes"])
    assert any("刀具消耗" in n for n in ti["quote"]["notes"])


def test_large_order_lead_time_extends_with_capacity():
    m = _metrics()
    small = build_quote(m, QuoteRequest(material="AL6061", quantity=1))["quote"]
    huge = build_quote(m, QuoteRequest(material="AL6061", quantity=1000))["quote"]
    assert huge["lead_days"] > small["lead_days"]      # can't ship 1000 in the tier window
    # every delivery option is floored by machining capacity too
    assert all(o["days"] >= small["lead_days"] for o in huge["lead_time_options"])


def test_min_order_floor_tops_up_small_orders():
    # a cheap single plastic part falls below the ¥200 minimum order
    small = build_quote(metrics_from_stl_bytes(cube_stl(20.0)),
                        QuoteRequest(material="ABS", quantity=1))["quote"]
    assert small["min_order_topup_cny"] > 0
    assert small["net_total_cny"] == small["min_order_cny"]
    assert abs(small["net_total_cny"] - (small["line_net_cny"] + small["min_order_topup_cny"])) < 0.01
    # tax is charged on the floored net, grand total consistent
    assert abs(small["total_incl_tax_cny"] - small["net_total_cny"] * (1 + small["tax_rate"])) < 0.05
    # a normal order is unaffected (no top-up)
    big = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=10))["quote"]
    assert big["min_order_topup_cny"] == 0.0


def test_quote_assumptions_present_and_thread_aware():
    import trimesh
    box = trimesh.creation.box((100, 75, 25)); box.apply_translation((50, 37.5, 12.5))
    c = trimesh.creation.cylinder(radius=3.3, height=40, sections=24); c.apply_translation((20, 20, 12.5))
    sb = box.difference(c).export(file_type="stl")
    a = build_quote(metrics_from_stl_bytes(sb),
                    QuoteRequest(material="AL6061", quantity=10), mesh_stl=sb)["assumptions"]
    assert any("公差" in x for x in a)
    assert any("热处理" in x for x in a)
    assert any("未攻丝" in x for x in a)          # auto-detected holes → thread caveat


def test_outsourced_finish_extends_lead_time():
    m = _metrics()
    raw = build_quote(m, QuoteRequest(material="AL6061", quantity=10, finish="none"))["quote"]
    ano = build_quote(m, QuoteRequest(material="AL6061", quantity=10, finish="anodize_clear"))["quote"]
    assert ano["lead_days"] > raw["lead_days"]      # anodizing is outsourced turnaround
    assert any("外协后处理" in n for n in ano["notes"])


def test_procurement_lead_extends_delivery_for_exotic_material():
    m = _metrics()
    al = build_quote(m, QuoteRequest(material="AL6061", quantity=5))["quote"]
    ti = build_quote(m, QuoteRequest(material="TITANIUM_TC4", quantity=5))["quote"]
    assert ti["lead_days"] > al["lead_days"]            # titanium not on the shelf
    assert ti["delivery_date"] > al["delivery_date"]
    # the procurement wait applies to every delivery option too
    assert all(o["days"] > 0 for o in ti["lead_time_options"])


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


def test_inspection_scales_with_feature_count_when_precise():
    m = _metrics()
    few = build_quote(m, QuoteRequest(material="AL6061", quantity=5, tolerance="precision",
                                      holes=[Hole(diameter_mm=6.0, depth_mm=15.0, count=2)]))
    many = build_quote(m, QuoteRequest(material="AL6061", quantity=5, tolerance="precision",
                                       holes=[Hole(diameter_mm=6.0, depth_mm=15.0, count=30)]))
    assert many["plan"]["times"]["inspection_min"] > few["plan"]["times"]["inspection_min"]
    # standard class is not feature-scaled (no full gauging)
    std = build_quote(m, QuoteRequest(material="AL6061", quantity=5, tolerance="standard",
                                      holes=[Hole(diameter_mm=6.0, depth_mm=15.0, count=30)]))
    assert std["plan"]["times"]["inspection_min"] == 0.0


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


def test_material_suggestions_cheaper_same_category():
    q = build_quote(_metrics(), QuoteRequest(material="SUS316", quantity=5))
    s = q["material_suggestions"]
    assert s and all(x["savings_pct"] > 0 for x in s)
    base = q["quote"]["requested"]["unit_price_cny"]
    assert all(x["unit_price_cny"] < base for x in s)
    # plastics shouldn't be suggested for a metal part
    assert all(k not in [x["key"] for x in s] for k in ["POM", "ABS", "PA6"])
    # every suggestion carries a strength verdict; equivalent-strength picks rank first
    assert all("strength_ok" in x for x in s)
    oks = [x["strength_ok"] for x in s]
    assert oks == sorted(oks, reverse=True)          # True (equivalent) before False (weaker)
    weak = [x for x in s if x["strength_ok"] is False]
    assert all(x.get("strength_hint") for x in weak)  # weaker subs explain the risk


def test_qa_addons_raise_price_and_appear():
    m = _metrics()
    base = build_quote(m, QuoteRequest(material="AL6061", quantity=10))
    add = build_quote(m, QuoteRequest(material="AL6061", quantity=10,
                                      addons=["material_cert", "dim_report"]))
    assert add["quote"]["requested"]["unit_price_cny"] > base["quote"]["requested"]["unit_price_cny"]
    assert {a["key"] for a in add["quote"]["addons"]} == {"material_cert", "dim_report"}
    # per-part addon (dim_report) means the gap is bigger than just amortized batch
    assert not base["quote"]["addons"]


def test_currency_fx_block():
    m = _metrics()
    cny = build_quote(m, QuoteRequest(material="AL6061", quantity=5, currency="CNY"))["fx"]
    usd = build_quote(m, QuoteRequest(material="AL6061", quantity=5, currency="USD"))["fx"]
    assert cny["rate"] == 1.0 and cny["symbol"] == "¥" and not cny["indicative"]
    assert usd["currency"] == "USD" and 0 < usd["rate"] < 1 and usd["indicative"]


def test_confidence_score_present_and_ranked():
    import trimesh

    from cnc.geometry import metrics_from_stl_bytes
    cb = cube_stl(50.0)
    simple = build_quote(metrics_from_stl_bytes(cb), QuoteRequest(material="AL6061", quantity=1),
                         mesh_stl=cb, backend="toolpath")["confidence"]
    sb = trimesh.creation.icosphere(subdivisions=3, radius=25).export(file_type="stl")
    hard = build_quote(metrics_from_stl_bytes(sb), QuoteRequest(material="AL6061", quantity=1),
                       mesh_stl=sb, backend="analytic")["confidence"]
    assert 30 <= hard["score"] <= simple["score"] <= 98
    assert simple["level"] in ("high", "medium") and hard["reasons"]
    # confidence carries a reference price band; lower confidence ⇒ wider band
    assert simple["price_range_cny"]["low"] < simple["price_range_cny"]["high"]
    assert hard["band_pct"] >= simple["band_pct"]


def test_logistics_weight_and_min_order():
    m = _metrics()
    one = build_quote(m, QuoteRequest(material="AL6061", quantity=1))["logistics"]
    fifty = build_quote(m, QuoteRequest(material="AL6061", quantity=50))["logistics"]
    # a 50mm aluminium cube ~0.34 kg → 50 of them ~17 kg
    assert 15 < fifty["order_weight_kg"] < 18
    assert fifty["shipping_cny"] > one["shipping_cny"]
    assert one["meets_min_order"] is False and one["shortfall_cny"] > 0
    assert fifty["meets_min_order"] is True
    # ~17kg order crosses the crate threshold →木箱 fee added; the single part doesn't
    assert fifty["crated"] is True and fifty["crate_cny"] > 0
    assert one["crated"] is False


def test_material_comparison_on_demand():
    m = _metrics()
    off = build_quote(m, QuoteRequest(material="AL6061", quantity=5))
    assert off["material_comparison"] is None        # not computed unless requested
    on = build_quote(m, QuoteRequest(material="AL6061", quantity=5), compare=True)
    rows = on["material_comparison"]
    assert len(rows) >= 3
    prices = [r["unit_price_cny"] for r in rows]
    assert prices == sorted(prices)                  # cheapest first
    assert all({"key", "density_g_cm3", "machinability"} <= set(r) for r in rows)


def test_unit_suspect_part_is_low_confidence():
    # a sub-3mm part is almost certainly a unit error → confidence must be low
    tiny = build_quote(metrics_from_stl_bytes(cube_stl(0.2)),
                       QuoteRequest(material="AL6061", quantity=10))
    assert tiny["confidence"]["level"] == "low"
    assert any("单位" in r for r in tiny["confidence"]["reasons"])
    # normal part is not penalised
    ok = build_quote(_metrics(), QuoteRequest(material="AL6061", quantity=10))
    assert ok["confidence"]["score"] > tiny["confidence"]["score"]
