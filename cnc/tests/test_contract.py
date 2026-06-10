"""Golden contract test: the full build_quote payload shape.

Locks the public payload contract (keys + critical nesting) so structural
refactors (phase decomposition, router split) can be verified mechanically.
Values are pinned only where they are deterministic-by-construction; everything
else asserts presence + type, not magnitude.
"""
from __future__ import annotations

from cnc.geometry import metrics_from_stl_bytes
from cnc.service import QuoteRequest, build_quote
from cnc.tests.fixtures import cube_stl


def test_payload_contract_full_shape():
    sb = cube_stl(50.0)
    p = build_quote(metrics_from_stl_bytes(sb),
                    QuoteRequest(material="AL6061", quantity=10, finish="bead_blast"),
                    mesh_stl=sb)

    # ---- top level ----
    assert set(p) >= {"input", "geometry", "plan", "quote", "price_drivers",
                      "warnings", "dfm", "dfm_summary", "assumptions",
                      "material_suggestions", "fx", "logistics", "confidence",
                      "estimator"}

    # ---- input echo ----
    inp = p["input"]
    assert inp["material"] == "AL6061" and inp["finish"] == "bead_blast"
    assert {"material_label", "finish_label", "quantity", "tolerance",
            "surface_finish", "price_source"} <= set(inp)

    # ---- geometry ----
    g = p["geometry"]
    assert {"dims_mm", "volume_cm3", "area_cm2", "bbox_fill_pct", "complexity",
            "triangles", "part_weight_g", "stock_weight_g"} <= set(g)
    assert len(g["dims_mm"]) == 3

    # ---- plan ----
    pl = p["plan"]
    assert {"machine", "machine_label", "stock", "setups", "tools", "times",
            "one_time_min", "backend", "process_steps", "calibration"} <= set(pl)
    t = pl["times"]
    assert {"roughing_min", "finishing_min", "drilling_min", "tapping_min",
            "toolchange_min", "fixturing_min", "inspection_min", "per_part_min"} <= set(t)
    steps = pl["process_steps"]
    assert steps and [s["step"] for s in steps] == list(range(1, len(steps) + 1))

    # ---- quote ----
    q = p["quote"]
    assert {"requested", "tiers", "one_time_cny", "lead_days", "delivery_date",
            "lead_time", "lead_time_options", "currency", "tax_rate", "tax_cny",
            "line_net_cny", "min_order_cny", "min_order_topup_cny",
            "net_total_cny", "total_incl_tax_cny", "valid_until", "notes"} <= set(q)
    r = q["requested"]
    assert {"quantity", "material_cny", "machining_cny", "finish_variable_cny",
            "addon_per_part_cny", "amortized_one_time_cny", "unit_cost_cny",
            "margin", "unit_price_cny", "line_total_cny"} <= set(r)
    # arithmetic invariants a customer can hand-check
    assert r["line_total_cny"] == round(r["unit_price_cny"] * r["quantity"], 2)
    assert abs((r["material_cny"] + r["machining_cny"] + r["finish_variable_cny"]
                + r["addon_per_part_cny"] + r["amortized_one_time_cny"])
               - r["unit_cost_cny"]) < 0.05
    assert round(q["line_net_cny"] + q["min_order_topup_cny"], 2) == q["net_total_cny"]
    for o in q["lead_time_options"]:
        assert {"key", "label", "days", "delivery_date", "factor",
                "unit_price_cny", "total_cny", "selected"} <= set(o)
        assert o["total_cny"] == round(o["unit_price_cny"] * r["quantity"], 2)
    assert sum(o["selected"] for o in q["lead_time_options"]) == 1

    # ---- confidence / drivers / dfm ----
    c = p["confidence"]
    assert {"score", "level", "reasons", "band_pct", "price_range_cny"} <= set(c)
    assert c["price_range_cny"]["low"] <= r["unit_price_cny"] <= c["price_range_cny"]["high"]
    d = p["price_drivers"]
    assert d["top"] and all({"label", "cny", "pct"} <= set(x) for x in d["top"])
    s = p["dfm_summary"]
    assert {"level", "headline", "counts"} <= set(s)
    assert all(({"severity", "code", "title", "detail", "suggestion"} <= set(f))
               for f in p["dfm"])
    assert p["assumptions"] and all(isinstance(a, str) for a in p["assumptions"])

    # ---- logistics ----
    L = p["logistics"]
    assert {"order_weight_kg", "shipping_cny", "crated", "min_order_cny",
            "meets_min_order", "shortfall_cny"} <= set(L)
