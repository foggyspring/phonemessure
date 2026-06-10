"""Meta routes: health, backends, materials, prices, and the frontend index."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from .. import store
from ..api import STATIC_DIR, _VERSION, _effective_shop
from ..pricing import get_price_service

log = logging.getLogger("cnc")

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    try:
        import OCC.Core  # noqa: F401
        kernel = True
    except Exception:
        kernel = False
    db_ok = True
    try:
        store.count_users()
    except Exception as exc:
        db_ok = False
        log.error("health: DB check failed: %s", exc)
    from ..ai import get_provider
    from ..pricing import get_price_service
    prov = get_provider()
    svc = get_price_service()
    return {
        "ok": db_ok,
        "version": _VERSION,
        "brep_kernel": kernel,
        "db": db_ok,
        "ai_provider": prov.name,
        "ai_live": prov.name != "mock",
        "price_feed": (svc.feed.name if svc and svc.feed else "static"),
    }


@router.get("/api/backends")
def backends() -> dict:
    from .. import estimators
    return {"available": estimators.backend_status(), "options": list(estimators.BACKENDS)}


@router.get("/api/prices")
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


@router.get("/api/materials")
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


@router.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        return HTMLResponse("<h1>CNC Quote Engine</h1><p>frontend missing</p>")
    return HTMLResponse(idx.read_text("utf-8"))
