"""Live metal-price feeds (China domestic) with a pluggable adapter design.

Primary adapter: SHFE futures via Sina (`hq.sinajs.cn`) — free, real-time
during trading, no API key. Main-contract price (¥/tonne) is used as a spot
proxy and converted to ¥/kg. The HTTP call is injectable so parsing is unit-
tested offline (this sandbox's network whitelist blocks finance hosts).

Adapters return {metal -> MetalQuote} for the exchange metals AL/CU/ZN/NI/SN/SS;
the pricing service maps each material to its `metal_basis` and applies a
`form_factor` (raw metal → finished bar/plate). Alloys with no exchange basis
(titanium, plastics) keep their static price.
"""
from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class MetalQuote:
    metal: str            # 'AL' | 'CU' | 'ZN' | 'NI' | 'SN' | 'SS'
    cny_per_kg: float
    source: str           # e.g. 'SHFE沪铝'
    asof: str             # date/time string from the feed


class PriceFeed:
    name = "base"

    def fetch(self) -> dict[str, MetalQuote]:
        return {}


class StaticFeed(PriceFeed):
    """No live data — callers fall back to each material's static price."""
    name = "static"


def _sina_get(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("gbk", errors="replace")   # Sina serves GBK


class SinaShfeFeed(PriceFeed):
    """SHFE main-contract futures via Sina hq.sinajs.cn (¥/tonne → ¥/kg)."""
    name = "sina-shfe"

    SYMBOLS = {"AL": "nf_AL0", "CU": "nf_CU0", "ZN": "nf_ZN0",
               "NI": "nf_NI0", "SN": "nf_SN0", "SS": "nf_SS0"}
    LABEL = {"AL": "SHFE沪铝", "CU": "SHFE沪铜", "ZN": "SHFE沪锌",
             "NI": "SHFE沪镍", "SN": "SHFE沪锡", "SS": "SHFE不锈钢"}
    # Plausible ¥/tonne ranges, used to reject a mis-parsed field.
    RANGE = {"AL": (8_000, 40_000), "CU": (40_000, 120_000), "ZN": (10_000, 40_000),
             "NI": (80_000, 300_000), "SN": (120_000, 400_000), "SS": (8_000, 30_000)}

    def __init__(self, http=None, timeout: float = 8.0):
        self._http = http or (lambda url: _sina_get(url, timeout))

    def fetch(self) -> dict[str, MetalQuote]:
        url = "https://hq.sinajs.cn/list=" + ",".join(self.SYMBOLS.values())
        return self.parse(self._http(url))

    def parse(self, text: str) -> dict[str, MetalQuote]:
        sym_to_metal = {v: k for k, v in self.SYMBOLS.items()}
        out: dict[str, MetalQuote] = {}
        for line in text.splitlines():
            if "hq_str_nf_" not in line or '="' not in line:
                continue
            try:
                key = line.split("hq_str_", 1)[1].split("=", 1)[0].strip()  # nf_AL0
                metal = sym_to_metal.get(key)
                if not metal:
                    continue
                body = line.split('="', 1)[1].rstrip().rstrip(";").strip('"')
                fields = body.split(",")
                if len(fields) < 8:
                    continue
                price_ton = self._pick_price(fields, metal)
                if price_ton is None:
                    continue
                asof = fields[-1].strip() if fields[-1].strip() else time.strftime("%Y-%m-%d")
                out[metal] = MetalQuote(metal, round(price_ton / 1000.0, 3),
                                        self.LABEL[metal], asof)
            except (IndexError, ValueError):
                continue
        return out

    def _pick_price(self, fields: list[str], metal: str) -> float | None:
        """Latest price for the nf_ layout (idx 8), with defensive fallbacks.

        Field order can drift, so we prefer the documented latest-price index,
        fall back to settlement/open, then any value in the metal's sane range.
        """
        lo, hi = self.RANGE[metal]
        for idx in (8, 9, 2, 3):
            try:
                v = float(fields[idx])
                if lo <= v <= hi:
                    return v
            except (ValueError, IndexError):
                continue
        for f in fields:
            try:
                v = float(f)
                if lo <= v <= hi:
                    return v
            except ValueError:
                continue
        return None
