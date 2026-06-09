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

import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth, store
from .engine import ShopData, apply_overrides, load
from .pricing import apply_market_prices, get_price_service
from .geometry import GeometryError, MeshMetrics
from .geometry.parser import (
    KernelUnavailable,
    metrics_from_dims,
    parse_bytes,
)
from .quote import build_quote_pdf
from .service import QuoteError, QuoteRequest, build_quote

STATIC_DIR = Path(__file__).resolve().parent / "static"
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


def _seed_admin() -> None:
    """Ensure one admin user exists; password from env or an insecure default."""
    try:
        if store.count_users() > 0:
            return
        user = os.environ.get("CNC_ADMIN_USER", "admin")
        pw = os.environ.get("CNC_ADMIN_PASSWORD")
        if not pw:
            pw = "admin"
            print("  ⚠ CNC_ADMIN_PASSWORD not set — seeding admin/admin; "
                  "set it (and change the password) before exposing this.", flush=True)
        store.create_user(user, auth.hash_password(pw), role="admin")
    except Exception as exc:  # never block startup on seeding
        print(f"  auth seed skipped: {exc}", flush=True)


# Simple in-memory login throttle (per client IP) to blunt brute-forcing.
# Single-process; for multi-worker deployments back this with Redis.
_LOGIN_MAX_FAILS = 8
_LOGIN_WINDOW_S = 60
_login_fails: dict[str, deque] = defaultdict(deque)


_AI_MAX_PER_MIN = 40
_ai_calls: dict[str, deque] = defaultdict(deque)
_AI_MSG_MAX = 2000
_AI_HISTORY_MAX = 24


def _ai_throttled(ip: str) -> bool:
    dq = _ai_calls[ip]
    now = time.time()
    while dq and now - dq[0] > 60:
        dq.popleft()
    if len(dq) >= _AI_MAX_PER_MIN:
        return True
    dq.append(now)
    return False


def _login_throttled(ip: str) -> bool:
    dq = _login_fails[ip]
    now = time.time()
    while dq and now - dq[0] > _LOGIN_WINDOW_S:
        dq.popleft()
    return len(dq) >= _LOGIN_MAX_FAILS


def _record_login_fail(ip: str) -> None:
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
    app = FastAPI(title="CNC Quote Engine", version="1.0.0")

    @app.get("/api/health")
    def health() -> dict:
        kernel = False
        try:  # is OpenCASCADE importable?
            import OCC.Core  # noqa: F401
            kernel = True
        except Exception:
            kernel = False
        return {"ok": True, "brep_kernel": kernel}

    @app.get("/api/backends")
    def backends() -> dict:
        from . import estimators
        return {"available": estimators.backend_status(), "options": list(estimators.BACKENDS)}

    @app.get("/api/ai/status")
    def ai_status() -> dict:
        from .ai import get_provider
        p = get_provider()
        return {"provider": p.name, "available": p.available,
                "live": p.name != "mock",
                "note": ("使用离线模拟助手；上线接入真实 LLM 后自动切换。"
                         if p.name == "mock" else "已接入真实 LLM。")}

    @app.post("/api/ai/chat")
    async def ai_chat(
        request: Request,
        message: str = Form(...),
        params: str = Form("{}"),
        history: str = Form("[]"),
        file: UploadFile | None = File(None),
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        from .ai.agent import run_agent
        from .ai.tools import AgentContext
        ip = request.client.host if request.client else "unknown"
        if _ai_throttled(ip):
            raise HTTPException(status_code=429, detail="AI 请求过于频繁，请稍后再试。")
        message = str(message)
        if len(message) > _AI_MSG_MAX:
            raise HTTPException(status_code=400, detail=f"消息过长（>{_AI_MSG_MAX} 字）。")
        try:
            p = json.loads(params) if params else {}
            hist = json.loads(history) if history else []
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"bad params/history: {exc}") from exc
        if not isinstance(p, dict) or not isinstance(hist, list):
            raise HTTPException(status_code=400, detail="params must be object, history a list")
        hist = hist[-_AI_HISTORY_MAX:]            # cap context window

        metrics = mesh_stl = None
        if file is not None:
            data = _read_capped(file)
            try:
                metrics, extra = _metrics_from_request(data, file.filename, None)
                if extra.get("preview_stl_b64"):
                    mesh_stl = base64.b64decode(extra["preview_stl_b64"])
                elif file.filename and file.filename.lower().endswith(".stl"):
                    mesh_stl = data
            except (GeometryError, KernelUnavailable):
                metrics = None

        tok = auth.bearer_from_header(authorization)
        is_admin = bool(tok and auth.verify_token(store.get_secret(), tok))
        shop, sources = _effective_shop()
        ctx = AgentContext(shop=shop, metrics=metrics, mesh_stl=mesh_stl, params=p,
                           price_sources=sources, calibration_factors=store.time_factors(),
                           is_admin=is_admin)
        return JSONResponse(run_agent(str(message), ctx, history=hist))

    @app.post("/api/ai/approve")
    def ai_approve(body: dict, _admin: dict = Depends(require_admin)) -> dict:
        """Execute an AI-proposed write action after explicit admin approval."""
        from .ai.tools import AgentContext, dispatch, get_tool
        tool = str(body.get("tool", ""))
        t = get_tool(tool)
        if t is None or not t.requires_approval:
            raise HTTPException(status_code=400, detail="not an approvable tool")
        shop, _ = _effective_shop()
        ctx = AgentContext(shop=shop, params=body.get("params") or {}, is_admin=True)
        res = dispatch(tool, body.get("arguments") or {}, ctx)
        return {"ok": res.get("error") is None, "summary": res.get("summary", ""),
                "error": res.get("error"), "data": res.get("data")}

    @app.get("/api/ai/tools")
    def ai_tools() -> dict:
        from .ai.tools import tool_schemas
        return {"tools": tool_schemas(is_admin=True)}

    @app.post("/api/ai/session")
    def ai_save_session(body: dict) -> dict:
        sid = str(body.get("id") or "")
        msgs = body.get("messages")
        if not sid or not isinstance(msgs, list):
            raise HTTPException(status_code=400, detail="id and messages[] required")
        title = str(body.get("title", ""))[:80]
        store.save_ai_session(sid, msgs[-_AI_HISTORY_MAX:], title)
        return {"ok": True, "id": sid}

    @app.get("/api/ai/sessions")
    def ai_sessions() -> dict:
        return {"sessions": store.list_ai_sessions()}

    @app.get("/api/ai/session/{sid}")
    def ai_get_session(sid: str) -> dict:
        s = store.get_ai_session(sid)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")
        return s

    @app.post("/api/ai/analyze")
    async def ai_analyze(
        params: str = Form("{}"),
        file: UploadFile | None = File(None),
    ) -> JSONResponse:
        from .ai.agent import analyze_part
        from .ai.tools import AgentContext
        try:
            p = json.loads(params) if params else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"bad params: {exc}") from exc
        if not isinstance(p, dict):
            raise HTTPException(status_code=400, detail="params must be an object")
        metrics = mesh_stl = None
        if file is not None:
            data = _read_capped(file)
            try:
                metrics, extra = _metrics_from_request(data, file.filename, None)
                if extra.get("preview_stl_b64"):
                    mesh_stl = base64.b64decode(extra["preview_stl_b64"])
                elif file.filename and file.filename.lower().endswith(".stl"):
                    mesh_stl = data
            except (GeometryError, KernelUnavailable):
                metrics = None
        shop, sources = _effective_shop()
        ctx = AgentContext(shop=shop, metrics=metrics, mesh_stl=mesh_stl, params=p,
                           price_sources=sources, calibration_factors=store.time_factors())
        return JSONResponse(analyze_part(ctx))

    @app.get("/api/prices")
    def prices() -> dict:
        """Current effective material ¥/kg and where each came from."""
        shop, sources = _effective_shop()
        svc = get_price_service()
        feed = svc.feed.name if svc and svc.feed else "static"
        quotes = {m: {"cny_per_kg": q.cny_per_kg, "source": q.source, "asof": q.asof}
                  for m, q in (svc.quotes().items() if svc else [])}
        return {
            "feed": feed,
            "metal_quotes": quotes,
            "materials": {
                k: {"price_cny_per_kg": m.price_cny_per_kg, "source": sources.get(k, "static")}
                for k, m in shop.materials.items()
            },
        }

    @app.post("/api/prices/refresh")
    def prices_refresh(_admin: dict = Depends(require_admin)) -> dict:
        svc = get_price_service()
        q = svc.quotes(force=True) if svc else {}
        return {"feed": svc.feed.name if svc and svc.feed else "static",
                "refreshed": len(q), "metals": sorted(q)}

    @app.get("/api/calibration")
    def calibration(_admin: dict = Depends(require_admin)) -> dict:
        """Current time-correction factors learned from real cycle times."""
        return {"factors": store.time_factors(), "samples": len(store.calibration_samples())}

    @app.post("/api/calibration/actual")
    def calibration_actual(body: dict, _admin: dict = Depends(require_admin)) -> dict:
        """Record a real cycle time. Either supply quote_id (estimate + material
        are looked up) or material + estimated_min explicitly; always actual_min.
        """
        try:
            actual_min = float(body["actual_min"])
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"actual_min required: {exc}") from exc
        if actual_min <= 0:
            raise HTTPException(status_code=400, detail="actual_min must be > 0")

        material = body.get("material")
        estimated = body.get("estimated_min")
        backend = body.get("backend")
        quote_id = body.get("quote_id")
        if quote_id:
            q = store.get_quote(str(quote_id))
            if q is None:
                raise HTTPException(status_code=404, detail=f"quote '{quote_id}' not found")
            material = q["input"]["material"]
            # The stored per_part_min may already include a calibration factor;
            # divide it out so we always calibrate against the *raw* model
            # estimate (otherwise the factor double-applies and oscillates).
            applied = (q["plan"].get("calibration") or {}).get("factor") or 1.0
            estimated = q["plan"]["times"]["per_part_min"] / (applied or 1.0)
            backend = q.get("estimator", {}).get("used")
        if not material or not estimated:
            raise HTTPException(status_code=400,
                                detail="provide quote_id, or material + estimated_min")
        try:
            store.add_calibration_sample(str(material), float(estimated), actual_min,
                                         backend=backend, quote_id=quote_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "material": material, "estimated_min": float(estimated),
                "actual_min": actual_min, "factors": store.time_factors()}

    @app.get("/api/materials")
    def materials() -> dict:
        shop, sources = _effective_shop()
        return {
            "materials": {
                k: {
                    "label": m.label,
                    "category": m.category,
                    "density_g_cm3": m.density_g_cm3,
                    "price_cny_per_kg": m.price_cny_per_kg,
                    "price_source": sources.get(k, "static"),
                    "finish_ok": list(m.finish_ok),
                }
                for k, m in shop.materials.items()
            },
            "finishes": {k: {"label": f.label} for k, f in shop.finishes.items()},
            "machines": {
                k: {"label": mc.label, "rate_cny_per_hour": mc.rate_cny_per_hour}
                for k, mc in shop.machines.items()
            },
            "business": shop.business,
        }

    @app.post("/api/parse")
    async def parse(file: UploadFile = File(...)) -> JSONResponse:
        data = _read_capped(file)
        metrics, extra = _metrics_from_request(data, file.filename, None)
        return JSONResponse({"geometry": _metrics_payload(metrics), **extra})

    @app.post("/api/quote")
    async def quote(
        params: str = Form(...),
        file: UploadFile | None = File(None),
    ) -> JSONResponse:
        try:
            p = json.loads(params)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"bad params JSON: {exc}") from exc
        if not isinstance(p, dict):
            raise HTTPException(status_code=400, detail="params must be a JSON object")

        data = _read_capped(file) if file is not None else None
        manual = p.get("manual_dims")
        metrics, extra = _metrics_from_request(data, file.filename if file else None, manual)

        # A triangle mesh (STL bytes) unlocks the toolpath-simulation backend:
        # native STL directly, or the OCCT-tessellated STL for STEP/IGES.
        mesh_stl: bytes | None = None
        if extra.get("source_format") == "stl":
            mesh_stl = data
        elif extra.get("preview_stl_b64"):
            import base64
            mesh_stl = base64.b64decode(extra["preview_stl_b64"])

        try:
            req = QuoteRequest.from_payload(p)
            eff_shop, price_sources = _effective_shop()
            payload = build_quote(
                metrics, req, shop=eff_shop, mesh_stl=mesh_stl,
                backend=str(p.get("backend", "auto")), price_sources=price_sources,
                calibration_factors=store.time_factors(), compare=bool(p.get("compare")),
            )
        except QuoteError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid request: {exc}") from exc

        payload["source"] = extra
        if p.get("save", True):
            try:
                payload["id"] = store.save_quote(payload)
            except Exception:  # persistence must never break a live quote
                payload["id"] = None
        return JSONResponse(payload)

    @app.post("/api/quote/pdf")
    async def quote_pdf(payload: dict) -> Response:
        if "quote" not in payload or "input" not in payload:
            raise HTTPException(status_code=400, detail="payload must be a /api/quote result")
        try:
            pdf = build_quote_pdf(payload)
        except Exception as exc:  # reportlab failure -> 500 with message
            raise HTTPException(status_code=500, detail=f"PDF render failed: {exc}") from exc
        name = (payload.get("input", {}).get("part_name") or "quote").replace(" ", "_")
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{name}_quote.pdf"'},
        )

    @app.get("/api/quotes")
    def quotes(limit: int = 50) -> dict:
        return {"quotes": store.list_quotes(limit=max(1, min(limit, 500)))}

    @app.get("/api/quotes/{quote_id}")
    def quote_by_id(quote_id: str) -> JSONResponse:
        payload = store.get_quote(quote_id)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"quote '{quote_id}' not found")
        return JSONResponse(payload)

    @app.get("/api/quotes/{quote_id}/pdf")
    def quote_pdf_by_id(quote_id: str) -> Response:
        payload = store.get_quote(quote_id)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"quote '{quote_id}' not found")
        pdf = build_quote_pdf(payload, quote_no=quote_id)
        name = (payload.get("input", {}).get("part_name") or "quote").replace(" ", "_")
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{name}_{quote_id}.pdf"'},
        )

    @app.post("/api/login")
    def login(body: dict, request: Request) -> dict:
        ip = request.client.host if request.client else "unknown"
        if _login_throttled(ip):
            raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试 too many attempts")
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        u = store.get_user(username)
        if not u or not auth.verify_password(password, u["pw_hash"]):
            _record_login_fail(ip)
            raise HTTPException(status_code=401, detail="用户名或密码错误 invalid credentials")
        _login_fails.pop(ip, None)   # reset on success
        ttl = 8 * 3600
        token = auth.make_token(store.get_secret(), username, u["role"], ttl)
        return {"token": token, "username": username, "role": u["role"], "expires_in": ttl}

    @app.get("/api/me")
    def me(authorization: str | None = Header(default=None)) -> dict:
        token = auth.bearer_from_header(authorization)
        payload = auth.verify_token(store.get_secret(), token) if token else None
        if not payload:
            raise HTTPException(status_code=401, detail="not authenticated")
        return {"username": payload["u"], "role": payload["r"], "exp": payload["exp"]}

    @app.put("/api/admin/price")
    def set_price(body: dict, _admin: dict = Depends(require_admin)) -> dict:
        """Maintain 当日市场克单价 / 机床时租 at runtime (the brief's admin panel).

        body = {"kind": "material"|"machine", "key": str, "field": str, "value": number}
        Requires an admin bearer token.
        """
        try:
            kind = str(body["kind"])
            key = str(body["key"])
            field = str(body["field"])
            value = float(body["value"])
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid body: {exc}") from exc

        # Validate the key exists and the field is overridable.
        shop = load()
        catalog = shop.materials if kind == "material" else shop.machines if kind == "machine" else None
        if catalog is None:
            raise HTTPException(status_code=400, detail="kind must be 'material' or 'machine'")
        if key not in catalog:
            raise HTTPException(status_code=404, detail=f"unknown {kind} '{key}'")
        try:
            store.set_override(kind, key, field, value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "kind": kind, "key": key, "field": field, "value": value}

    @app.get("/api/admin/overrides")
    def overrides(_admin: dict = Depends(require_admin)) -> dict:
        return store.get_overrides()

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        idx = STATIC_DIR / "index.html"
        if not idx.exists():
            return HTMLResponse("<h1>CNC Quote Engine</h1><p>frontend missing</p>")
        return HTMLResponse(idx.read_text("utf-8"))

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    _seed_admin()
    return app


app = build_app()
