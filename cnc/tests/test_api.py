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
