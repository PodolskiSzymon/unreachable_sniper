"""Koszty AI (2026-10-31, prośba użytkownika: mniej danych do API i z API bez utraty jakości): wiersze rodziny zamiast
całej tabeli, zmiana kategorii oferty z tytułu, bramka wg reguły zysku, bez AI przy braku danych, ocena dwuetapowa."""
import asyncio

from sniper.evaluator import (EVALUATION_SCHEMA, LEGACY_ROWS, STATUS_EVALUATED, STATUS_FILTERED, OfferEvaluator,
                              rule_max_buy)
from sniper.guides import GuideBook, cap_table, family_rows, laptop_cpu, manual_rules, manual_notes, parse_rows
from sniper.tests.test_categories import PHONE_GUIDE, _comparables_db, _market_rows, phone
from sniper.tests.test_evaluator import EVAL_DEAL, FakeClient, FakeNotifier, ai_cfg, laptop, response, run

HEADER = ("| grupa | ofert | p25 | mediana | p75 | PL / zagr. mediana | zniknęło (sprzed.) | sprzedam za ~ | max zakup | "
          "zysk / marża | % ofert ≤ max |\n|---|---|---|---|---|---|---|---|---|---|---|\n")


def _row(name, n=50, sale=1000, buy=600):
    return f"| {name} | {n} | 900 | 1 000 | 1 100 | 950 / 1 050 | 20 (2) | {sale} | {buy} | 300 / 30% | 2% |\n"


PHONES = HEADER + "".join(_row(name, n) for name, n in [
    ("iphone 13 128 GB", 3000), ("iphone 13 256 GB", 400), ("iphone 13 pro 128 GB", 700), ("iphone 13 mini 128 GB", 500),
    ("iphone 12 64 GB", 1300), ("iphone 12 mini 64 GB", 200), ("galaxy s7 32 GB", 30), ("galaxy s21 fe 128 GB", 90),
    ("huawei p30 pro 256 GB", 60), ("huawei p40 pro 256 GB", 80)])
TABLETS = HEADER + "".join(_row(name, n) for name, n in [
    ("ipad air (2020) 64 GB", 300), ("ipad air (2022) 64 GB", 200), ("galaxy tab s7 128 GB", 80),
    ("galaxy tab s7 fe 64 GB", 40), ("matepad 11 128 GB", 30)])
LAPTOPS = HEADER + "".join(_row(name, n, sale, buy) for name, n, sale, buy in [
    ("RTX 4060", 157, 3600, 2670), ("RTX 3060", 120, 2800, 2000), ("RTX 3060 Ti", 30, 3000, 2150),
    ("MacBook Air M1 8 GB RAM", 80, 1900, 1300), ("MacBook Air M1", 120, 1950, 1330),
    ("intel core i5 8–9 gen. · 8 GB RAM (zintegrowana grafika)", 400, 650, 304),
    ("intel core i5 8–9 gen. · 16 GB RAM (zintegrowana grafika)", 300, 800, 410),
    ("intel core i3 12+ gen. · 8 GB RAM (zintegrowana grafika)", 90, 1000, 610)])
CPUS = HEADER + _row("AMD Ryzen 5 3600", 90, 300, 0) + _row("Intel Core i5-9400F", 60, 250, 0)
BOARDS = HEADER + _row("Płyta B450 (AM4)", 120, 300, 0) + _row("Płyta Z390 (LGA1151)", 40, 400, 0)
BUNDLES = HEADER + _row("Zestaw z AMD Ryzen 5 3600", 50, 900, 500)


def _book(tmp_path):
    folder = tmp_path / "guides"
    folder.mkdir(exist_ok=True)
    for name, text in (("phone", PHONES), ("tablet", TABLETS), ("laptop", LAPTOPS), ("cpu", CPUS),
                       ("motherboard", BOARDS), ("bundle", BUNDLES)):
        (folder / f"{name}.md").write_text(f"# {name}\n\n## Ceny\n\n{text}", encoding="utf-8")
    manual = tmp_path / "guidelines.md"
    manual.write_text(MANUAL, encoding="utf-8")
    return GuideBook(folder, manual)


MANUAL = """REGUŁA ZYSKU (stara, 26.10): marża ≥ 40% → 300 zł, 30% → 500 zł

--- ZESTAWIENIE MODELI I WIDEŁKI CENOWE ---

4. RTX 4060
- Szacowana cena sprzedaży: 3150 zł
- Maksymalna cena zakupu: 2155 zł
- Notatki: Obecnie najbardziej pożądana karta na rynku.

Inna karta: oszacuj realną cenę sprzedaży.

--- ZASADY DODATKOWE ---

3. STAN WIZUALNY - NIE PRZESADZAJ: kurz i odciski to nie rysy.

Rynkowa minimalna wartość:
* **RTX 4060:** 3050 zł (Allegro)
* **RTX 4070:** 4020 zł (OLX)
"""


def names(rows):
    return [r.name for r in rows]


# ----------------------------------------------------------------------------- rodziny (dobór wierszy)
def test_family_levels_title_description_family_brand():
    rows = parse_rows(PHONES)
    found, level, matched = family_rows(rows, "iPhone 13 128GB czarny", "", "phone")
    assert level == 1 and names(matched) == ["iphone 13 128 GB"]
    # warianty tego samego modelu (AI może poprawić wariant po zdjęciach), bez innych modeli
    assert set(names(found)) == {"iphone 13 128 GB", "iphone 13 256 GB", "iphone 13 pro 128 GB", "iphone 13 mini 128 GB"}
    found, level, _ = family_rows(rows, "iPhone 13 Pro Max 256", "", "phone")      # brak wiersza Pro Max
    assert level == 2 and "iphone 13 pro 128 GB" in names(found) and "iphone 12 64 GB" not in names(found)
    found, level, _ = family_rows(rows, "Sprzedam telefon", "Model: iPhone 12 mini 64GB, bateria 87%", "phone")
    assert level == 3 and names(found)[0] == "iphone 12 mini 64 GB"
    found, level, _ = family_rows(rows, "iPhone na sprzedaż", "stan dobry", "phone")
    assert level == 4 and names(found)[0] == "iphone 13 128 GB" and not any("galaxy" in n for n in names(found))
    found, level, _ = family_rows(rows, "Huawei", "", "phone")
    assert level == 5 and set(names(found)) == {"huawei p40 pro 256 GB", "huawei p30 pro 256 GB"}
    assert family_rows(rows, "Nokia 3310", "", "phone")[1] == 0


def test_route_switches_misplaced_offers_and_mixes_ambiguous_brand(tmp_path):
    book = _book(tmp_path)
    ipad = book.route("phone", "iPad Air 4 64GB")                       # iPad w katalogu telefonów
    assert ipad.category == "tablet" and ipad.level == 1 and "tablet" in ipad.note
    assert {"ipad air (2020) 64 GB", "ipad air (2022) 64 GB"} <= set(names(ipad.rows))   # rok bez znaczenia w tytule
    s21 = book.route("tablet", "Samsung Galaxy S21 FE 128GB")
    assert s21.category == "phone" and names(s21.matched) == ["galaxy s21 fe 128 GB"]
    tab = book.route("tablet", "Galaxy Tab S7 FE 64GB")                 # „galaxy s7” (telefon) mniej szczegółowy
    assert tab.category == "tablet" and names(tab.matched)[0] == "galaxy tab s7 fe 64 GB"
    assert book.route("tablet", "Galaxy S7 32GB").category == "phone"
    huawei = book.route("phone", "Huawei", "stan dobry")                # marka bez modelu: telefony + tablety?
    assert huawei.category == "phone" and huawei.level == 5
    huawei_tab = book.route("phone", "Huawei tab")                      # „tab” → tylko tablety
    assert huawei_tab.category == "tablet" and "telefon" not in book.family_text(huawei_tab)
    pad = book.route("phone", "Huawei MatePad 11 128GB")
    assert pad.category == "tablet" and names(pad.matched) == ["matepad 11 128 GB"]
    mac = book.route("tablet", "MacBook Air M1 8GB 256GB")
    assert mac.category == "laptop" and names(mac.rows)[0] == "MacBook Air M1 8 GB RAM"


def test_route_laptops_by_gpu_cpu_and_description(tmp_path):
    book = _book(tmp_path)
    gaming = book.route("laptop", "Lenovo Legion 5 RTX 3060 i7 16GB")
    assert names(gaming.matched) == ["RTX 3060"] and "RTX 3060 Ti" in names(gaming.rows)
    office = book.route("laptop", "Laptop Dell Latitude", "Procesor i5-8350U, 16GB RAM, SSD 256")
    assert office.level == 3 and names(office.rows)[0].startswith("intel core i5 8–9 gen. · 16 GB")
    probook = book.route("laptop", "HP ProBook 440 G9 i3-1215U 8GB RAM")
    assert probook.level == 1 and names(probook.rows) == ["intel core i3 12+ gen. · 8 GB RAM (zintegrowana grafika)"]
    assert laptop_cpu("Ryzen 5 5500U 16 GB RAM") == ("amd ryzen 5", "4000–5000", 16)
    assert laptop_cpu("i7-1165G7") == ("intel core i7", "10–11 gen.", None) and laptop_cpu("Lenovo IdeaPad") is None
    text = book.family_text(book.route("laptop", "MSI Katana RTX 4060"))
    assert "Obecnie najbardziej pożądana" in text and "3050 zł (Allegro)" in text and "4020" not in text


def test_route_cpu_and_board_in_title_is_bundle(tmp_path):
    book = _book(tmp_path)
    bundle = book.route("motherboard", "Płyta MSI B450 Tomahawk + Ryzen 5 3600")
    assert bundle.category == "bundle" and "zestaw" in bundle.note
    assert {c for c, _r in bundle.extra} == {"cpu", "motherboard"}
    board = book.route("motherboard", "MSI B450 Tomahawk Max")
    assert board.category == "motherboard" and names(board.matched) == ["Płyta B450 (AM4)"]   # „(AM4)” nie wymagane
    cpu = book.route("cpu", "AMD Ryzen 5 3600 BOX")
    assert cpu.category == "cpu" and names(cpu.matched) == ["AMD Ryzen 5 3600"]


def test_rules_text_static_and_manual_without_old_profit_rule(tmp_path):
    book = _book(tmp_path)
    phone_rules = book.rules_text("phone")
    assert phone_rules == book.rules_text("phone") and "Koszty sprzedaży: 46 zł" in phone_rules
    assert "STAN WIZUALNY" not in phone_rules                           # ręczne zasady tylko dla laptopów
    lap = book.rules_text("laptop")
    assert "STAN WIZUALNY" in lap and "40%" not in lap and "2155" not in lap   # bez starej reguły i progów
    assert manual_rules("bez sekcji") == "" and manual_notes(MANUAL, []) == []


def test_cap_table_for_legacy_mode():
    table = "# X\n\n" + HEADER + "".join(_row(f"model {i}") for i in range(100)) + "\nPraktycznie nieopłacalne: a"
    capped = cap_table(table, LEGACY_ROWS)
    assert capped.count("| model ") == LEGACY_ROWS and "(… i 20 mniej licznych grup)" in capped
    assert capped.endswith("Praktycznie nieopłacalne: a") and cap_table(table, None) == table


# ----------------------------------------------------------------------------- ocena: kategoria, bramka, brak danych
def test_misplaced_ipad_gets_tablet_rows_and_category(tmp_path):
    _book(tmp_path)
    client = FakeClient(response({**EVAL_DEAL, "gpu_model": None, "laptop_model": "iPad Air 4"}))
    evaluator = OfferEvaluator(ai_cfg(tmp_path, guide_rows="rodzina"), FakeNotifier(), client=client)
    offer = phone(title="iPad Air 4 64GB", price=500.0, total_price=515.0)
    record = run(evaluator.evaluate(offer))
    assert offer.category == "tablet" and record["offer"]["category"] == "tablet"
    system = client.calls[0]["system"][0]["text"]
    task = client.calls[0]["messages"][0]["content"][-1]["text"]
    assert "kategoria: tablety" in system and "ipad air (2020) 64 GB" in task and "iphone" not in task.lower()


def test_comparables_gate_uses_profit_rule(tmp_path):
    """Szacunek z bazy ~1 175 zł (iPhone 13): max zakup przy wycenie × 1,2 = 1 410 → reguła wymaga kosztu ≤ ~977 zł
    (dawniej 0,9 × szacunku ≈ 1 058)."""
    (tmp_path / "guides").mkdir()
    _comparables_db(tmp_path / "guides" / "comparables.db", _market_rows())
    (tmp_path / "guides" / "phone.md").write_text("# Telefony\n", encoding="utf-8")
    evaluator = OfferEvaluator(ai_cfg(tmp_path, comparable_factor=1.2), FakeNotifier(), client=FakeClient(response()))
    comps = evaluator.lookup_comparables(phone())
    limit = rule_max_buy(comps["estimate"] * 1.2, evaluator.cfg)
    assert 900 < limit < comps["estimate"] * 0.9
    above = phone(id=1, price=limit + 10, total_price=limit + 10)
    assert "max zakup" in evaluator.gate(above)
    below = phone(id=2, price=limit - 80, total_price=limit - 80)        # koszt z opłatą Vinted < limitu
    assert evaluator.gate(below) is None


def test_no_ai_without_any_market_data(tmp_path):
    """Tryb mediany bez żadnej mediany = program i tak da najwyżej 3 → bez wywołania AI (zero kosztów)."""
    (tmp_path / "guides").mkdir()
    _comparables_db(tmp_path / "guides" / "comparables.db", _market_rows())
    (tmp_path / "guides" / "phone.md").write_text("# Telefony\n\n" + PHONE_GUIDE.split("<!-- komentarz")[1],
                                                  encoding="utf-8")
    client = FakeClient(response())
    evaluator = OfferEvaluator(ai_cfg(tmp_path, eval_mode="mediany"), FakeNotifier(), log_dir=tmp_path,
                               client=client)
    unknown = phone(id=11, title="Xiaomi Redmi Note 99 Ultra", price=600.0, total_price=600.0)
    assert evaluator.gate(unknown).startswith("brak danych rynku")
    assert evaluator.gate(phone(id=12, price=600.0, total_price=600.0)) is None        # iPhone 13 ma mediany
    assert OfferEvaluator(ai_cfg(tmp_path, eval_mode="mediany", skip_no_data=False), FakeNotifier(),
                          client=client).gate(unknown) is None
    # tryb zwykły: brak danych → ocena najwyżej 7 = próg maila → AI potrzebne (alert 7/10 możliwy)
    assert OfferEvaluator(ai_cfg(tmp_path), FakeNotifier(), client=client).gate(unknown) is None
    low_cap = OfferEvaluator(ai_cfg(tmp_path, no_data_max_score=6.0), FakeNotifier(), client=client)
    assert low_cap.gate(unknown).startswith("brak danych rynku")

    async def scenario():
        evaluator.submit(unknown)
        await asyncio.sleep(0)
    run(scenario())
    assert client.calls == [] and evaluator.total["filtered"] == 1


# ----------------------------------------------------------------------------- ocena dwuetapowa i krótsza odpowiedź
def test_two_stage_quick_then_full_only_for_promising(tmp_path):
    poor = {**EVAL_DEAL, "is_deal": False, "score": 3}
    client = FakeClient(response(poor))
    evaluator = OfferEvaluator(ai_cfg(tmp_path, two_stage=True, effort="medium", quick_effort="low"), FakeNotifier(),
                               client=client)
    record = run(evaluator.evaluate(laptop()))
    assert len(client.calls) == 1 and client.calls[0]["output_config"]["effort"] == "low"
    assert record["evaluation"]["stage"] == "1/2" and record["usage"]["output_tokens"] == 300

    client = FakeClient(response({**EVAL_DEAL, "score": 7}), response(EVAL_DEAL))
    evaluator = OfferEvaluator(ai_cfg(tmp_path, two_stage=True, effort="medium", quick_effort="low"), FakeNotifier(),
                               client=client)
    record = run(evaluator.evaluate(laptop()))
    assert [c["output_config"]["effort"] for c in client.calls] == ["low", "medium"]
    ev = record["evaluation"]
    assert ev["stage"] == "2/2" and ev["score_quick"] == 7 and record["status"] == STATUS_EVALUATED
    assert record["usage"]["output_tokens"] == 600 and evaluator.total["calls"] == 2       # koszt obu etapów

    same = OfferEvaluator(ai_cfg(tmp_path, two_stage=True, effort="low", quick_effort="low", media_resolution="",
                                 quick_media_resolution=""), FakeNotifier(), client=FakeClient(response()))
    assert len(same.stage_configs()) == 1                                # szybka = pełna → jeden etap


def test_gemini_quick_stage_uses_medium_photos(tmp_path):
    from sniper.config import AiConfig
    from sniper.evaluator import build_gemini_request
    cfg = ai_cfg(tmp_path, provider="gemini", model="gemini-3.8-flash", two_stage=True, quick_effort="low",
                 quick_media_resolution="medium", media_resolution="")
    evaluator = OfferEvaluator.__new__(OfferEvaluator)
    evaluator.cfg = cfg
    quick, full = evaluator.stage_configs()
    request = build_gemini_request(laptop(), quick, "x", [("https://a/1.jpg", None)])
    assert request["config"].media_resolution is not None and "MEDIUM" in str(request["config"].media_resolution)
    assert build_gemini_request(laptop(), full, "x", [("https://a/1.jpg", None)])["config"].media_resolution is None
    assert isinstance(quick, AiConfig) and quick.effort == "low" and full.effort == cfg.effort


def test_schema_without_fields_the_program_recomputes():
    props = EVALUATION_SCHEMA["properties"]
    assert not {"max_buy_price_pln", "potential_profit_pln", "margin_pct", "meets_profit_rule"} & set(props)
    assert set(EVALUATION_SCHEMA["required"]) == set(props)


def test_filtered_offer_has_reason_in_record(tmp_path):
    _book(tmp_path)
    client = FakeClient(response())
    evaluator = OfferEvaluator(ai_cfg(tmp_path), FakeNotifier(), log_dir=tmp_path, client=client)
    record = {}

    def keep(offer, rec):
        record.update(rec)
    evaluator._finish = keep
    evaluator.submit(phone(title="iPhone 13 128GB", price=5000.0, total_price=5000.0))
    assert record["status"] == STATUS_FILTERED and "za drogo wg tabeli" in record["prefilter_reason"]
