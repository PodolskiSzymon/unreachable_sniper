"""Kilka proxy (osobne pętle skanu) i przyspieszanie przy przepełnionej stronie - 2026-10-30.
Uruchom: python -m pytest sniper/tests"""
import asyncio
import logging
import time

import httpx

import sniper.session as sess
from sniper.categories import CatalogSpec
from sniper.config import (MAIN_PROXY, ScoutConfig, SmtpConfig, extra_proxy_url, proxy_poll_interval,
                           proxy_routes_from_env)
from sniper.notifier import EmailNotifier
from sniper.scout import Scout
from sniper.session import VintedSession


def _session(handler):
    session = VintedSession()
    session.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=session.client.headers)
    return session


def _catalog(ids):
    return httpx.Response(200, json={"items": [{"id": i, "title": "iPhone 13", "user": {}} for i in ids]})


def test_extra_proxy_from_env(monkeypatch, caplog):
    """Dodatkowe proxy = adres, login:hasło i kategorie (SNIPER_PROXY_2_*); polskie nazwy kategorii też działają."""
    monkeypatch.setenv("SNIPER_PROXY_2_HOST", "gw.dataimpulse.com:823")
    monkeypatch.setenv("SNIPER_PROXY_2_AUTH", "login__cr.pl:has@lo")
    monkeypatch.setenv("SNIPER_PROXY_2_KATEGORIE", "telefony, Console")
    monkeypatch.setenv("SNIPER_PROXY_2_POLL", "3")
    monkeypatch.setenv("SNIPER_PROXY_3_URL", "http://u:p@p.webshare.io:80")
    monkeypatch.setenv("SNIPER_PROXY_3_KATEGORIE", "3602")
    monkeypatch.setenv("SNIPER_PROXY_4_HOST", "zapas:1")                # adres bez kategorii - nieużywane
    monkeypatch.setenv("SNIPER_PROXY_HOST", "geo.iproyal.com:12321")
    monkeypatch.setenv("SNIPER_PROXY_AUTH", "LOGIN:HASLO_country-pl")
    with caplog.at_level(logging.WARNING):
        assert proxy_routes_from_env() == {"phone": "2", "console": "2", "3602": "3"}
    assert "SNIPER_PROXY_4_KATEGORIE" in caplog.text
    assert extra_proxy_url("2") == "http://login__cr.pl:has%40lo@gw.dataimpulse.com:823"
    assert extra_proxy_url("3") == "http://u:p@p.webshare.io:80"
    assert extra_proxy_url("5") == ""
    assert extra_proxy_url(MAIN_PROXY) == "http://LOGIN:HASLO_country-pl@geo.iproyal.com:12321"
    assert proxy_poll_interval("2", 7.0) == 3.0 and proxy_poll_interval("3", 7.0) == 7.0
    assert proxy_poll_interval(MAIN_PROXY, 7.0) == 7.0                 # główne: SNIPER_POLL_INTERVAL


def test_build_extra_sessions_skips_proxy_without_address(monkeypatch, tmp_path, caplog):
    from sniper.__main__ import build_extra_sessions

    monkeypatch.setenv("SNIPER_PROXY_2_URL", "http://u:p@gw.dataimpulse.com:823")
    monkeypatch.delenv("SNIPER_PROXY_3_URL", raising=False)
    monkeypatch.delenv("SNIPER_PROXY_3_HOST", raising=False)
    cfg = ScoutConfig(proxy_routes={"phone": "2", "console": "3"}, log_dir=str(tmp_path))
    with caplog.at_level(logging.INFO):
        sessions = build_extra_sessions(cfg)
    try:
        assert list(sessions) == ["2"]                                  # proxy 3 bez adresu
        assert sessions["2"]._state_file == tmp_path / "session_2.json"   # osobne ciastka na proxy
        assert "SNIPER_PROXY_3_HOST" in caplog.text and "u:p" not in caplog.text      # bez hasła w logu
    finally:
        for s in sessions.values():
            asyncio.run(s.close())


def test_routes_split_catalogs_between_proxies_and_details_follow():
    """Telefony i konsole przez proxy „di”, reszta przez główne; szczegóły oferty przez to samo proxy co katalog."""
    calls = {"glowne": [], "di": []}

    def handler_for(name):
        def handler(request):
            calls[name].append(request.url.path.rsplit("/", 1)[-1])
            if "svc-catalogue" in request.url.path:
                return _catalog([int(request.url.params["attribute_ids[catalog]"])])
            if request.url.path.endswith("shipping_details"):
                return httpx.Response(200, json={"shipping_details": {"price": {"amount": "12", "currency_code": "PLN"}}})
            return httpx.Response(200, json={"id": 1, "title": "iPhone 13", "price": {"amount": "900",
                                                                                      "currency_code": "PLN"}})
        return handler

    async def scenario():
        main, di = _session(handler_for("glowne")), _session(handler_for("di"))
        cfg = ScoutConfig(category="3580", catalogs="3580:300-6000, 3661:300-3600, 3025:300-4400", price_floor=0,
                          proxy_routes={"phone": "di", "console": "di"}, log_dir="")
        scout = Scout(cfg, main, EmailNotifier(SmtpConfig()), sessions={"di": di})
        emitted = []
        scout.emit = emitted.append
        lanes = scout.lanes
        main_specs = {scout.next_spec(lanes["glowne"]).catalog_id for _ in range(3)}
        di_specs = {scout.next_spec(lanes["di"]).catalog_id for _ in range(4)}
        phone = CatalogSpec(3661, "300", "3600")
        await scout.poll_catalog(phone)                    # bez podanej pętli - wg trasy
        await scout.inspect({"id": 1, "title": "iPhone 13"}, phone, "phone")
        await main.close()
        await di.close()
        return main_specs, di_specs, emitted

    main_specs, di_specs, emitted = asyncio.run(scenario())
    assert main_specs == {3580} and di_specs == {3661, 3025}
    assert sorted(calls["di"]) == ["items", "shipping_details", "sidebar"] and calls["glowne"] == []
    assert len(emitted) == 1 and emitted[0].total_price == 912.0


def test_overlap_with_previous_scan_keeps_normal_pace():
    """Jest oferta z poprzedniego skanu → nic nie umknęło: bez dociągania stron i bez przyspieszania."""
    market, pages = [101, 100], []

    def handler(request):
        page = int(request.url.params["page"])
        pages.append(page)
        return _catalog(market[(page - 1) * 2: page * 2])

    async def scenario():
        session = _session(handler)
        cfg = ScoutConfig(category="3661", catalogs="", per_page=2, price_floor=0, poll_interval=8.0)
        scout = Scout(cfg, session, EmailNotifier(SmtpConfig()))
        inspected = []

        async def fake_inspect(item, spec, category):
            inspected.append(item["id"])
        scout.inspect = fake_inspect
        await scout.poll_catalog()                         # rozgrzewka
        market.insert(0, 102)                              # jedna nowa - 101 zostaje na stronie 1
        await scout.poll_catalog()
        await asyncio.gather(*scout._tasks)
        await session.close()
        return scout, inspected

    scout, inspected = asyncio.run(scenario())
    assert pages == [1, 1] and inspected == [102]
    assert list(scout._per_catalog.values()) == [[2, 1, 1, 0]]
    assert scout.lanes["glowne"].fast == {}


def test_shared_browser_lock_refreshes_one_session_at_a_time(monkeypatch):
    """Kilka proxy = kilka sesji; przeglądarki odświeżają się po kolei (serwer ma 4 GB RAM)."""
    running, peak = {"n": 0}, {"n": 0}

    async def fake_tokens(proxy_url=None, wait_ms=0, **kw):
        running["n"] += 1
        peak["n"] = max(peak["n"], running["n"])
        await asyncio.sleep(0.05)
        running["n"] -= 1
        return [{"name": "anon_id", "value": "a", "domain": ".vinted.pl", "path": "/"}], {"x-csrf-token": "t"}

    monkeypatch.setattr(sess, "fetch_fresh_tokens", fake_tokens)

    async def scenario(shared):
        sessions = [VintedSession(refresh_retry_delay=0) for _ in range(3)]
        lock = asyncio.Lock()
        for s in sessions:
            s.browser_lock = lock if shared else None
        await asyncio.gather(*(s.refresh() for s in sessions))
        for s in sessions:
            await s.close()

    asyncio.run(scenario(shared=True))
    assert peak["n"] == 1
    peak["n"] = 0
    asyncio.run(scenario(shared=False))
    assert peak["n"] == 3                                  # bez wspólnej blokady - naraz (jedno proxy jak dotąd)


def test_failing_proxy_does_not_stop_other_lanes(monkeypatch):
    """Błąd jednego proxy (np. 500 / zerwane połączenie) - jego pętla czeka (backoff), pozostałe skanują dalej."""
    async def fake_tokens(proxy_url=None, wait_ms=0, **kw):
        return [{"name": "anon_id", "value": "a", "domain": ".vinted.pl", "path": "/"}], {"x-csrf-token": "t"}

    monkeypatch.setattr(sess, "fetch_fresh_tokens", fake_tokens)
    counts = {"glowne": 0, "di": 0}

    def ok(request):
        counts["glowne"] += 1
        return _catalog([counts["glowne"]])

    def broken(request):
        counts["di"] += 1
        return httpx.Response(500, text="awaria")

    async def scenario():
        main, di = _session(ok), _session(broken)
        cfg = ScoutConfig(category="3580", catalogs="3580:300-6000, 3661:300-3600", price_floor=0, log_dir="",
                          proxy_routes={"phone": "di"}, poll_interval=0.02, poll_min=0.01, poll_jitter=0.0, heartbeat_interval=0,
                          session_max_age_min=0)
        scout = Scout(cfg, main, EmailNotifier(SmtpConfig()), sessions={"di": di})

        async def fake_inspect(item, spec, category):
            return None
        scout.inspect = fake_inspect
        task = asyncio.create_task(scout.run())
        await asyncio.sleep(0.6)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await main.close()
        await di.close()

    started = time.monotonic()
    asyncio.run(scenario())
    assert time.monotonic() - started < 5
    assert counts["di"] >= 1 and counts["glowne"] >= 5      # „di” w backoffie (≥ 2 s), główne skanuje co 0,02 s


def test_burst_in_one_catalog_speeds_up_only_that_catalog(monkeypatch):
    """Wysyp telefonów nie przyspiesza RAM-u na tym samym proxy: telefony co SNIPER_POLL_MIN, reszta jak dotąd."""
    async def fake_tokens(proxy_url=None, wait_ms=0, **kw):
        return [{"name": "anon_id", "value": "a", "domain": ".vinted.pl", "path": "/"}], {"x-csrf-token": "t"}

    monkeypatch.setattr(sess, "fetch_fresh_tokens", fake_tokens)
    scans = {"3661": 0, "3603": 0}

    def handler(request):
        catalog = request.url.params["attribute_ids[catalog]"]
        scans[catalog] += 1
        if catalog == "3661":                              # telefony: za każdym razem cała strona nowych
            return _catalog([1000 * scans[catalog] + i for i in range(2)])
        return _catalog([7, 6])                            # RAM: spokojnie, te same oferty

    async def scenario():
        session = _session(handler)
        cfg = ScoutConfig(category="3580", catalogs="3661:300-3600, 3603:300-2700", price_floor=0, log_dir="",
                          per_page=2, poll_interval=0.2, poll_min=0.02, poll_step=0.19, poll_jitter=0.0,
                          fast_minutes=10.0, heartbeat_interval=0, session_max_age_min=0)
        scout = Scout(cfg, session, EmailNotifier(SmtpConfig()))

        async def fake_inspect(item, spec, category):
            return None
        scout.inspect = fake_inspect
        task = asyncio.create_task(scout.run())
        await asyncio.sleep(1.5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await session.close()
        return scout

    scout = asyncio.run(scenario())
    assert [b["spec"].catalog_id for b in scout.lanes["glowne"].fast.values()] == [3661]
    assert scans["3661"] >= 4 * scans["3603"] and scans["3603"] <= 5     # RAM co ~0,4 s (2 katalogi × 0,2 s)
