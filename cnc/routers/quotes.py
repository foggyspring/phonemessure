"""Quoting routes: parse, quote, quote history, and PDF rendering."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from .. import store
from ..api import (
    _effective_shop,
    _metrics_from_request,
    _metrics_payload,
    _read_capped,
    effective_cutting,
)
from ..quote import build_quote_pdf
from ..service import QuoteError, QuoteRequest, build_quote

log = logging.getLogger("cnc")

router = APIRouter()


def _render_pdf(payload: dict, **kw) -> bytes:
    """Render with a logged, generic 500 — never echo internals to the client."""
    try:
        return build_quote_pdf(payload, **kw)
    except Exception:
        log.exception("PDF render failed")
        raise HTTPException(status_code=500, detail="PDF 生成失败，请稍后重试。") from None


@router.post("/api/parse")
async def parse(file: UploadFile = File(...)) -> JSONResponse:
    data = _read_capped(file)
    metrics, extra = _metrics_from_request(data, file.filename, None)
    return JSONResponse({"geometry": _metrics_payload(metrics), **extra})


@router.post("/api/quote")
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
            calibration_factors=store.time_factors(), cutting=effective_cutting(),
            compare=bool(p.get("compare")),
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


@router.post("/api/quote/pdf")
async def quote_pdf(payload: dict) -> Response:
    if "quote" not in payload or "input" not in payload:
        raise HTTPException(status_code=400, detail="payload must be a /api/quote result")
    pdf = _render_pdf(payload)
    name = (payload.get("input", {}).get("part_name") or "quote").replace(" ", "_")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}_quote.pdf"'},
    )


@router.get("/api/quotes")
def quotes(limit: int = 50, offset: int = 0, search: str = "") -> dict:
    rows, total = store.list_quotes(limit=limit, offset=offset, search=search.strip())
    return {"quotes": rows, "total": total, "offset": max(0, offset), "limit": limit}


@router.get("/api/quotes/{quote_id}")
def quote_by_id(quote_id: str) -> JSONResponse:
    payload = store.get_quote(quote_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"quote '{quote_id}' not found")
    return JSONResponse(payload)


@router.get("/api/quotes/{quote_id}/pdf")
def quote_pdf_by_id(quote_id: str) -> Response:
    payload = store.get_quote(quote_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"quote '{quote_id}' not found")
    pdf = _render_pdf(payload, quote_no=quote_id)
    name = (payload.get("input", {}).get("part_name") or "quote").replace(" ", "_")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}_{quote_id}.pdf"'},
    )
