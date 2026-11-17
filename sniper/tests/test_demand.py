"""Popyt (2026-11-16, analyse_data/POPYT.md): wycena ceną ofert, które zeszły, klasy popytu zamiast rotacji,
wariant ogłoszenia (bateria, gwarancja), linia „Popyt” w alercie."""
import json
import sqlite3
from types import SimpleNamespace

from sniper.comparables import Comparables, apply_clearing
from sniper.experiment import finish_medians
from sniper.market import apply_market_rules, demand, has_warranty, niche_limit, price_factor, value_factor
from sniper.tests.test_categories import phone

CFG = SimpleNamespace(min_market_month=10.0, niche_turnover=0.10, niche_max_share=0.5, min_turnover=0.20,
                      market_categories=("phone", "tablet", "console"), no_box_factor=0.88, low_demand_max_share=0.3,
                      comparable_factor=1.2, rule_min_profit=300.0, rule_margin_cheap=28.0,
                      rule_margin_expensive=25.0, rule_cheap_sale=1000.0, rule_expensive_sale=2500.0,
                      rule_deal_score=8.0, rule_costs=46.0, pl_bonus=0.0, demand_rules=True, demand_min_fresh=6,
                      demand_hot_share=0.5, demand_hot_week=3.0, demand_slow_share=0.15, demand_slow_week=2.0,
                      demand_dead_share=0.10, demand_dead_week=0.7, demand_dead_max_share=0.5,
                      demand_slow_profit=1.5, demand_hot_bonus=0.5, demand_normal_factor=1.1,
                      demand_slow_factor=1.0, low_battery_pct=80.0, low_battery_factor=0.95, warranty_factor=1.05,
                      demand_hot_min_sold=2, demand_no_sales_clear=10)


def level(estimate=1000, median=1100, fresh=20, gone7=6, stale=10, active=30, gone=10, sold=0, clear=None,
          days=14.0, label="model"):
    clear = clear if clear is not None else []
    return {"label": label, "n_active": active, "n_gone": gone, "median": median, "estimate": estimate,
            "estimate_old": estimate, "p25": median * 0.9, "p75": median * 1.1, "gone_median": None,
            "words": ["x"], "n_clear": len(clear), "clear_median": sorted(clear)[len(clear) // 2] if clear else None,
            "n_sold": sold, "sold_median": None, "n_fast": len(clear), "n_fresh": fresh, "n_gone7": gone7,
            "n_stale": stale, "fresh_days": days}


def deal(value, score=9):
    return {"is_deal": True, "score": float(score), "market_value_pln": value, "in_medians": True, "median_used": 1,
            "red_flags": [], "gpu_model": None, "laptop_model": None}


# ---------------------------------------------------------------------------------------------- wycena
def test_clearing_estimate_from_offers_that_sold():
    """reMarkable 2 (07.11): aktywne ~1600 (wiszą), zeszły po ~1100-1300 → szacunek z zeszłych, nie z aktywnych."""
    levels = [level(estimate=1442, median=1600, clear=[1017, 1113, 1184, 1284, 1307])]
    apply_clearing(levels)
    assert levels[0]["estimate"] == 1184 and "zeszły" in levels[0]["estimate_basis"]
    assert levels[0]["estimate_old"] == 1442


def test_clearing_ratio_carries_to_detailed_level():
    """Wariant (pojemność) ma mało zeszłych - mediana aktywnych × współczynnik zejścia ogólnego poziomu."""
    detail = level(estimate=1013, median=1112, clear=[819, 1000], label="model + pojemność")
    general = level(estimate=1096, median=1194, clear=[900, 950, 1000, 1007, 1010, 1050, 1100], label="model")
    apply_clearing([detail, general])
    assert general["estimate"] == 1007
    ratio = 1007 / 1194
    assert abs(detail["estimate"] - ratio * 1112) < 1 and detail["clear_ratio"] == ratio


def test_clearing_without_sales_data_is_careful():
    """Nic nie zeszło (MatePad 11, tablety graficzne) - p25 aktywnych, nigdy wyżej niż dawny szacunek."""
    lv = level(estimate=950, median=1000, clear=[])
    apply_clearing([lv])
    assert lv["estimate"] == 900 and "p25" in lv["estimate_basis"]
    lower = level(estimate=891, median=1000, clear=[])
    assert apply_clearing([lower])[0]["estimate"] == 891                            # nigdy wyżej niż dawny
    hot = level(estimate=1700, median=1750, clear=[1800, 1850, 1900, 1950, 2000])    # zeszły drożej niż mediana
    apply_clearing([hot])
    assert hot["estimate"] == 1750                                                   # najwyżej mediana / dawny


def test_old_file_levels_unchanged():
    old = {"label": "x", "n_active": 10, "n_gone": 5, "median": 1000, "estimate": 950, "p25": 900}
    assert apply_clearing([old])[0]["estimate"] == 950


def _demand_db(path, rows, fresh_days=14.0):
    """Jak analizator od 2026-11-16: flagi popytu + meta fresh_days."""
    from sniper.guides import tokens
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE VIRTUAL TABLE offers USING fts5(words, category UNINDEXED, price UNINDEXED, gone UNINDEXED,
                                               cond UNINDEXED, fast UNINDEXED, sold UNINDEXED, fresh UNINDEXED,
                                               gone7 UNINDEXED, stale UNINDEXED,
                                               tokenize = 'unicode61 remove_diacritics 2');
        CREATE VIRTUAL TABLE vocab USING fts5vocab(offers, 'row');
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);""")
    db.executemany("INSERT INTO offers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   [(" ".join(tokens(t)), c, p, g, "used", *flags) for t, c, p, g, flags in rows])
    db.execute("INSERT INTO meta VALUES ('rows', ?)", (str(len(rows)),))
    db.execute("INSERT INTO meta VALUES ('cond', '1')")
    db.execute("INSERT INTO meta VALUES ('demand', '1')")
    db.execute("INSERT INTO meta VALUES ('fresh_days', ?)", (json.dumps({"tablet": fresh_days, "phone": fresh_days}),))
    db.commit()
    db.close()


def _remarkable_rows():
    """Wiszące reMarkable po ~1600-1900 i kilka, które zeszły po ~1100-1300 (jak na kopii 07.11)."""
    rows = [(f"reMarkable 2 tablet {i}", "tablet", 1600 + 25 * i, 0, (0, 0, 0, 0, 1)) for i in range(14)]
    rows += [(f"reMarkable 2 nowy {i}", "tablet", 1500 + 50 * i, 0, (0, 0, 1, 0, 0)) for i in range(4)]
    rows += [(f"reMarkable 2 + marker {i}", "tablet", p, 1, (1, 0, 1, 1, 0))
             for i, p in enumerate((1017, 1113, 1184, 1284, 1307))]
    rows += [(f"iPad 11 A16 128GB {i}", "tablet", 1650 + 10 * i, 1, (1, int(i < 2), 1, 1, 0)) for i in range(9)]
    rows += [(f"iPad 11 A16 128GB etui {i}", "tablet", 1700 + 20 * i, 0, (0, 0, int(i < 2), 0, 0)) for i in range(8)]
    rows += [(f"Huawei MatePad 11 {i}", "tablet", 900 + 10 * i, 0, (0, 0, int(i < 9), 0, int(i >= 9)))
             for i in range(40)]
    rows += [(f"Huawei MatePad 11 tanio {i}", "tablet", 379, 1, (1, 0, 1, 1, 0)) for i in range(1)]
    # reszta rynku - żeby „huawei” / „matepad” / „remarkable” były rzadkie (znaczące) jak w prawdziwej bazie
    rows += [(f"Lenovo Tab P{i} 64GB", "tablet", 500 + i, i % 2, (0, 0, 0, 0, 0)) for i in range(150)]
    return rows


def test_comparables_levels_with_demand_flags(tmp_path):
    _demand_db(tmp_path / "comparables.db", _remarkable_rows())
    comps = Comparables(tmp_path / "comparables.db")
    rm = comps.levels("tablet", "reMarkable 2 + Folio", "Bardzo dobry")
    assert rm and rm[0]["n_clear"] == 5 and rm[0]["estimate"] == 1184 and rm[0]["estimate_old"] > 1500
    assert rm[0]["fresh_days"] == 14.0 and rm[0]["n_fresh"] == 9 and rm[0]["n_stale"] == 14
    ipad = comps.levels("tablet", "iPad A16 128GB", "Bardzo dobry")
    assert ipad and ipad[0]["n_sold"] == 2
    found = demand(ipad, CFG)
    assert found["class"] == "gorący" and abs(found["share"] - 9 / 11) < 1e-9
    assert demand(comps.levels("tablet", "Huawei MatePad 11", "Bardzo dobry"), CFG)["class"] == "martwy"
    from sniper.comparables import describe_levels
    assert "zeszło (≤ 3 dni od wystawienia albo potwierdzona sprzedaż) 5" in describe_levels(rm)


def test_comparables_parts_keep_old_estimate(tmp_path):
    """Podzespoły (decyzja 15.11) - bez wyceny ceną zejścia."""
    rows = [(f"RTX 3060 12GB {i}", "gpu", 1000 + 10 * i, 0, (0, 0, 1, 0, 1)) for i in range(10)]
    rows += [(f"RTX 3060 12GB tanio {i}", "gpu", 700, 1, (1, 0, 1, 1, 0)) for i in range(6)]
    _demand_db(tmp_path / "comparables.db", rows)
    lv = Comparables(tmp_path / "comparables.db").levels("gpu", "Karta RTX 3060 12GB", None)
    assert lv and lv[0]["estimate"] == lv[0]["estimate_old"] and "fresh_days" not in lv[0]


# ---------------------------------------------------------------------------------------------- klasy
def test_demand_classes():
    assert demand([level(fresh=8, gone7=7, days=14.6, sold=2)], CFG)["class"] == "gorący"   # iPad A16, 2 sprzedaże
    assert demand([level(fresh=67, gone7=16, days=14.7, sold=1)], CFG)["class"] == "normalny"   # S23: 24%, 7,6 / tydz.
    assert demand([level(fresh=17, gone7=3, days=14.6)], CFG)["class"] == "wolny"       # Tab S6 Lite: 1,4 / tydz.
    assert demand([level(fresh=11, gone7=1, days=14.6)], CFG)["class"] == "martwy"      # MatePad 11
    # mało świeżych ofert: martwy tylko, gdy ≥ 70% aktywnych wisi; inaczej bez klasy
    assert demand([level(fresh=1, gone7=0, stale=26, active=28)], CFG)["class"] == "martwy"   # Wacom
    assert demand([level(fresh=3, gone7=3, stale=2, active=5)], CFG)["class"] is None           # Pixel 10a
    # potwierdzone sprzedaże = ktoś kupuje
    assert demand([level(fresh=11, gone7=1, sold=3)], CFG)["class"] == "wolny"
    assert demand([{"label": "stary plik", "n_active": 3, "n_gone": 1}], CFG) is None


def test_disappearing_without_sales_is_not_demand():
    """17.11: iPad 10 64 GB (kupiony ~900 zł, nie schodzi za 1100) - 8 z 12 nowych ofert zniknęło w tydzień, ale
    żadna nie była potwierdzoną sprzedażą (usunięte / ukryte) → nie „gorący”; przy ≥ 10 takich zniknięć „wolny”."""
    ipad10 = demand([level(fresh=12, gone7=8, days=14.7, sold=0, clear=[1100] * 9)], CFG)
    assert ipad10["class"] == "normalny" and "potwierdzonych" in ipad10["note"]
    many = demand([level(fresh=40, gone7=20, days=14.7, sold=0, clear=[1100] * 12)], CFG)
    assert many["class"] == "wolny" and "bez żadnej potwierdzonej sprzedaży" in many["note"]
    offer = phone(category="tablet", title="iPad 10 gen 64 GB")
    assert value_factor([level(fresh=12, gone7=8, sold=0)], offer, CFG) == 1.0      # bez sprzedaży AI ≤ dane
    # iPad 10 za 856 + wysyłka: AI 1380, szacunek 1245 → najwyżej 1245, zysk < 300 → nie okazja
    ev = finish_medians(deal(1380), phone(category="tablet", title="iPad 10 gen", price=850.0, total_price=856.0,
                                           description="64 GB, bateria 99%"),
                        CFG, [level(estimate=1245, median=1335, fresh=12, gone7=8, sold=0, clear=[1245] * 6)])
    assert ev["market_value_pln"] == 1245 and ev["is_deal"] is False


def test_value_factor_by_class():
    hot, slow = [level(fresh=8, gone7=7, sold=2)], [level(fresh=17, gone7=3, sold=1)]
    offer = phone(category="tablet", title="iPad A16")
    assert value_factor(hot, offer, CFG) == 1.2
    assert value_factor([level(fresh=67, gone7=16, sold=1)], offer, CFG) == 1.1
    assert value_factor(slow, offer, CFG) == 1.0
    assert value_factor(hot, phone(category="tablet", title="reMarkable 2"), CFG) == 1.0      # czytnik
    assert value_factor(slow, phone(category="gpu", title="RTX 3060"), CFG) == 1.2           # poza zasięgiem
    assert value_factor([{"n_active": 3, "n_gone": 1}], offer, CFG) == 1.2                   # stary plik


def test_dead_market_limit_and_gate():
    dead = [level(estimate=780, median=1000, fresh=11, gone7=1)]
    offer = phone(category="tablet", title="Huawei MatePad 11", price=530.0, total_price=545.0, description="")
    limit = niche_limit(dead, offer, CFG)
    assert limit[0] == 390 and "martwy popyt" in limit[1]
    ev = apply_market_rules(deal(780), offer, CFG, dead)
    assert ev["is_deal"] is False and ev["market"]["class"] == "martwy"
    assert niche_limit([level(fresh=67, gone7=16)], offer, CFG) is None                      # normalny - bez limitu


def test_slow_market_needs_bigger_profit():
    slow = [level(estimate=1000, median=1100, fresh=17, gone7=3)]
    offer = phone(category="tablet", title="Galaxy Tab S6 Lite", price=520.0, total_price=535.0, description="")
    ev = finish_medians(deal(1000), offer, CFG, slow)
    # zysk 1000 - 535 - 46 = 419 ≥ 300, ale < 1,5 × 300 → nie okazja
    assert ev["is_deal"] is False and any("wolny popyt" in f for f in ev["red_flags"])
    cheap = phone(category="tablet", title="Galaxy Tab S6 Lite", price=400.0, total_price=415.0, description="")
    ev = finish_medians(deal(1000), cheap, CFG, slow)
    assert ev["is_deal"] is True and ev["score"] == 7.0                                       # 8 - 1


def test_hot_market_bonus_and_list_price():
    hot = [level(estimate=1700, median=1800, fresh=8, gone7=7, sold=2)]
    offer = phone(category="tablet", title="iPad A16 128GB", price=1100.0, total_price=1130.0,
                  description="Na gwarancji, z pudełkiem")
    ev = finish_medians(deal(2100), offer, CFG, hot)
    # gwarancja ×1,05 → szacunek 1785; gorący ×1,2 → wycena najwyżej 2142
    assert ev["market_value_pln"] == 2100 and ev["is_deal"] is True and ev["score"] == 9.5
    assert ev["market"]["class"] == "gorący" and ev["market"]["list_price"] == 1890           # mediana × 1,05
    assert "potwierdzonych sprzedaży 2" in ev["market"]["text"]


def test_ai_value_capped_by_class():
    """reMarkable-podobny wolny model: AI wycenia 1800, program przepuszcza najwyżej szacunek × 1,0."""
    slow = [level(estimate=1184, median=1600, fresh=17, gone7=3)]
    offer = phone(category="tablet", title="Kobo-like tablet do notatek", price=1200.0, total_price=1215.0,
                  description="")
    ev = finish_medians(deal(1800), offer, CFG, slow)
    assert ev["market_value_pln"] == 1184 and ev["is_deal"] is False


# ---------------------------------------------------------------------------------------------- wariant
def test_price_factor_battery_and_warranty():
    low = phone(title="iPhone 12 64GB", description="Kondycja baterii 76%, sprawny")
    assert round(price_factor(low, CFG)[0], 3) == 0.95
    assert price_factor(phone(title="iPhone 12", description="Bateria 91%"), CFG)[0] == 1.0
    assert price_factor(phone(category="tablet", title="iPad", description="bateria 70%"), CFG)[0] == 1.0
    assert has_warranty(phone(description="Na gwarancji do 2027, faktura VAT"))
    assert not has_warranty(phone(description="Telefon bez gwarancji"))
    assert not has_warranty(phone(description="Gwarancja się skończyła"))
    assert not has_warranty(phone(description="Brak paragonu"))
    both = phone(description="Bez pudełka, bateria 75%, na gwarancji")
    factor, notes = price_factor(both, CFG)
    assert abs(factor - 0.88 * 0.95 * 1.05) < 1e-9 and len(notes) == 3


def test_push_message_shows_demand():
    from sniper.push import build_deal_message
    offer = {"title": "iPad A16", "price": 1100, "total_price": 1130, "category": "tablet", "url": "u"}
    ev = {"score": 9.5, "max_buy_price_pln": 1500, "potential_profit_pln": 900, "market_value_pln": 2100,
          "market": {"text": "gorący (schodzi szybko): w tydzień schodzi 88% nowych ofert", "list_price": 1890}}
    message = build_deal_message(offer, ev, "t")["message"]
    assert "Popyt: gorący (schodzi szybko)" in message and "wystaw ~1 890 zł" in message


def test_http_error_log_is_readable(caplog):
    """16.11 (log pilota): 404 shipping_details jako strona HTML vinted.fr - w logu tytuł strony i przekierowanie,
    a nie 300 znaków znaczników."""
    import asyncio
    import logging

    import httpx

    from sniper.session import VintedSession

    def handler(request):
        if request.url.host == "www.vinted.pl":
            return httpx.Response(302, headers={"location": "https://www.vinted.fr" + request.url.path})
        return httpx.Response(404, headers={"content-type": "text/html; charset=utf-8"},
                              text="<!DOCTYPE html><html><head><title>La page n&#39;existe pas</title>"
                                   "<link hreflang='fr-fr' href='https://www.vinted.fr/x'></head></html>")

    async def scenario():
        session = VintedSession()
        session.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=session.client.headers,
                                           follow_redirects=True)
        try:
            await session.get_json("https://www.vinted.pl/api/v2/items/1/shipping_details")
        except httpx.HTTPStatusError:
            pass
        await session.close()

    with caplog.at_level(logging.WARNING, logger="sniper.session"):
        asyncio.run(scenario())
    line = next(r.getMessage() for r in caplog.records if "[HTTP]" in r.getMessage())
    assert "404 www.vinted.fr/api/v2/items/1/shipping_details (przekierowanie z www.vinted.pl)" in line
    assert "strona HTML „La page n'existe pas”" in line and "hreflang" not in line
