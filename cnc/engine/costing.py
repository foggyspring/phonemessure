"""Costing engine — turns the process plan into money and tiered prices.

Implements the brief's master formula:

    总报价 = (材料成本 + 加工成本 + 表面处理成本 + 编程与准备工时) × (1 + 利润率)

Two cost classes matter for tiered pricing:

  * per-part variable cost  — material, cutting time, per-part finishing
  * one-time batch cost      — programming, first article, finish line setup

The one-time bucket is amortised over the order quantity, which is *why* unit
price drops as quantity rises (量大从优). We expose a price-break table so the
customer sees the curve, not just one number.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .capp import ProcessPlan
from .shopdata import Finish, Material, ShopData


@dataclass
class CostBreakdown:
    quantity: int
    material_cny: float
    machining_cny: float
    finish_variable_cny: float
    amortized_one_time_cny: float
    unit_cost_cny: float
    margin: float
    unit_price_cny: float
    line_total_cny: float

    def to_dict(self) -> dict:
        d = {k: (round(v, 2) if isinstance(v, float) else v)
             for k, v in asdict(self).items()}
        # Reconcile the *displayed* figures: total must equal the rounded unit
        # price × quantity, so a customer can check unit×qty == total by hand
        # (otherwise sub-cent rounding makes them disagree at large quantities).
        d["unit_price_cny"] = round(self.unit_price_cny, 2)
        d["line_total_cny"] = round(d["unit_price_cny"] * self.quantity, 2)
        return d


@dataclass
class Quote:
    requested: CostBreakdown
    tiers: list[CostBreakdown]
    one_time_cny: float
    finish_setup_cny: float
    lead_days: int
    rush: bool
    currency: str
    machine_rate_cny_h: float
    notes: list[str]
    tax_rate: float = 0.0
    tax_label: str = ""
    valid_days: int = 0
    material_gross_cny: float = 0.0
    scrap_credit_cny: float = 0.0

    def to_dict(self) -> dict:
        # Tax is charged on the requested line total; the grand total is what
        # the customer pays. Quote carries a validity window since prices move.
        from datetime import date, timedelta

        net = round(self.requested.unit_price_cny, 2) * self.requested.quantity
        tax = net * self.tax_rate
        valid_until = (date.today() + timedelta(days=self.valid_days)).isoformat() \
            if self.valid_days else None
        return {
            "requested": self.requested.to_dict(),
            "tiers": [t.to_dict() for t in self.tiers],
            "one_time_cny": round(self.one_time_cny, 2),
            "finish_setup_cny": round(self.finish_setup_cny, 2),
            "lead_days": self.lead_days,
            "rush": self.rush,
            "currency": self.currency,
            "machine_rate_cny_h": self.machine_rate_cny_h,
            "tax_rate": self.tax_rate,
            "tax_label": self.tax_label,
            "tax_cny": round(tax, 2),
            "net_total_cny": round(net, 2),
            "total_incl_tax_cny": round(net + tax, 2),
            "valid_days": self.valid_days,
            "valid_until": valid_until,
            "material_gross_cny": round(self.material_gross_cny, 2),
            "scrap_credit_cny": round(self.scrap_credit_cny, 2),
            "notes": self.notes,
        }


def _material_cost(plan: ProcessPlan, material: Material) -> tuple[float, float, float]:
    """Return (net, gross, scrap_credit) per part in CNY.

    Gross = stock weight × price. The removed metal (chips) is credited back at
    scrap_credit_frac of its value — material for expensive alloys (titanium /
    stainless chips have real recovery value), negligible for plastics.
    """
    price = material.price_cny_per_kg
    gross = (plan.stock_volume_cm3 * material.density_g_cm3 / 1000.0) * price
    removed_kg = plan.removed_volume_cm3 * material.density_g_cm3 / 1000.0
    credit = removed_kg * price * material.scrap_credit_frac
    return gross - credit, gross, credit


def _machining_cost(plan: ProcessPlan) -> float:
    return (plan.times.per_part_min / 60.0) * plan.machine.rate_cny_per_hour


def _breakdown(
    qty: int,
    material_cny: float,
    machining_cny: float,
    finish_var_cny: float,
    one_time_total_cny: float,
    margin: float,
) -> CostBreakdown:
    amortized = one_time_total_cny / qty if qty > 0 else one_time_total_cny
    unit_cost = material_cny + machining_cny + finish_var_cny + amortized
    unit_price = unit_cost * (1.0 + margin)
    return CostBreakdown(
        quantity=qty,
        material_cny=material_cny,
        machining_cny=machining_cny,
        finish_variable_cny=finish_var_cny,
        amortized_one_time_cny=amortized,
        unit_cost_cny=unit_cost,
        margin=margin,
        unit_price_cny=unit_price,
        line_total_cny=unit_price * qty,
    )


def price(
    plan: ProcessPlan,
    material: Material,
    finish: Finish,
    shop: ShopData,
    quantity: int,
    tight_tolerance: bool = False,
    rush: bool = False,
) -> Quote:
    biz = shop.business
    margin = float(biz["margin"])
    notes: list[str] = []

    if tight_tolerance:
        margin += float(biz["tight_tolerance_margin_bonus"])
        notes.append("精密公差：风险溢价提高利润率")

    # Per-part variable costs.
    material_cny, material_gross_cny, scrap_credit_cny = _material_cost(plan, material)
    machining_cny = _machining_cost(plan)

    # Surface treatment is priced on the part's real surface area (cm^2 -> dm^2).
    area_dm2 = plan.part_area_cm2 / 100.0
    finish_var_cny = finish.per_dm2_cny * area_dm2

    # One-time batch cost (programming + first article); constant per order.
    programming_cny = (plan.one_time_min / 60.0) * plan.machine.rate_cny_per_hour
    finish_setup_cny = finish.setup_cny

    def finish_one_time(qty: int) -> float:
        """Finish fixed cost for an order of *qty*, honouring the line minimum.

        Per-part finishing (finish_var × qty) is already counted per unit; this
        returns the remaining fixed portion = max(setup, min − var×qty). It is
        evaluated *per quantity* so each price-break enforces the minimum
        correctly (the earlier code applied one top-up across all tiers).
        """
        if finish.min_cny > 0:
            return max(finish_setup_cny, finish.min_cny - finish_var_cny * qty)
        return finish_setup_cny

    def order_one_time(qty: int) -> float:
        return programming_cny + finish_one_time(qty)

    rq = max(1, quantity)
    if finish.min_cny > 0 and finish_setup_cny + finish_var_cny * rq < finish.min_cny:
        notes.append(f"表面处理起步价 {finish.min_cny:g} 元，已按数量补足差额")

    rush = bool(rush)
    rush_factor = float(biz["rush_factor"]) if rush else 1.0
    lead_days = int(biz["rush_lead_days"] if rush else biz["standard_lead_days"])
    if rush:
        notes.append(f"加急 {lead_days} 天交付：加急系数 ×{rush_factor}")

    def make(qty: int) -> CostBreakdown:
        b = _breakdown(qty, material_cny, machining_cny, finish_var_cny,
                       order_one_time(qty), margin)
        if rush_factor != 1.0:
            b.unit_price_cny *= rush_factor
            b.line_total_cny = b.unit_price_cny * qty
        return b

    requested = make(rq)
    breaks = sorted({*[int(x) for x in biz["quantity_breaks"]], rq})
    tiers = [make(q) for q in breaks]
    one_time_total = order_one_time(rq)

    return Quote(
        requested=requested,
        tiers=tiers,
        one_time_cny=one_time_total,
        finish_setup_cny=finish_setup_cny,
        lead_days=lead_days,
        rush=rush,
        currency=str(biz["currency"]),
        machine_rate_cny_h=plan.machine.rate_cny_per_hour,
        notes=notes,
        tax_rate=float(biz.get("tax_rate", 0.0)),
        tax_label=str(biz.get("tax_label", "")),
        valid_days=int(biz.get("quote_valid_days", 0)),
        material_gross_cny=material_gross_cny,
        scrap_credit_cny=scrap_credit_cny,
    )
