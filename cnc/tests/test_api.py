"""API contract / input-validation tests (in-process Starlette TestClient).

Locks the regressions found by API fuzzing — notably that malformed-but-valid
JSON params (a list/number/string instead of an object) must return 400, not a
500. Skips cleanly if httpx (TestClient's dependency) is unavailable.
"""
from __future__ import annotations

import json
import warnings

import pytest

pytest.importorskip("httpx")
warnings.filterwarnings("ignore")

from starlette.testclient import TestClient  # noqa: E402

from cnc.api import app  # noqa: E402
from cnc.tests.fixtures import cube_stl  # noqa: E402

client = TestClient(app)
STL = cube_stl(40.0)


def _quote(params, with_file=True):
    files = {"file": ("p.stl", STL)} if with_file else None
    return client.post("/api/quote", data={"params": params}, files=files)


@pytest.mark.parametrize("params", ["[1,2,3]", "42", '"hi"', "true", "null"])
def test_non_object_params_return_400(params):
    # regression: these are valid JSON but not objects -> must be 400, not 500
    assert _quote(params).status_code == 400


def test_bad_json_params_400():
    assert _quote("{not json").status_code == 400


def test_unknown_material_422():
    assert _quote(json.dumps({"material": "NOPE", "quantity": 1})).status_code == 422


def test_invalid_finish_for_material_422():
    r = _quote(json.dumps({"material": "SUS304", "finish": "anodize_clear", "quantity": 1}))
    assert r.status_code == 422


def test_no_geometry_400():
    assert _quote(json.dumps({"material": "AL6061", "quantity": 1}), with_file=False).status_code == 400


def test_garbage_stl_4xx():
    r = client.post("/api/quote", data={"params": json.dumps({"material": "AL6061", "quantity": 1})},
                    files={"file": ("p.stl", b"not an stl")})
    assert r.status_code in (400, 422)


def test_unsupported_extension_4xx():
    assert client.post("/api/parse", files={"file": ("p.txt", b"hi")}).status_code in (400, 422)


def test_pdf_endpoint_rejects_junk_400():
    assert client.post("/api/quote/pdf", json={"foo": "bar"}).status_code == 400


def test_admin_price_requires_auth_401():
    # unauthenticated -> 401 before any body validation (auth gate runs first)
    r = client.put("/api/admin/price",
                   json={"kind": "material", "key": "NOPE", "field": "price_cny_per_kg", "value": 5})
    assert r.status_code == 401


def test_missing_quote_404():
    assert client.get("/api/quotes/Qnope").status_code == 404


def test_valid_quote_round_trip_200():
    r = _quote(json.dumps({"material": "AL6061", "quantity": 10, "finish": "none"}))
    assert r.status_code == 200
    body = r.json()
    assert body["quote"]["requested"]["unit_price_cny"] > 0
    assert "total_incl_tax_cny" in body["quote"]


def test_db_schema_version_stamped(tmp_path):
    import sqlite3

    from cnc import store
    db = tmp_path / "v.db"
    store.count_users(path=db)                      # forces connect → migrate
    v = sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0]
    assert v == store._SCHEMA_VERSION


def test_health_reports_dependencies():
    h = client.get("/api/health").json()
    assert h["ok"] is True and h["db"] is True
    assert "version" in h and "ai_provider" in h and "price_feed" in h
    assert h["ai_provider"] == "mock" and h["ai_live"] is False


def test_quote_history_pagination_and_search(tmp_path, monkeypatch):
    monkeypatch.setenv("CNC_DB", str(tmp_path / "h.db"))
    from cnc.api import build_app
    cl = TestClient(build_app())
    stl = STL
    for mat in ["AL6061", "AL6061", "SUS304"]:
        cl.post("/api/quote", data={"params": json.dumps({"material": mat, "quantity": 1, "finish": "none"})},
                files={"file": ("p.stl", stl)})
    page = cl.get("/api/quotes?limit=2&offset=0").json()
    assert page["total"] == 3 and len(page["quotes"]) == 2 and page["limit"] == 2
    assert len(cl.get("/api/quotes?limit=2&offset=2").json()["quotes"]) == 1
    s = cl.get("/api/quotes?search=SUS304").json()
    assert s["total"] == 1 and s["quotes"][0]["material"] == "SUS304"


def test_parse_cache_serves_repeat_uploads():
    # the UI re-posts the same file on every chat/quote; identical bytes must hit
    # the cache instead of re-parsing.
    import trimesh
    from cnc import api as A
    from starlette.testclient import TestClient
    A._parse_cache.clear()
    c = TestClient(A.build_app())
    sb = trimesh.creation.box((50, 40, 20)).export(file_type="stl")
    a = c.post("/api/parse", files={"file": ("p.stl", sb)}).json()
    assert len(A._parse_cache) == 1
    b = c.post("/api/parse", files={"file": ("p.stl", sb)}).json()
    assert a["geometry"]["dims_mm"] == b["geometry"]["dims_mm"]   # same result
    # a different file is a distinct entry (no cross-contamination)
    sb2 = trimesh.creation.box((80, 30, 10)).export(file_type="stl")
    c.post("/api/parse", files={"file": ("q.stl", sb2)})
    assert len(A._parse_cache) == 2


def test_batch_quote_rolls_up_as_one_order():
    import trimesh
    from starlette.testclient import TestClient

    from cnc import api as A
    c = TestClient(A.build_app())
    mk = lambda d: trimesh.creation.box(d).export(file_type="stl")
    files = [("files", ("a.stl", mk((100, 75, 25)))),
             ("files", ("b.stl", mk((120, 90, 6)))),
             ("files", ("bad.stl", b"garbage")),
             ("files", ("c.stl", mk((40, 40, 40))))]
    r = c.post("/api/quote/batch", files=files,
               data={"params": '{"material":"AL6061","quantity":10}'})
    assert r.status_code == 200
    d = r.json()
    ok = [x for x in d["parts"] if x["ok"]]
    bad = [x for x in d["parts"] if not x["ok"]]
    assert len(ok) == 3 and len(bad) == 1          # per-file failure isolated
    a = d["aggregate"]
    # one-order semantics: subtotal = Σ pre-topup line nets; tax on floored net
    assert abs(a["subtotal_net_cny"] - sum(x["line_net_cny"] for x in ok)) < 0.01
    assert abs(a["total_incl_tax_cny"] - round(a["net_total_cny"] * (1 + a["tax_rate"]), 2)) < 0.05
    assert a["lead_days"] == max(x["lead_days"] for x in ok)
    assert a["n_failed"] == 1
    # the thin 6mm plate should flag for review (high/medium risk rollup)
    assert a["risk_counts"]["high"] + a["risk_counts"]["medium"] >= 1
    assert any("b.stl" in f for f in a["needs_review"])


def test_batch_quote_min_order_topup_applies_once():
    import trimesh
    from starlette.testclient import TestClient

    from cnc import api as A
    c = TestClient(A.build_app())
    # two tiny cheap parts, each alone under the ¥200 floor
    mk = lambda: trimesh.creation.box((20, 20, 20)).export(file_type="stl")
    files = [("files", ("t1.stl", mk())), ("files", ("t2.stl", mk()))]
    r = c.post("/api/quote/batch", files=files,
               data={"params": '{"material":"ABS","quantity":1}'})
    a = r.json()["aggregate"]
    # the floor tops up the BATCH once — never per part
    assert a["net_total_cny"] == max(a["subtotal_net_cny"], 200.0)
    assert a["min_order_topup_cny"] == round(max(0.0, 200.0 - a["subtotal_net_cny"]), 2)


def test_batch_quote_file_cap():
    import trimesh
    from starlette.testclient import TestClient

    from cnc import api as A
    c = TestClient(A.build_app())
    sb = trimesh.creation.box((20, 20, 20)).export(file_type="stl")
    files = [("files", (f"p{i}.stl", sb)) for i in range(21)]
    assert c.post("/api/quote/batch", files=files,
                  data={"params": "{}"}).status_code == 413
