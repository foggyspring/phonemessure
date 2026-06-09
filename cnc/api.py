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
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import store
from .engine import ShopData, apply_overrides, load
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


def _effective_shop() -> ShopData:
    """Base reference data with any runtime price/rate overrides applied."""
    return apply_overrides(load(), store.get_overrides())


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

    @app.get("/api/materials")
    def materials() -> dict:
        shop = _effective_shop()
        return {
            "materials": {
                k: {
                    "label": m.label,
                    "category": m.category,
                    "density_g_cm3": m.density_g_cm3,
                    "price_cny_per_kg": m.price_cny_per_kg,
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
            payload = build_quote(
                metrics, req, shop=_effective_shop(),
                mesh_stl=mesh_stl, backend=str(p.get("backend", "auto")),
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

    @app.put("/api/admin/price")
    def set_price(body: dict) -> dict:
        """Maintain 当日市场克单价 / 机床时租 at runtime (the brief's admin panel).

        body = {"kind": "material"|"machine", "key": str, "field": str, "value": number}
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
    def overrides() -> dict:
        return store.get_overrides()

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        idx = STATIC_DIR / "index.html"
        if not idx.exists():
            return HTMLResponse("<h1>CNC Quote Engine</h1><p>frontend missing</p>")
        return HTMLResponse(idx.read_text("utf-8"))

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


app = build_app()
