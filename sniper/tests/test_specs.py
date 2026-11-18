"""Laptopy wyceniane po specyfikacji (2026-11-01): karta graficzna / procesor + generacja / RAM / dysk zamiast słów
z tytułu; AI rozpoznaje specyfikację, gdy tekst jej nie podaje."""
import json
import sqlite3

from sniper.comparables import Comparables
from sniper.evaluator import STATUS_EVALUATED, OfferEvaluator
from sniper.specs import laptop_spec, merge, same, spec_from_ai
from sniper.tests.test_categories import _comparables_db
from sniper.tests.test_evaluator import EVAL_DEAL, FakeClient, FakeNotifier, ai_cfg, laptop, response, run

MEDIANS_DEAL = {**EVAL_DEAL, "sell_days_estimate": 4, "test_needs": "", "photos_seen": 3, "photo_notes": "",
                "in_medians": True, "median_used": 1, "corrected_model": None,
                "laptop_specs": {"gpu": "RTX 3060", "cpu": "Ryzen 7 5800H", "ram_gb": 16, "ssd_gb": 512,
                                 "source": "tekst"}}


def _specs_db(path, rows, with_specs=True):
    """comparables.db jak z analizatora: oferty po słowach + tabela laptop_specs."""
    _comparables_db(path, [("Telefon Samsung", "phone", 300, 0)] * 10)
    if not with_specs:
        return
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE laptop_specs (gpu TEXT, cpu_line TEXT, cpu_gen TEXT, chip TEXT, line TEXT,
                                                   ram INTEGER, ssd INTEGER, price REAL, gone INTEGER);""")
    db.executemany("INSERT INTO laptop_specs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    db.commit()
    db.close()


def _market():
    rows = []
    for i in range(12):
        rows.append(("rtx 3060", "amd ryzen 7", "4000–5000", None, None, 16, 512, 2900 + 20 * i, 0))
        rows.append(("rtx 3060", "intel core i7", "10–11 gen.", None, None, 16, 1000, 3100 + 20 * i, 1))
        rows.append((None, "intel core i5", "8–9 gen.", None, None, 8, 256, 600 + 10 * i, 0))
        rows.append((None, "intel core i5", "8–9 gen.", None, None, 16, 256, 750 + 10 * i, 0))
        rows.append((None, None, None, "m1", "macbook air", 8, 256, 1900 + 15 * i, 0))
    # laptop z RTX i tym samym i5 - nie miesza się z medianą zintegrowanej grafiki
    rows += [("gtx 1650", "intel core i5", "8–9 gen.", None, None, 8, 256, 1500, 0)] * 10
    return rows


def _setup(tmp_path, with_specs=True):
    (tmp_path / "guides").mkdir()
    _specs_db(tmp_path / "guides" / "comparables.db", _market(), with_specs)
    (tmp_path / "guides" / "laptop.md").write_text("# Laptopy\n", encoding="utf-8")


# Te same przypadki w unreachable_sniper/sniper/tests/test_specs.py i analyse_data/analyzer/tests/test_analysis.py -
# reguły specyfikacji laptopa muszą dawać to samo w Sniperze i analizatorze (inaczej mediany się nie trafią).
SPEC_PARITY = [
    ("Lenovo Legion 5 RTX 3060 16GB 512GB", "Ryzen 7 5800H",
     {"gpu": "rtx 3060", "cpu_line": "amd ryzen 7", "cpu_gen": "4000–5000", "chip": None, "line": None, "ram": 16,
      "ssd": 512}),
    ("Laptop Dell Latitude 3410", "i3-10110U | 8GB RAM | 256GB SSD",
     {"gpu": None, "cpu_line": "intel core i3", "cpu_gen": "10–11 gen.", "chip": None, "line": None, "ram": 8,
      "ssd": 256}),
    ("MacBook Air M3 16GB 15.3 Cal", "",
     {"gpu": None, "cpu_line": "apple m3", "cpu_gen": None, "chip": "m3", "line": "macbook air", "ram": 16,
      "ssd": None}),
    ("hp pavilion 14", "Processzor: Intel Core i5-1135G7 • Memória mérete: 8 GB • SSD 512GB",
     {"gpu": None, "cpu_line": "intel core i5", "cpu_gen": "10–11 gen.", "chip": None, "line": None, "ram": 8,
      "ssd": 512}),
    ("Laptop Lenovo", "Sprzedam, stan bdb", None),
]


def test_laptop_spec_parity_with_analyzer():
    for title, desc, expected in SPEC_PARITY:
        assert laptop_spec(title, desc) == expected, title


# ----------------------------------------------------------------------------- specyfikacja z tekstu i od AI
def test_laptop_spec_from_text_and_form():
    gaming = laptop_spec("Lenovo Legion 5 RTX 3060 16GB 512GB", "Ryzen 7 5800H, ekran 165 Hz")
    assert (gaming["gpu"], gaming["cpu_line"], gaming["cpu_gen"], gaming["ram"], gaming["ssd"]) == \
        ("rtx 3060", "amd ryzen 7", "4000–5000", 16, 512)
    office = laptop_spec("Laptop Dell Latitude", "i5-8350U | 8GB RAM | 240GB SSD")
    assert (office["gpu"], office["cpu_line"], office["cpu_gen"], office["ram"], office["ssd"]) == \
        (None, "intel core i5", "8–9 gen.", 8, 256)
    mac = laptop_spec("MacBook Air M1 8GB", "")
    assert (mac["line"], mac["chip"], mac["ram"]) == ("macbook air", "m1", 8)
    form = laptop_spec("HP ProBook", "i5-1135G7", {"computer_ram": "16 GB", "computer_storage_capacity": "512 GB"})
    assert (form["cpu_gen"], form["ram"], form["ssd"]) == ("10–11 gen.", 16, 512)
    assert laptop_spec("Laptop Lenovo", "Sprzedam, stan bdb") is None


def test_spec_from_ai_and_merge():
    ai = spec_from_ai({"gpu": "NVIDIA GeForce RTX 3060 Laptop", "cpu": "AMD Ryzen 7 5800H", "ram_gb": 16,
                       "ssd_gb": 512, "source": "zdjęcia"}, "Lenovo Legion 5")
    assert (ai["gpu"], ai["cpu_line"], ai["ram"], ai["ssd"]) == ("rtx 3060", "amd ryzen 7", 16, 512)
    assert spec_from_ai({"gpu": None, "cpu": None, "ram_gb": None, "ssd_gb": None, "source": "brak"}) is None
    mac = spec_from_ai({"gpu": "zintegrowana", "cpu": "M2", "ram_gb": 8, "ssd_gb": 256, "source": "tekst"},
                       "MacBook Air 13")
    assert (mac["line"], mac["chip"]) == ("macbook air", "m2")
    text = laptop_spec("Dell i5-8350U 8GB RAM", "")
    merged = merge(text, spec_from_ai({"gpu": "GTX 1650", "cpu": None, "ram_gb": None, "ssd_gb": 256,
                                       "source": "zdjęcia"}))
    assert merged["gpu"] == "gtx 1650" and merged["ram"] == 8 and merged["ssd"] == 256 and not same(merged, text)


def test_spec_levels_from_specific_to_general(tmp_path):
    _setup(tmp_path)
    comps = Comparables(tmp_path / "guides" / "comparables.db")
    levels = comps.spec_levels(laptop_spec("Legion RTX 3060 16GB 512GB", ""))
    assert [lv["kind"] for lv in levels] == ["spec"] * len(levels)
    assert levels[0]["n_active"] == 12 and "RTX 3060" in levels[0]["label"] and "512 GB" in levels[0]["label"]
    # ogólniejszy poziom (każdy dysk) łączy 512 GB i 1 TB; „sama karta” ma te same oferty → nie dublujemy
    assert levels[-1]["n_active"] + levels[-1]["n_gone"] == 24 and "każdy dysk" in levels[-1]["label"]
    office = comps.spec_levels(laptop_spec("Dell i5-8350U 8GB 256GB SSD", ""))
    assert office[0]["median"] < 700 and "zintegrowana" in office[0]["label"]        # bez laptopów z GTX 1650
    assert comps.spec_levels(laptop_spec("Laptop i7-13700H RTX 4090 64GB", "")) == []
    _specs_db(tmp_path / "stary.db", [], with_specs=False)
    assert not Comparables(tmp_path / "stary.db").has_specs()


# ----------------------------------------------------------------------------- ocena
def test_laptop_with_spec_uses_spec_medians(tmp_path):
    _setup(tmp_path)
    client = FakeClient(response({**MEDIANS_DEAL, "market_value_pln": 3000}))
    evaluator = OfferEvaluator(ai_cfg(tmp_path, eval_mode="mediany"), FakeNotifier(), client=client)
    offer = laptop(category="laptop", title="Lenovo Legion 5 RTX 3060 16GB 512GB", description="Ryzen 7 5800H",
                   price=1800.0, total_price=1815.0)
    record = run(evaluator.evaluate(offer))
    assert record["status"] == STATUS_EVALUATED and len(client.calls) == 1
    task = client.calls[0]["messages"][0]["content"][-1]["text"]
    assert "<mediany>" in task and "RTX 3060 · 16 GB RAM · 512 GB" in task and "specyfikacja:" in task
    schema = client.calls[0]["output_config"]["format"]["schema"]
    assert "laptop_specs" in schema["properties"]
    ev = record["evaluation"]
    assert ev["spec"].startswith("RTX 3060") and ev["levels"] and ev["is_deal"]      # wycena z danych, bez limitu 7


def test_laptop_without_spec_ai_reads_it_then_spec_medians(tmp_path):
    """Tekst bez specyfikacji („Laptop Lenovo”): szybka ocena podaje specyfikację ze zdjęć → mediany dla niej →
    pełna ocena z nimi; czerwona flaga, że specyfikację rozpoznało AI."""
    _setup(tmp_path)
    first = {**MEDIANS_DEAL, "is_deal": False, "score": 3, "in_medians": False, "median_used": None,
             "market_value_pln": None, "laptop_specs": {"gpu": "RTX 3060", "cpu": "Ryzen 7 5800H", "ram_gb": 16,
                                                        "ssd_gb": 512, "source": "zdjęcia"}}
    client = FakeClient(response(first), response({**MEDIANS_DEAL, "market_value_pln": 3000}))
    evaluator = OfferEvaluator(ai_cfg(tmp_path, eval_mode="mediany"), FakeNotifier(), client=client)
    offer = laptop(category="laptop", title="Laptop Lenovo", description="Sprzedam, stan bdb", price=1800.0,
                   total_price=1815.0)
    assert evaluator.gate(offer) is None                       # bez specyfikacji - nie pomijamy (AI ją odczyta)
    record = run(evaluator.evaluate(offer))
    assert len(client.calls) == 2
    second = client.calls[1]["messages"][0]["content"][-1]["text"]
    assert "RTX 3060 · 16 GB RAM · 512 GB" in second and "rozpoznana przez AI" in second
    ev = record["evaluation"]
    assert ev["spec_source"] == "zdjęcia" and ev["stage"] == "2/2" and ev["is_deal"]
    assert any("rozpoznało AI" in f for f in ev["red_flags"])


def test_laptop_spec_gate_and_unknown_spec_without_data(tmp_path):
    _setup(tmp_path)
    client = FakeClient(response(MEDIANS_DEAL))
    evaluator = OfferEvaluator(ai_cfg(tmp_path, eval_mode="mediany"), FakeNotifier(), client=client)
    dear = laptop(id=1, category="laptop", title="Dell i5-8350U 8GB 256GB SSD", price=900.0, total_price=915.0)
    assert evaluator.gate(dear).startswith("za drogo wg median specyfikacji")
    cheap = laptop(id=2, category="laptop", title="Dell i5-8350U 8GB 256GB SSD", price=150.0, total_price=165.0)
    assert evaluator.gate(cheap) is None
    rare = laptop(id=3, category="laptop", title="Laptop i7-13700H RTX 4090 64GB", price=3000.0, total_price=3015.0)
    assert evaluator.gate(rare).startswith("brak danych rynku")     # specyfikacja znana, median brak - AI nic nie da


def test_old_comparables_without_specs_keeps_regular_laptop_mode(tmp_path):
    _setup(tmp_path, with_specs=False)
    client = FakeClient(response(EVAL_DEAL))
    evaluator = OfferEvaluator(ai_cfg(tmp_path, eval_mode="mediany"), FakeNotifier(), client=client)
    record = run(evaluator.evaluate(laptop(category="laptop")))
    schema = client.calls[0]["output_config"]["format"]["schema"]
    assert record["status"] == STATUS_EVALUATED and "laptop_specs" not in schema["properties"]
    assert "in_medians" not in schema["properties"]                 # zwykły tryb jak przed 01.11


def test_offer_attributes_from_sidebar():
    from sniper.extractor import build_offer
    sidebar = {"title": "HP ProBook", "price": {"amount": "900", "currency_code": "PLN"},
               "plugins": [{"name": "attributes", "data": {"attributes": [
                   {"code": "computer_ram", "data": {"value": "16 GB"}},
                   {"code": "computer_storage_capacity", "data": {"value": "512 GB"}},
                   {"code": "status", "data": {"value": "Bardzo dobry"}}]}}]}
    offer = build_offer(5, sidebar, {"shipping_details": {"price": {"amount": 7.05, "currency_code": "PLN"}}})
    assert offer.attributes["computer_ram"] == "16 GB" and offer.condition == "Bardzo dobry"
    assert json.loads(json.dumps(offer.to_dict()))["attributes"]["computer_storage_capacity"] == "512 GB"


def test_form_fields_and_storage_for_phone_tablet_lookups():
    from sniper.evaluator import lookup_title, offer_text
    tab = laptop(category="tablet", title="iPad Air 4", description="Stan idealny",
                 attributes={"internal_memory_capacity": "64 GB"})
    assert lookup_title(tab) == "iPad Air 4 64 GB" and "Formularz Vinted: pamięć: 64 GB" in offer_text(tab)
    phone = laptop(category="phone", title="iPhone 13", description="pamięć 128GB, bateria 88%")
    assert lookup_title(phone) == "iPhone 13 128 GB"
    assert lookup_title(laptop(category="phone", title="iPhone 13 256GB")) == "iPhone 13 256GB"
    assert lookup_title(laptop(category="laptop", title="Dell 16GB")) == "Dell 16GB"     # laptop - po specyfikacji
    assert "Formularz Vinted" not in offer_text(laptop())


def test_shipping_details_sample_from_user():
    """Odpowiedź shipping_details wklejona przez użytkownika 2026-11-01 (kwota liczbą, discount null)."""
    from sniper.extractor import Seller, extract_shipping, fill_country_from_shipping
    payload = {"shipping_details": {"pickup_only": False, "multiple_shipping_options_available": True,
                                    "free_shipping": False, "discount": None,
                                    "price": {"amount": 7.05, "currency_code": "PLN"}}, "code": 0}
    ship = extract_shipping(payload)
    assert (ship.price, ship.currency, ship.price_pln, ship.multiple_options, ship.discount) == \
        (7.05, "PLN", 7.05, True, None)
    seller = fill_country_from_shipping(Seller(*[None] * 8), ship)
    assert seller.country_code == "PL"


def test_anthropic_download_mode_sends_photos_in_request(tmp_path):
    import asyncio

    import httpx
    from sniper.evaluator import AnthropicBackend

    def handler(request):
        return httpx.Response(200, content=b"\x89PNGzdjecie")
    client = FakeClient(response())
    backend = AnthropicBackend(ai_cfg(tmp_path, photos="download"), client=client)
    backend._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    offer = laptop(photo_urls=["https://images1.vinted.net/a.webp", "https://images1.vinted.net/b.jpg"])
    _resp, _usage, photos = asyncio.run(backend.generate(offer, "x", True))
    content = client.calls[0]["messages"][0]["content"]
    images = [c for c in content if c["type"] == "image"]
    assert photos == 2 and [i["source"]["type"] for i in images] == ["base64", "base64"]
    assert images[0]["source"]["media_type"] == "image/webp"


# ----------------------------------------------------------------------------- stare laptopy (2026-11-18)
def _old_setup(tmp_path):
    """Rynek z laptopami z Intelem 6. gen. 8 GB / 256 GB: 700…1030 zł → p10 733, dawny szacunek (p25+med)/2 ≈ 824."""
    (tmp_path / "guides").mkdir()
    old = [(None, "intel core i5", "do 7 gen.", None, None, 8, 256, 700 + 30 * i, 0) for i in range(12)]
    _specs_db(tmp_path / "guides" / "comparables.db", _market() + old)
    (tmp_path / "guides" / "laptop.md").write_text("# Laptopy\n", encoding="utf-8")


def test_old_laptop_by_age_only():
    from types import SimpleNamespace

    from sniper.market import old_laptop
    offer = lambda title, category="laptop": SimpleNamespace(category=category, title=title)  # noqa: E731
    assert old_laptop(offer("MacBook Air 13 Early 2015 8GB 256GB")) == "MacBook z Intelem (2015)"
    assert old_laptop(offer("Apple Mac Book Pro 13 2017")) == "MacBook z Intelem (2017)"
    assert old_laptop(offer("MacBook Air M1 2020 8/256")) is None                 # Apple M - nowy
    assert old_laptop(offer("MacBook Pro 2019 i7 16GB")) is None                  # Intel 8-9 gen. schodzi normalnie
    assert old_laptop(offer("Dell"), {"cpu_gen": "do 7 gen.", "line": None}) == \
        "laptop z procesorem Intel do 7. generacji"
    assert old_laptop(offer("MacBook Air"), {"cpu_gen": "do 7 gen.", "line": "macbook air"}) == \
        "MacBook z Intelem do 2017"
    assert old_laptop(offer("Dell"), {"cpu_gen": "8–9 gen."}) is None
    assert old_laptop(offer("MSI GTX 1050"), {"cpu_gen": "do 7 gen.", "gpu": "gtx 1050"}) is None   # z kartą - nie
    assert old_laptop(offer("MacBook Air 2015", category="tablet")) is None
    assert old_laptop(offer("MacBook Air 2015"), cfg=SimpleNamespace(old_laptop_rules=False)) is None


def test_old_laptop_valued_at_low_prices_gate_and_cap(tmp_path):
    """Stary laptop: szacunek = dolne 10% (733 zł zamiast ~824), wycena AI najwyżej × 1,0, bramka liczy od tego."""
    _old_setup(tmp_path)
    old_ai = {**MEDIANS_DEAL, "market_value_pln": 820,
              "laptop_specs": {"gpu": None, "cpu": "i5-6300U", "ram_gb": 8, "ssd_gb": 256, "source": "tekst"}}
    client = FakeClient(response(old_ai))
    evaluator = OfferEvaluator(ai_cfg(tmp_path, eval_mode="mediany"), FakeNotifier(), client=client)
    title = "Dell Latitude E7470 i5-6300U 8GB 256GB SSD"
    levels = evaluator.levels(laptop(id=11, category="laptop", title=title))
    assert levels[0]["old_laptop"] and round(levels[0]["estimate"]) == 733
    assert round(levels[0]["estimate_old"]) == 824 and "dolne 10%" in levels[0]["estimate_basis"]
    # koszt 438 zł: dawniej max zakup 642 zł (824 × 1,2) - przepuszczony; teraz 387 zł (733 × 1,0) - pominięty
    mid = laptop(id=12, category="laptop", title=title, price=400.0, total_price=415.0)
    assert evaluator.gate(mid).startswith("za drogo wg median specyfikacji")
    cheap = laptop(id=13, category="laptop", title=title, price=300.0, total_price=315.0)
    assert evaluator.gate(cheap) is None
    record = run(evaluator.evaluate(cheap))
    task = client.calls[0]["messages"][0]["content"][-1]["text"]
    assert "Stary laptop (laptop z procesorem Intel do 7. generacji)" in task and "dolne 10%" in task
    ev = record["evaluation"]
    assert ev["market_value_pln"] == 733 and ev["max_buy_price_pln"] == 387
    assert any("stary laptop" in f for f in ev["red_flags"])
    # nowszy procesor - nie „stary”: cena szybkiej sprzedaży = dolne 20% (600…710 zł → 622 zamiast ~641)
    newer = evaluator.levels(laptop(id=14, category="laptop", title="Dell i5-8350U 8GB 256GB SSD"))
    assert newer and not newer[0].get("old_laptop") and newer[0]["laptop_fast"]
    assert round(newer[0]["estimate"]) == 622 and round(newer[0]["estimate_old"]) == 641


def test_laptop_valued_at_fast_sale_price(tmp_path):
    """2026-11-18 (użytkownik: „chciałbym patrzeć na szybkie sprzedaże”): laptop - szacunek = dolne 20% cen aktywnych
    (po tyle schodzą w ≤ 3 dni), wycena AI najwyżej × 1,1; dopisek dla AI. Wyłączalne SNIPER_LAPTOP_VALUE_RULES."""
    _setup(tmp_path)
    client = FakeClient(response({**MEDIANS_DEAL, "market_value_pln": 3300}))
    evaluator = OfferEvaluator(ai_cfg(tmp_path, eval_mode="mediany"), FakeNotifier(), client=client)
    offer = laptop(category="laptop", title="Lenovo Legion 5 RTX 3060 16GB 512GB", description="Ryzen 7 5800H",
                   price=1800.0, total_price=1815.0)
    levels = evaluator.levels(offer)
    assert round(levels[0]["estimate"]) == 2944 and "dolne 20%" in levels[0]["estimate_basis"]   # 2900…3120 zł
    record = run(evaluator.evaluate(offer))
    task = client.calls[0]["messages"][0]["content"][-1]["text"]
    assert "cena szybkiej sprzedaży" in task
    ev = record["evaluation"]
    assert ev["market_value_pln"] == 3238 and ev["market_value_ai"] == 3300          # 2944 × 1,1
    off = OfferEvaluator(ai_cfg(tmp_path, eval_mode="mediany", laptop_value_rules=False), FakeNotifier(),
                         client=FakeClient())
    plain = off.levels(laptop(id=2, category="laptop", title="Lenovo Legion 5 RTX 3060 16GB 512GB",
                              description="Ryzen 7 5800H"))
    assert not plain[0].get("laptop_fast") and plain[0]["estimate"] > 2944


def test_old_laptop_rule_can_be_turned_off(tmp_path):
    _old_setup(tmp_path)
    evaluator = OfferEvaluator(ai_cfg(tmp_path, eval_mode="mediany", old_laptop_rules=False), FakeNotifier(),
                               client=FakeClient())
    mid = laptop(id=12, category="laptop", title="Dell Latitude E7470 i5-6300U 8GB 256GB SSD", price=400.0,
                 total_price=415.0)
    assert evaluator.gate(mid) is None and not evaluator.levels(mid)[0].get("old_laptop")


def test_notebook_and_chromebook_are_not_readers():
    """„e-book” w LOW_DEMAND łapał „Notebook” / „Chromebook” - laptop tracił okazję poza ceną ≤ 30% szacunku."""
    from types import SimpleNamespace

    from sniper.market import low_demand
    for title in ("Notebook Lenovo ThinkPad T480", "HP Chromebook 14", "Acer Notebook 15"):
        assert not low_demand(SimpleNamespace(title=title))
    for title in ("Kindle Paperwhite", "Czytnik e-book PocketBook", "ebook reader Tolino", "czytnik e-ink"):
        assert low_demand(SimpleNamespace(title=title))
