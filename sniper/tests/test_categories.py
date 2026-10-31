"""D5: wiele katalogów, wytyczne per kategoria, bramka ceny. Uruchom: python -m pytest sniper/tests"""
import asyncio
import json
import os
import time

import httpx
import pytest
from dataclasses import replace

from sniper.categories import (ANALYSIS_CATALOGS, CatalogSource, CatalogSpec, category_for, load_catalogs_json,
                               parse_catalogs, schedule)
from sniper.config import ScoutConfig, SmtpConfig
from sniper.evaluator import STATUS_EVALUATED, STATUS_FILTERED, OfferEvaluator, prefilter
from sniper.guides import GuideBook, match, parse_rows, tokens
from sniper.notifier import EmailNotifier
from sniper.push import build_deal_message
from sniper.scout import Scout, catalog_params
from sniper.session import VintedSession
from sniper.tests.test_evaluator import (EVAL_DEAL, FakeClient, FakeNotifier, ai_cfg, laptop, response, run,
                                        submit_and_wait)

# Fragment pliku z analizatora (analyzer/guide.py: table) - te same kolumny.
PHONE_GUIDE = """# Telefony – wytyczne oceny okazji (z danych rynku, 2026-10-25)

## Strategia kupującego
- Koszty jednej transakcji: Podbicie ~26 zł + wysyłka ~20 zł = **46 zł**.

<!-- komentarz dla człowieka -->
| grupa | ofert | p25 | mediana | p75 | PL / zagr. mediana | zniknęło (sprzed.) | sprzedam za ~ | max zakup | zysk / marża | % ofert ≤ max |
|---|---|---|---|---|---|---|---|---|---|---|
| iPhone 13 128 GB | 412 | 1 200 | 1 350 | 1 500 | 1 300 / 1 400 | 230 (12) | 1 300 | 754 | 500 / 38% | 3% |
| iPhone 13 Pro 128 GB | 190 | 1 700 | 1 900 | 2 100 | 1 850 / 1 950 | 120 (5) | 1 850 | 1 104 | 700 / 38% | 2% |
| Galaxy S23 256 GB | 80 | 1 500 | 1 700 | 1 900 | 1 600 / 1 750 | 40 (2) | 1 600 | 954 | 600 / 38% | 1% |
"""

LAPTOP_GUIDE = """# Laptopy
| grupa | ofert | p25 | mediana | p75 | PL / zagr. mediana | zniknęło (sprzed.) | sprzedam za ~ | max zakup | zysk / marża | % ofert ≤ max |
|---|---|---|---|---|---|---|---|---|---|---|
| RTX 4060 | 300 | 3 000 | 3 400 | 3 800 | 3 300 / 3 500 | 150 (9) | 3 358 | 2 300 | 1 000 / 30% | 5% |
| RTX 4060 Ti | 40 | 3 400 | 3 800 | 4 200 | 3 700 / 3 900 | 20 (1) | 3 700 | 2 600 | 1 050 / 28% | 4% |
| Intel Core i5 · 8 GB RAM (zintegrowana grafika) | 900 | 500 | 700 | 900 | 650 / 750 | 400 (20) | 650 | 304 | 300 / 46% | 2% |
"""


def guides(tmp_path):
    folder = tmp_path / "guides"
    folder.mkdir(exist_ok=True)
    (folder / "phone.md").write_text(PHONE_GUIDE, encoding="utf-8")
    return folder


def phone(**kw):
    data = dict(id=8001, url="https://www.vinted.pl/items/8001-iphone", title="iPhone 13 128GB czarny",
                price=700.0, total_price=715.0, brand="Apple", description="Sprawny, bateria 88%.",
                category="phone")
    data.update(kw)
    return laptop(**data)


# ----------------------------------------------------------------------------- katalogi
def test_category_for_catalogs():
    assert category_for(3580, "Legion RTX 4060") == "laptop"
    assert category_for("3661", "iPhone") == "phone"
    assert category_for(3582, "Karta graficzna RTX 3060 12GB") == "gpu"
    assert category_for(3582, "Pamięć RAM DDR4 16GB") == "ram"
    assert category_for(3583, "Dysk SSD NVMe 1TB") == "storage"
    assert category_for(3582, "Wentylator 120mm RGB") == "skip"
    assert category_for(3584, "Kabel HDMI") == "skip"
    assert category_for(3063, "karta microSD") is None          # dawny tryb jednego katalogu
    assert category_for("karty_pamieci", "x") is None


def test_parse_catalogs_and_weights():
    specs = parse_catalogs("3580:250-6100*3, 3661:100-3600, 3582, zle", "100", "900")
    assert [(s.catalog_id, s.price_from, s.price_to, s.share) for s in specs] == [
        (3580, "250", "6100", 3), (3661, "100", "3600", 1), (3582, "100", "900", 1)]
    analysis = parse_catalogs(ANALYSIS_CATALOGS)
    assert len(analysis) == 7 and analysis[0].share == 3      # laptopy domyślnie ×3
    order = [s.catalog_id for s in schedule(analysis)]
    assert len(order) == 9 and order.count(3580) == 3 and set(order) == {s.catalog_id for s in analysis}
    assert "telefony" in analysis[1].label and "100-3600" in analysis[1].label


def test_catalog_source_modes(tmp_path):
    single = CatalogSpec("3580", "100", "")
    assert CatalogSource("", tmp_path, single).get() == [single]
    assert [s.catalog_id for s in CatalogSource("3661, 3728", tmp_path, single).get()] == [3661, 3728]
    assert CatalogSource("3661", tmp_path, single).get()[0].price_from == "100"     # domyślny zakres

    auto = CatalogSource("auto", tmp_path, single)
    assert len(auto.get()) == 7                        # brak catalogs.json → zakresy z analizy
    path = tmp_path / "catalogs.json"
    path.write_text(json.dumps({"catalogs": [{"catalog_id": 3580, "price_from": 300, "price_to": 5000.0,
                                              "weight": 2}]}), encoding="utf-8")
    specs = auto.get()
    assert [(s.catalog_id, s.price_from, s.price_to, s.share) for s in specs] == [(3580, "300", "5000", 2)]
    path.write_text(json.dumps({"catalogs": [{"catalog_id": 3661, "price_from": 100, "price_to": 900}]}),
                    encoding="utf-8")
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert [s.catalog_id for s in auto.get()] == [3661]          # nowy plik bez restartu
    assert load_catalogs_json(tmp_path / "brak.json") == []


def test_catalog_params_use_spec():
    cfg = ScoutConfig(category="3580", price_from="100", price_to="")
    params = catalog_params(cfg, CatalogSpec(3661, "150", "3600"))
    assert (params["attribute_ids[catalog]"], params["price_from"], params["price_to"]) == ("3661", "150", "3600")
    assert catalog_params(cfg)["attribute_ids[catalog]"] == "3580" and "price_to" not in catalog_params(cfg)


# ----------------------------------------------------------------------------- wytyczne i bramka
def test_tokens_and_rows():
    assert tokens("iPhone13 Pro 128GB") == ["iphone", "13", "pro", "128", "gb"]
    assert tokens("PS5 Slim") == tokens("ps 5 slim") == ["playstation", "5", "slim"]
    rows = parse_rows(PHONE_GUIDE)
    assert [(r.name, r.sale, r.max_buy) for r in rows] == [
        ("iPhone 13 128 GB", 1300.0, 754.0), ("iPhone 13 Pro 128 GB", 1850.0, 1104.0),
        ("Galaxy S23 256 GB", 1600.0, 954.0)]


def test_match_prefers_specific_and_respects_variants():
    phones, laptops = parse_rows(PHONE_GUIDE), parse_rows(LAPTOP_GUIDE)
    assert [r.name for r in match(phones, "iPhone 13 128GB", "phone")] == ["iPhone 13 128 GB"]
    assert [r.name for r in match(phones, "iPhone 13 Pro 128 GB", "phone")] == ["iPhone 13 Pro 128 GB"]
    assert match(phones, "iPhone 13 Pro Max 128GB", "phone") == []      # wariantu Max nie ma w tabeli
    assert match(phones, "iPhone 13 mini", "phone") == []               # brak pamięci w tytule
    assert [r.name for r in match(laptops, "Lenovo Legion 5 Pro RTX4060 16GB", "laptop")] == ["RTX 4060"]
    assert [r.name for r in match(laptops, "MSI RTX 4060 Ti", "laptop")][0] == "RTX 4060 Ti"
    assert match(laptops, "Dell i5 8GB RAM, 5 lat", "laptop") == []     # zintegrowane - za luźne


def test_guidebook_paths_and_gate(tmp_path):
    folder = guides(tmp_path)
    fallback = tmp_path / "guidelines.md"
    fallback.write_text("RTX 4060: max 1950 zł", encoding="utf-8")
    book = GuideBook(folder, fallback)
    assert book.path("phone") == folder / "phone.md"
    assert book.path("laptop") == fallback and book.path(None) == fallback      # brak guides/laptop.md
    assert book.path("console") is None
    assert "komentarz" not in book.text("phone") and "iPhone 13 128 GB" in book.text("phone")
    assert book.gate("phone", "iPhone 13 128GB", 900.0, 1.25) is None            # 900 ≤ 754 × 1,25
    assert "max zakup 754" in book.gate("phone", "iPhone 13 128GB", 1000.0, 1.25)
    assert book.gate("phone", "iPhone 13 128GB", 1000.0, 0) is None              # bramka wyłączona
    assert book.gate("phone", "Nokia 3310", 5000.0, 1.25) is None                # brak wiersza → AI
    (folder / "laptop.md").write_text(LAPTOP_GUIDE, encoding="utf-8")
    assert book.path("laptop") == folder / "laptop.md"
    laptop_text = book.text("laptop")                        # dane rynku + ręczne wytyczne jako uzupełnienie
    assert laptop_text.startswith("# Laptopy") and "ręczne wytyczne użytkownika" in laptop_text
    assert laptop_text.endswith("RTX 4060: max 1950 zł") and "RTX 4060: max" not in book.text("phone")
    assert [r.name for r in book.match("laptop", "Legion RTX 4060")] == ["RTX 4060"]


# ----------------------------------------------------------------------------- ocena per kategoria
def test_category_filters(tmp_path):
    cfg = ai_cfg(tmp_path, price_max=3000.0, keywords=("rtx",), exclude_keywords=("thinkpad",),
                 category_exclude={"phone": ("icloud",)})
    assert prefilter(phone(), cfg) is None                       # słowa i ceny laptopów nie dotyczą telefonu
    assert prefilter(phone(total_price=5000.0, price=5000.0), cfg) is None
    assert "icloud" in prefilter(phone(description="Blokada iCloud"), cfg)
    assert prefilter(laptop(title="Thinkpad RTX"), cfg).startswith("słowo wykluczające")
    assert prefilter(laptop(category="laptop", title="Dell"), cfg) == "brak słów kluczowych"


def test_phone_offer_uses_phone_guide_and_market_card(tmp_path):
    guides(tmp_path)
    client = FakeClient(response({**EVAL_DEAL, "gpu_model": None, "laptop_model": "iPhone 13 128 GB",
                                  "margin_pct": 38, "sell_days_estimate": 4, "meets_profit_rule": True}))
    evaluator = OfferEvaluator(ai_cfg(tmp_path), FakeNotifier(), log_dir=tmp_path, client=client)
    record = run(evaluator.evaluate(phone()))
    assert record["status"] == STATUS_EVALUATED
    ev = record["evaluation"]
    assert (ev["margin_pct"], ev["sell_days_estimate"], ev["meets_profit_rule"]) == (74.2, 4, True)   # marżę liczy program: (3100 - 754,6 - 46) / 3100
    request = client.calls[0]
    system = request["system"][0]["text"]
    assert "kategoria: telefony" in system and "iPhone 13 Pro 128 GB" in system and "RTX 4060" not in system
    task = request["messages"][0]["content"][-1]["text"]
    assert "<karta_rynku>" in task and "| iPhone 13 128 GB |" in task and "Pro" not in task.split("<karta_rynku>")[1]
    evaluator.save(record)
    csv = (tmp_path / "evaluations.csv").read_text(encoding="utf-8-sig").splitlines()
    assert "kategoria" in csv[0] and ";phone;" in csv[1] and ";74;4;" in csv[1]


def test_gate_and_missing_guide_skip_ai(tmp_path):
    guides(tmp_path)
    client = FakeClient(response())
    evaluator = OfferEvaluator(ai_cfg(tmp_path), FakeNotifier(), log_dir=tmp_path, client=client)

    async def scenario():
        evaluator.submit(phone(price=1200.0, total_price=1215.0))
        evaluator.submit(phone(id=8002, category="console", title="PS5 Slim"))
        await asyncio.gather(*evaluator._tasks)

    run(scenario())
    assert client.calls == []
    records = [json.loads(x) for x in (tmp_path / "evaluations.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["status"] for r in records] == [STATUS_FILTERED, STATUS_FILTERED]
    assert "za drogo wg tabeli" in records[0]["prefilter_reason"]
    assert "brak wytycznych dla kategorii console" == records[1]["prefilter_reason"]


def test_laptop_without_category_still_uses_guidelines_md(tmp_path):
    client = FakeClient(response())
    evaluator = OfferEvaluator(ai_cfg(tmp_path), FakeNotifier(), log_dir=tmp_path, client=client)
    run(evaluator.evaluate(laptop()))
    system = client.calls[0]["system"][0]["text"]
    assert "maksymalna cena zakupu poniżej 1950 zł" in system and "laptopy gamingowe" in system


def test_push_shows_category_margin_days():
    msg = build_deal_message({"title": "iPhone 13", "total_price": 715, "price": 700, "category": "phone"},
                             {"score": 8, "max_buy_price_pln": 754, "potential_profit_pln": 540,
                              "margin_pct": 41.5, "sell_days_estimate": 3, "market_value_pln": 1300}, "t")
    assert msg["title"].startswith("Okazja 8/10 (telefony): iPhone 13")
    assert "(42%)" in msg["message"] and "~3 dni" in msg["message"] and "Sprzedam za ~1 300 zł" in msg["message"]


# ----------------------------------------------------------------------------- zwiadowca: wiele katalogów
def test_scout_rotates_catalogs_and_skips_other_parts():
    requests = []
    pages = {"3580": [{"id": 1, "title": "Legion RTX 4060"}],
             "3582": [{"id": 10, "title": "Wentylator 120mm"}, {"id": 11, "title": "RTX 3060 12GB"}]}
    polls = {"3580": 0, "3582": 0}

    def handler(request):
        catalog = request.url.params["attribute_ids[catalog]"]
        requests.append((catalog, request.url.params.get("price_from"), request.url.params.get("price_to")))
        polls[catalog] += 1
        items = list(pages[catalog])
        if polls[catalog] == 2:
            items += ([{"id": 20, "title": "Kabel zasilający"}, {"id": 21, "title": "Dysk SSD NVMe 1TB"}]
                      if catalog == "3582" else [{"id": 2, "title": "MSI RTX 4070"}])
        return httpx.Response(200, json={"items": [{**it, "user": {}} for it in items]})

    async def scenario():
        session = VintedSession()
        session.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=session.client.headers)
        cfg = ScoutConfig(category="3580", catalogs="3580:250-6100*1, 3582:150-2700", price_floor=0)
        scout = Scout(cfg, session, EmailNotifier(SmtpConfig()))
        inspected = []

        async def fake_inspect(item, spec, category):
            inspected.append((item["id"], spec.catalog_id, category))
        scout.inspect = fake_inspect
        for _ in range(4):
            await scout.poll_catalog()           # 3580 rozgrzewka, 3582 rozgrzewka, 3580, 3582
        await asyncio.gather(*scout._tasks)
        await session.close()
        return scout, inspected

    scout, inspected = asyncio.run(scenario())
    assert requests == [("3580", "250", "6100"), ("3582", "150", "2700")] * 2
    assert inspected == [(2, 3580, "laptop"), (21, 3582, "storage")]      # kabel pominięty bez detali
    assert scout._skipped == {"inna kategoria": 1}
    assert {k.split()[0]: v for k, v in scout._per_catalog.items()} == {"3580": [2, 1, 1, 0], "3582": [2, 2, 1, 0]}   # rozgrzewka nie liczy nowych
    assert scout.seen.maxlen >= 200                         # pamięć ID na 2 katalogi


def test_gate_without_capacity_uses_highest_variant(tmp_path):
    book = GuideBook(guides(tmp_path), tmp_path / "brak.md")
    (tmp_path / "guides" / "phone.md").write_text(PHONE_GUIDE + "| iPhone 13 256 GB | 50 | 1 | 1 | 1 | 1 / 1 | 1 (1) | "
                                                  "1 500 | 900 | 1 / 1% | 0% |\n", encoding="utf-8")
    assert book.gate("phone", "iPhone 13 stan bdb", 1100.0, 1.25) is None            # 1100 ≤ 900 × 1,25
    reason = book.gate("phone", "iPhone 13 stan bdb", 1200.0, 1.25)
    assert "max zakup 900" in reason and "2 wariantów" in reason
    assert book.gate("phone", "iPhone 13 Pro", 5000.0, 1.25) is not None             # Pro ma własny wiersz
    assert book.gate("phone", "iPhone 13 Pro Max", 5000.0, 1.25) is None             # brak wiersza → AI


def test_daily_ai_limit(tmp_path):
    client = FakeClient(response())
    evaluator = OfferEvaluator(ai_cfg(tmp_path, daily_limit_usd=0.001), FakeNotifier(), log_dir=tmp_path,
                               client=client)

    async def scenario():
        evaluator.submit(laptop())
        await asyncio.gather(*evaluator._tasks)
        evaluator.submit(laptop(id=7002))                 # pierwsze wywołanie przekroczyło $0,001
        await asyncio.gather(*evaluator._tasks)

    run(scenario())
    assert len(client.calls) == 1
    last = json.loads((tmp_path / "evaluations.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last["status"] == STATUS_FILTERED and last["prefilter_reason"].startswith("dzienny limit AI $0.001")
    evaluator.today = type(evaluator.today)()            # nowa doba
    assert evaluator.over_daily_limit() is None


def test_test_kits_in_push():
    from sniper.categories import test_kit
    assert test_kit("laptop").startswith("nic")
    assert "stanowisko A" in test_kit("gpu", "RTX 3060")
    assert "SO-DIMM→DIMM" in test_kit("ram", "Pamięć DDR4 SO-DIMM 16GB")
    assert "płyta DDR4" in test_kit("ram", "Corsair DDR4 2x16GB")
    assert "DDR5 SO-DIMM" in test_kit("ram", "DDR5 SODIMM 32GB do laptopa")
    assert "stanowisko B" in test_kit("ram", "Kingston Fury DDR5 32GB")
    assert "NVMe" in test_kit("storage", "Samsung 980 Pro NVMe 1TB") and "SATA" in test_kit("storage", "SSD 2TB")
    assert test_kit(None) is None
    msg = build_deal_message({"title": "RTX 4080 Gigabyte", "total_price": 2500, "category": "gpu"},
                             {"score": 8, "max_buy_price_pln": 2700, "potential_profit_pln": 900,
                              "test_needs": "zasilacz z 12V-2x6"}, "t")
    line = next(x for x in msg["message"].splitlines() if x.startswith("Do testu:"))
    assert "stanowisko A" in line and line.endswith("zasilacz z 12V-2x6")


def test_missing_equipment_warning(monkeypatch):
    from sniper.categories import test_kit, test_requirement
    assert [test_requirement("ram", t) for t in ("DDR5 32GB", "DDR4 SO-DIMM 8GB", "DDR5 SODIMM 16GB", "RAM 16GB")] == \
        ["ddr5", "sodimm_ddr4", "sodimm_ddr5", None]
    monkeypatch.setenv("SNIPER_TEST_HAVE", "gpu, ddr4, sodimm_ddr4, nvme, sata")
    assert test_kit("ram", "Kingston DDR4 16GB").startswith("✓ masz sprzęt")
    assert test_kit("ram", "Kingston Fury DDR5 32GB") == \
        "⚠ BRAK SPRZĘTU – do kupna potrzebujesz: płyta główna z DDR5 (np. H610M / B660M DDR5 + Pentium G7400)"
    assert "laptop / mini PC z DDR5 SO-DIMM" in test_kit("ram", "DDR5 SO-DIMM 16GB")
    assert test_kit("laptop").startswith("nic")                      # laptop / telefon - bez oznaczeń
    msg = build_deal_message({"title": "DDR5 32GB 6000", "total_price": 500, "category": "ram"},
                             {"score": 8}, "t")
    assert "⚠ BRAK SPRZĘTU" in msg["message"]


def test_enabled_categories():
    from sniper.categories import wanted_specs
    specs = parse_catalogs(ANALYSIS_CATALOGS)
    assert [s.catalog_id for s in wanted_specs(specs, ("laptop", "ram"))] == [3580, 3582, 3584]
    assert len(wanted_specs(specs, ())) == 7
    assert wanted_specs([CatalogSpec("3063")], ("phone",))[0].catalog_id == "3063"   # tryb jednego katalogu
    assert category_for(3661, "iPhone", ("laptop",)) == "skip"
    assert category_for(3582, "RTX 3060 + RAM DDR4 16GB", ("ram",)) == "ram"         # karta wyłączona → RAM
    assert category_for(3582, "RTX 3060 12GB", ("ram", "storage")) == "skip"


def test_scout_skips_disabled_catalogs():
    cfg = ScoutConfig(category="3580", catalogs="3580:250-6100*1,3661:100-3600,3582:150-2700",
                      categories=("phone", "gpu"))
    scout = Scout(cfg, VintedSession(), EmailNotifier(SmtpConfig()))
    assert sorted({scout.next_spec().catalog_id for _ in range(4)}) == [3582, 3661]


def test_handheld_console_kit_and_mail_rows():
    from sniper.categories import test_kit
    from sniper.notifier import ai_lines
    assert test_kit("console", "Nintendo DS lite").startswith("nic - ładowarka")
    assert test_kit("console", "Nintendo 3DS XL") .startswith("nic - ładowarka")
    assert test_kit("console", "Nintendo Switch OLED").startswith("nic w trybie przenośnym")
    assert test_kit("console", "PlayStation 5 Slim").startswith("telewizor")
    ai = {"status": "oceniona", "photos_sent": 6, "offer": {"category": "console", "title": "DS Lite"},
          "evaluation": {"score": 5, "laptop_model": "Nintendo DS lite", "red_flags": []}}
    labels = [k for k, _ in ai_lines(ai)]
    assert "Karta" not in labels and "Model" in labels
    ai["offer"]["category"] = "laptop"
    assert "Karta" in [k for k, _ in ai_lines(ai)]


def test_purchase_cost_includes_buyer_fee(tmp_path):
    from sniper.evaluator import offer_text
    from sniper.extractor import buyer_fee, purchase_cost
    assert buyer_fee(416.80) == 23.74                                 # S22 z Vinted 2026-10-26: 440,54 - 416,80
    assert purchase_cost({"price": 416.80, "total_price": 425.55}) == 449.29
    offer = phone(price=416.80, total_price=425.55)
    assert "KOSZT ZAKUPU (cena + wysyłka + opłata): 449.29 PLN" in offer_text(offer)
    book_gate = OfferEvaluator(ai_cfg(tmp_path), FakeNotifier(), client=FakeClient(response()))
    guides(tmp_path)
    assert book_gate.gate(phone(price=600.0, total_price=615.0)) is None          # 650 ≤ 754 × 1,25


def test_catalog_weights_override():
    from sniper.categories import apply_weights, parse_weights
    assert parse_weights("laptop:3, phone:3, 3582:2, zle") == {"laptop": 3, "phone": 3, "3582": 2}
    specs = apply_weights(parse_catalogs(ANALYSIS_CATALOGS), parse_weights("laptop:3,phone:3,ram:2"))
    shares = {s.catalog_id: s.share for s in specs}
    assert shares[3580] == 3 and shares[3661] == 3 and shares[3728] == 1 and shares[3582] == 2
    cfg = ScoutConfig(category="3580", catalogs="3580:250-6100,3661:100-3600,3728:100-3200",
                      categories=(), catalog_weights="laptop:3,phone:3")
    scout = Scout(cfg, VintedSession(), EmailNotifier(SmtpConfig()))
    order = [scout.next_spec().catalog_id for _ in range(7)]
    assert order.count(3580) == 3 and order.count(3661) == 3 and order.count(3728) == 1


def test_schedule_interleaves_catalogs():
    """Waga 6 = 6 skanów w cyklu, ale rozrzuconych - nigdy ten sam katalog dwa razy z rzędu."""
    from sniper.categories import apply_weights, parse_weights
    for catalogs in ("3580,3661,3728,3025", ANALYSIS_CATALOGS):
        specs = apply_weights(parse_catalogs(catalogs),
                              parse_weights("laptop:6,phone:6,tablet:2,console:2,gpu:1,ram:1,storage:1"))
        order = [s.catalog_id for s in schedule(specs)] * 2          # także na styku cykli
        assert all(a != b for a, b in zip(order, order[1:])), order


def test_pl_bonus_only_for_polish_deals(tmp_path):
    from sniper.evaluator import apply_pl_bonus
    from sniper.extractor import Seller
    pl = Seller(id=1, name="x", country="Polska", country_code="PL", feedback_count=5, feedback_reputation=1.0,
                stars=5.0, business=False)
    ro = Seller(id=2, name="y", country="Rumunia", country_code="RO", feedback_count=5, feedback_reputation=1.0,
                stars=5.0, business=False)
    ev = apply_pl_bonus({"score": 7.0, "is_deal": True}, phone(seller=pl), 1)
    assert ev["score"] == 8.0 and ev["score_ai"] == 7.0 and ev["pl_bonus"] == 1.0
    assert apply_pl_bonus({"score": 9.5, "is_deal": True}, phone(seller=pl), 1)["score"] == 10.0
    assert apply_pl_bonus({"score": 7.0, "is_deal": True}, phone(seller=ro), 1)["score"] == 7.0
    assert apply_pl_bonus({"score": 5.0, "is_deal": False}, phone(seller=pl), 1)["score"] == 5.0   # nie robi okazji
    client = FakeClient(response({**EVAL_DEAL, "score": 7}))
    record = run(OfferEvaluator(ai_cfg(tmp_path, pl_bonus=1.0), FakeNotifier(), client=client).evaluate(laptop()))
    assert record["evaluation"]["score"] == 9.0      # reguła spełniona → 8, sprzedawca z PL (laptop()) → +1
    system = client.calls[0]["system"][0]["text"]
    assert "premię za Polskę dolicza program" in system


def test_prompt_scoring_targets_capital_turnover():
    from sniper.evaluator import system_text
    text = system_text("x", "phone")
    assert "ograniczony kapitał" in text and "45%" in text and "800 zł" in text and "9 → 10" in text
    assert "- 8: okazja - reguła zysku spełniona" in text and "28%" in text and "25%" in text
    assert "orientacyjne" in text                                      # nie na sztywno


def _openrouter_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_openrouter_backend_request_cost_and_json_fallback(tmp_path):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.headers["authorization"] == "Bearer test"
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(400, text='{"error": "response_format json_schema is not supported"}')
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": "```json\n" + json.dumps(EVAL_DEAL) + "\n```"}}],
            "usage": {"prompt_tokens": 3000, "completion_tokens": 400, "cost": 0.0021,
                      "prompt_tokens_details": {"cached_tokens": 1000}}})

    cfg = ai_cfg(tmp_path, provider="openrouter", model="qwen/qwen-vl-test", base_url="https://openrouter.ai/api/v1",
                 effort="low")
    evaluator = OfferEvaluator(cfg, FakeNotifier(), client=_openrouter_client(handler))
    record = run(evaluator.evaluate(laptop()))
    assert record["status"] == STATUS_EVALUATED and record["evaluation"]["score"] == 8
    assert seen[0]["response_format"]["type"] == "json_schema" and seen[1]["response_format"]["type"] == "json_object"
    user = seen[1]["messages"][1]["content"]
    assert [c["type"] for c in user].count("image_url") == 3 and seen[1]["reasoning"] == {"effort": "low"}
    assert seen[1]["usage"] == {"include": True}
    assert evaluator.total["cost_reported"] == pytest.approx(0.0021)
    assert evaluator.total.cost_usd(0, 0) == pytest.approx(0.0021)     # dzienny limit liczy koszt z OpenRouter


def test_openrouter_errors_described(tmp_path):
    from sniper.evaluator import ApiError, OpenAICompatBackend
    backend = OpenAICompatBackend(ai_cfg(tmp_path, provider="openrouter", model="x/y",
                                         base_url="https://openrouter.ai/api/v1"), client=_openrouter_client(None))
    assert "OPENROUTER_API_KEY" in backend.describe(ApiError(401, "no"))
    assert "brak środków" in backend.describe(ApiError(402, "credits"))
    assert backend.is_retryable(ApiError(429, "slow")) and not backend.is_retryable(ApiError(400, "bad"))
    assert backend.is_photo_error(ApiError(400, "failed to fetch image url"))


def test_compare_lines(tmp_path):
    from sniper.evaluator import compare_lines
    old = {"status": STATUS_EVALUATED, "model": "gemini-3.8-flash",
           "offer": {"id": 7001, "title": "Legion"}, "evaluation": {"score": 8, "is_deal": True}}
    (tmp_path / "evaluations.jsonl").write_text(json.dumps(old) + "\n", encoding="utf-8")
    new = {"offer": {"id": 7001, "title": "Legion"}, "evaluation": {"score": 4, "is_deal": False}}
    text = "\n".join(compare_lines(tmp_path, [new], "qwen/x"))
    assert "ta sama decyzja „okazja / nie” 0/1" in text and "INNA DECYZJA" in text and "8 → 4" in text
    assert "brak wcześniejszych" in compare_lines(tmp_path, [new], "gemini-3.8-flash")[0]


def test_program_enforces_profit_rule(tmp_path):
    """S25 2026-10-26: AI dało 9/10 OKAZJA przy koszcie 1 899 zł, wartości 2 394 zł (marża 19%) - program to blokuje."""
    from sniper.evaluator import enforce_profit_rule, rule_max_buy
    cfg = ai_cfg(tmp_path, rule_min_profit=300.0, rule_margin_cheap=28.0, rule_margin_expensive=25.0,
                 rule_cheap_sale=1000.0, rule_expensive_sale=2500.0, rule_costs=46.0, rule_deal_score=8.0)
    s25 = phone(title="Galaxy S25 256GB", price=1805.0, total_price=1805.0)          # koszt z opłatą ~1 898 zł
    ev = enforce_profit_rule({"score": 9.0, "is_deal": True, "market_value_pln": 2394.0}, s25, cfg)
    assert ev["is_deal"] is False and ev["score"] == 6.0 and ev["score_ai"] == 9.0 and ev["rule_check"] == "naruszona"
    assert ev["margin_pct"] == pytest.approx(18.8, abs=0.1) and "marża 19%" in ev["red_flags"][-1]
    # 2394 zł: min. marża 28% - 3 pkt × (1394 / 1500) = 25,2% → zysk ≥ 604 zł → max zakup 2394 - 46 - 604
    assert ev["max_buy_price_pln"] == rule_max_buy(2394.0, cfg) == 1744
    assert rule_max_buy(700.0, cfg) == 354 and rule_max_buy(4000.0, cfg) == 2954         # 300 zł / 25%
    ok = enforce_profit_rule({"score": 7.0, "is_deal": True, "market_value_pln": 2394.0},
                             phone(price=1500.0, total_price=1500.0), cfg)               # koszt ~1 578 zł → 32%
    assert ok["is_deal"] is True and ok["score"] == 8.0 and ok["score_ai"] == 7.0 and ok["meets_profit_rule"]
    edge = enforce_profit_rule({"score": 8.0, "is_deal": True, "market_value_pln": 3000.0},
                               phone(price=2100.0, total_price=2100.0), cfg)             # koszt ~2 208 → 24,9%
    assert edge["is_deal"] is False and "min. 25% marży przy sprzedaży 3000 zł" in edge["red_flags"][-1]
    cheap = enforce_profit_rule({"score": 8.0, "is_deal": True, "market_value_pln": 700.0},
                                phone(price=330.0, total_price=330.0), cfg)              # zysk ~304 zł przy 43%
    assert cheap["is_deal"] is True
    low = enforce_profit_rule({"score": 8.0, "is_deal": True, "market_value_pln": 700.0},
                              phone(price=360.0, total_price=360.0), cfg)                # zysk ~273 zł < 300
    assert low["is_deal"] is False
    blind = enforce_profit_rule({"score": 8.0, "is_deal": True, "market_value_pln": None}, s25, cfg)
    assert blind["is_deal"] is False and blind["rule_check"] == "brak wyceny"


def test_cap_without_market_data(tmp_path):
    from sniper.evaluator import cap_without_market_data
    guides(tmp_path)
    book = GuideBook(tmp_path / "guides", tmp_path / "brak.md")
    gbc = phone(category="phone", title="Nintendo Gameboy Color Pokemon")
    ev = cap_without_market_data({"score": 9.0, "is_deal": True, "market_value_pln": 850}, gbc, book, 7)
    assert ev["score"] == 7.0 and ev["score_ai"] == 9.0 and "brak tego modelu w danych rynku" in ev["red_flags"][-1]
    known = cap_without_market_data({"score": 9.0, "is_deal": True}, phone(title="iPhone 13 128GB"), book, 7)
    assert known["score"] == 9.0
    nomem = cap_without_market_data({"score": 9.0, "is_deal": True}, phone(title="iPhone 13 stan bdb"), book, 7)
    assert nomem["score"] == 9.0                                       # wiersz bez pojemności też się liczy
    lap = cap_without_market_data({"score": 9.0, "is_deal": True}, laptop(category="laptop", title="Dell XPS"), book, 7)
    assert lap["score"] == 7.0                                         # laptopy bez danych też (30.10)


def _comparables_db(path, rows):
    """Ten sam układ co analyzer/comparables.py (FTS5 + vocab + meta)."""
    import sqlite3
    from sniper.guides import tokens
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE VIRTUAL TABLE offers USING fts5(words, category UNINDEXED, price UNINDEXED, gone UNINDEXED,
                                               tokenize = 'unicode61 remove_diacritics 2');
        CREATE VIRTUAL TABLE vocab USING fts5vocab(offers, 'row');
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);""")
    db.executemany("INSERT INTO offers VALUES (?, ?, ?, ?)",
                   [(" ".join(tokens(t)), c, p, g) for t, c, p, g in rows])
    db.execute("INSERT INTO meta VALUES ('rows', ?)", (str(len(rows)),))
    db.commit()
    db.close()


def _market_rows():
    rows = []
    for i in range(12):
        rows.append((f"iPhone 13 128GB stan bdb telefon {i}", "phone", 1000 + 20 * i, 0))
        rows.append((f"iPhone 13 128 GB czarny telefon", "phone", 1150 + 10 * i, 1))
        rows.append((f"iPhone 13 Pro 128GB telefon", "phone", 2000 + 30 * i, 0))
        rows.append((f"Nintendo Game Boy Color Pikachu", "console", 380 + 10 * i, 0))
        rows.append((f"Nintendo Switch OLED", "console", 1000 + 10 * i, 0))
    rows += [(f"Telefon Samsung sprawny {i}", "phone", 300, 0) for i in range(40)]
    return rows


def test_comparables_lookup(tmp_path):
    from sniper.comparables import Comparables, describe
    _comparables_db(tmp_path / "comparables.db", _market_rows())
    comps = Comparables(tmp_path / "comparables.db")
    iph = comps.lookup("phone", "iPhone 13 128GB niebieski")
    assert iph and iph["n_active"] == 12 and iph["n_gone"] == 12                    # bez wersji Pro
    assert 1100 <= iph["estimate"] <= 1250 and "13" in iph["words"]
    pro = comps.lookup("phone", "iPhone 13 Pro 128 GB")
    assert pro and pro["median"] > 2000
    gbc = comps.lookup("console", "Nintendo Gameboy Color Pokemon")
    assert gbc and 380 <= gbc["median"] <= 500                                     # „gameboy” = „game boy”
    gbc2 = comps.lookup("console", "Game Boy Color Pokemon edition")
    assert gbc2 and 380 <= gbc2["median"] <= 500 and gbc2["n_active"] == 12          # Switch nie miesza się
    assert comps.lookup("phone", "Xiaomi Redmi Note 99") is None                     # brak podobnych
    assert comps.lookup("laptop", "iPhone 13 128GB") is None                         # inna kategoria
    assert "<porownywalne>" in describe(iph) and "mediana" in describe(iph)
    assert Comparables(tmp_path / "brak.db").lookup("phone", "iPhone 13") is None


def test_comparables_cap_ai_value_in_evaluation(tmp_path):
    """Game Boy z AI wycenionym na 850 zł, a podobne oferty ~420 zł → zysk od wartości z danych, nie okazja."""
    (tmp_path / "guides").mkdir()
    _comparables_db(tmp_path / "guides" / "comparables.db", _market_rows())
    (tmp_path / "guides" / "console.md").write_text("# Konsole\n", encoding="utf-8")
    deal = {**EVAL_DEAL, "score": 9, "market_value_pln": 850, "gpu_model": None, "laptop_model": "Game Boy Color"}
    client = FakeClient(response(deal))
    evaluator = OfferEvaluator(ai_cfg(tmp_path, comparable_factor=1.2), FakeNotifier(), client=client)
    gb = phone(category="console", title="Game Boy Color Pokemon", price=434.54, total_price=440.0)
    record = run(evaluator.evaluate(gb))
    ev = record["evaluation"]
    assert ev["market_value_ai"] == 850 and ev["market_value_pln"] < 650
    assert ev["is_deal"] is False and ev["score"] <= 6 and ev["comparables"]["n_active"] == 12
    assert "<porownywalne>" in client.calls[0]["messages"][0]["content"][-1]["text"]


def test_skip_already_evaluated_offer_unless_cheaper(tmp_path):
    client = FakeClient(response())
    cfg = ai_cfg(tmp_path, skip_seen=True)
    evaluator = OfferEvaluator(cfg, FakeNotifier(), log_dir=tmp_path, client=client)
    run(submit_and_wait(evaluator, laptop()))
    run(submit_and_wait(evaluator, laptop()))                              # ten sam ID, ta sama cena
    assert len(client.calls) == 1
    again = OfferEvaluator(cfg, FakeNotifier(), log_dir=tmp_path, client=client)   # po restarcie - z pliku
    run(submit_and_wait(again, laptop()))
    assert len(client.calls) == 1
    last = json.loads((tmp_path / "evaluations.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last["prefilter_reason"].startswith("już oceniona")
    run(submit_and_wait(again, laptop(price=1500.0, total_price=1515.0)))   # tańsza → ocena od nowa
    assert len(client.calls) == 2


def test_gate_by_comparables_and_failed_mail_switch(tmp_path):
    (tmp_path / "guides").mkdir()
    _comparables_db(tmp_path / "guides" / "comparables.db", _market_rows())
    (tmp_path / "guides" / "phone.md").write_text("# Telefony\n", encoding="utf-8")
    evaluator = OfferEvaluator(ai_cfg(tmp_path, comparable_factor=1.2), FakeNotifier(), client=FakeClient(response()))
    pricey = phone(title="iPhone 13 128GB", price=1100.0, total_price=1115.0)    # szacunek ~1 200 zł
    assert "za drogo wg podobnych ofert" in evaluator.gate(pricey)
    assert evaluator.gate(phone(id=8002, title="iPhone 13 128GB", price=600.0, total_price=615.0)) is None
    from sniper.evaluator import STATUS_FAILED
    failed = {"status": STATUS_FAILED}
    assert evaluator.should_notify(failed) is True
    evaluator.cfg = replace(evaluator.cfg, notify_failed=False)
    assert evaluator.should_notify(failed) is False


def test_very_cheap_deal_gets_warning(tmp_path):
    from sniper.evaluator import enforce_profit_rule
    cfg = ai_cfg(tmp_path)
    ipad = phone(category="tablet", title="iPad A16 128GB", price=600.0, total_price=606.0)
    ev = enforce_profit_rule({"score": 10.0, "is_deal": True, "market_value_pln": 1847.0}, ipad, cfg)
    assert ev["is_deal"] is True and any("bardzo tanio" in f for f in ev["red_flags"])


def test_price_floor_raises_cheap_catalogs():
    from sniper.categories import apply_floor
    specs = parse_catalogs("3580:200-7400*3, 3661:100-5400, 3728:500-3500, 3584:20-100, 3025")
    out = apply_floor(specs, 300)
    assert [(s.catalog_id, s.price_from, s.price_to, s.weight) for s in out] == [
        (3580, "300", "7400", 3), (3661, "300", "5400", 0), (3728, "500", "3500", 0),
        (3584, "20", "100", 0),                       # świadomie tani zakres (do < progu) - bez zmian
        (3025, "300", "", 0)]
    assert apply_floor(specs, 0) == specs and apply_floor(specs, None) == specs


def test_scout_queries_with_price_floor():
    requests = []

    def handler(request):
        requests.append((request.url.params["attribute_ids[catalog]"], request.url.params.get("price_from")))
        return httpx.Response(200, json={"items": [{"id": len(requests), "title": "iPhone 13", "user": {}}]})

    async def scenario():
        session = VintedSession()
        session.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=session.client.headers)
        cfg = ScoutConfig(category="3580", catalogs="3661:100-5400, 3025:400-4300", price_floor=300)
        scout = Scout(cfg, session, EmailNotifier(SmtpConfig()))
        for _ in range(2):
            await scout.poll_catalog()
        await session.close()

    asyncio.run(scenario())
    assert requests == [("3661", "300"), ("3025", "400")]


def test_full_page_of_new_offers_speeds_up_step_by_step(caplog):
    """Na stronie 1 nie ma żadnej z ofert poprzedniego skanu = odświeżamy za wolno → odstęp TEGO katalogu krótszy
    o krok (od zwykłego, najwyżej do minimum), bez pobierania strony 2; po spokojnym czasie wraca o krok
    (prośba użytkownika 2026-10-31)."""
    market = [101, 100]                                    # najnowsze pierwsze (newest_first)
    pages = []

    def handler(request):
        pages.append(int(request.url.params["page"]))
        return httpx.Response(200, json={"items": [{"id": i, "title": "iPhone", "user": {}} for i in market[:2]]})

    async def scenario():
        session = VintedSession()
        session.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=session.client.headers)
        cfg = ScoutConfig(category="3661", catalogs="", per_page=2, price_floor=0, poll_interval=10.0, poll_min=3.0,
                          poll_step=3.0, fast_minutes=10.0)
        scout = Scout(cfg, session, EmailNotifier(SmtpConfig()))

        async def fake_inspect(item, spec, category):
            return None
        scout.inspect = fake_inspect
        await scout.poll_catalog()                         # rozgrzewka: pamięta 101, 100
        periods = []
        for burst in range(4):                             # za każdym razem 3 nowe oferty przy stronie na 2
            market[:0] = [1000 + 10 * burst + i for i in range(3)]
            await scout.poll_catalog()
            periods.append(scout.lanes["glowne"].fast[("3661", "100", "")]["period"])
        await asyncio.gather(*scout._tasks)
        await session.close()
        return scout, periods

    scout, periods = asyncio.run(scenario())
    assert set(pages) == {1}                               # bez strony 2
    assert periods == [7.0, 4.0, 3.0, 3.0]                 # 10 → 7 → 4 → minimum 3
    assert "cała strona (2) to nowe oferty" in caplog.text
    lane = scout.lanes["glowne"]
    boost = lane.fast[("3661", "100", "")]
    scout._relax(lane, boost["since"] + 601)               # 10 min spokoju → o krok wolniej
    assert boost["period"] == 6.0
    scout._relax(lane, boost["since"] + 601)
    assert boost["period"] == 9.0
    scout._relax(lane, boost["since"] + 601)               # 12 ≥ zwykłe 10 → zwykłe tempo
    assert lane.fast == {}


def test_tokens_foreign_letters_and_frequent_brand(tmp_path):
    """27.10 na prawdziwej bazie: „Vând” → „v nd”, a „iphone” (25% ofert) wypadał ze słów → „iPhone 7” = każdy
    telefon z „7” w tytule."""
    from sniper.comparables import Comparables
    assert tokens("Vând iPhone 17 Pro Max") == ["vând", "iphone", "17", "pro", "max"]
    assert tokens("Säljer iPad6") == ["säljer", "ipad", "6"]
    rows = [("iPhone 7 32GB", "phone", 350, 0)] * 10 + [("Xiaomi Redmi 7", "phone", 900, 0)] * 10
    rows += [("iPhone 12", "phone", 600, 0)] * 10 + [("Samsung Galaxy", "phone", 500, 0)] * 60   # „iphone” w 22%
    _comparables_db(tmp_path / "d.db", rows)
    found = Comparables(tmp_path / "d.db").lookup("phone", "Vând iPhone 7")
    assert found and found["median"] == 350 and "iphone" in found["words"] and "vând" not in found["words"]


def test_rule_min_margin_cheap_to_expensive(tmp_path):
    from sniper.evaluator import rule_min_margin, rule_required_profit
    cfg = ai_cfg(tmp_path)
    assert rule_min_margin(600, cfg) == pytest.approx(0.28) and rule_min_margin(1000, cfg) == pytest.approx(0.28)
    assert rule_min_margin(1750, cfg) == pytest.approx(0.265) and rule_min_margin(5000, cfg) == pytest.approx(0.25)
    assert rule_required_profit(0.27, cfg, 800) is None and rule_required_profit(0.27, cfg, 3000) == 300


def test_auto_catalogs_start_exactly_at_floor():
    from sniper.categories import apply_floor
    specs = parse_catalogs("3580:400-7400*3, 3661:100-5400, 3584:20-100")
    assert [s.price_from for s in apply_floor(specs, 300, exact=True)] == ["300", "300", "20"]
    assert [s.price_from for s in apply_floor(specs, 300)] == ["400", "300", "20"]      # lista ręczna: wyższe zostaje
