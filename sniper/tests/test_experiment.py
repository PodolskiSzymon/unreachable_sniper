"""Eksperyment 2026-10-27 (sniper/experiment.py): tryby „mediany” i „model” na atrapach."""
import json

from sniper.comparables import Comparables, describe_levels
from sniper.evaluator import OfferEvaluator, build_request
from sniper.experiment import (MEDIANS_TASK, MODEL_TASK, cheap_guard, finish_model, formula_score, last_evaluated,
                               line, run_one)
from sniper.tests.test_categories import _comparables_db, _market_rows, phone
from sniper.tests.test_evaluator import EVAL_DEAL, FakeClient, FakeNotifier, ai_cfg, response, run


def _setup(tmp_path, rows=None):
    (tmp_path / "guides").mkdir()
    _comparables_db(tmp_path / "guides" / "comparables.db", rows or _market_rows())
    (tmp_path / "guides" / "phone.md").write_text("# Telefony\n", encoding="utf-8")
    (tmp_path / "guides" / "console.md").write_text("# Konsole\n", encoding="utf-8")


def test_levels_from_specific_to_family(tmp_path):
    _setup(tmp_path)
    comps = Comparables(tmp_path / "guides" / "comparables.db")
    levels = comps.levels("phone", "iPhone 13 128GB niebieski")
    assert levels[0]["n_active"] == 12 and levels[0]["n_gone"] == 12                  # sam iPhone 13 (bez Pro)
    assert levels[-1]["label"].startswith("cała rodzina") and levels[-1]["n_active"] == 24   # + iPhone 13 Pro
    text = describe_levels(levels)
    assert "<mediany>" in text and "1. " in text
    assert "BRAK" in describe_levels([])
    assert comps.levels("phone", "Xiaomi Redmi Note 99") == []


def test_medians_mode_prompt_and_missing_item(tmp_path):
    _setup(tmp_path)
    reply = {**EVAL_DEAL, "market_value_pln": 1500, "in_medians": False, "median_used": None,
             "corrected_model": None}
    client = FakeClient(response(reply))
    evaluator = OfferEvaluator(ai_cfg(tmp_path), FakeNotifier(), client=client)
    ev, _usage, _photos, error = run(run_one(evaluator, "mediany", phone(title="iPhone 13 128GB")))
    assert error is None
    request = client.calls[0]
    assert "<mediany>" in request["messages"][0]["content"][-1]["text"]
    assert "Zasady tego trybu" in request["system"][0]["text"]
    assert "in_medians" in request["output_config"]["format"]["schema"]["required"]
    assert ev["is_deal"] is False and ev["score"] <= 3 and "brak przedmiotu w medianach" in ev["red_flags"][-1]


def test_medians_mode_rule_uses_chosen_median(tmp_path):
    _setup(tmp_path)
    reply = {**EVAL_DEAL, "score": 9, "market_value_pln": 3000, "in_medians": True, "median_used": 1,
             "corrected_model": "iPhone 13"}
    evaluator = OfferEvaluator(ai_cfg(tmp_path), FakeNotifier(), client=FakeClient(response(reply)))
    ev, *_ = run(run_one(evaluator, "mediany", phone(title="iPhone 13 128GB", price=500.0, total_price=515.0)))
    assert ev["market_value_ai"] == 3000 and ev["market_value_pln"] < 1600            # przycięta do mediany ×1,2
    assert ev["corrected_model"] == "iPhone 13" and ev["levels"]


MODEL_REPLY = {"brand": "Apple", "model": "iPhone 13", "model_confidence": 70,
               "params": [{"name": "pamięć", "value": "128 GB", "confidence": 90, "source": "tytuł"}],
               "variants": [{"label": "iPhone 13 128 GB", "search_words": "iphone 13 128", "probability": 70},
                            {"label": "iPhone 13 Pro 128 GB", "search_words": "iphone 13 pro 128",
                             "probability": 30}],
               "condition": "sprawny", "red_flags": [], "photos_seen": 3, "reasoning": "x"}


def test_model_mode_prices_worse_variant_and_shows_upside(tmp_path):
    _setup(tmp_path)
    client = FakeClient(response(MODEL_REPLY))
    evaluator = OfferEvaluator(ai_cfg(tmp_path), FakeNotifier(), client=client)
    ev, *_ = run(run_one(evaluator, "model", phone(title="iPhone 13 128GB", price=450.0, total_price=465.0)))
    assert "variants" in client.calls[0]["output_config"]["format"]["schema"]["required"]
    assert "NIE wyceniasz" in client.calls[0]["system"][0]["text"]
    assert ev["priced_as"] == "iPhone 13 128 GB"                                     # gorszy (tańszy) wariant
    assert ev["is_deal"] is True and 7 <= ev["score"] <= 10
    assert ev["upside"].startswith("jeśli to iPhone 13 Pro 128 GB")
    assert "Pro" in line({"score": 5, "is_deal": False}, ev, phone(), None)


def test_model_mode_no_data_damage_and_rule(tmp_path):
    _setup(tmp_path)
    evaluator = OfferEvaluator(ai_cfg(tmp_path), FakeNotifier(), client=FakeClient(response(MODEL_REPLY)))
    cfg = evaluator.cfg
    unknown = {**MODEL_REPLY, "variants": [{"label": "Xiaomi 99", "search_words": "xiaomi redmi 99",
                                            "probability": 100}], "red_flags": []}
    ev = finish_model(json.loads(json.dumps(unknown)), phone(), cfg, evaluator)
    assert ev["is_deal"] is False and "brak danych" in ev["red_flags"][-1]
    broken = {**MODEL_REPLY, "condition": "blokada", "red_flags": []}
    ev = finish_model(json.loads(json.dumps(broken)), phone(price=300.0, total_price=315.0), cfg, evaluator)
    assert ev["is_deal"] is False and ev["score"] <= 2
    pricey = finish_model(json.loads(json.dumps({**MODEL_REPLY, "red_flags": []})),
                          phone(price=1000.0, total_price=1015.0), cfg, evaluator)
    assert pricey["is_deal"] is False and pricey["score"] <= 6
    assert formula_score(0.46, 900) == 10 and formula_score(0.46, 600) == 9 and formula_score(0.31, 600) == 7


def test_cheap_guard_new_account_and_manual_check():
    """27.10: iPhone 16 za 800 zł od konta bez opinii dostał 10/10 w trybie „model” - teraz nie okazja."""
    from dataclasses import replace
    offer = phone(price=800.0, total_price=800.0)
    new_seller = replace(offer.seller, feedback_count=0)
    ev = cheap_guard({"is_deal": True, "score": 10.0}, replace(offer, seller=new_seller), 842.9, 2085)
    assert ev["is_deal"] is False and ev["score"] == 3.0 and "bez opinii" in ev["red_flags"][-1]
    ev = cheap_guard({"is_deal": True, "score": 10.0}, offer, 842.9, 2085)          # konto z opiniami
    assert ev["is_deal"] is True and ev["score"] == 7.0 and "sprawdź ręcznie" in ev["red_flags"][-1]
    ev = cheap_guard({"is_deal": True, "score": 8.0}, offer, 901.3, 1457)            # iPhone 14 Pro: 62% - bez zmian
    assert ev == {"is_deal": True, "score": 8.0}


def test_default_task_unchanged():
    from sniper.tests.test_evaluator import laptop
    req, _ = build_request(laptop(), ai_cfg_plain(), "W")
    assert "is_deal" in req["output_config"]["format"]["schema"]["required"]
    assert MODEL_TASK.schema is not MEDIANS_TASK.schema


def ai_cfg_plain():
    import pathlib
    import tempfile
    return ai_cfg(pathlib.Path(tempfile.mkdtemp()))


def test_last_evaluated_takes_latest_distinct(tmp_path):
    def rec(i, score, status="oceniona", source=None):
        return json.dumps({"status": status, "source": source, "evaluation": {"score": score, "is_deal": False},
                           "offer": phone(id=i).to_dict()})
    lines = [rec(1, 3), rec(2, 4), rec(1, 5), rec(3, 6, status="odfiltrowana"), rec(4, 9, source="experiment_model")]
    (tmp_path / "evaluations.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    pairs = last_evaluated(tmp_path, 50)
    assert [(o.id, e["score"]) for o, e in pairs] == [(2, 4), (1, 5)]
    assert [o.id for o, _ in last_evaluated(tmp_path, 1)] == [1]
