"""Loads and validates the shop's reference data (materials, machines, rates).

In production these rows live in PostgreSQL and are edited through an admin
panel / supplier API (per the brief's phase 3). Here they are JSON so the whole
system runs with zero infrastructure, but everything reads through this single
module so swapping the backing store later touches one file.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class Material:
    key: str
    label: str
    category: str
    density_g_cm3: float
    price_cny_per_kg: float          # finished-stock ¥/kg (static fallback)
    machinability: float
    finish_ok: tuple[str, ...]
    tensile_mpa: float = 0.0          # typical tensile strength, for equiv-strength substitution
    stock_lead_days: int = 0          # procurement wait for non-stocked材料 (钛/316…)
    metal_basis: str | None = None   # exchange metal for live pricing (AL/CU/SS/…)
    form_factor: float = 0.0         # spot → finished bar/plate multiplier
    scrap_credit_frac: float = 0.0   # fraction of removed-metal value recovered


@dataclass(frozen=True)
class Finish:
    key: str
    label: str
    setup_cny: float
    per_dm2_cny: float
    min_cny: float


@dataclass(frozen=True)
class Machine:
    key: str
    label: str
    rate_cny_per_hour: float
    base_mrr_cm3_min: float
    max_axes: int


@dataclass(frozen=True)
class ShopData:
    materials: dict[str, Material]
    finishes: dict[str, Finish]
    machines: dict[str, Machine]
    capp: dict[str, float]
    business: dict[str, object]

    def material(self, key: str) -> Material:
        try:
            return self.materials[key]
        except KeyError:
            raise KeyError(f"unknown material '{key}'") from None

    def finish(self, key: str) -> Finish:
        try:
            return self.finishes[key]
        except KeyError:
            raise KeyError(f"unknown finish '{key}'") from None

    def machine(self, key: str) -> Machine:
        try:
            return self.machines[key]
        except KeyError:
            raise KeyError(f"unknown machine '{key}'") from None


@lru_cache(maxsize=1)
def load(data_dir: str | None = None) -> ShopData:
    d = Path(data_dir) if data_dir else DATA_DIR
    mats_raw = json.loads((d / "materials.json").read_text("utf-8"))
    mach_raw = json.loads((d / "machines.json").read_text("utf-8"))

    materials = {
        k: Material(
            key=k,
            label=v["label"],
            category=v["category"],
            density_g_cm3=v["density_g_cm3"],
            price_cny_per_kg=v["price_cny_per_kg"],
            machinability=v["machinability"],
            finish_ok=tuple(v["finish_ok"]),
            tensile_mpa=float(v.get("tensile_mpa", 0.0)),
            stock_lead_days=int(v.get("stock_lead_days", 0)),
            metal_basis=v.get("metal_basis"),
            form_factor=float(v.get("form_factor", 0.0)),
            scrap_credit_frac=float(v.get("scrap_credit_frac", 0.0)),
        )
        for k, v in mats_raw["materials"].items()
    }
    finishes = {
        k: Finish(
            key=k,
            label=v["label"],
            setup_cny=v["setup_cny"],
            per_dm2_cny=v["per_dm2_cny"],
            min_cny=v["min_cny"],
        )
        for k, v in mats_raw["finishes"].items()
    }
    machines = {
        k: Machine(
            key=k,
            label=v["label"],
            rate_cny_per_hour=v["rate_cny_per_hour"],
            base_mrr_cm3_min=v["base_mrr_cm3_min"],
            max_axes=v["max_axes"],
        )
        for k, v in mach_raw["machines"].items()
    }
    return ShopData(
        materials=materials,
        finishes=finishes,
        machines=machines,
        capp=mach_raw["capp"],
        business=mach_raw["business"],
    )


# Fields an admin/supplier feed is allowed to override at runtime, per kind.
_OVERRIDE_FIELDS = {
    "material": {"price_cny_per_kg", "machinability", "density_g_cm3",
                 "form_factor", "scrap_credit_frac", "tensile_mpa", "stock_lead_days"},
    "machine": {"rate_cny_per_hour", "base_mrr_cm3_min", "max_axes"},
    "finish": {"setup_cny", "per_dm2_cny", "min_cny"},
}
# Business/process scalars an operator may maintain at runtime (利润率/税率/去毛刺
# /物流/最小起订 等).
_BUSINESS_OVERRIDABLE = {
    "margin", "tax_rate", "tight_tolerance_margin_bonus", "rush_factor",
    "deburr_base_cny", "deburr_per_dm2_cny", "packaging_cny",
    "shipping_cny_per_kg", "min_order_cny", "quote_valid_days",
}
# Nested array params, addressed as "<array>.<key>.<sub>" (e.g.
# "lead_time_tiers.express.factor"). {array: {editable sub-fields}}.
_BUSINESS_NESTED = {
    "lead_time_tiers": {"factor", "days"},
    "tolerance_classes": {"margin_bonus", "machining_factor", "inspection_min"},
    "surface_classes": {"finish_factor"},
    "addons": {"batch_cny", "per_part_cny"},
}
# CAPP timing scalars (编程/装夹/首件/公差工时 等).
_CAPP_OVERRIDABLE = {
    "fixture_min_per_setup", "toolchange_min_per_tool", "programming_min_base",
    "programming_min_per_complexity", "first_article_min",
    "tight_tolerance_machining_factor", "tight_tolerance_inspection_min_per_part",
    "inspection_min_per_feature",
    "min_machine_min_per_part", "stock_margin_mm",
}


def business_field_ok(field: str) -> bool:
    """True if a business override field (flat or nested path) is editable."""
    if "." in field:
        parts = field.split(".")
        return len(parts) == 3 and parts[0] in _BUSINESS_NESTED and parts[2] in _BUSINESS_NESTED[parts[0]]
    return field in _BUSINESS_OVERRIDABLE


def _set_business(biz: dict, field: str, value: float) -> None:
    if "." in field:
        arr, key, sub = field.split(".", 2)
        for el in biz.get(arr, []):
            if el.get("key") == key:
                el[sub] = value
                return
    elif field in _BUSINESS_OVERRIDABLE:
        biz[field] = value


def apply_overrides(shop: ShopData, overrides: dict | None) -> ShopData:
    """Return a copy of *shop* with material prices / machine rates patched.

    *overrides* is {'material': {key: {field: value}}, 'machine': {...}} as
    produced by store.get_overrides(). The base JSON stays immutable (and
    lru-cached); only the returned copy carries the edits.
    """
    if not overrides:
        return shop

    materials = dict(shop.materials)
    for key, fields in (overrides.get("material") or {}).items():
        if key not in materials:
            continue
        patch = {f: float(v) for f, v in fields.items() if f in _OVERRIDE_FIELDS["material"]}
        if patch:
            materials[key] = replace(materials[key], **patch)

    machines = dict(shop.machines)
    for key, fields in (overrides.get("machine") or {}).items():
        if key not in machines:
            continue
        patch = {f: float(v) for f, v in fields.items() if f in _OVERRIDE_FIELDS["machine"]}
        if patch:
            machines[key] = replace(machines[key], **patch)

    finishes = dict(shop.finishes)
    for key, fields in (overrides.get("finish") or {}).items():
        if key not in finishes:
            continue
        patch = {f: float(v) for f, v in fields.items() if f in _OVERRIDE_FIELDS["finish"]}
        if patch:
            finishes[key] = replace(finishes[key], **patch)

    # deep-copy business/capp before mutating nested structures so the
    # lru-cached base shop is never corrupted.
    business = copy.deepcopy(shop.business) if overrides.get("business") else shop.business
    for _key, fields in (overrides.get("business") or {}).items():
        for f, v in fields.items():
            if business_field_ok(f):
                _set_business(business, f, float(v))

    capp = copy.deepcopy(shop.capp) if overrides.get("capp") else shop.capp
    for _key, fields in (overrides.get("capp") or {}).items():
        for f, v in fields.items():
            if f in _CAPP_OVERRIDABLE:
                capp[f] = float(v)

    return replace(shop, materials=materials, machines=machines,
                   finishes=finishes, business=business, capp=capp)
