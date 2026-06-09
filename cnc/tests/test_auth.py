"""Auth unit + API tests: password hashing, signed tokens, protected endpoints."""
from __future__ import annotations

import time
import warnings

import pytest

from cnc import auth


# ---- password hashing ----
def test_password_roundtrip():
    h = auth.hash_password("s3cret!")
    assert auth.verify_password("s3cret!", h)
    assert not auth.verify_password("wrong", h)


def test_password_salts_differ():
    assert auth.hash_password("x") != auth.hash_password("x")  # random salt


def test_verify_bad_stored_is_false():
    assert not auth.verify_password("x", "garbage")
    assert not auth.verify_password("x", "")


# ---- tokens ----
def test_token_roundtrip():
    s = "secret-key"
    t = auth.make_token(s, "alice", "admin", ttl_s=60)
    p = auth.verify_token(s, t)
    assert p and p["u"] == "alice" and p["r"] == "admin"


def test_token_rejects_tampering():
    s = "secret-key"
    t = auth.make_token(s, "alice", "admin")
    assert auth.verify_token(s, t[:-2] + ("aa" if not t.endswith("aa") else "bb")) is None
    assert auth.verify_token("other-secret", t) is None
    assert auth.verify_token(s, "not.a.token") is None


def test_token_expiry():
    s = "k"
    t = auth.make_token(s, "u", "admin", ttl_s=-1)   # already expired
    assert auth.verify_token(s, t) is None


def test_bearer_parsing():
    assert auth.bearer_from_header("Bearer abc.def") == "abc.def"
    assert auth.bearer_from_header("bearer xyz") == "xyz"
    assert auth.bearer_from_header("Basic abc") is None
    assert auth.bearer_from_header(None) is None


# ---- API integration ----
pytest.importorskip("httpx")
warnings.filterwarnings("ignore")
from starlette.testclient import TestClient  # noqa: E402

from cnc.api import build_app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CNC_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("CNC_ADMIN_PASSWORD", "pw12345")
    monkeypatch.delenv("CNC_SECRET", raising=False)
    from cnc import api
    api._login_fails.clear()   # reset the per-process login throttle between tests
    return TestClient(build_app())


def _price_body():
    return {"kind": "material", "key": "AL6061", "field": "price_cny_per_kg", "value": 40}


def test_admin_price_requires_auth(client):
    assert client.put("/api/admin/price", json=_price_body()).status_code == 401


def test_login_and_use_token(client):
    r = client.post("/api/login", json={"username": "admin", "password": "pw12345"})
    assert r.status_code == 200
    tok = r.json()["token"]
    assert r.json()["role"] == "admin"
    # protected endpoint now works with the token
    ok = client.put("/api/admin/price", json=_price_body(),
                    headers={"Authorization": f"Bearer {tok}"})
    assert ok.status_code == 200
    # /api/me reflects the session
    me = client.get("/api/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.status_code == 200 and me.json()["username"] == "admin"


def test_login_bad_password_401(client):
    assert client.post("/api/login", json={"username": "admin", "password": "nope"}).status_code == 401


def test_login_brute_force_throttled(client):
    # repeated failures from the same client eventually return 429
    codes = [client.post("/api/login", json={"username": "admin", "password": "x"}).status_code
             for _ in range(12)]
    assert 429 in codes
    assert codes.count(401) <= 8   # capped before throttling kicks in


def test_bad_token_rejected(client):
    assert client.put("/api/admin/price", json=_price_body(),
                      headers={"Authorization": "Bearer forged.token"}).status_code == 401


def test_overrides_endpoint_protected(client):
    assert client.get("/api/admin/overrides").status_code == 401


def test_public_endpoints_still_open(client):
    assert client.get("/api/materials").status_code == 200
    assert client.get("/api/backends").status_code == 200


def test_authed_admin_unknown_key_404(client):
    tok = client.post("/api/login", json={"username": "admin", "password": "pw12345"}).json()["token"]
    r = client.put("/api/admin/price",
                   json={"kind": "material", "key": "NOPE", "field": "price_cny_per_kg", "value": 5},
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404   # authed, but the key doesn't exist
