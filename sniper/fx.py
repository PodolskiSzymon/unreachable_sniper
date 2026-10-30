"""Kursy walut z tego samego źródła, którego używa strona Vinted (2026-10-30, adres z przeglądarki użytkownika):
`https://cdn.jsdelivr.net/gh/prebid/currency-file@1/latest.json` - {"conversions": {"USD": {"PLN": 3.7, "EUR": 0.86,
…}, "GBP": {…}}} (1 jednostka waluty bazowej = tyle jednostek waluty docelowej).

Służą do przeliczenia wysyłki z waluty sprzedawcy na zł (`shipping_details` podaje np. EUR z Finlandii - bywa
> 100 zł, co zwykle zabija okazję). Plik pobierany bezpośrednio (CDN, nie Vinted - bez proxy) na starcie i co
`SNIPER_FX_REFRESH_H` godzin; aktualizuje `config.FX_TO_PLN` w miejscu. Błąd sieci albo kurs mocno odbiegający od
zapasowego (> 50% - pewnie zły format) → zostają dotychczasowe kursy.
"""
import asyncio
import logging
from datetime import date

import httpx

from . import config

log = logging.getLogger("sniper.fx")

FX_URL = "https://cdn.jsdelivr.net/gh/prebid/currency-file@1/latest.json"
HEADERS = {"Referer": "https://www.vinted.pl/", "sec-ch-ua-platform": '"Windows"',
           "User-Agent": config.BROWSER_USER_AGENT}
FALLBACK = dict(config.FX_TO_PLN)            # kursy zapasowe (config + SNIPER_FX_RATES) - punkt odniesienia


def rates_to_pln(data):
    """Plik prebid → {waluta: zł za 1 jednostkę}. Najpierw kursy bezpośrednie (waluta bazowa → PLN), potem
    przeliczone przez walutę bazową dla pozostałych."""
    tables = {base.upper(): table for base, table in ((data or {}).get("conversions") or {}).items()
              if isinstance(table, dict) and isinstance(table.get("PLN"), (int, float)) and table["PLN"] > 0}
    out = {base: float(table["PLN"]) for base, table in tables.items()}
    for table in tables.values():
        for code, rate in table.items():
            if isinstance(rate, (int, float)) and rate > 0:
                out.setdefault(code.upper(), float(table["PLN"]) / float(rate))
    out["PLN"] = 1.0
    return out


def apply(rates, target=None):
    """Wpisuje kursy do FX_TO_PLN (albo `target`), pomijając podejrzane. → liczba zaktualizowanych walut."""
    target = config.FX_TO_PLN if target is None else target
    changed = 0
    for code, rate in rates.items():
        reference = FALLBACK.get(code)
        if reference and not 0.5 * reference <= rate <= 1.5 * reference:
            log.warning("[FX] Pomijam kurs %s = %.4f zł (zapasowy %.4f) - podejrzany.", code, rate, reference)
            continue
        target[code] = round(rate, 6)
        changed += 1
    return changed


async def refresh(client):
    response = await client.get(FX_URL, params={"date": date.today().strftime("%Y%m%d")}, headers=HEADERS)
    response.raise_for_status()
    changed = apply(rates_to_pln(response.json()))
    log.info("[FX] Kursy walut zaktualizowane (%d walut, EUR = %.4f zł).", changed, config.FX_TO_PLN.get("EUR", 0))
    return changed


async def run(hours=12.0, client=None):
    """W tle przez cały czas pracy Snipera."""
    own = client is None
    client = client or httpx.AsyncClient(trust_env=False, timeout=20.0, follow_redirects=True)
    try:
        while True:
            try:
                await refresh(client)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("[FX] Nie pobrałem kursów walut (%r) - zostają dotychczasowe.", exc)
            await asyncio.sleep(max(hours, 0.5) * 3600)
    finally:
        if own:
            await client.aclose()
