"""Live-price feed parsing + the spot→stock conversion model and fallback."""
from __future__ import annotations

from cnc.engine import load
from cnc.geometry import metrics_from_stl_bytes
from cnc.pricing import (
    PriceService,
    apply_market_prices,
    get_price_service,
)
from cnc.pricing.feed import MetalQuote, SinaShfeFeed, StaticFeed
from cnc.service import QuoteRequest, build_quote
from cnc.tests.fixtures import cube_stl

# A faithful Sina hq_str_nf_ sample (¥/tonne); latest price sits at field index 8.
SINA_SAMPLE = (
    'var hq_str_nf_AL0="沪铝主连,210000,20100,20300,20050,20150,20130,20140,20145,20135,'
    '20100,5,7,300000,120000,SHFE,AL,2026-06-09";\n'
    'var hq_str_nf_CU0="沪铜主连,800000,74800,75200,74600,75000,74950,75010,75060,75020,'
    '74800,3,4,200000,90000,SHFE,CU,2026-06-09";\n'
    'var hq_str_nf_SS0="不锈钢主连,140000,13900,14100,13850,14000,13980,13990,14020,14005,'
    '13900,2,5,150000,60000,SHFE,SS,2026-06-09";\n'
)


def test_sina_parse_sample():
    feed = SinaShfeFeed(http=lambda url: SINA_SAMPLE)
    q = feed.fetch()
    assert set(q) >= {"AL", "CU", "SS"}
    assert abs(q["AL"].cny_per_kg - 20.145) < 1e-6   # 20145 ¥/t ÷ 1000
    assert abs(q["CU"].cny_per_kg - 75.06) < 1e-6
    assert q["AL"].source == "SHFE沪铝" and q["AL"].asof == "2026-06-09"


def test_sina_parse_rejects_out_of_range():
    # a garbage line must be skipped, not yield an absurd price
    bad = 'var hq_str_nf_AL0="x,0,0,0,0,0,0,0,0,0,SHFE,AL,2026-06-09";\n'
    assert SinaShfeFeed(http=lambda url: bad).fetch() == {}


def test_price_service_applies_form_factor():
    shop = load()
    svc = PriceService(SinaShfeFeed(http=lambda url: SINA_SAMPLE))
    # AL6061: spot 20.145 × form_factor 1.75 ≈ 35.25
    price, src = svc.price_for(shop.material("AL6061"))
    assert abs(price - round(20.145 * 1.75, 2)) < 1e-6
    assert "SHFE沪铝" in src
    # Titanium has no exchange basis -> static price, source 'static'
    price_ti, src_ti = svc.price_for(shop.material("TITANIUM_TC4"))
    assert price_ti == shop.material("TITANIUM_TC4").price_cny_per_kg
    assert src_ti == "static"


def test_price_service_falls_back_when_feed_empty():
    shop = load()
    svc = PriceService(SinaShfeFeed(http=lambda url: "garbage"))
    price, src = svc.price_for(shop.material("AL6061"))
    assert price == shop.material("AL6061").price_cny_per_kg
    assert src == "static"


def test_static_feed_is_noop():
    shop = load()
    svc = PriceService(StaticFeed())
    assert svc.price_for(shop.material("AL6061"))[1] == "static"


def test_apply_market_prices_updates_shop_and_sources():
    shop = load()
    svc = PriceService(SinaShfeFeed(http=lambda url: SINA_SAMPLE))
    new_shop, sources = apply_market_prices(shop, svc)
    assert new_shop.material("AL6061").price_cny_per_kg != shop.material("AL6061").price_cny_per_kg
    assert "SHFE" in sources["AL6061"]
    assert sources["TITANIUM_TC4"] == "static"
    # original shop untouched (immutability)
    assert shop.material("AL6061").price_cny_per_kg == 35.0


def test_quote_carries_price_source():
    shop = load()
    svc = PriceService(SinaShfeFeed(http=lambda url: SINA_SAMPLE))
    new_shop, sources = apply_market_prices(shop, svc)
    q = build_quote(metrics_from_stl_bytes(cube_stl(50.0)),
                    QuoteRequest(material="AL6061", quantity=1), new_shop,
                    price_sources=sources)
    assert "SHFE" in q["input"]["price_source"]
    assert q["input"]["material_price_cny_per_kg"] == new_shop.material("AL6061").price_cny_per_kg


def test_caching_reuses_quotes():
    calls = {"n": 0}
    def http(url):
        calls["n"] += 1
        return SINA_SAMPLE
    svc = PriceService(SinaShfeFeed(http=http), ttl_s=999)
    svc.quotes(); svc.quotes(); svc.quotes()
    assert calls["n"] == 1                 # cached within TTL


def test_stale_cache_is_abandoned():
    import time as _t
    calls = {"n": 0}
    def http(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return SINA_SAMPLE        # first fetch succeeds
        raise RuntimeError("feed down")  # subsequent fetches fail
    svc = PriceService(SinaShfeFeed(http=http), ttl_s=0, max_age_s=1.0)
    assert svc.quotes()                 # first call populates cache
    svc._ts = _t.time() - 10            # pretend the cache is 10s old (> max_age 1s)
    assert svc.quotes() == {}           # too stale + feed down → fall back to static


def test_default_service_is_static(monkeypatch):
    import cnc.pricing as pricing
    monkeypatch.delenv("CNC_PRICE_FEED", raising=False)
    pricing._SERVICE = None                # reset singleton
    svc = get_price_service()
    assert svc.feed is None
    assert svc.quotes() == {}
