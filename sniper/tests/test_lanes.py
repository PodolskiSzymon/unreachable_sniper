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


# ---------------------------------------------------------------- padnięte proxy nie wstrzymuje innych (2026-11-01)
def test_proxy_407_aborts_browser_visit_at_once(monkeypatch):
    """407 od proxy przerywa wizytę przeglądarki od razu (wcześniej czekała do limitu próby i gubiła 407)."""
    async def upstream(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    async def hanging_browser(cfg, wait_ms, light):
        host, port = cfg["server"].rsplit("/", 1)[1].split(":")
        reader, writer = await asyncio.open_connection(host, int(port))
        writer.write(b"CONNECT www.vinted.pl:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        await asyncio.sleep(60)                           # Chromium „wisi” po ERR_PROXY_AUTH_UNSUPPORTED
    monkeypatch.setattr(sess, "_browse_vinted", hanging_browser)

    async def scenario():
        server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        started = time.monotonic()
        try:
            await sess._browse_via_proxy(f"http://U:ZLE@127.0.0.1:{port}", 1000)
        except sess.ProxyAuthRejected as exc:
            return time.monotonic() - started, str(exc)
        finally:
            server.close()

    took, message = asyncio.run(scenario())
    assert took < 5 and "407" in message and "127.0.0.1" in message and "ZLE" not in message


def test_407_ends_refresh_series_without_retries(monkeypatch):
    calls = []

    async def rejected(*a, **k):
        calls.append(1)
        raise sess.ProxyAuthRejected("407")
    monkeypatch.setattr(sess, "fetch_fresh_tokens", rejected)

    async def scenario():
        session = VintedSession(refresh_attempts=6, refresh_retry_delay=0)
        try:
            await session.refresh()
        except sess.ProxyAuthFailed:
            return True
        finally:
            await session.close()

    assert asyncio.run(scenario()) is True and len(calls) == 1


def test_failing_proxy_does_not_hold_shared_browser(monkeypatch):
    """Seria nieudanych prób jednego proxy zajmuje przeglądarkę tylko na czas próby - inne proxy odświeża się
    w przerwie między próbami (log 01.11: laptopy stały 11 min, bo proxy 2 trzymało blokadę przez 6 prób)."""
    order = []

    async def fake_fetch(proxy_url, wait_ms, meter=None, light=True):
        await asyncio.sleep(0.05)
        if proxy_url == "http://zly:1":
            order.append("zly-próba")
            raise TimeoutError()
        order.append("dobry-ok")
        return [], {"x-csrf-token": "t"}
    monkeypatch.setattr(sess, "fetch_fresh_tokens", fake_fetch)

    async def scenario():
        lock = asyncio.Lock()
        bad = VintedSession(proxy_url="http://zly:1", refresh_attempts=3, refresh_retry_delay=0.2)
        good = VintedSession(proxy_url="http://dobry:1")
        bad.browser_lock = good.browser_lock = lock
        failed = asyncio.ensure_future(bad.refresh())
        await asyncio.sleep(0.01)
        await good.refresh()                               # nie czeka na wszystkie 3 próby złego proxy
        done_early = not failed.done()
        try:
            await failed
        except sess.SessionExpired:
            pass
        for s in (bad, good):
            await s.close()
        return done_early

    assert asyncio.run(scenario()) is True
    assert order.index("dobry-ok") < len(order) - 1 and order.count("zly-próba") == 3


def test_lane_backoff_grows_and_alerts_once(monkeypatch):
    sent = []

    class FakePush:
        cfg = type("C", (), {"topic": "temat"})()

        async def send(self, msg):
            sent.append(msg)
            return True

    async def scenario():
        cfg = ScoutConfig(category="3661", catalogs="", price_floor=0, refresh_backoff=30, refresh_backoff_max=600,
                          proxy_auth_cooldown=600, proxy_alert_after=3)
        session = VintedSession()
        scout = Scout(cfg, session, EmailNotifier(SmtpConfig()))
        scout.evaluator = type("E", (), {"push": FakePush()})()
        lane = scout.lanes[MAIN_PROXY]
        waits = [scout._lane_failed(lane, "timeout") for _ in range(6)]
        alerts_after_series = len(sent)
        scout._lane_ok(lane)
        auth = [scout._lane_failed(lane, "407", auth=True) for _ in range(5)]
        await asyncio.sleep(0)
        await asyncio.gather(*scout._tasks)
        await session.close()
        return waits, alerts_after_series, auth, lane

    waits, alerts, auth, lane = asyncio.run(scenario())
    assert waits == [30, 60, 120, 240, 480, 600]
    assert auth == [600, 1200, 2400, 3600, 3600]
    assert [m["title"] for m in sent] == ["Sniper: proxy glowne nie działa", "Sniper: proxy glowne znów działa",
                                          "Sniper: proxy glowne nie działa"]
    assert "407" in sent[2]["message"] and lane.failures == 5


def test_stale_price_range_stops_fast_scans():
    """Nowy zakres cen z catalogs.json → przyspieszony wpis starego zakresu znika (log 01.11: tablety 300-3900
    skanowane co 3 s obok nowego 300-4100)."""
    async def scenario():
        cfg = ScoutConfig(category="3580", catalogs="3728:300-4100", price_floor=0, poll_interval=15, poll_min=3)
        session = VintedSession()
        scout = Scout(cfg, session, EmailNotifier(SmtpConfig()))
        lane = scout.lanes[MAIN_PROXY]
        new = scout.active_specs()[0]
        old = CatalogSpec(3728, "300", "3900")
        lane.fast = {(3728, "300", "3900"): {"spec": old, "period": 3, "normal": 15, "since": time.monotonic()}}
        scout._speed_up(lane, new)
        scout._relax(lane, time.monotonic())
        await session.close()
        return list(lane.fast)

    assert asyncio.run(scenario()) == [(3728, "300", "4100")]
