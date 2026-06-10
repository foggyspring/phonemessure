"""FastAPI app for the CNC quoting system.

Endpoints
---------
GET  /                  -> Three.js single-page frontend
GET  /api/health        -> liveness + whether a B-rep kernel is present
GET  /api/materials     -> materials / finishes / machines for the UI
POST /api/parse         -> upload a CAD file, return geometry metrics (+preview)
POST /api/quote         -> metrics (or manual dims) + params -> full quote
POST /api/quote/pdf     -> render a quote payload to a PDF download

The upload size is capped to keep a 200 MB assembly from taking down the box
(the brief's "大文件崩溃" pitfall); real deployments would push parsing onto a
Celery worker instead of doing it inline.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

log = logging.getLogger("cnc")

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth, store
from .engine import ShopData, apply_overrides, load
from .geometry import GeometryError, MeshMetrics
from .geometry.parser import (
    KernelUnavailable,
    metrics_from_dims,
    parse_bytes,
)
from .pricing import apply_market_prices, get_price_service
from .quote import build_quote_pdf
from .service import QuoteError, QuoteRequest, build_quote

STATIC_DIR = Path(__file__).resolve().parent / "static"
_VERSION = "0.3.0"   # keep in sync with pyproject.toml
MAX_UPLOAD_BYTES = 60 * 1024 * 1024  # 60 MB — reject monster assemblies early


def _effective_shop() -> tuple[ShopData, dict]:
    """Reference data with live market prices then manual overrides applied.

    Precedence: manual override > live market feed > static base. Returns the
    shop plus a {material_key: price_source} map for transparency in the quote.
    """
    base = load()
    market_shop, sources = apply_market_prices(base, get_price_service())
    overrides = store.get_overrides()
    shop = apply_overrides(market_shop, overrides)
    for k in (overrides.get("material") or {}):
        if "price_cny_per_kg" in overrides["material"][k]:
            sources[k] = "manual override"
    return shop, sources


def effective_cutting() -> dict:
    """Feeds/speeds with runtime overrides applied — resolved here (API layer)
    so the estimator stays pure of persistence."""
    from .estimators.toolpath import _load_cutting
    return _load_cutting(store.get_overrides().get("cutting"))


def _current_value(kind: str, key: str, field: str, base_shop):
    """Effective value of a (kind,key,field) before a change, for audit trails.

    Returns the active override if one exists, else the base default, else None
    (e.g. nested business paths we don't resolve here). Pure read; best-effort.
    """
    ov = store.get_overrides().get(kind, {}).get(key, {})
    if field in ov:
        return ov[field]
    try:
        if kind in ("material", "machine", "finish"):
            catalog = {"material": base_shop.materials, "machine": base_shop.machines,
                       "finish": base_shop.finishes}[kind]
            return getattr(catalog[key], field, None)
        if kind == "business" and "." not in field:
            return base_shop.business.get(field)
        if kind == "capp":
            return base_shop.capp.get(field)
        if kind == "cutting":
            return effective_cutting().get("materials", {}).get(key, {}).get(field)
    except Exception:
        return None
    return None


def _seed_admin() -> None:
    """Ensure one admin user exists; password from env or an insecure default."""
    try:
        if store.count_users() > 0:
            return
        user = os.environ.get("CNC_ADMIN_USER", "admin")
        pw = os.environ.get("CNC_ADMIN_PASSWORD")
        if not pw:
            pw = "admin"
            log.warning("CNC_ADMIN_PASSWORD not set — seeding admin/admin; "
                        "set it (and change the password) before exposing this.")
        store.create_user(user, auth.hash_password(pw), role="admin")
    except Exception as exc:  # never block startup on seeding
        log.warning("auth seed skipped: %s", exc)


# Simple in-memory login throttle (per client IP) to blunt brute-forcing.
# Single-process; for multi-worker deployments back this with Redis.
_LOGIN_MAX_FAILS = 8
_LOGIN_WINDOW_S = 60
_login_fails: dict[str, deque] = defaultdict(deque)
# A fixed PBKDF2 hash to verify against when the username doesn't exist, so the
# login path takes constant time regardless of whether the user is real.
_DUMMY_PW_HASH = auth.hash_password("cnc-timing-equalizer")


_AI_MAX_PER_MIN = 40
_AI_MAX_PER_DAY = 500
_ai_calls: dict[str, deque] = defaultdict(deque)
_ai_calls_day: dict[str, deque] = defaultdict(deque)
_AI_MSG_MAX = 2000
# Sync endpoints run in a thread pool, so the throttle deques are touched from
# multiple threads. The prune-then-check-then-append sequences aren't atomic;
# one lock keeps them consistent (and avoids an IndexError on a racing popleft).
_throttle_lock = threading.Lock()
_AI_HISTORY_MAX = 24


def _ai_cost_guard(ip: str, authorization: str | None) -> None:
    """Protect LLM spend: a live provider requires login unless AI_PUBLIC=1,
    plus a per-IP daily cap. The offline mock stays open (free) for demo."""
    from .ai import get_provider
    live = get_provider().name != "mock"
    if live and os.environ.get("AI_PUBLIC", "0") != "1":
        tok = auth.bearer_from_header(authorization)
        if not (tok and auth.verify_token(store.get_secret(), tok)):
            raise HTTPException(status_code=401,
                                detail="AI 已接入真实模型，请登录后使用（或设 AI_PUBLIC=1 开放）。")
    now = time.time()
    with _throttle_lock:
        dq = _ai_calls_day[ip]
        while dq and now - dq[0] > 86400:
            dq.popleft()
        if len(dq) >= _AI_MAX_PER_DAY:
            raise HTTPException(status_code=429, detail="今日 AI 调用已达上限。")
        dq.append(now)


def _ai_throttled(ip: str) -> bool:
    now = time.time()
    with _throttle_lock:
        dq = _ai_calls[ip]
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= _AI_MAX_PER_MIN:
            return True
        dq.append(now)
    return False


def _login_throttled(ip: str) -> bool:
    now = time.time()
    with _throttle_lock:
        dq = _login_fails[ip]
        while dq and now - dq[0] > _LOGIN_WINDOW_S:
            dq.popleft()
        return len(dq) >= _LOGIN_MAX_FAILS


def _record_login_fail(ip: str) -> None:
    with _throttle_lock:
        _login_fails[ip].append(time.time())


def require_admin(authorization: str | None = Header(default=None)) -> dict:
    """FastAPI dependency: require a valid admin bearer token."""
    token = auth.bearer_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    payload = auth.verify_token(store.get_secret(), token)
    if not payload:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    if payload.get("r") != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return payload


def _metrics_payload(m: MeshMetrics) -> dict:
    dims = m.dims_mm
    return {
        "dims_mm": [round(dims[0], 3), round(dims[1], 3), round(dims[2], 3)],
        "bbox_min": [round(x, 3) for x in m.bbox_min],
        "bbox_max": [round(x, 3) for x in m.bbox_max],
        "volume_cm3": round(m.volume_mm3 / 1000.0, 3),
        "area_cm2": round(m.area_mm2 / 100.0, 3),
        "bbox_fill_pct": (
            round(100.0 * m.volume_mm3 / m.bbox_volume_mm3, 1)
            if m.bbox_volume_mm3 > 0 else None
        ),
        "complexity": round(m.complexity, 3),
        "triangles": m.triangles,
    }


def _metrics_from_request(
    file_bytes: bytes | None,
    filename: str | None,
    manual: dict | None,
) -> tuple[MeshMetrics, dict]:
    """Resolve geometry from either an uploaded file or manual dimensions.

    Returns (metrics, extra) where extra may hold a base64 preview mesh.
    """
    if file_bytes:
        try:
            res = parse_bytes(filename or "part.stl", file_bytes)
        except KernelUnavailable as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except GeometryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        extra = {"source_format": res.source_format, "kernel": res.kernel}
        if res.rendered_mesh_b64:
            extra["preview_stl_b64"] = res.rendered_mesh_b64
        return res.metrics, extra

    if manual:
        try:
            m = metrics_from_dims(
                float(manual["length_mm"]),
                float(manual["width_mm"]),
                float(manual["height_mm"]),
                float(manual["volume_mm3"]) if manual.get("volume_mm3") else None,
            )
        except (GeometryError, KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"manual dims invalid: {exc}") from exc
        return m, {"source_format": "manual", "kernel": "manual"}

    raise HTTPException(status_code=400, detail="provide a CAD file or manual dimensions")


def _read_capped(file: UploadFile) -> bytes:
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file exceeds {MAX_UPLOAD_BYTES // (1024*1024)} MB cap; "
                   f"simplify the model or use manual dimensions.",
        )
    return data


def build_app() -> FastAPI:
    app = FastAPI(title="CNC Quote Engine", version=_VERSION)

    # Imported here (not at module top) so the router modules can import the
    # shared helpers/state above from cnc.api without a circular-import bite.
    from .routers import admin as admin_routes
    from .routers import ai as ai_routes
    from .routers import auth as auth_routes
    from .routers import meta as meta_routes
    from .routers import quotes as quote_routes

    app.include_router(meta_routes.router)
    app.include_router(quote_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(ai_routes.router)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    _seed_admin()
    return app


app = build_app()
