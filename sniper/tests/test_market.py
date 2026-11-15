"""Reguły rynku (sniper/market.py, 2026-11-15): pudełko, nisza (niska rotacja), wolna rotacja, zasięg kategorii."""
from types import SimpleNamespace

from sniper.comparables import Comparables
from sniper.experiment import finish_medians
from sniper.market import apply_market_rules, box_status, liquidity, market_note, niche_limit, no_box_factor
from sniper.tests.test_categories import phone

CFG = SimpleNamespace(min_market_month=10.0, niche_turnover=0.10, niche_max_share=0.5, min_turnover=0.20,
                      market_categories=("phone", "tablet", "console"), no_box_factor=0.88,
                      comparable_factor=1.2, rule_min_profit=300.0, rule_margin_cheap=28.0, rule_margin_expensive=25.0,
                      rule_cheap_sale=1000.0, rule_expensive_sale=2500.0, rule_deal_score=8.0, rule_costs=46.0,
                      pl_bonus=0.0)


def level(n_active, n_gone, estimate, label="model"):
    return {"label": label, "n_active": n_active, "n_gone": n_gone, "median": estimate, "estimate": estimate,
            "p25": estimate * 0.9, "p75": estimate * 1.1, "gone_median": None, "words": ["x"]}


def deal(value, score=9):
    return {"is_deal": True, "score": float(score), "market_value_pln": value, "in_medians": True, "median_used": 1,
            "red_flags": [], "gpu_model": None, "laptop_model": None}


# ---------------------------------------------------------------------------------------------- pudełko
def test_box_status_texts():
    assert box_status(phone(title="iPhone 13 128GB", description="Sprzedam bez pudełka, stan bdb")) == "bez"
    assert box_status(phone(title="iPhone 13 128GB", description="Brak oryginalnego pudełka.")) == "bez"
    assert box_status(phone(title="iPhone 13 128GB komplet", description="")) == "jest"
    assert box_status(phone(title="iPhone 13", description="W zestawie pudełko i ładowarka")) == "jest"
    assert box_status(phone(title="iPhone 13", description="Vând iPhone fără cutie")) == "bez"
    assert box_status(phone(title="Samsung S23", description="Full box, originál")) == "jest"
    assert box_status(phone(title="iPhone 13", description="Stan bardzo dobry, bateria 88%.")) is None
    # „bez pudełka” wygrywa ze wzmianką o pudełku w tym samym tekście
    assert box_status(phone(title="iPhone 13", description="Ładowarka w zestawie, bez pudełka")) == "bez"


def test_no_box_lowers_value_in_finish_medians():
    offer = phone(title="iPhone 13 128GB", description="Bez pudełka, sprawny.", price=500.0, total_price=515.0)
    ev = finish_medians(deal(1000), offer, CFG, [level(300, 200, 1000)], span_days=30)
    assert ev["market_value_pln"] == 880                       # 1000 × 0,88
    assert any("bez pudełka" in f for f in ev["red_flags"])
    assert ev["market"]["box"] == "bez"
    plain = finish_medians(deal(1000), phone(title="iPhone 13 128GB", description="Sprawny.", price=500.0,
                                             total_price=515.0), CFG, [level(300, 200, 1000)], span_days=30)
    assert plain["market_value_pln"] == 1000


def test_no_box_factor_off():
    offer = phone(description="bez pudełka")
    assert no_box_factor(offer, CFG) == 0.88
    assert no_box_factor(offer, SimpleNamespace(no_box_factor=0)) == 1.0
    assert no_box_factor(phone(description="z pudełkiem"), CFG) == 1.0


# ---------------------------------------------------------------------------------------------- płynność
def test_liquidity_uses_widest_level_and_span():
    levels = [level(10, 5, 1600, "model + pojemność"), level(30, 14, 1500, "ten sam model")]
    market = liquidity(levels, span_days=27)
    assert market["n"] == 44 and round(market["month"]) == 49 and round(market["turnover"], 2) == 0.32
    assert liquidity(levels, span_days=None) is None            # stary comparables.db - bez reguł rynku
    assert liquidity([], span_days=30) is None


def test_niche_by_very_low_turnover():
    """Stare iPhone'y (iPhone 8: ~520 ofert / mies., znika ~7%) - okazja tylko przy cenie ≤ 50% szacunku."""
    levels = [level(190, 10, 1500)]                             # rotacja 5%
    offer = phone(title="iPhone 8 64GB", category="phone", price=900.0, total_price=915.0, description="")
    ev = apply_market_rules(deal(1500), offer, CFG, levels, span_days=30)
    assert ev["is_deal"] is False and ev["score"] <= 6
    assert any("nisza" in f for f in ev["red_flags"])
    cheap = phone(title="iPhone 8 64GB", category="phone", price=600.0, total_price=615.0, description="")
    assert apply_market_rules(deal(1500), cheap, CFG, levels, span_days=30)["is_deal"] is True
    assert niche_limit(levels, cheap, CFG, span_days=30)[0] == 750


def test_rare_but_selling_is_not_niche():
    """Pixel 10a: ~9 ofert / mies., ale 38% znika - nie nisza; reMarkable (24%, jak S23) też nie."""
    assert niche_limit([level(6, 3, 1500)], phone(category="phone"), CFG, span_days=30) is None
    assert niche_limit([level(41, 13, 1500)], phone(category="tablet"), CFG, span_days=30) is None
    # prawie brak ofert i niska rotacja - nisza
    assert niche_limit([level(6, 0, 1500)], phone(category="tablet"), CFG, span_days=30)


def test_parts_and_laptops_out_of_scope():
    levels = [level(190, 2, 1500)]                              # rotacja 1%
    for category in ("gpu", "cpu", "ram", "laptop", "bundle"):
        offer = phone(category=category, price=900.0, total_price=915.0, description="")
        assert niche_limit(levels, offer, CFG, span_days=30) is None
        ev = apply_market_rules(deal(1500), offer, CFG, levels, span_days=30)
        assert ev["is_deal"] is True and ev["score"] == 9 and "market" not in ev


def test_big_market_unaffected():
    levels = [level(2000, 900, 700)]                            # rotacja 31%
    ev = apply_market_rules(deal(700), phone(price=300.0, total_price=315.0, description=""), CFG, levels,
                            span_days=27)
    assert ev["is_deal"] is True and ev["score"] == 9
    assert niche_limit(levels, phone(), CFG, span_days=27) is None


def test_slow_turnover_lowers_score():
    levels = [level(160, 20, 800)]                              # MatePad: rotacja 11%
    ev = apply_market_rules(deal(800, score=8), phone(price=300.0, total_price=315.0, description=""), CFG, levels,
                            span_days=30)
    assert ev["score"] == 7 and ev["is_deal"] is True
    assert any("wolna rotacja" in f for f in ev["red_flags"])
    assert ev["market"] == {"month": 180, "turnover_pct": 11, "box": None}


def test_rules_disabled_by_zero():
    off = SimpleNamespace(**{**CFG.__dict__, "min_market_month": 0, "min_turnover": 0, "niche_turnover": 0})
    ev = apply_market_rules(deal(1500), phone(price=900.0, total_price=915.0, description=""), off,
                            [level(100, 1, 1500)], span_days=30)
    assert ev["is_deal"] is True and ev["score"] == 9


def test_market_note_for_ai():
    note = market_note([level(26, 18, 1500)], phone(description="bez pudełka"), CFG, span_days=27)
    assert "<rynek>" in note and "~49 ofert na miesiąc" in note and "BEZ pudełka" in note and "Dodatki" in note
    assert "Rynek tego modelu" not in market_note([level(26, 18, 1500)], phone(description=""), CFG, span_days=None)


# ---------------------------------------------------------------------------------------------- comparables.db
def test_span_days_from_meta(tmp_path):
    from sniper.tests.test_categories import _comparables_db, _market_rows
    import sqlite3
    path = tmp_path / "comparables.db"
    _comparables_db(path, _market_rows())
    assert Comparables(path).span_days() is None
    db = sqlite3.connect(path)
    db.execute("INSERT INTO meta VALUES ('span_days', '34.5')")
    db.commit()
    db.close()
    assert Comparables(path).span_days() == 34.5


def test_gate_skips_niche_before_ai(tmp_path):
    """Bramka przed AI (tryb mediany): nisza (rotacja 5%) i cena powyżej 50% szacunku → bez wywołania AI."""
    import sqlite3
    from sniper.evaluator import OfferEvaluator
    from sniper.tests.test_categories import _comparables_db
    from sniper.tests.test_evaluator import FakeClient, FakeNotifier, ai_cfg
    guides = tmp_path / "guides"
    guides.mkdir()
    (guides / "phone.md").write_text("# telefony\n", encoding="utf-8")
    (guides / "tablet.md").write_text("# tablety\n", encoding="utf-8")
    rows = [(f"iPhone 8 64GB telefon {i}", "phone", 1400 + 10 * i, int(i == 0)) for i in range(20)]
    rows += [(f"reMarkable 2 czytnik {i}", "tablet", 1400 + 10 * i, i % 2) for i in range(20)]
    _comparables_db(guides / "comparables.db", rows)
    db = sqlite3.connect(guides / "comparables.db")
    db.execute("INSERT INTO meta VALUES ('span_days', '30')")
    db.commit()
    db.close()
    cfg = ai_cfg(tmp_path, eval_mode="mediany", guides_dir=str(guides))
    evaluator = OfferEvaluator(cfg, FakeNotifier(), client=FakeClient([]))
    offer = phone(id=91, title="iPhone 8 64GB", category="phone", price=1000.0, total_price=1015.0,
                  description="")
    reason = evaluator.gate(offer)
    assert reason and reason.startswith("nisza")
    reader = phone(id=92, title="reMarkable 2 + folio", category="tablet", price=900.0, total_price=915.0,
                   description="")
    reason = evaluator.gate(reader)
    assert reason and reason.startswith("czytnik")


# ---------------------------------------------------------------------------------------------- czytniki
def test_readers_always_low_demand_without_rotation():
    """reMarkable 2 + folio: bez względu na rotację (nawet bez span_days) okazja tylko ≤ 30% szacunku."""
    from sniper.market import low_demand
    levels = [level(300, 200, 1500)]                            # rotacja 40% - i tak bez znaczenia
    folio = phone(title="reMarkable 2 + folio z klawiaturą", category="tablet", price=900.0, total_price=915.0,
                  description="")
    assert low_demand(folio)
    ev = apply_market_rules(deal(1500), folio, CFG_LOW, levels, span_days=None)
    assert ev["is_deal"] is False and any("czytnik" in f for f in ev["red_flags"])
    assert niche_limit(levels, folio, CFG_LOW, span_days=None)[0] == 450
    cheap = phone(title="reMarkable 2", category="tablet", price=380.0, total_price=395.0, description="")
    assert apply_market_rules(deal(1500), cheap, CFG_LOW, levels, span_days=None)["is_deal"] is True
    for title in ("Kindle Paperwhite 5", "Kobo Clara 2E", "PocketBook Era", "Onyx Boox Note Air", "Czytnik e-book",
                  "Wacom Intuos Pro M", "Huion Kamvas 13", "XP-Pen Artist 12"):
        assert low_demand(phone(title=title)), title
    for title in ("iPad Air 5 64GB", "Galaxy Tab S9 FE", "Samsung A17 5G"):
        assert not low_demand(phone(title=title)), title


def test_reader_note_for_ai():
    note = market_note([level(30, 10, 1500)], phone(title="Kindle Oasis", category="tablet", description=""),
                       CFG_LOW, span_days=30)
    assert "znikome zapotrzebowanie" in note and "Rynek tego modelu" not in note


CFG_LOW = SimpleNamespace(**{**CFG.__dict__, "low_demand_max_share": 0.3})
