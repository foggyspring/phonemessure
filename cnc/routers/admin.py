"""Admin routes: runtime price/config maintenance, audit, refresh, calibration."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import store
from ..api import _current_value, _effective_shop, require_admin
from ..engine import load
from ..pricing import get_price_service

router = APIRouter()


@router.get("/api/admin/audit")
def admin_audit(_admin: dict = Depends(require_admin)) -> dict:
    return {"audit": store.list_audit()}


@router.post("/api/prices/refresh")
def prices_refresh(_admin: dict = Depends(require_admin)) -> dict:
    svc = get_price_service()
    q = svc.quotes(force=True) if svc else {}
    return {"feed": svc.feed.name if svc and svc.feed else "static",
            "refreshed": len(q), "metals": sorted(q)}


@router.get("/api/calibration")
def calibration(_admin: dict = Depends(require_admin)) -> dict:
    """Current time-correction factors learned from real cycle times."""
    return {"factors": store.time_factors(), "samples": len(store.calibration_samples())}


@router.post("/api/calibration/actual")
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


@router.put("/api/admin/price")
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
    from ..engine.shopdata import _CAPP_OVERRIDABLE, _OVERRIDE_FIELDS, business_field_ok
    shop = load()
    if kind in ("material", "machine", "finish"):
        catalog = {"material": shop.materials, "machine": shop.machines,
                   "finish": shop.finishes}[kind]
        # custom (operator-added) entities live outside load() — accept them too
        custom_keys = {"material": lambda: store.get_custom_materials(),
                       "finish": lambda: store.get_custom_finishes(),
                       "machine": lambda: store.get_custom_machines()}[kind]()
        if key not in catalog and key not in custom_keys:
            raise HTTPException(status_code=404, detail=f"unknown {kind} '{key}'")
        if field not in _OVERRIDE_FIELDS[kind]:
            raise HTTPException(status_code=400, detail=f"field '{field}' not overridable for {kind}")
    elif kind == "business":
        if not business_field_ok(field):
            raise HTTPException(status_code=400, detail=f"field '{field}' not a maintainable business param")
        key = key or "business"
    elif kind == "capp":
        if field not in _CAPP_OVERRIDABLE:
            raise HTTPException(status_code=400, detail=f"field '{field}' not a maintainable capp param")
        key = key or "capp"
    elif kind == "cutting":
        from ..api import effective_cutting
        from ..estimators.toolpath import _CUTTING_OVERRIDABLE
        cut = effective_cutting()
        if key not in cut.get("materials", {}):
            raise HTTPException(status_code=404, detail=f"unknown cutting material '{key}'")
        if field not in _CUTTING_OVERRIDABLE:
            raise HTTPException(status_code=400, detail=f"field '{field}' not a maintainable cutting param")
    else:
        raise HTTPException(status_code=400, detail="invalid kind")
    # Capture the value being replaced for an auditable before→after trail.
    before = _current_value(kind, key, field, shop)
    try:
        store.set_override(kind, key, field, value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    before_s = f"{before:g}" if isinstance(before, (int, float)) else "默认"
    store.add_audit(_admin.get("u", "?"), "set_price",
                    f"{kind}/{key}/{field}: {before_s}→{value:g}")
    return {"ok": True, "kind": kind, "key": key, "field": field,
            "value": value, "before": before}


@router.get("/api/admin/overrides")
def overrides(_admin: dict = Depends(require_admin)) -> dict:
    return store.get_overrides()


@router.get("/api/admin/config")
def admin_config(_admin: dict = Depends(require_admin)) -> dict:
    """Everything an operator can maintain at runtime (with overrides applied)."""
    from ..engine.shopdata import (
        _BUSINESS_NESTED, _BUSINESS_OVERRIDABLE, _CAPP_OVERRIDABLE, _OVERRIDE_FIELDS,
    )
    from ..api import effective_cutting
    from ..estimators.toolpath import _CUTTING_OVERRIDABLE
    shop, _ = _effective_shop()
    biz = shop.business
    tiers = {arr: [{"key": el.get("key"), "label": el.get("label", el.get("key")),
                    **{s: el.get(s) for s in subs}}
                   for el in (biz.get(arr) or [])]
             for arr, subs in _BUSINESS_NESTED.items()}
    cut = effective_cutting()
    return {
        "materials": {k: {"label": m.label,
                          **{f: getattr(m, f) for f in sorted(_OVERRIDE_FIELDS["material"])}}
                      for k, m in shop.materials.items()},
        "machines": {k: {"label": mc.label,
                         **{f: getattr(mc, f) for f in sorted(_OVERRIDE_FIELDS["machine"])}}
                     for k, mc in shop.machines.items()},
        "finishes": {k: {"label": fn.label,
                         **{f: getattr(fn, f) for f in sorted(_OVERRIDE_FIELDS["finish"])}}
                     for k, fn in shop.finishes.items() if k != "none"},
        "business": {f: biz.get(f) for f in sorted(_BUSINESS_OVERRIDABLE) if biz.get(f) is not None},
        "tiers": tiers,
        "capp": {f: shop.capp.get(f) for f in sorted(_CAPP_OVERRIDABLE) if shop.capp.get(f) is not None},
        "cutting": {m: {f: v.get(f) for f in sorted(_CUTTING_OVERRIDABLE) if v.get(f) is not None}
                    for m, v in cut.get("materials", {}).items()},
    }


# --------------------------------------------------------- AI skill library --
@router.get("/api/admin/skills")
def list_skills(_admin: dict = Depends(require_admin)) -> dict:
    """The effective skill library (defaults + runtime edits) for maintenance."""
    from ..ai import skills as sk
    lib = sk.load_skills(store.get_skill_overrides())
    return {"skills": lib, "editable_fields": sorted(sk.editable_fields())}


@router.put("/api/admin/skills")
def save_skill(body: dict, _admin: dict = Depends(require_admin)) -> dict:
    """Edit a builtin (editable fields only) or upsert a custom skill."""
    from ..ai import skills as sk
    key = str(body.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="key required")
    try:
        if sk.is_builtin(key):
            patch = {f: body[f] for f in sk.editable_fields() if f in body}
            if not patch:
                raise HTTPException(status_code=400, detail="no editable field provided")
            store.save_skill(key, patch)
            detail = f"builtin {key}: {', '.join(patch)}"
        else:
            normalized = sk.validate_custom(body)
            store.save_skill(key, normalized)
            detail = f"custom {key} ({normalized['kind']})"
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store.add_audit(_admin.get("u", "?"), "save_skill", detail)
    return {"ok": True, "key": key}


@router.delete("/api/admin/skills/{key}")
def delete_skill(key: str, _admin: dict = Depends(require_admin)) -> dict:
    """Revert a builtin to default, or delete a custom skill."""
    from ..ai import skills as sk
    n = store.delete_skill(key)
    store.add_audit(_admin.get("u", "?"), "delete_skill",
                    f"{key} ({'reverted builtin' if sk.is_builtin(key) else 'deleted custom'}, n={n})")
    return {"ok": True, "reverted": n, "builtin": sk.is_builtin(key)}


# ------------------------------------------------- custom material library --
@router.get("/api/admin/materials")
def list_custom_materials(_admin: dict = Depends(require_admin)) -> dict:
    """Operator-added materials + the field template for the add form."""
    from ..engine.shopdata import (
        _MATERIAL_CATEGORIES,
        _MATERIAL_OPTIONAL,
        _MATERIAL_REQUIRED,
    )
    return {
        "custom": store.get_custom_materials(),
        "builtin_keys": list(load().materials),
        "finishes": [k for k in load().finishes],
        "categories": list(_MATERIAL_CATEGORIES),
        "required_fields": list(_MATERIAL_REQUIRED),
        "optional_fields": list(_MATERIAL_OPTIONAL),
    }


@router.put("/api/admin/materials")
def upsert_custom_material(body: dict, _admin: dict = Depends(require_admin)) -> dict:
    """Add or edit an operator-defined material (no code change / restart)."""
    from ..engine.shopdata import validate_material
    key = str(body.get("key") or "").strip()
    if key in load().materials:
        raise HTTPException(status_code=400, detail=f"'{key}' 是内置材料，请用价格维护页编辑")
    try:
        mat = validate_material(key, body, valid_finishes=set(load().finishes))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = {"label": mat.label, "category": mat.category, "density_g_cm3": mat.density_g_cm3,
            "price_cny_per_kg": mat.price_cny_per_kg, "machinability": mat.machinability,
            "tensile_mpa": mat.tensile_mpa, "scrap_credit_frac": mat.scrap_credit_frac,
            "form_factor": mat.form_factor, "stock_lead_days": mat.stock_lead_days,
            "metal_basis": mat.metal_basis, "finish_ok": list(mat.finish_ok)}
    existed = key in store.get_custom_materials()
    store.save_custom_material(key, data)
    store.add_audit(_admin.get("u", "?"), "save_material",
                    f"{'edit' if existed else 'add'} {key} ({mat.category}, ¥{mat.price_cny_per_kg:g}/kg)")
    return {"ok": True, "key": key, "edited": existed}


@router.delete("/api/admin/materials/{key}")
def delete_custom_material(key: str, _admin: dict = Depends(require_admin)) -> dict:
    if key in load().materials:
        raise HTTPException(status_code=400, detail="内置材料不可删除")
    n = store.delete_custom_material(key)
    store.add_audit(_admin.get("u", "?"), "delete_material", f"{key} (n={n})")
    return {"ok": True, "deleted": n}


@router.get("/api/admin/finishes")
def list_custom_finishes(_admin: dict = Depends(require_admin)) -> dict:
    shop = load()
    return {"custom": store.get_custom_finishes(),
            "builtin_keys": list(shop.finishes),
            "materials": list(shop.materials) + list(store.get_custom_materials())}


@router.put("/api/admin/finishes")
def upsert_custom_finish(body: dict, _admin: dict = Depends(require_admin)) -> dict:
    """Add/edit an operator-defined finish (电镀/PVD…) and attach it to materials."""
    from ..engine.shopdata import validate_finish
    key = str(body.get("key") or "").strip()
    if key in load().finishes:
        raise HTTPException(status_code=400, detail=f"'{key}' 是内置工艺，请用价格维护页编辑")
    valid_mats = set(load().materials) | set(store.get_custom_materials())
    try:
        fin, apply_to = validate_finish(key, body, valid_materials=valid_mats)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    existed = key in store.get_custom_finishes()
    store.save_custom_finish(key, {"label": fin.label, "setup_cny": fin.setup_cny,
                                   "per_dm2_cny": fin.per_dm2_cny, "min_cny": fin.min_cny,
                                   "lead_days": fin.lead_days, "apply_to": apply_to})
    store.add_audit(_admin.get("u", "?"), "save_finish",
                    f"{'edit' if existed else 'add'} {key} (¥{fin.per_dm2_cny:g}/dm², 适用 {len(apply_to)} 种材料)")
    return {"ok": True, "key": key, "edited": existed}


@router.delete("/api/admin/finishes/{key}")
def delete_custom_finish(key: str, _admin: dict = Depends(require_admin)) -> dict:
    if key in load().finishes:
        raise HTTPException(status_code=400, detail="内置工艺不可删除")
    n = store.delete_custom_finish(key)
    store.add_audit(_admin.get("u", "?"), "delete_finish", f"{key} (n={n})")
    return {"ok": True, "deleted": n}


@router.get("/api/admin/machines")
def list_custom_machines(_admin: dict = Depends(require_admin)) -> dict:
    return {"custom": store.get_custom_machines(), "builtin_keys": list(load().machines)}


@router.put("/api/admin/machines")
def upsert_custom_machine(body: dict, _admin: dict = Depends(require_admin)) -> dict:
    """Add/edit an operator-defined machine (a new cell on the floor)."""
    from ..engine.shopdata import validate_machine
    key = str(body.get("key") or "").strip()
    if key in load().machines:
        raise HTTPException(status_code=400, detail=f"'{key}' 是内置机床，请用价格维护页编辑")
    try:
        m = validate_machine(key, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    existed = key in store.get_custom_machines()
    store.save_custom_machine(key, {"label": m.label, "rate_cny_per_hour": m.rate_cny_per_hour,
                                    "base_mrr_cm3_min": m.base_mrr_cm3_min, "max_axes": m.max_axes})
    store.add_audit(_admin.get("u", "?"), "save_machine",
                    f"{'edit' if existed else 'add'} {key} (¥{m.rate_cny_per_hour:g}/h, {m.max_axes}轴)")
    return {"ok": True, "key": key, "edited": existed}


@router.delete("/api/admin/machines/{key}")
def delete_custom_machine(key: str, _admin: dict = Depends(require_admin)) -> dict:
    if key in load().machines:
        raise HTTPException(status_code=400, detail="内置机床不可删除")
    n = store.delete_custom_machine(key)
    store.add_audit(_admin.get("u", "?"), "delete_machine", f"{key} (n={n})")
    return {"ok": True, "deleted": n}


@router.delete("/api/admin/price")
def revert_price(body: dict, _admin: dict = Depends(require_admin)) -> dict:
    """Undo a price change: revert one override, or all when scope=all."""
    if body.get("scope") == "all":
        store.clear_overrides()
        store.add_audit(_admin.get("u", "?"), "revert_price", "all")
        return {"ok": True, "reverted": "all"}
    try:
        kind, key, field = str(body["kind"]), str(body["key"]), str(body["field"])
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"need kind/key/field or scope=all: {exc}") from exc
    n = store.clear_override(kind, key, field)
    store.add_audit(_admin.get("u", "?"), "revert_price", f"{kind}/{key}/{field} (n={n})")
    return {"ok": True, "reverted": n}
