"""python -m sniper.bench - porównanie wariantów oceny (jakość / koszt) na atrapie API."""
import json

import pytest

from sniper.bench import (estimate, load_golden, load_offers, parse_custom, resolve_variants, run_bench, split_overrides,
                          summary)
from sniper.tests.test_categories import phone
from sniper.tests.test_evaluator import EVAL_DEAL, FakeClient, ai_cfg, response, run
from sniper.tests.test_experiment import _setup


def _offers(tmp_path):
    offers = [phone(id=1, price=450.0, total_price=465.0), phone(id=2, price=700.0, total_price=715.0),
              phone(id=3, price=1500.0, total_price=1515.0), phone(id=4, price=120.0, total_price=130.0)]
    lines = [json.dumps({"status": "oceniona", "evaluation": {"score": 5, "is_deal": False}, "offer": o.to_dict()})
             for o in offers]
    lines.append(json.dumps({"status": "oceniona", "source": "bench", "evaluation": {"score": 1},
                             "offer": phone(id=9).to_dict()}))
    (tmp_path / "evaluations.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return offers


def test_variants_and_custom():
    assert parse_custom("moj:model=gemini-3.1-flash-lite,max_photos=4,tryb=mediany") == (
        "moj", {"model": "gemini-3.1-flash-lite", "max_photos": 4, "tryb": "mediany"})
    assert split_overrides({"tryb": "kaskada", "max_photos": 2}) == ("kaskada", {"max_photos": 2},
                                                                     "gemini-3.1-flash-lite")
    with pytest.raises(SystemExit):
        split_overrides({"zdjecia": 3})                            # literówka → czytelny błąd, nie cichy brak zmiany
    with pytest.raises(SystemExit):
        resolve_variants("obecny,nie_ma")
    variants = resolve_variants("obecny,niska_rozdz", ["x:model=gemini-3.1-flash-lite"])
    assert list(variants) == ["obecny", "niska_rozdz", "x"]
    base = ai_cfg(__import__("pathlib").Path("."), model="gemini-3.8-flash", max_photos=6)
    full = estimate(base, resolve_variants("obecny"), 10)
    low = estimate(base, resolve_variants("niska_rozdz"), 10)
    assert 0 < low < full                                          # niska rozdzielczość = mniej tokenów zdjęć


def test_load_offers_last_and_golden(tmp_path):
    _offers(tmp_path)
    assert [o.id for o, _ in load_offers(tmp_path, 2)] == [3, 4]                 # bez wpisu z bench
    assert [o.id for o, _ in load_offers(tmp_path, 2, golden_ids={1: {}})] == [3, 4, 1]
    (tmp_path / "g.json").write_text(json.dumps({"offers": {"1": {"deal": True}}}), encoding="utf-8")
    assert load_golden(tmp_path / "g.json") == {1: {"deal": True}}
    assert load_golden(tmp_path / "brak.json") == {}


def test_run_bench_gate_golden_and_cascade(tmp_path):
    _setup(tmp_path)
    offers = [(o, {}) for o in _offers(tmp_path)]
    golden = {1: {"deal": True, "sale": [1000, 1300]}, 2: {"deal": False}, 3: {"deal": False}}
    client = FakeClient(response({**EVAL_DEAL, "market_value_pln": 1200, "score": 8}))
    variants = resolve_variants("obecny,bez_zdjec,kaskada")
    rows, skipped = run(run_bench(ai_cfg(tmp_path), offers, variants, golden, concurrency=4, client=client,
                                  floor=300))
    assert set(skipped) == {3, 4} and "podobnych" in skipped[3] and "300" in skipped[4]
    assert len(rows) == 6 and len(client.calls) == 2 + 2 + 4                   # kaskada: etap 1 + etap 2
    no_photos = [c for c in client.calls if not any(b.get("type") == "image" for m in c["messages"]
                                                       for b in m["content"])]
    assert len(no_photos) >= 2 + 2                                              # bez_zdjec + etap 1 kaskady
    deal = {(r["variant"], r["id"]): r["evaluation"]["is_deal"] for r in rows}
    assert deal[("obecny", 1)] is True and deal[("obecny", 2)] is False         # program: reguła zysku
    lines = summary(rows, skipped, offers, golden, "obecny", variants)
    text = "\n".join(lines)
    assert "do AI 2 (bramka odcięła 2)" in text and "złoty zbiór 3 (okazji 1)" in text
    row = next(line for line in lines if line.startswith("obecny"))
    assert "1 /        0 /        0" in row and "1/1" in row and "(odniesienie)" in row
    assert next(line for line in lines if line.startswith("bez_zdjec")).split()[-3:] == ["100%"] * 3
    assert "złote T" in text


def test_gemini_media_resolution(tmp_path):
    from sniper.evaluator import build_gemini_request
    cfg = ai_cfg(tmp_path, provider="gemini", model="gemini-3.8-flash", media_resolution="low")
    request = build_gemini_request(phone(), cfg, "W", [("https://x/1.jpg", None)])
    assert request["config"].media_resolution.name == "MEDIA_RESOLUTION_LOW"
    assert build_gemini_request(phone(), cfg, "W", [])["config"].media_resolution is None     # bez zdjęć - nic
    plain = build_gemini_request(phone(), ai_cfg(tmp_path, provider="gemini", model="gemini-3.8-flash"), "W",
                                 [("https://x/1.jpg", None)])
    assert plain["config"].media_resolution is None


def test_eval_mode_medians_in_sniper(tmp_path):
    """SNIPER_EVAL_MODE=mediany: zapytanie z <mediany>, program jak w eksperymencie; laptop dalej zwykle."""
    from sniper.evaluator import OfferEvaluator
    from sniper.tests.test_evaluator import laptop
    _setup(tmp_path)
    reply = {**EVAL_DEAL, "score": 9, "market_value_pln": 3000, "in_medians": True, "median_used": 1,
             "corrected_model": None}
    client = FakeClient(response(reply))
    evaluator = OfferEvaluator(ai_cfg(tmp_path, eval_mode="mediany"), None, client=client)
    record = run(evaluator.evaluate(phone(title="iPhone 13 128GB", price=500.0, total_price=515.0)))
    assert "<mediany>" in client.calls[0]["messages"][0]["content"][-1]["text"]
    ev = record["evaluation"]
    assert ev["levels"] and ev["market_value_pln"] < 1600 and ev["market_value_ai"] == 3000   # przycięte do mediany
    run(evaluator.evaluate(laptop()))
    assert "<mediany>" not in client.calls[1]["messages"][0]["content"][-1]["text"]


PHONE_TABLE = """# Telefony

| grupa | ofert | sprzedam za ~ | max zakup |
|---|---|---|---|
| iphone 13 128 GB | 100 | 1 100 | 700 |
| iphone 13 pro 128 GB | 50 | 1 900 | 1 300 |
| galaxy a51 128 GB | 20 | 380 | 100 |
"""


def _basal_client(seen, choice="m1", akcesorium=0.05):
    import httpx

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        keys = list(body["questions"]["model"]["criteria"])
        answers = {"model": {"type": "choice", "choice": choice,
                             "probabilities": {k: (0.9 if k == choice else 0.1 / (len(keys) - 1)) for k in keys}},
                   "akcesorium": {"type": "noul", "noul": akcesorium},
                   "uszkodzony": {"type": "noul", "noul": 0.02}, "poza_vinted": {"type": "noul", "noul": 0.01}}
        return httpx.Response(200, json={"answers": answers, "usage": {"latency_ms": 42}})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_basal_mode_candidates_medians_and_answer(tmp_path):
    from sniper import basal
    from sniper.evaluator import OfferEvaluator
    _setup(tmp_path)
    (tmp_path / "guides" / "phone.md").write_text(PHONE_TABLE, encoding="utf-8")
    offer = phone(title="Iphone 13 niebieski", description="Sprawny, 128 GB, bateria 88%", price=500.0,
                  total_price=515.0)
    evaluator = OfferEvaluator(ai_cfg(tmp_path), None, client=FakeClient(response(
        {**EVAL_DEAL, "score": 8, "market_value_pln": 1200, "in_medians": True, "median_used": 1,
         "corrected_model": None})))
    cands = basal.candidates(evaluator.guides._file("phone").rows, offer)
    assert [r.name for _k, r in cands][:2] == ["iphone 13 128 GB", "iphone 13 pro 128 GB"]
    assert "galaxy a51 128 GB" not in [r.name for _k, r in cands]
    seen = []
    ev, usage, photos, error, info = run(basal.run(evaluator, offer, client=_basal_client(seen)))
    assert error is None and info["choice"] == "iphone 13 128 GB" and info["latency_ms"] == 42
    request = seen[0]
    assert request["questions"]["model"]["type"] == "choice" and "inny" in request["questions"]["model"]["criteria"]
    assert set(request["questions"]) == {"model", "akcesorium", "uszkodzony", "poza_vinted"}
    assert "Tytuł: Iphone 13 niebieski" in request["state"]
    prompt = evaluator.client.calls[0]["messages"][0]["content"][-1]["text"]
    assert "<rozpoznanie_basal>" in prompt and "iphone 13 128 GB (90%)" in prompt and "<mediany>" in prompt
    assert ev["basal"]["query"] == "iphone 13 128 GB" and ev["levels"]
    assert ev["is_deal"] is True                                         # 1200 - 515 - opłata - 46 → reguła OK


def test_basal_unreachable_and_test_day_set(tmp_path):
    import httpx

    from sniper import basal
    from sniper.bench import SETS, VARIANTS
    from sniper.evaluator import OfferEvaluator
    _setup(tmp_path)
    (tmp_path / "guides" / "phone.md").write_text(PHONE_TABLE, encoding="utf-8")

    def down(request):
        raise httpx.ConnectError("odmowa połączenia")
    evaluator = OfferEvaluator(ai_cfg(tmp_path), None, client=FakeClient(response()))
    ev, _u, _p, error, _info = run(basal.run(evaluator, phone(), client=httpx.AsyncClient(
        transport=httpx.MockTransport(down))))
    assert ev is None and "basal niedostępny" in error and evaluator.client.calls == []   # bez kosztu AI
    names = resolve_variants("dzien_testowy")
    assert len(names) == 12 == len(SETS["dzien_testowy"].split(","))
    assert VARIANTS["bas_25l_z3"][0] == {"tryb": "basal", "model": "gemini-2.5-flash-lite", "max_photos": 3,
                                         "effort": "medium"}
    assert {mode for mode, _o, _s in names.values()} == {"mediany", "model", "basal"}


def test_bench_runs_basal_variant(tmp_path):
    _setup(tmp_path)
    (tmp_path / "guides" / "phone.md").write_text(PHONE_TABLE, encoding="utf-8")
    offers = [(phone(id=1, title="iPhone 13 128GB", price=450.0, total_price=465.0), {})]
    reply = {**EVAL_DEAL, "market_value_pln": 1200, "in_medians": True, "median_used": 1, "corrected_model": None}
    variants = resolve_variants("bas_38_z3")
    rows, _skipped = run(run_bench(ai_cfg(tmp_path), offers, variants, {}, client=FakeClient(response(reply)),
                                   basal_client=_basal_client([])))
    assert rows[0]["basal"]["choice"] == "iphone 13 128 GB" and rows[0]["evaluation"]["is_deal"] is True
    text = "\n".join(summary(rows, {}, offers, {}, "bas_38_z3", variants))
    assert "basal: 1 rozpoznań, mediana czasu 42 ms" in text


def test_gemini_2_thinking_budget(tmp_path):
    from sniper.evaluator import build_gemini_request
    old = build_gemini_request(phone(), ai_cfg(tmp_path, provider="gemini", model="gemini-2.5-flash-lite",
                                               effort="medium"), "W", [])
    assert old["config"].thinking_config.thinking_budget == 2048 and old["config"].thinking_config.thinking_level is None
    new = build_gemini_request(phone(), ai_cfg(tmp_path, provider="gemini", model="gemini-3.8-flash",
                                               effort="medium"), "W", [])
    assert new["config"].thinking_config.thinking_level.name == "MEDIUM"
