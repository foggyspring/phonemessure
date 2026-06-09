"""Pricing service: turn live exchange quotes into per-material ¥/kg.

PriceService caches a feed's quotes (TTL) and resolves each material's price by
precedence: it computes spot × form_factor when a live quote exists for the
material's metal_basis, else falls back to the material's static price. The API
layer applies manual admin overrides *after* this, so precedence end-to-end is:

    manual override  >  live market (feed × form_factor)  >  static base

The active feed is chosen by $CNC_PRICE_FEED (sina | none, default none) so the
system behaves exactly as before until a feed is switched on.
"""
from __future__ import annotations

import os
import time
from dataclasses import replace

from ..engine.shopdata import Material, ShopData
from .feed import MetalQuote, PriceFeed, SinaShfeFeed, StaticFeed

__all__ = ["PriceService", "apply_market_prices", "get_price_service",
           "PriceFeed", "SinaShfeFeed", "StaticFeed", "MetalQuote"]


class PriceService:
    def __init__(self, feed: PriceFeed | None = None, ttl_s: float = 900.0,
                 max_age_s: float = 6 * 3600.0):
        self.feed = feed
        self.ttl = ttl_s            # how often to re-fetch
        self.max_age_s = max_age_s  # beyond this a stale cache is abandoned
        self._cache: dict[str, MetalQuote] = {}
        self._ts = 0.0

    def quotes(self, *, force: bool = False) -> dict[str, MetalQuote]:
        if self.feed is None or isinstance(self.feed, StaticFeed):
            return {}
        now = time.time()
        if not force and self._cache and now - self._ts < self.ttl:
            return self._cache
        try:
            q = self.feed.fetch()
            if q:                       # fresh data → update cache
                self._cache, self._ts = q, now
                return q
        except Exception as exc:        # network/parse failure → consider stale cache
            import logging
            logging.getLogger("cnc.pricing").warning("price feed %s failed: %s", self.feed.name, exc)
        # Re-fetch failed: serve the cache only if it is not dangerously stale,
        # otherwise fall back to static (never quote on day-old metal prices).
        if self._cache and now - self._ts <= self.max_age_s:
            return self._cache
        return {}

    def price_for(self, material: Material, *, force: bool = False) -> tuple[float, str]:
        """Return (price_cny_per_kg, source_label)."""
        basis = material.metal_basis
        if basis and material.form_factor:
            q = self.quotes(force=force).get(basis)
            if q:
                price = round(q.cny_per_kg * material.form_factor, 2)
                return price, f"{q.source} {q.asof} ×{material.form_factor}"
        return material.price_cny_per_kg, "static"


def apply_market_prices(
    shop: ShopData, svc: PriceService | None, *, force: bool = False
) -> tuple[ShopData, dict[str, str]]:
    """Return (shop with live prices, {material_key: source_label})."""
    if svc is None:
        return shop, {k: "static" for k in shop.materials}
    materials = dict(shop.materials)
    sources: dict[str, str] = {}
    for k, m in shop.materials.items():
        price, src = svc.price_for(m, force=force)
        sources[k] = src
        if price != m.price_cny_per_kg:
            materials[k] = replace(m, price_cny_per_kg=price)
    return replace(shop, materials=materials), sources


# ---- process-wide service selected by env ---------------------------------
_SERVICE: PriceService | None = None


def get_price_service() -> PriceService:
    global _SERVICE
    if _SERVICE is None:
        kind = os.environ.get("CNC_PRICE_FEED", "none").lower()
        ttl = float(os.environ.get("CNC_PRICE_TTL", "900"))
        feed: PriceFeed | None
        if kind in ("sina", "sina-shfe", "shfe"):
            feed = SinaShfeFeed()
        else:
            feed = None
        _SERVICE = PriceService(feed, ttl_s=ttl)
    return _SERVICE
