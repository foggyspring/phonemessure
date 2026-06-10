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


_BATCH_MAX_FILES = 20


@router.post("/api/quote/batch")
async def quote_batch(
    params: str = Form("{}"),
    files: list[UploadFile] = File(...),
) -> JSONResponse:
    """Multi-part (BOM) import: quote every file with shared params, then roll
    the parts up as ONE order — the minimum-order floor applies once to the
    batch (not per part), shipping is one consolidated shipment, and the lead
    time is the slowest part. Per-file failures don't kill the batch.
    """
    try:
        p = json.loads(params) if params else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"bad params JSON: {exc}") from exc
    if not isinstance(p, dict):
        raise HTTPException(status_code=400, detail="params must be a JSON object")
    if len(files) > _BATCH_MAX_FILES:
        raise HTTPException(status_code=413,
                            detail=f"批量最多 {_BATCH_MAX_FILES} 个文件，请分批上传。")

    shop, price_sources = _effective_shop()
    cutting = effective_cutting()
    cal = store.time_factors()
    save = bool(p.get("save", False))

    parts: list[dict] = []
    for f in files:
        name = f.filename or "part"
        try:
            data = _read_capped(f)
            metrics, extra = _metrics_from_request(data, name, None)
            mesh_stl = None
            if extra.get("source_format") == "stl":
                mesh_stl = data
            elif extra.get("preview_stl_b64"):
                import base64
                mesh_stl = base64.b64decode(extra["preview_stl_b64"])
            req = QuoteRequest.from_payload({**p, "part_name": name})
            payload = build_quote(
                metrics, req, shop=shop, mesh_stl=mesh_stl,
                backend=str(p.get("backend", "auto")), price_sources=price_sources,
                calibration_factors=cal, cutting=cutting,
            )
            q = payload["quote"]
            qid = None
            if save:
                try:
                    qid = store.save_quote(payload)
                except Exception:
                    qid = None
            parts.append({
                "ok": True, "file": name, "id": qid,
                "part_name": name,
                "material_label": payload["input"]["material_label"],
                "quantity": q["requested"]["quantity"],
                "unit_price_cny": q["requested"]["unit_price_cny"],
                "line_net_cny": q["line_net_cny"],          # pre-topup: the batch
                "lead_days": q["lead_days"],                 # floor applies ONCE below
                "order_weight_kg": payload["logistics"]["order_weight_kg"],
                "confidence": payload["confidence"]["score"],
                "risk": payload["dfm_summary"]["level"],
                "risk_counts": payload["dfm_summary"]["counts"],
                "dims_mm": payload["geometry"]["dims_mm"],
            })
        except HTTPException as exc:
            parts.append({"ok": False, "file": name, "error": str(exc.detail)})
        except Exception:
            log.exception("batch part failed: %s", name)
            parts.append({"ok": False, "file": name, "error": "解析或报价失败"})

    ok = [x for x in parts if x["ok"]]
    biz = shop.business
    subtotal = round(sum(x["line_net_cny"] for x in ok), 2)
    min_order = float(biz.get("min_order_cny", 0))
    topup = round(max(0.0, min_order - subtotal), 2) if ok else 0.0
    net = round(subtotal + topup, 2)
    tax_rate = float(biz.get("tax_rate", 0.0))
    tax = round(net * tax_rate, 2)
    weight = round(sum(x["order_weight_kg"] for x in ok), 3)
    shipping = float(biz.get("packaging_cny", 0)) + weight * float(biz.get("shipping_cny_per_kg", 0))
    crate_threshold = float(biz.get("crate_threshold_kg", 0) or 0)
    crated = crate_threshold > 0 and weight > crate_threshold
    if crated:
        shipping += float(biz.get("crate_cny", 0) or 0)
    review = [x["file"] for x in ok
              if x["risk"] in ("high", "medium") or x["confidence"] < 60]
    risk_counts = {"high": sum(x["risk_counts"]["high"] for x in ok),
                   "medium": sum(x["risk_counts"]["medium"] for x in ok),
                   "low": sum(x["risk_counts"]["low"] for x in ok)}
    return JSONResponse({
        "parts": parts,
        "aggregate": {
            "n_parts": len(parts), "n_ok": len(ok), "n_failed": len(parts) - len(ok),
            "subtotal_net_cny": subtotal,
            "min_order_topup_cny": topup,           # once for the whole order
            "net_total_cny": net,
            "tax_cny": tax, "tax_rate": tax_rate,
            "total_incl_tax_cny": round(net + tax, 2),
            "lead_days": max((x["lead_days"] for x in ok), default=0),
            "order_weight_kg": weight,
            "shipping_cny": round(shipping, 2), "crated": crated,
            "risk_counts": risk_counts,
            "needs_review": review,
        },
    })


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
