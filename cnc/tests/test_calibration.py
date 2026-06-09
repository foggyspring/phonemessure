"""Calibration: factor computation, persistence, and quote application."""
from __future__ import annotations

import warnings

import pytest

from cnc import calibration, store
from cnc.calibration import compute_time_factors, factor_for
from cnc.geometry import metrics_from_stl_bytes
from cnc.service import QuoteRequest, build_quote
from cnc.tests.fixtures import cube_stl


# ---- core math ----
def test_factor_needs_min_samples():
    # two samples for AL -> not enough for its own factor, only global
    f = compute_time_factors([
        {"material": "AL6061", "estimated_min": 10, "actual_min": 13},
        {"material": "AL6061", "estimated_min": 10, "actual_min": 12},
    ])
    assert "AL6061" not in f
    assert f["_global"]["n"] == 2


def test_material_factor_is_median_ratio():
    f = compute_time_factors([
        {"material": "AL6061", "estimated_min": 10, "actual_min": 12},  # 1.2
        {"material": "AL6061", "estimated_min": 10, "actual_min": 13},  # 1.3
        {"material": "AL6061", "estimated_min": 10, "actual_min": 14},  # 1.4
    ])
    assert f["AL6061"]["factor"] == pytest.approx(1.3, abs=1e-6)
    assert f["AL6061"]["n"] == 3


def test_factor_clamped_and_outlier_resistant():
    f = compute_time_factors([
        {"material": "SUS304", "estimated_min": 10, "actual_min": 11},   # 1.1
        {"material": "SUS304", "estimated_min": 10, "actual_min": 12},   # 1.2
        {"material": "SUS304", "estimated_min": 10, "actual_min": 9999}, # absurd outlier
    ])
    assert 1.0 <= f["SUS304"]["factor"] <= 1.5   # median ignores the outlier


def test_backend_specific_factors():
    samples = ([{"material": "AL6061", "backend": "toolpath", "estimated_min": 10, "actual_min": 15}] * 3
               + [{"material": "AL6061", "backend": "analytic", "estimated_min": 10, "actual_min": 11}] * 3)
    f = compute_time_factors(samples)
    assert f["AL6061|toolpath"]["factor"] == pytest.approx(1.5)
    assert f["AL6061|analytic"]["factor"] == pytest.approx(1.1)
    # exact backend wins; unknown backend falls back to the material aggregate (n=6)
    assert factor_for(f, "AL6061", "toolpath") == (1.5, 3)
    assert factor_for(f, "AL6061", "analytic") == (1.1, 3)
    assert factor_for(f, "AL6061", "5axis")[1] == 6


def test_factor_for_fallback_chain():
    factors = {"AL6061": {"factor": 1.25, "n": 5}, "_global": {"factor": 1.1, "n": 20}}
    assert factor_for(factors, "AL6061") == (1.25, 5)     # own
    assert factor_for(factors, "SUS304") == (1.1, 20)     # global fallback
    assert factor_for(None, "AL6061") == (1.0, 0)         # no data


# ---- application to a quote ----
def test_calibration_scales_quote_time_and_cost():
    m = metrics_from_stl_bytes(cube_stl(50.0))
    base = build_quote(m, QuoteRequest(material="AL6061", quantity=1))
    factors = {"AL6061": {"factor": 1.5, "n": 4}}
    cal = build_quote(m, QuoteRequest(material="AL6061", quantity=1),
                      calibration_factors=factors)
    bt = base["plan"]["times"]["per_part_min"]
    ct = cal["plan"]["times"]["per_part_min"]
    assert ct == pytest.approx(bt * 1.5, rel=1e-3)
    assert cal["quote"]["requested"]["machining_cny"] > base["quote"]["requested"]["machining_cny"]
    assert cal["plan"]["calibration"] == {"factor": 1.5, "n": 4}


# ---- persistence ----
def test_store_samples_and_factors(tmp_path):
    db = tmp_path / "c.db"
    for act in (12, 13, 14):
        store.add_calibration_sample("AL6061", 10.0, act, backend="toolpath", path=db)
    assert len(store.calibration_samples(path=db)) == 3
    f = store.time_factors(path=db)
    assert f["AL6061"]["factor"] == pytest.approx(1.3, abs=1e-6)


# ---- API ----
pytest.importorskip("httpx")
warnings.filterwarnings("ignore")
from starlette.testclient import TestClient  # noqa: E402
from cnc.api import build_app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CNC_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CNC_ADMIN_PASSWORD", "pw")
    monkeypatch.delenv("CNC_PRICE_FEED", raising=False)
    from cnc import api
    api._login_fails.clear()
    return TestClient(build_app())


def _token(client):
    return client.post("/api/login", json={"username": "admin", "password": "pw"}).json()["token"]


def test_calibration_endpoint_requires_auth(client):
    assert client.get("/api/calibration").status_code == 401
    assert client.post("/api/calibration/actual", json={"actual_min": 5}).status_code == 401


def test_record_actual_by_material(client):
    tok = _token(client)
    h = {"Authorization": f"Bearer {tok}"}
    for act in (12, 13, 14):
        r = client.post("/api/calibration/actual",
                        json={"material": "AL6061", "estimated_min": 10, "actual_min": act}, headers=h)
        assert r.status_code == 200
    factors = client.get("/api/calibration", headers=h).json()["factors"]
    assert factors["AL6061"]["factor"] == pytest.approx(1.3, abs=1e-6)


def test_quote_id_uses_raw_estimate_no_double_apply(client):
    """Regression: recording an actual against an already-calibrated quote must
    divide out the applied factor, so the loop converges instead of oscillating."""
    from cnc.tests.fixtures import cube_stl
    tok = _token(client)
    h = {"Authorization": f"Bearer {tok}"}
    stl = cube_stl(50.0)

    def quote():
        r = client.post("/api/quote",
                        data={"params": '{"material":"AL6061","quantity":1,"finish":"none","backend":"analytic"}'},
                        files={"file": ("p.stl", stl)})
        return r.json()

    # seed a factor of ~1.4 via three material-based records
    q0 = quote()
    raw = q0["plan"]["times"]["per_part_min"]
    for _ in range(3):
        client.post("/api/calibration/actual",
                    json={"material": "AL6061", "estimated_min": raw, "actual_min": raw * 1.4}, headers=h)
    q1 = quote()
    assert q1["plan"]["calibration"]["factor"] == pytest.approx(1.4, abs=0.01)
    cal_est = q1["plan"]["times"]["per_part_min"]   # ~1.4×raw, already calibrated

    # record several actuals EQUAL to the calibrated estimate via quote_id.
    # raw estimate recovered = cal_est / 1.4 ≈ raw, ratio = (1.4·raw)/raw = 1.4,
    # so the factor must STAY ~1.4. With the double-apply bug each ratio would be
    # 1.0 and, being the majority, drag the median down toward 1.0.
    for _ in range(5):
        client.post("/api/calibration/actual",
                    json={"quote_id": q1["id"], "actual_min": cal_est}, headers=h)
    q2 = quote()
    assert q2["plan"]["calibration"]["factor"] >= 1.3   # bug would pull it to ~1.0


def test_record_actual_bad_input(client):
    tok = _token(client)
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post("/api/calibration/actual", json={"material": "AL6061"}, headers=h).status_code == 400
    assert client.post("/api/calibration/actual",
                       json={"actual_min": -1, "material": "AL6061", "estimated_min": 10},
                       headers=h).status_code == 400
