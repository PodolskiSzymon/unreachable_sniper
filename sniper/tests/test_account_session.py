"""Testy sesji konta (sniper/account_session.py) - części bez przeglądarki (konwersja ciastek, konfiguracja)."""
from pathlib import Path

import pytest

from sniper import account_session as acc
from sniper.config import AccountConfig, ScoutConfig


def test_cookie_header_to_playwright():
    cookies = acc.cookie_header_to_playwright("a=1; access_token_web=TOK.EN; b=2 ")
    assert cookies == [
        {"name": "a", "value": "1", "domain": ".vinted.pl", "path": "/"},
        {"name": "access_token_web", "value": "TOK.EN", "domain": ".vinted.pl", "path": "/"},
        {"name": "b", "value": "2", "domain": ".vinted.pl", "path": "/"},
    ]
    assert acc.cookie_header_to_playwright("") == []
    assert acc.cookie_header_to_playwright("smieci_bez_rowna") == []


def test_load_account_cookies(tmp_path):
    f = tmp_path / "my_headers.txt"
    f.write_text("curl 'https://www.vinted.pl/' -b 'anon_id=abc; access_token_web=T.O.K' "
                 "-H 'user-agent: Edg/154'", encoding="utf-8")
    cookies, ua = acc.load_account_cookies(f)
    names = {c["name"] for c in cookies}
    assert names == {"anon_id", "access_token_web"} and ua == "Edg/154"


def test_load_account_cookies_requires_cookie(tmp_path):
    f = tmp_path / "my_headers.txt"
    f.write_text("curl 'https://www.vinted.pl/' -H 'user-agent: Edg/154'", encoding="utf-8")
    with pytest.raises(ValueError, match="Brak ciastek"):
        acc.load_account_cookies(f)


def _cfg(tmp_path, **kw):
    """Konfiguracja z profilem w tmp_path (domyślny ./profiles/scraper zaśmiecałby katalog roboczy)."""
    kw.setdefault("profile_dir", str(tmp_path / "profiles" / "scraper"))
    return AccountConfig(**kw)


def test_account_paths_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRAPER_PROFILE_DIR", raising=False)
    cfg = AccountConfig()
    account = acc.VintedAccount(cfg, tmp_path)
    assert account.headers_file == tmp_path / "my_headers.txt"
    assert account.profile_dir == Path("profiles") / "scraper"          # ./profiles/scraper - osobny profil bota


def test_account_paths_from_config(tmp_path):
    cfg = AccountConfig(headers_file=str(tmp_path / "h.txt"), profile_dir=str(tmp_path / "prof"))
    account = acc.VintedAccount(cfg, tmp_path / "logs")
    assert account.headers_file == tmp_path / "h.txt" and account.profile_dir == tmp_path / "prof"


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
                               "no_viewport": True}
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


def test_seed_only_new_or_changed_headers(tmp_path):
    """Stary my_headers.txt nie może nadpisywać odświeżonych tokenów profilu przy każdym starcie."""
    import asyncio
    import os

    class FakeContext:
        def __init__(self):
            self.added = []

        async def add_cookies(self, cookies):
            self.added.append(cookies)

    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.profile_dir.mkdir(parents=True)
    account.context = FakeContext()
    account.headers_file.write_text("curl 'https://www.vinted.pl/' -b 'access_token_web=STARY; a=1'",
                                    encoding="utf-8")
    assert account.needs_seed() is True
    asyncio.run(account._seed_cookies())
    assert len(account.context.added) == 1                              # pierwszy raz: wgrane
    assert account.needs_seed() is False
    asyncio.run(account._seed_cookies())
    assert len(account.context.added) == 1                              # restart: NIE nadpisuje profilu
    stat = account.headers_file.stat()
    os.utime(account.headers_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))   # świeży cURL
    assert account.needs_seed() is True
    account.reset_profile()
    account.profile_dir.mkdir(parents=True)
    assert account.needs_seed() is True                                 # po --reset wgrywa od nowa


def test_start_reseeds_when_profile_not_logged_in(tmp_path, monkeypatch):
    """Profil bez ważnej sesji + pominięte ciastka -> start wgrywa je jeszcze raz i sprawdza ponownie."""
    import asyncio
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.profile_dir.mkdir(parents=True)
    account.headers_file.write_text("curl 'https://www.vinted.pl/' -b 'a=1'", encoding="utf-8")
    (account.profile_dir / account.SEED_MARKER).write_text(account._headers_stamp(), encoding="utf-8")
    seeds, checks = [], iter([False, True])

    class FakeCtx:
        pages = ["strona"]

        def set_default_navigation_timeout(self, ms):
            pass

        async def add_cookies(self, cookies):
            seeds.append(cookies)

    class FakeChromium:
        async def launch_persistent_context(self, **kw):
            return FakeCtx()

    class FakePW:
        chromium = FakeChromium()

        async def start(self):
            return self

    import patchright.async_api as pra
    monkeypatch.setattr(pra, "async_playwright", lambda: FakePW())

    async def fake_check():
        return next(checks)
    monkeypatch.setattr(account, "refresh_and_check", fake_check)
    asyncio.run(account.start())
    assert len(seeds) == 1 and account.logged_in is True     # pominięte przy starcie, wgrane po porażce
    account._lock.release()


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
    account.headers_file = tmp_path / "brak.txt"                      # needs_seed() = False
    assert asyncio.run(account.refresh_and_check()) is False


def test_banners_ok_with_token_and_no_login_button_is_logged_in(tmp_path):
    import asyncio
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.page = GuestPage(login_visible=False)
    account.context = _Ctx([{"name": "access_token_web", "value": "tok"}])
    account.headers_file = tmp_path / "brak.txt"
    assert asyncio.run(account.refresh_and_check()) is True


def test_own_login_never_uses_my_headers(tmp_path):
    """Po --login bot ma własną sesję: my_headers.txt (kopia z Edge) nie może jej nadpisać - ani przy starcie,
    ani przy awaryjnym ponownym wgraniu."""
    import asyncio
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.profile_dir.mkdir(parents=True)
    account.headers_file.write_text("curl 'https://www.vinted.pl/' -b 'access_token_web=KOPIA'", encoding="utf-8")
    assert account.needs_seed() is True
    (account.profile_dir / account.OWN_LOGIN_MARKER).write_text("szymooon_koala", encoding="utf-8")
    assert account.has_own_login() and account.needs_seed() is False

    class Ctx:
        added = []

        async def add_cookies(self, cookies):
            self.added.append(cookies)
    account.context = Ctx()
    assert asyncio.run(account._seed_cookies(force=True)) == ([], None) and Ctx.added == []


def test_no_seeding_while_logging_in(tmp_path):
    account = acc.VintedAccount(_cfg(tmp_path), tmp_path)
    account.profile_dir.mkdir(parents=True)
    account.headers_file.write_text("curl 'https://www.vinted.pl/' -b 'a=1'", encoding="utf-8")
    account._logging_in = True
    assert account.needs_seed() is False


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
