"""Powiadomienia push na telefon (sniper/push.py) - bez sieci (httpx.MockTransport)."""
import asyncio
import json
from types import SimpleNamespace

import httpx

from sniper import push as pu


def cfg(**kw):
    base = dict(topic="sniper-a8f3kq", server="https://ntfy.sh", token="", min_score=8.0, priority=5, attach_photo=True)
    base.update(kw)
    return SimpleNamespace(**base)


OFFER = {"title": "Legion 5 RTX 3060", "price": 2400.0, "total_price": 2421.9,
         "url": "https://www.vinted.pl/items/1-legion", "photo_urls": ["https://images1.vinted.net/a.jpg"]}
EVAL = {"score": 9, "is_deal": True, "max_buy_price_pln": 3000, "potential_profit_pln": 600,
        "gpu_model": "RTX 3060", "reasoning": "Cena dużo poniżej rynku."}


def test_deal_message_polish_utf8_click_and_photo():
    msg = pu.build_deal_message(OFFER, EVAL, "sniper-a8f3kq", priority=5)
    assert msg["topic"] == "sniper-a8f3kq" and msg["priority"] == 5
    assert msg["title"] == "Okazja 9/10: Legion 5 RTX 3060"
    assert "2 545 zł z wysyłką i opłatą Vinted" in msg["message"]          # 2 421,90 + 5% × 2 400 + 2,90 and "zysk ~600 zł" in msg["message"]
    assert msg["click"] == OFFER["url"] and msg["actions"][0]["url"] == OFFER["url"]
    assert msg["attach"] == OFFER["photo_urls"][0]
    assert "attach" not in pu.build_deal_message(OFFER, EVAL, "t", attach_photo=False)


def test_wants_only_evaluated_deals_above_threshold():
    p = pu.PushNotifier(cfg(min_score=8))
    assert p.wants({"status": "oceniona", "evaluation": {"score": 8}})
    assert not p.wants({"status": "oceniona", "evaluation": {"score": 7.5}})
    assert not p.wants({"status": "odfiltrowana", "evaluation": None})
    assert not p.wants({"status": "nieoceniona", "evaluation": None})


def test_send_posts_json_to_server_and_survives_errors():
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("authorization"), json.loads(request.content)))
        return httpx.Response(200 if len(seen) == 1 else 500)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = pu.PushNotifier(cfg(token="tk_abc"), client=client)
            ok1 = await p.send(pu.build_deal_message(OFFER, EVAL, "sniper-a8f3kq"))
            ok2 = await p.send(pu.build_deal_message(OFFER, EVAL, "sniper-a8f3kq"))   # 500 - bez wyjątku
            return ok1, ok2, p.sent, p.failed
    ok1, ok2, sent, failed = asyncio.run(scenario())
    assert (ok1, ok2, sent, failed) == (True, False, 1, 1)
    url, auth, body = seen[0]
    assert url == "https://ntfy.sh" and auth == "Bearer tk_abc" and body["title"].startswith("Okazja 9/10")


def test_build_push_disabled_without_topic():
    assert pu.build_push(SimpleNamespace(ntfy=cfg(topic=""))) is None
    assert pu.build_push(SimpleNamespace(ntfy=cfg())) is not None


def test_evaluator_pushes_deal_even_when_mail_suppressed(tmp_path):
    """Push idzie niezależnie od maili (np. SNIPER_MAIL_ONLY_PURCHASES) i od auto-zakupu."""
    from sniper.evaluator import OfferEvaluator
    from sniper.tests.test_evaluator import ai_cfg, FakeNotifier

    class FakePush:
        def __init__(self):
            self.records = []

        def wants(self, record):
            return record["status"] == "oceniona" and record["evaluation"]["score"] >= 8

        def notify_deal(self, record):
            self.records.append(record)

        def wants_question(self, record):
            return False

    ev = OfferEvaluator(ai_cfg(tmp_path), FakeNotifier(), log_dir=tmp_path)
    ev.push = FakePush()
    from sniper.notifier import sample_offer
    offer = sample_offer()
    good = ev._record(offer, "oceniona", evaluation=dict(EVAL, score=9))
    weak = ev._record(offer, "oceniona", evaluation=dict(EVAL, score=5))
    ev._finish(offer, good)
    ev._finish(offer, weak)
    assert ev.push.records == [good] and good.get("pushed") is True


QUESTION = dict(EVAL, score=6, is_deal=False, laptop_model="iPad 10 64 GB (Wi-Fi?)",
                market_value_pln=950, seller_question="Dzień dobry, czy to wersja Wi-Fi, czy z kartą SIM (5G)?",
                buy_if_answer="jeśli 5G - kupuj, zysk ~350 zł")


def test_question_message_low_priority_with_ready_text():
    msg = pu.build_question_message(OFFER, QUESTION, "sniper-a8f3kq", priority=3)
    assert msg["priority"] == 3 and msg["title"].startswith("Zapytaj sprzedawcę: ")
    assert "Wyślij: Dzień dobry, czy to wersja Wi-Fi" in msg["message"]
    assert "Kupuj, jeśli: jeśli 5G - kupuj" in msg["message"]
    assert "gorszego wariantu: 6/10" in msg["message"] and msg["click"] == OFFER["url"]


def test_wants_question_only_with_question_and_min_score():
    p = pu.PushNotifier(cfg(ask_min_score=5, ask_priority=3))
    assert p.wants_question({"status": "oceniona", "evaluation": QUESTION})
    assert not p.wants_question({"status": "oceniona", "evaluation": dict(QUESTION, seller_question="")})
    assert not p.wants_question({"status": "oceniona", "evaluation": dict(QUESTION, score=3)})
    assert not p.wants_question({"status": "odfiltrowana", "evaluation": None})


def test_deal_message_shows_question_when_present():
    msg = pu.build_deal_message(OFFER, dict(QUESTION, score=9), "t")
    assert "Najpierw zapytaj: Dzień dobry" in msg["message"] and "Kupuj, jeśli:" in msg["message"]


def test_evaluator_pushes_question_when_not_deal(tmp_path):
    from sniper.evaluator import OfferEvaluator
    from sniper.notifier import sample_offer
    from sniper.tests.test_evaluator import FakeNotifier, ai_cfg

    sent = []
    ev = OfferEvaluator(ai_cfg(tmp_path), FakeNotifier(), log_dir=tmp_path)
    ev.push = pu.PushNotifier(cfg(ask_min_score=5, ask_priority=3))
    ev.push.notify_deal = lambda record: sent.append(("deal", record))
    ev.push.notify_question = lambda record: sent.append(("question", record))
    offer = sample_offer()
    asked = ev._record(offer, "oceniona", evaluation=QUESTION)
    ev._finish(offer, asked)
    assert [kind for kind, _ in sent] == ["question"] and asked.get("pushed_question") is True


def test_normalize_keeps_seller_question():
    from sniper.evaluator import normalize_evaluation
    ev = normalize_evaluation(json.dumps({"score": 6, "seller_question": " Czy 5G? ", "buy_if_answer": None}))
    assert ev["seller_question"] == "Czy 5G?" and ev["buy_if_answer"] == ""
