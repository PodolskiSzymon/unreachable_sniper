"""Kursy walut z pliku Vinted (prebid) i brak kosztu wysyłki - 2026-10-30. Uruchom: python -m pytest sniper/tests"""
import asyncio

import httpx

from sniper import config, fx
from sniper.evaluator import cap_without_shipping
from sniper.extractor import shipping_pln
from sniper.tests.test_evaluator import laptop

PREBID = {"dataAsOf": "2026-10-30", "conversions": {
    "USD": {"PLN": 3.70, "EUR": 0.86, "HUF": 340.0, "GBP": 0.75},
    "GBP": {"PLN": 4.93, "USD": 1.333}}}


def test_rates_to_pln_from_prebid_file():
    rates = fx.rates_to_pln(PREBID)
    assert rates["PLN"] == 1.0 and rates["USD"] == 3.70 and rates["GBP"] == 4.93
    assert abs(rates["EUR"] - 3.70 / 0.86) < 1e-9                # ~4,30 zł
    assert abs(rates["HUF"] - 3.70 / 340.0) < 1e-9
    assert fx.rates_to_pln({}) == {"PLN": 1.0} and fx.rates_to_pln({"conversions": {"USD": {"EUR": 1}}}) == {"PLN": 1.0}


def test_apply_skips_suspicious_rates():
    target = {"EUR": 4.25}
    assert fx.apply({"EUR": 4.31, "HUF": 0.0109, "XYZ": 2.0}, target) == 3
    assert target["EUR"] == 4.31
    assert fx.apply({"EUR": 0.86}, target) == 0 and target["EUR"] == 4.31   # odwrotny kurs - pomijany


def test_refresh_updates_shipping_conversion(monkeypatch):
    rates = dict(config.FX_TO_PLN)
    monkeypatch.setattr(config, "FX_TO_PLN", rates)
    import sniper.extractor as extractor
    monkeypatch.setattr(extractor, "FX_TO_PLN", rates)
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), referer=request.headers.get("referer"))
        return httpx.Response(200, json=PREBID)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fx.refresh(client)

    assert asyncio.run(go()) >= 4
    assert seen["url"].startswith(fx.FX_URL + "?date=") and seen["referer"] == "https://www.vinted.pl/"
    assert shipping_pln(25.0, "EUR") == round(25.0 * 3.70 / 0.86, 2)       # Finlandia: 25 EUR ≈ 107,56 zł


def test_missing_shipping_caps_score():
    """Opcjonalny limit oceny (SNIPER_NO_SHIPPING_MAX_SCORE, np. 7 jak przed 16.11)."""
    offer = laptop()
    offer.shipping = None
    ev = cap_without_shipping({"score": 9.0, "is_deal": True}, offer, cap=7.0)
    assert ev["score"] == 7.0 and ev["score_ai"] == 9.0 and "nie podał kosztu wysyłki" in ev["red_flags"][-1]
    assert cap_without_shipping({"score": 9.0, "is_deal": True}, laptop())["score"] == 9.0


def test_missing_shipping_still_alerts_with_estimate():
    """16.11: shipping_details zwraca 404 dla wszystkich ofert - alert dalej idzie, koszt z szacunkiem wysyłki,
    w ntfy ostrzeżenie."""
    from sniper.config import SHIPPING_FALLBACK_PLN
    from sniper.extractor import build_offer, purchase_cost
    from sniper.push import build_deal_message
    from sniper.tests.test_scout import FIX
    offer = build_offer(10775259629, FIX["sidebar_active"], None)
    assert offer.shipping is None and offer.total_price == offer.price + SHIPPING_FALLBACK_PLN
    assert purchase_cost(offer) > offer.price + SHIPPING_FALLBACK_PLN           # + opłata Vinted
    ev = cap_without_shipping({"score": 9.0, "is_deal": True}, offer)
    assert ev["score"] == 9.0 and ev["shipping_assumed"] == SHIPPING_FALLBACK_PLN
    message = build_deal_message(offer.to_dict(), ev, "t")["message"]
    assert "Wysyłka nieznana" in message and f"przyjęto ~{SHIPPING_FALLBACK_PLN:.0f} zł" in message
