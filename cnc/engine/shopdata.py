"""Loads and validates the shop's reference data (materials, machines, rates).

In production these rows live in PostgreSQL and are edited through an admin
panel / supplier API (per the brief's phase 3). Here they are JSON so the whole
system runs with zero infrastructure, but everything reads through this single
module so swapping the backing store later touches one file.
"""
from __future__ import annotations

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
    "material": {"price_cny_per_kg", "machinability", "density_g_cm3"},
    "machine": {"rate_cny_per_hour", "base_mrr_cm3_min"},
    "finish": {"setup_cny", "per_dm2_cny", "min_cny"},
}
# Business/process scalars an operator may maintain at runtime (利润率/税率/去毛刺
# /物流/最小起订 等). Only flat numeric keys — nested tier arrays stay in JSON.
_BUSINESS_OVERRIDABLE = {
    "margin", "tax_rate", "tight_tolerance_margin_bonus", "rush_factor",
    "deburr_base_cny", "deburr_per_dm2_cny", "packaging_cny",
    "shipping_cny_per_kg", "min_order_cny", "quote_valid_days",
}


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

    business = dict(shop.business)
    for _key, fields in (overrides.get("business") or {}).items():
        for f, v in fields.items():
            if f in _BUSINESS_OVERRIDABLE:
                business[f] = float(v)

    return replace(shop, materials=materials, machines=machines,
                   finishes=finishes, business=business)
