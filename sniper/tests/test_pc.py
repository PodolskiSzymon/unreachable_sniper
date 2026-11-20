"""Komputery stacjonarne (2026-11-20): wycena po specyfikacji (desktop_specs) i na części (sniper/pc.py)."""
import sqlite3

from sniper.comparables import Comparables
from sniper.evaluator import OfferEvaluator
from sniper.pc import desktop_spec, part_values, parts, routes
from sniper.tests.test_categories import _comparables_db
from sniper.tests.test_evaluator import FakeClient, FakeNotifier, ai_cfg, laptop, response, run
from sniper.tests.test_specs import MEDIANS_DEAL

# Te same przypadki w analyse_data/analyzer/tests/test_analysis.py (DESKTOP_PARITY).
DESKTOP_PARITY = [
    ("Komputer gamingowy i5-9400F RTX 3060 12GB 16GB RAM SSD 512GB", "Zasilacz 600W, płyta B365",
     {"gpu": "rtx 3060", "cpu_line": "intel core i5", "cpu_gen": "8–9 gen.", "chip": None, "line": None, "ram": 16,
      "ssd": 512}),
    ("PC Ryzen 5 5600 RX 6700 XT 32GB DDR4 1TB NVMe + monitor 24", "",
     {"gpu": "rx 6700 xt", "cpu_line": "amd ryzen 5", "cpu_gen": "4000–5000", "chip": None, "line": None, "ram": 32,
      "ssd": 1000}),
    ("Komputer i5-6500 16GB SSD 256", "Dysk HDD 1TB, zintegrowana grafika",
     {"gpu": None, "cpu_line": "intel core i5", "cpu_gen": "do 7 gen.", "chip": None, "line": None, "ram": 16,
      "ssd": 256}),
    ("Komputer stacjonarny", "Sprzedam, działa", None),
]


def test_desktop_spec_parity_with_analyzer():
    for title, desc, expected in DESKTOP_PARITY:
        assert desktop_spec(title, desc) == expected, title


def test_parts_from_listing():
    p = parts("Komputer gamingowy i5-9400F RTX 3060 12GB 16GB RAM SSD 512GB",
              "Zasilacz 600W, płyta B365, dysk HDD 1TB")
    assert (p["cpu"], p["gpu"], p["ram_gb"], p["ssd_gb"], p["psu_w"], p["chipset"], p["hdd_gb"]) == \
        ("i5-9400f", "rtx 3060", 16, 512, 600, "b365", 1000)


def _parts_market():
    rows = []
    for i in range(12):
        rows.append((f"Karta graficzna RTX 3060 12GB {i}", "gpu", 1000 + 20 * i, 0))     # p20 = 1044
        rows.append((f"Procesor Intel i5-9400F {i}", "cpu", 280 + 5 * i, 0))
        rows.append((f"Pamięć DDR4 16GB 3200 {i}", "ram", 200 + 5 * i, 0))
        rows.append((f"Dysk SSD 512GB NVMe {i}", "storage", 150 + 5 * i, 0))
        rows.append((f"Płyta główna B365 {i}", "motherboard", 250 + 5 * i, 0))
    return rows


def _setup(tmp_path, desktop_rows=()):
    (tmp_path / "guides").mkdir()
    path = tmp_path / "guides" / "comparables.db"
    _comparables_db(path, _parts_market() + [("Telefon Samsung", "phone", 300, 0)] * 10)
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE laptop_specs (gpu TEXT, cpu_line TEXT, cpu_gen TEXT, chip TEXT, line TEXT,
                                                   ram INTEGER, ssd INTEGER, price REAL, gone INTEGER);
                        CREATE TABLE desktop_specs (gpu TEXT, cpu_line TEXT, cpu_gen TEXT, chip TEXT, line TEXT,
                                                    ram INTEGER, ssd INTEGER, price REAL, gone INTEGER);""")
    db.executemany("INSERT INTO desktop_specs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", desktop_rows)
    db.commit()
    db.close()
    (tmp_path / "guides" / "laptop.md").write_text("# Laptopy\n", encoding="utf-8")
    return Comparables(path)


def test_part_values_and_routes(tmp_path):
    comps = _setup(tmp_path)
    p = parts("Komputer i5-9400F RTX 3060 16GB RAM SSD 512GB", "Zasilacz 600W, płyta B365")
    values = {x["part"]: x["value"] for x in part_values(p, comps, ai_cfg(tmp_path))}
    assert values["gpu"] == 1044 and values["psu"] == 220 and values["case"] == 150
    assert {"cpu", "ram", "ssd", "motherboard"} <= set(values)
    total = sum(values.values())
    plist = part_values(p, comps, ai_cfg(tmp_path))
    cfg = ai_cfg(tmp_path)
    # całość warta tyle co części → części nie są lepsze o 200 zł (7 ogłoszeń × 46 zł kosztów)
    same = routes(plist, 900.0, cfg, whole_value=total)
    assert same["better_by"] < 200 and not same["parts_ok"] and same["parts_max_buy"] is None
    # całość tania (np. stary PC bez popytu) → części lepsze; okazja, gdy koszt ≤ max zakup części
    cheap = routes(plist, 900.0, cfg, whole_value=1200)
    assert cheap["better_by"] >= 200 and cheap["parts_ok"] and cheap["parts_max_buy"] >= 900
    assert not routes(plist, cheap["parts_max_buy"] + 1, cfg, whole_value=1200)["parts_ok"]
    # same stałe (płyta, zasilacz, obudowa) bez części głównej - brak wyceny na części
    assert routes(part_values(parts("Komputer stacjonarny", ""), comps, cfg), 100.0, cfg)["parts_value"] == 0


def test_desktop_gate_and_parts_deal(tmp_path):
    """Komputer: mediany po specyfikacji (desktop_specs) i okazja NA CZĘŚCI, gdy całość mało warta."""
    whole = [("rtx 3060", "intel core i5", "8–9 gen.", None, None, 16, 512, 1400 + 10 * i, 0) for i in range(12)]
    _setup(tmp_path, whole)
    ai = {**MEDIANS_DEAL, "is_deal": False, "score": 4, "market_value_pln": 1450,
          "laptop_specs": {"gpu": "RTX 3060", "cpu": "i5-9400F", "ram_gb": 16, "ssd_gb": 512, "source": "tekst"}}
    client = FakeClient(response(ai))
    evaluator = OfferEvaluator(ai_cfg(tmp_path, eval_mode="mediany"), FakeNotifier(), client=client)
    offer = laptop(id=21, category="desktop", title="Komputer i5-9400F RTX 3060 16GB RAM SSD 512GB",
                   description="Zasilacz 600W, płyta B365", price=900.0, total_price=915.0)
    assert evaluator.spec_mode(offer) and evaluator.levels(offer)[0]["n_active"] == 12
    assert evaluator.gate(offer) is None                        # całość za droga, ale na części się mieści
    dear = laptop(id=22, category="desktop", title=offer.title, description=offer.description, price=2500.0,
                  total_price=2515.0)
    assert evaluator.gate(dear).startswith("za drogo także na części")
    record = run(evaluator.evaluate(offer))
    ev = record["evaluation"]
    assert ev["is_deal"] and ev["route"] == "części" and ev["score"] >= 8
    assert any("OKAZJA NA CZĘŚCI" in f for f in ev["red_flags"]) and ev["pc_parts"]["n_listings"] == 7


def test_desktop_without_whole_medians_parts_only_and_damaged(tmp_path):
    _setup(tmp_path)                                           # brak desktop_specs w tej konfiguracji
    ai = {**MEDIANS_DEAL, "is_deal": False, "score": 3, "in_medians": False, "median_used": None,
          "market_value_pln": None, "laptop_specs": {"gpu": None, "cpu": None, "ram_gb": None, "ssd_gb": None,
                                                     "source": "brak"}}
    evaluator = OfferEvaluator(ai_cfg(tmp_path, eval_mode="mediany"), FakeNotifier(),
                               client=FakeClient(response(ai), response(ai)))
    ok = laptop(id=31, category="desktop", title="PC i5-9400F RTX 3060 16GB RAM SSD 512GB", description="",
                price=700.0, total_price=715.0)
    ev = run(evaluator.evaluate(ok))["evaluation"]
    assert ev["is_deal"] and ev["route"] == "części"
    broken = laptop(id=32, category="desktop", title="PC i5-9400F RTX 3060 16GB RAM SSD 512GB",
                    description="Uszkodzony, nie włącza się", price=700.0, total_price=715.0)
    ev = run(evaluator.evaluate(broken))["evaluation"]
    assert not ev["is_deal"] and any("na części" in f for f in ev["red_flags"])


def test_push_shows_parts_route():
    from sniper.push import build_deal_message
    offer = {"title": "PC i5-9400F RTX 3060", "category": "desktop", "price": 700.0, "total_price": 715.0,
             "url": "https://www.vinted.pl/items/1", "photo_urls": []}
    ev = {"score": 8, "is_deal": True, "route": "części", "pc_parts": {"parts_value": 2100, "n_listings": 7,
                                                                       "parts_profit": 900, "whole_profit": 400}}
    body = build_deal_message(offer, ev, "t", attach_photo=False)["message"]
    assert "Kup NA CZĘŚCI" in body and "7 ogłoszeniach" in body
