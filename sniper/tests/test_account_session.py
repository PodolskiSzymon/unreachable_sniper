"""Testy sesji konta (sniper/account_session.py) - części bez przeglądarki (konwersja ciastek, konfiguracja)."""
from pathlib import Path

import pytest

from sniper import account_session as acc
from sniper.config import AccountConfig, ScoutConfig


def _cfg(tmp_path, **kw):
    """Konfiguracja z profilem w tmp_path (domyślny ./profiles/scraper zaśmiecałby katalog roboczy)."""
    kw.setdefault("profile_dir", str(tmp_path / "profiles" / "scraper"))
    return AccountConfig(**kw)


def test_account_paths_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRAPER_PROFILE_DIR", raising=False)
    cfg = AccountConfig()
    account = acc.VintedAccount(cfg, tmp_path)
    assert not hasattr(account, "headers_file")                       # my_headers.txt nie jest już używany
    assert account.profile_dir == Path("profiles") / "scraper"          # ./profiles/scraper - osobny profil bota


def test_account_paths_from_config(tmp_path):
    cfg = AccountConfig(profile_dir=str(tmp_path / "prof"))
    account = acc.VintedAccount(cfg, tmp_path / "logs")
    assert account.profile_dir == tmp_path / "prof" and account.log_dir == tmp_path / "logs"


def test_account_disabled_by_default():
    assert ScoutConfig().account.enabled is False


class FakePage:
    def __init__(self, banners_result):
        self._banners = banners_result
        self.goto_urls = []

    async def goto(self, url, wait_until=None):
        self.goto_urls.append(url)
        return None

    async def evaluate(self, js, arg=None):
        return self._banners

    @property
    def url(self):
        return self.goto_urls[-1] if self.goto_urls else ""


def _account_with_page(tmp_path, banners_result):
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.page = FakePage(banners_result)
    return account


def test_refresh_and_check_reads_username(tmp_path):
    import asyncio
    body = '{"banners":{"x":{"extra":{"invite_url":"https://www.vinted.pl/invite/koala_test/tok"}}},"code":0}'
    account = _account_with_page(tmp_path, {"status": 200, "body": body})
    assert asyncio.run(account.refresh_and_check()) is True
    assert account.username == "koala_test" and acc.HOME_URL in account.page.goto_urls


def test_refresh_and_check_detects_expired(tmp_path):
    import asyncio
    account = _account_with_page(tmp_path, {"status": 401, "body": '{"code":100}'})
    assert asyncio.run(account.refresh_and_check()) is False and account.username is None


def test_refresh_and_check_logged_in_without_banner(tmp_path):
    import asyncio
    account = _account_with_page(tmp_path, {"status": 200, "body": '{"banners":{},"code":0}'})
    assert asyncio.run(account.refresh_and_check()) is True      # code:0 = sesja aktywna, choć bez nazwy


def test_open_item_navigates_without_buying(tmp_path):
    import asyncio
    account = _account_with_page(tmp_path, {"status": 200, "body": "{}"})
    url = "https://www.vinted.pl/items/123-laptop"
    assert asyncio.run(account.open_item(url)) == url and account.page.goto_urls == [url]


def test_is_session_refresh_detects_loop():
    assert acc.VintedAccount.is_session_refresh("https://www.vinted.pl/session-refresh?ref_url=%2F") is True
    assert acc.VintedAccount.is_session_refresh("https://www.vinted.pl/") is False
    assert acc.VintedAccount.is_session_refresh("") is False


def test_reset_profile_removes_dir(tmp_path):
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.profile_dir.mkdir(parents=True)
    (account.profile_dir / "Cookies").write_text("stare", encoding="utf-8")
    account.reset_profile()
    assert not account.profile_dir.exists()


def test_stuck_on_session_refresh_when_not_refresh(tmp_path):
    import asyncio
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.page = FakePage({"status": 200, "body": "{}"})
    account.page.goto_urls.append("https://www.vinted.pl/")      # nie jest to session-refresh
    assert asyncio.run(account._stuck_on_session_refresh()) is False


def test_profile_lock_blocks_second_instance(tmp_path):
    """Drugi proces na tym samym profilu -> czytelny ProfileInUseError (nie stack trace Chrome)."""
    import subprocess
    import sys
    profile = tmp_path / "profiles" / "scraper"
    lock = acc.ProfileLock(profile).acquire()
    try:
        code = ("import sys; from sniper.account_session import ProfileLock, ProfileInUseError\n"
                "try:\n    ProfileLock(sys.argv[1]).acquire()\nexcept ProfileInUseError as e:\n"
                "    print(e); sys.exit(3)\n")
        other = subprocess.run([sys.executable, "-c", code, str(profile)], capture_output=True, text=True,
                               cwd=Path(__file__).parents[2])
        assert other.returncode == 3 and "JUŻ UŻYWANY" in other.stdout
    finally:
        lock.release()
    acc.ProfileLock(profile).acquire().release()            # po zwolnieniu - znów wolny


def test_reset_profile_refuses_locked_profile(tmp_path):
    import subprocess
    import sys
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.profile_dir.mkdir(parents=True)
    (account.profile_dir / "Cookies").write_text("sesja", encoding="utf-8")
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys,time; from sniper.account_session import ProfileLock; "
         "lock = ProfileLock(sys.argv[1]).acquire(); print('ok', flush=True); time.sleep(30)", str(account.profile_dir)],
        stdout=subprocess.PIPE, text=True, cwd=Path(__file__).parents[2])
    try:
        assert holder.stdout.readline().strip() == "ok"
        with pytest.raises(acc.ProfileInUseError):
            account.reset_profile()
        assert (account.profile_dir / "Cookies").exists()     # profil innego procesu NIE skasowany
    finally:
        holder.kill()
        holder.wait()


def test_launch_profile_uses_patchright_defaults(tmp_path, monkeypatch):
    """channel=chrome, headless=False, no_viewport=True i NIC więcej (bez UA, nagłówków, flag, proxy)."""
    import asyncio
    calls = {}

    class Ctx:
        pages = []

        def set_default_navigation_timeout(self, ms):
            calls["timeout"] = ms

        async def new_page(self):
            return "nowa-strona"

    class Chromium:
        async def launch_persistent_context(self, **kw):
            calls["kw"] = kw
            return Ctx()

    class PW:
        chromium = Chromium()

        async def start(self):
            return self

        async def stop(self):
            calls["stopped"] = True

    import patchright.async_api as pra
    monkeypatch.setattr(pra, "async_playwright", lambda: PW())
    profile = tmp_path / "profiles" / "scraper"
    pw, ctx, page, lock = asyncio.run(acc.launch_profile(profile, 45))
    try:
        assert calls["kw"] == {"user_data_dir": str(profile), "channel": "chrome", "headless": False,
                               "no_viewport": True, "chromium_sandbox": True}      # bez --no-sandbox
        assert page == "nowa-strona" and profile.is_dir() and calls["timeout"] == 45000
    finally:
        lock.release()


def test_launch_profile_releases_lock_on_failure(tmp_path, monkeypatch):
    import asyncio

    class Chromium:
        async def launch_persistent_context(self, **kw):
            raise Exception("Target page, context or browser has been closed")

    class PW:
        chromium = Chromium()

        async def start(self):
            return self

        async def stop(self):
            pass

    import patchright.async_api as pra
    monkeypatch.setattr(pra, "async_playwright", lambda: PW())
    profile = tmp_path / "p"
    with pytest.raises(acc.ProfileInUseError, match="Nie udało się otworzyć Chrome"):
        asyncio.run(acc.launch_profile(profile))
    acc.ProfileLock(profile).acquire().release()            # blokada zwolniona mimo błędu


def test_no_antidetect_tweaks_in_account_code():
    """Patchright działa najlepiej na domyślnej konfiguracji - żadnych łatek w kodzie konta."""
    for name in ("account_session.py", "check_detection.py"):
        src = (Path(acc.__file__).with_name(name)).read_text(encoding="utf-8")
        for banned in ("add_init_script", "user_agent=", "extra_http_headers", "AutomationControlled",
                       "playwright.async_api", "args=["):
            assert banned not in src.replace("patchright.async_api", ""), (name, banned)


def test_is_checkout_url():
    assert acc.VintedAccount._is_checkout_url("https://www.vinted.pl/api/v2/purchases/abc/checkout") is True
    assert acc.VintedAccount._is_checkout_url("https://www.vinted.pl/api/v2/purchases/abc/checkout?x=1") is True
    assert acc.VintedAccount._is_checkout_url("https://www.vinted.pl/api/v2/items/123") is False
    assert acc.VintedAccount._is_checkout_url("") is False


class _Loc:
    def __init__(self, visible):
        self._visible = visible

    async def count(self):
        return 1 if self._visible is not None else 0

    def nth(self, i):
        return self

    async def is_visible(self):
        return bool(self._visible)


class GuestPage(FakePage):
    """Strona jak dla gościa: banners 200/code:0 bez nazwy, ale w nagłówku „Zaloguj się”."""
    def __init__(self, login_visible):
        super().__init__({"status": 200, "body": '{"banners":{},"code":0}'})
        self._login_visible = login_visible

    async def wait_for_load_state(self, state, timeout=None):
        return None

    def get_by_text(self, pattern):
        return _Loc(self._login_visible)


class _Ctx:
    def __init__(self, cookies):
        self._cookies = cookies

    async def cookies(self, url=None):
        return self._cookies


def test_banners_ok_but_login_button_means_logged_out(tmp_path):
    import asyncio
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.page = GuestPage(login_visible=True)
    assert asyncio.run(account.refresh_and_check()) is False        # fałszywe „Sesja aktywna” z logu użytkownika


def test_banners_ok_without_account_token_means_logged_out(tmp_path):
    import asyncio
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.page = GuestPage(login_visible=None)
    account.context = _Ctx([{"name": "anon_id", "value": "x"}])
    assert asyncio.run(account.refresh_and_check()) is False


def test_banners_ok_with_token_and_no_login_button_is_logged_in(tmp_path):
    import asyncio
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.page = GuestPage(login_visible=False)
    account.context = _Ctx([{"name": "access_token_web", "value": "tok"}])
    assert asyncio.run(account.refresh_and_check()) is True


def test_delay_ranges_from_env(monkeypatch):
    from sniper.config import _env_range
    monkeypatch.setenv("X_RANGE", "12-4")
    assert _env_range("X_RANGE", (1, 2)) == (4.0, 12.0)                # odwrócony zakres poprawiony
    monkeypatch.setenv("X_RANGE", "0,8-2,5")
    assert _env_range("X_RANGE", (1, 2)) == (0.8, 2.5)                 # polski przecinek
    monkeypatch.setenv("X_RANGE", "bzdura")
    assert _env_range("X_RANGE", (1, 2)) == (1, 2)


def test_keepalive_pacer_random_with_long_pause():
    from sniper.config import DelayConfig, KeepalivePacer
    d = DelayConfig(keepalive_min=(15.0, 25.0), long_pause_every=(12.0, 18.0), long_pause_min=(40.0, 60.0))
    pacer = KeepalivePacer(d)
    minutes = [pacer.next_seconds() / 60 for _ in range(200)]
    normal = [m for m in minutes if m <= 25]
    long_ = [m for m in minutes if m >= 40]
    assert len(normal) + len(long_) == 200 and all(15 <= m <= 25 for m in normal)
    assert 200 // 18 <= len(long_) <= 200 // 12 + 1                    # dłuższa pauza co kilkanaście wejść
    assert len({round(m, 3) for m in normal}) > 50                      # naprawdę losowe, nie stałe


def test_no_my_headers_in_account_session():
    """Logowanie wyłącznie ręczne w oknie bota - account_session nie czyta my_headers.txt ani nie wgrywa ciastek."""
    src = Path(acc.__file__).read_text(encoding="utf-8")
    assert "add_cookies" not in src and "read_headers" not in src and "headers_file" not in src


def test_passive_check_does_not_reload_page(tmp_path):
    """Czekanie na ręczne logowanie: bez goto (przeładowanie przerwałoby wpisywanie hasła)."""
    import asyncio
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.page = GuestPage(login_visible=True)
    account.page.goto_urls.append("https://www.vinted.pl/member/login")
    assert asyncio.run(account.refresh_and_check(navigate=False)) is False
    assert account.page.goto_urls == ["https://www.vinted.pl/member/login"]      # strona NIE przeładowana

    account.page = GuestPage(login_visible=False)
    account.page.goto_urls.append("https://www.vinted.pl/")
    account.context = _Ctx([{"name": "access_token_web", "value": "tok"}])
    assert asyncio.run(account.refresh_and_check(navigate=False)) is True
    assert account.page.goto_urls == ["https://www.vinted.pl/"]


def test_start_not_logged_in_keeps_window_and_wait_for_login_detects(tmp_path, monkeypatch):
    import asyncio
    from sniper.config import DelayConfig
    cfg = _cfg(tmp_path, delays=DelayConfig(login_check_s=(0.0, 0.0)))
    account = acc.VintedAccount(cfg, tmp_path)
    calls, states = [], iter([False, False, False, True])

    async def fake_launch():
        calls.append("launch")

    async def fake_check(navigate=True):
        calls.append(navigate)
        return next(states)

    async def fake_focus():
        calls.append("focus")
    monkeypatch.setattr(account, "_launch", fake_launch)
    monkeypatch.setattr(account, "refresh_and_check", fake_check)
    monkeypatch.setattr(account, "focus", fake_focus)

    asyncio.run(account.start())
    assert account.logged_in is False and calls == ["launch", True, "focus"]   # okno zostaje, na wierzch
    assert asyncio.run(account.wait_for_login(timeout=5)) is True and account.logged_in is True
    assert calls[3:] == [False, False, False]                                  # czekanie bez przeładowań


def test_wait_for_login_timeout(tmp_path, monkeypatch):
    import asyncio
    from sniper.config import DelayConfig
    account = acc.VintedAccount(_cfg(tmp_path, delays=DelayConfig(login_check_s=(0.01, 0.01))), tmp_path)

    async def never(navigate=True):
        return False
    monkeypatch.setattr(account, "refresh_and_check", never)
    assert asyncio.run(account.wait_for_login(timeout=0.1)) is False


def test_passive_check_name_wins_over_stale_login_button(tmp_path):
    """Strona nieprzerysowana po zalogowaniu („Zaloguj się” wciąż widać), ale banners zna konto -> zalogowany."""
    import asyncio
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    body = '{"banners":{"x":{"extra":{"invite_url":"https://www.vinted.pl/invite/koala_test/tok"}}},"code":0}'
    account.page = GuestPage(login_visible=True)
    account.page._banners = {"status": 200, "body": body}
    account.page.goto_urls.append("https://www.vinted.pl/")
    assert asyncio.run(account.refresh_and_check(navigate=False)) is True and account.username == "koala_test"


def test_passive_check_token_change_triggers_one_reload(tmp_path):
    """Nowy access_token_web = logowanie zakończone -> jedno sprawdzenie z przeładowaniem (strona mogła nie
    przerysować nagłówka albo logowanie było w innej karcie)."""
    import asyncio
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.page = GuestPage(login_visible=True)
    account.page.goto_urls.append("https://www.vinted.pl/")
    account.context = _Ctx([{"name": "access_token_web", "value": "GOSC"}])
    assert asyncio.run(account.refresh_and_check(navigate=False)) is False      # gość - linia bazowa tokenu
    assert account.page.goto_urls == ["https://www.vinted.pl/"]
    account.context = _Ctx([{"name": "access_token_web", "value": "KONTO"}])   # po zalogowaniu
    asyncio.run(account.refresh_and_check(navigate=False))
    assert account.page.goto_urls[-1] == acc.HOME_URL and len(account.page.goto_urls) == 2


def test_passive_check_logs_reason_once_a_minute(tmp_path, caplog):
    import asyncio
    import logging
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.page = GuestPage(login_visible=True)
    account.page.goto_urls.append("https://www.vinted.pl/")
    with caplog.at_level(logging.INFO, logger="sniper.account"):
        asyncio.run(account.refresh_and_check(navigate=False))
        asyncio.run(account.refresh_and_check(navigate=False))
    lines = [r.getMessage() for r in caplog.records if "Czekam na logowanie" in r.getMessage()]
    assert len(lines) == 1 and "„Zaloguj się” na stronie: TAK" in lines[0] and "token konta: BRAK" in lines[0]


def _jwt(exp):
    import base64
    import json
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'HS256'})}.{enc({'exp': exp, 'iss': 'vinted-iam-oauth'})}.podpis"


def test_jwt_expiry():
    assert acc.jwt_expiry(_jwt(1760000000)) == 1760000000
    assert acc.jwt_expiry("smieci") is None and acc.jwt_expiry("") is None


def test_keepalive_refreshes_ahead_of_token_expiry(tmp_path):
    """Token wygasa za 20 min -> następne podtrzymanie ~8-12 min wcześniej, nie po losowych 15-25 min."""
    import asyncio
    import time
    from sniper.config import DelayConfig, KeepalivePacer
    d = DelayConfig(keepalive_min=(15.0, 25.0), long_pause_every=(100.0, 100.0), refresh_ahead_min=(10.0, 10.0))
    account = acc.VintedAccount(_cfg(tmp_path, delays=d), tmp_path)
    account.context = _Ctx([{"name": "access_token_web", "value": _jwt(time.time() + 20 * 60)}])
    delay = asyncio.run(account.next_keepalive_delay(KeepalivePacer(d)))
    assert 9 * 60 <= delay <= 10 * 60 + 5                               # 20 min - 10 min zapasu
    account.context = _Ctx([{"name": "access_token_web", "value": _jwt(time.time() + 60)}])
    assert asyncio.run(account.next_keepalive_delay(KeepalivePacer(d))) == 60.0   # minimum 1 min
    account.context = _Ctx([])                                          # bez tokenu - zwykły losowy odstęp
    assert 15 * 60 <= asyncio.run(account.next_keepalive_delay(KeepalivePacer(d))) <= 25 * 60


def test_verify_session_needs_several_failures_in_a_row(tmp_path, monkeypatch):
    """Jedno potknięcie (np. strona w trakcie odświeżania tokenu) to NIE wylogowanie."""
    import asyncio
    from sniper.config import DelayConfig
    d = DelayConfig(session_fail_checks=3, session_retry_s=(0.0, 0.0))
    account = acc.VintedAccount(_cfg(tmp_path, delays=d), tmp_path)
    results = iter([False, RuntimeError("Execution context was destroyed"), True])
    calls = []

    async def check(navigate=True):
        calls.append(navigate)
        r = next(results)
        if isinstance(r, Exception):
            raise r
        account.last_reason = "banners: status 0"
        return r
    monkeypatch.setattr(account, "refresh_and_check", check)
    assert asyncio.run(account.verify_session()) is True and len(calls) == 3

    calls.clear()
    results = iter([False, False, False])
    assert asyncio.run(account.verify_session()) is False and len(calls) == 3


def test_single_tab_before_refresh(tmp_path):
    import asyncio

    class P:
        def __init__(self):
            self.closed = False

        def is_closed(self):
            return self.closed

        async def close(self):
            self.closed = True

    class C:
        def __init__(self, pages):
            self._pages = pages

        @property
        def pages(self):
            return [p for p in self._pages if not p.closed]

    main, extra1, extra2 = P(), P(), P()
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.context, account.page = C([main, extra1, extra2]), main
    asyncio.run(account._ensure_single_tab())
    assert account.context.pages == [main] and account.page is main
    main.closed = True                                                  # użytkownik zamknął kartę bota
    other = P()
    account.context = C([other])
    asyncio.run(account._ensure_single_tab())
    assert account.page is other
