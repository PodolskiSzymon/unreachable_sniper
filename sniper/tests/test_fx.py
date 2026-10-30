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
    offer = laptop()
    offer.shipping = None
    ev = cap_without_shipping({"score": 9.0, "is_deal": True}, offer)
    assert ev["score"] == 7.0 and ev["score_ai"] == 9.0 and "brak kosztu wysyłki" in ev["red_flags"][-1]
    assert cap_without_shipping({"score": 9.0, "is_deal": True}, laptop())["score"] == 9.0
