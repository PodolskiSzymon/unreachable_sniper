"""Osobny program: utrzymuje sesję TWOJEGO konta Vinted zalogowaną 24/7 (krok do auto-zakupu).

Działa z domowego IP, NIGDY przez proxy IPRoyal - sesja konta i ciastka cf_clearance/datadome są związane
z Twoim IP i przeglądarką. To osobny proces niż Zwiadowca (ten skanuje przez proxy).

Przeglądarka: Patchright (łatana wersja Playwrighta) + Google Chrome (channel="chrome"), widoczne okno, domyślna
konfiguracja - bez własnego user-agenta, nagłówków, skryptów i flag. Stały, OSOBNY profil bota (SCRAPER_PROFILE_DIR,
domyślnie ./profiles/scraper) - nie Twój główny profil Chrome/Edge. Jeden proces na profil (blokada sniper.lock).

Jak to działa:
  1. Logowanie TYLKO ręczne w oknie bota - bot NIE wczytuje ciastek z my_headers.txt. Gdy profil nie jest
     zalogowany, okno zostaje otwarte na stronie głównej Vinted: logujesz się w nim, a bot sam to wykrywa
     (sprawdza co kilka sekund BEZ przeładowania strony) i od razu korzysta z tej sesji. Profil ją pamięta.
     Alternatywa od zera: `python -m sniper.account_session --login` (czysty profil, Enter w konsoli).
  2. Trzyma otwartą przeglądarkę i co kilkanaście minut wchodzi na stronę. Własny JavaScript Vinted odświeża
     wtedy access_token (żyje ~1 h) refresh-tokenem (żyje ~7 dni) - dzięki temu sesja nie wygasa.
  3. Co pętlę sprawdza przez /api/v2/banners, czy wciąż jesteś zalogowany (czyta nazwę konta).
  4. `open_item(url)` otwiera ogłoszenie na Twoim zalogowanym koncie - fundament pod auto-zakup.
"""
import asyncio
import logging
import os
from pathlib import Path

from .account import detect_banners
from .config import AccountConfig, DelayConfig, KeepalivePacer, ScoutConfig

log = logging.getLogger("sniper.account")

HOME_URL = "https://www.vinted.pl/"
# W przeglądarce robimy fetch względny - leci jako same-origin z ciastkami konta (tak jak robi to strona).
BANNERS_PATH = "/api/v2/banners"


# JS wykonywany w kontekście strony: pobiera /api/v2/banners i zwraca {status, body}.
_BANNERS_FETCH = """
async (path) => {
  try {
    const r = await fetch(path, {headers: {accept: 'application/json'}, credentials: 'include'});
    return {status: r.status, body: await r.text()};
  } catch (e) { return {status: 0, body: String(e)}; }
}
"""


class ProfileInUseError(RuntimeError):
    """Profil bota jest już używany przez inny proces (drugi Zwiadowca / account_session / check_detection)."""


class ProfileLock:
    """Blokada „jeden proces na profil” - plik sniper.lock w folderze profilu, blokowany przez system.

    System zwalnia blokadę sam, gdy proces się zakończy (także po zabiciu / awarii), więc nic nie trzeba sprzątać.
    """
    FILE = "sniper.lock"

    def __init__(self, profile_dir):
        self.path = Path(profile_dir) / self.FILE
        self._file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise ProfileInUseError(
                f"Profil bota {self.path.parent.resolve()} jest JUŻ UŻYWANY przez inny program sniper "
                "(Zwiadowca z auto-zakupem, sniper.account_session, sniper.buyer albo check_detection). "
                "Zamknij tamten program (Ctrl+C) i jego okno przeglądarki, potem uruchom ponownie.") from None
        self._file = f
        return self

    def release(self):
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._file.close()
        self._file = None


async def launch_profile(profile_dir, nav_timeout=None, sandbox=True):
    """Patchright + Chrome na stałym profilu bota. Zwraca (patchright, context, page, lock).

    Domyślna konfiguracja Patchrighta (działa najlepiej bez dodatków): channel="chrome", headless=False,
    no_viewport=True, bez proxy, bez user-agenta / nagłówków / skryptów / dodatkowych flag.
    chromium_sandbox=True: Playwright/Patchright domyślnie dodaje --no-sandbox (pasek „nieobsługiwana flaga”
    w oknie, test u użytkownika 2026-10-09) - to WYŁĄCZA zabezpieczenie Chrome, więc je przywracamy.
    Zajęty profil -> ProfileInUseError z czytelnym komunikatem.
    """
    from patchright.async_api import async_playwright

    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    lock = ProfileLock(profile_dir).acquire()
    pw = ctx = None
    try:
        pw = await async_playwright().start()
        try:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir), channel="chrome", headless=False, no_viewport=True,
                chromium_sandbox=sandbox)
        except Exception as exc:
            text = str(exc)
            if "chrome" in text.lower() and ("not found" in text.lower() or "install" in text.lower()):
                raise RuntimeError("Nie znalazłem Google Chrome. Zainstaluj go poleceniem: patchright install chrome "
                                   f"(albo zwykły instalator Chrome). Szczegół: {text[:300]}") from None
            raise ProfileInUseError(
                f"Nie udało się otworzyć Chrome na profilu {profile_dir.resolve()}. Najczęściej profil jest otwarty "
                "w innym oknie Chrome (np. pozostałym po poprzednim uruchomieniu) - zamknij je i spróbuj ponownie. "
                f"Szczegół: {text[:300]}") from None
        if nav_timeout:
            ctx.set_default_navigation_timeout(nav_timeout * 1000)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        return pw, ctx, page, lock
    except BaseException:
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:
                pass
        if pw is not None:
            await pw.stop()
        lock.release()
        raise


def jwt_expiry(token):
    """Czas wygaśnięcia (epoch, s) z tokenu JWT (pole exp) albo None. Bez weryfikacji podpisu - tylko odczyt."""
    import base64
    import json
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return None


class SessionJournal:
    """Dziennik sesji konta -> logs/session_events.csv (otwórz w Excelu) + linie [SESJA] w logu.

    Zdarzenia: zalogowany (start / ponowne zalogowanie - z długością przerwy), wylogowany (z powodem, długością
    sesji, ważnością tokenu i czasem od ostatniego udanego sprawdzenia), sprawdzenie_ok / sprawdzenie_nieudane
    (każde podtrzymanie, z ważnością tokenu). Cel: ustalić, PO JAKIM CZASIE i DLACZEGO sesja pada.
    """
    FILE = "session_events.csv"
    FIELDS = ("czas", "zdarzenie", "konto", "powod", "sesja_trwala_min", "przerwa_min", "token_wazny_min",
              "ostatnie_ok_min_temu")

    def __init__(self, log_dir):
        self.path = Path(log_dir) / self.FILE if log_dir else None
        self.up_since = None            # od kiedy zalogowany
        self.down_since = None          # od kiedy wylogowany
        self.last_ok = None             # ostatnie udane sprawdzenie
        self.last_ok_token_min = None   # ważność tokenu przy ostatnim udanym sprawdzeniu

    @staticmethod
    def _now():
        from datetime import datetime
        return datetime.now()

    @staticmethod
    def _minutes(value):
        if value is None:
            return ""
        seconds = value.total_seconds() if hasattr(value, "total_seconds") else value
        return f"{seconds / 60:.1f}"

    def _write(self, event, **fields):
        import csv
        row = {"czas": self._now().strftime("%Y-%m-%d %H:%M:%S"), "zdarzenie": event}
        row.update({k: ("" if v is None else v) for k, v in fields.items()})
        log.info("[SESJA] %s | %s", event, ", ".join(f"{k}={v}" for k, v in row.items()
                                                      if k not in ("czas", "zdarzenie") and v != ""))
        if self.path is None:
            return
        try:
            new = not self.path.exists()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=self.FIELDS, delimiter=";", extrasaction="ignore")
                if new:
                    w.writeheader()
                w.writerow(row)
        except OSError as exc:
            log.warning("[SESJA] Nie zapisałem %s: %s", self.path, exc)

    def checked_ok(self, konto, token_left=None):
        self.last_ok = self._now()
        self.last_ok_token_min = None if token_left is None else token_left / 60
        self._write("sprawdzenie_ok", konto=konto, token_wazny_min=self._minutes(token_left))

    def check_failed(self, attempt, total, reason, token_left=None):
        self._write("sprawdzenie_nieudane", powod=f"próba {attempt}/{total}: {reason}",
                    token_wazny_min=self._minutes(token_left))

    def up(self, konto, how):
        """Zalogowany (start albo wykryte ponowne zalogowanie). Zwraca opis do maila."""
        now = self._now()
        gap = now - self.down_since if self.down_since else None
        self.up_since, self.down_since, self.last_ok = now, None, now
        self._write("zalogowany", konto=konto, powod=how, przerwa_min=self._minutes(gap))
        text = f"Zalogowanie wykryte: {now:%Y-%m-%d %H:%M:%S} ({how})."
        if gap is not None:
            text += f" Przerwa (bez sesji) trwała {gap.total_seconds() / 60:.1f} min."
        return text

    def down(self, reason, token_left=None):
        """Wylogowany / brak sesji. Zwraca opis do maila."""
        now = self._now()
        lasted = now - self.up_since if self.up_since else None
        since_ok = now - self.last_ok if self.last_ok else None
        self.down_since, self.up_since = now, None
        self._write("wylogowany", powod=reason, sesja_trwala_min=self._minutes(lasted),
                    token_wazny_min=self._minutes(token_left), ostatnie_ok_min_temu=self._minutes(since_ok))
        text = f"Brak zalogowania wykryty: {now:%Y-%m-%d %H:%M:%S}. Powód: {reason or '?'}."
        if lasted is not None:
            text += (f" Sesja trwała {lasted.total_seconds() / 60:.1f} min "
                     f"(zalogowany od {now - lasted:%H:%M:%S}).")
        if since_ok is not None:
            text += f" Ostatnie udane sprawdzenie {since_ok.total_seconds() / 60:.1f} min wcześniej"
            if self.last_ok_token_min is not None:
                text += f" (token był wtedy ważny jeszcze {self.last_ok_token_min:.0f} min)"
            text += "."
        return text + f" Szczegóły: sniper/logs/{self.FILE}."


LOGIN_HELP = (
    "1. W oknie Chrome bota kliknij „Zaloguj się” i wybierz logowanie E-MAILEM i HASŁEM Vinted.\n"
    "   NIE „Kontynuuj z Google/Facebook/Apple” - Google blokuje logowanie w przeglądarce sterowanej\n"
    "   przez program. Nie masz hasła do Vinted? „Nie pamiętasz hasła?” -> ustaw je linkiem z maila.\n"
    "   Login i hasło wpisujesz SAM - program niczego nie wpisuje.\n")


class VintedAccount:
    """Trwała sesja przeglądarki zalogowanej na Twoje konto (bez proxy)."""

    def __init__(self, cfg: AccountConfig, log_dir):
        self.cfg = cfg
        self.profile_dir = Path(cfg.profile_dir or "./profiles/scraper")
        self.log_dir = Path(log_dir)                # zrzuty ekranu przy błędach (nie do profilu z ciastkami)
        self.delays = getattr(cfg, "delays", None) or DelayConfig()
        self._pw = None
        self._lock = None
        self.context = None
        self.page = None
        self.username = None
        self.logged_in = False
        self._last_token = None                     # do wykrycia końca logowania (zmiana access_token_web)
        self.last_reason = ""                       # dlaczego ostatnie sprawdzenie sesji się nie udało
        self.journal = SessionJournal(self.log_dir)  # logs/session_events.csv - kiedy i dlaczego sesja pada
        self._last_diag = float("-inf")             # ostatnia linia diagnostyczna przy czekaniu na logowanie

    async def start(self):
        """Uruchamia przeglądarkę i sprawdza sesję. Niezalogowany -> okno ZOSTAJE na stronie głównej do logowania."""
        await self._launch()
        self.logged_in = await self.refresh_and_check()
        if not self.logged_in:
            await self.focus()
            log.warning("[KONTO] NIE jesteś zalogowany. ZALOGUJ SIĘ w otwartym oknie Chrome bota (strona główna "
                        "Vinted, e-mail + hasło) - bot sam wykryje logowanie i z niego skorzysta.")
        return self

    async def _launch(self):
        # Trwały profil => sesja przeżywa restart programu. BEZ proxy - domowe IP.
        self._pw, self.context, self.page, self._lock = await launch_profile(
            self.profile_dir, self.cfg.nav_timeout, getattr(self.cfg, "chrome_sandbox", True))
        log.info("[KONTO] Chrome (Patchright) uruchomiony - profil: %s, bez proxy.", self.profile_dir.resolve())

    async def _pause(self, bounds):
        await asyncio.sleep(DelayConfig.pick(bounds))

    async def wait_for_login(self, timeout=None):
        """Czeka, aż zalogujesz się RĘCZNIE w otwartym oknie. Sprawdza co kilka s BEZ przeładowania strony.

        Zwraca True po wykryciu logowania, False po upływie timeout (s; None = bez limitu).
        """
        import time as _t
        deadline = None if timeout is None else _t.monotonic() + timeout
        while deadline is None or _t.monotonic() < deadline:
            await self._pause(self.delays.login_check_s)
            try:
                if await self.refresh_and_check(navigate=False):
                    self.logged_in = True
                    log.warning("[KONTO] Wykryłem logowanie w oknie bota (%s) - korzystam z tej sesji.",
                                self.username or "konto")
                    return True
            except Exception as exc:                    # np. strona w trakcie przeładowania po zalogowaniu
                log.debug("[KONTO] Sprawdzenie logowania nieudane (%s) - ponowię.", exc)
        return False

    async def interactive_login(self, wait_for_user=None, attempts=5):
        """--login: czysty profil, Ty logujesz się w spokoju, potem Enter w konsoli.

        Program NIC nie robi w oknie, dopóki nie naciśniesz Enter. Zwraca True, gdy logowanie potwierdzone.
        """
        if wait_for_user is None:
            async def wait_for_user():
                return await asyncio.get_running_loop().run_in_executor(None, input)
        await self._launch()
        await self.page.goto(self.cfg.login_url or HOME_URL, wait_until="domcontentloaded")
        await self._dismiss_consent()
        print("\n=== LOGOWANIE BOTA ===\n" + LOGIN_HELP +
              "2. Gdy zobaczysz swoje konto (awatar w prawym górnym rogu), wróć tutaj i naciśnij ENTER.\n"
              "   (q + Enter = przerwij)")
        for attempt in range(1, attempts + 1):
            answer = (await wait_for_user() or "").strip().lower()
            if answer == "q":
                print("Przerwano.")
                return False
            print("Sprawdzam logowanie...")
            if await self.refresh_and_check():
                self.logged_in = True
                log.warning("[KONTO] Zalogowano w oknie bota jako %s - profil ma teraz WŁASNĄ sesję.",
                            self.username or "?")
                return True
            print(f"Nie widzę zalogowania (próba {attempt}/{attempts}). Dokończ logowanie w oknie "
                  "(strona mogła się przeładować - zaloguj się jeszcze raz) i naciśnij ENTER. q = przerwij.")
        log.error("[KONTO] Nie potwierdziłem zalogowania po %d próbach.", attempts)
        return False

    async def refresh_and_check(self, navigate=True):
        """Czy jesteś zalogowany? Zwraca bool.

        navigate=True: wchodzi na stronę główną (JS Vinted odświeża wtedy token) - podtrzymanie sesji.
        navigate=False: tylko patrzy na obecną stronę, bez przeładowania - gdy czekamy, aż zalogujesz się ręcznie
        (przeładowanie w trakcie wpisywania hasła przerwałoby logowanie).
        """
        quiet = not navigate
        if not navigate:
            return await self._passive_check()
        if navigate:
            await self._ensure_single_tab()
            await self.page.goto(HOME_URL, wait_until="domcontentloaded")
            if await self._stuck_on_session_refresh():
                self.last_reason = "strona utknęła na /session-refresh"
                log.warning("[KONTO] Pętla 'session-refresh' - sesja w profilu jest nieważna. Zaloguj się "
                            "ponownie w oknie bota (albo: zatrzymaj program i python -m sniper.account_session "
                            "--login, czyści profil).")
                self.username = None
                return False
        result = await self.page.evaluate(_BANNERS_FETCH, BANNERS_PATH)
        status, body = result.get("status"), result.get("body") or ""
        if status == 401:
            self.last_reason = "401 z /api/v2/banners"
            if not quiet:
                log.warning("[KONTO] 401 - sesja wygasła. Zaloguj się ponownie w oknie bota.")
            self.username = None
            return False
        _, name = detect_banners(body)
        if name:
            self.username = name
            log.info("[KONTO] Zalogowany jako: %s", name)
            return True
        # /api/v2/banners odpowiada 200/code:0 także NIEZALOGOWANEMU gościowi - samo to nie dowodzi sesji.
        # Dodatkowo: brak widocznego „Zaloguj się” na stronie i ciastko konta access_token_web.
        if status != 200 or '"code":0' not in body:
            self.last_reason = f"banners: status {status} (0 = zapytanie przerwane, np. przekierowanie)"
            if not quiet:
                log.info("[KONTO] Sesja niepewna (banner bez nazwy, status %s) - traktuję jako niezalogowany.",
                         status)
            self.username = None
            return False
        login_button = await self._login_button_visible()
        has_token = await self._has_account_token()
        if login_button or has_token is False:
            self.last_reason = ("na stronie widać „Zaloguj się”" if login_button else "brak ciastka access_token_web")
            if not quiet:
                log.warning("[KONTO] NIE jesteś zalogowany (%s). Zaloguj się w oknie bota (strona główna Vinted).",
                            "na stronie jest „Zaloguj się”" if login_button else "brak ciastka access_token_web")
            self.username = None
            return False
        log.info("[KONTO] Sesja aktywna (banner bez nazwy, ale bez „Zaloguj się” i z tokenem konta).")
        return True

    async def _account_token(self):
        if self.context is None:
            return None
        try:
            cookies = await self.context.cookies("https://www.vinted.pl")
        except Exception:
            return None
        return next((c.get("value") for c in cookies if c.get("name") == "access_token_web"), None)

    async def token_expires_in(self):
        """Ile sekund zostało do wygaśnięcia tokenu dostępu (access_token_web, JWT exp). None = nie wiadomo."""
        import time as _t
        exp = jwt_expiry(await self._account_token() or "")
        return None if exp is None else exp - _t.time()

    async def next_keepalive_delay(self, pacer):
        """Odstęp do następnego podtrzymania: losowy (pacer), ale ZAWSZE przed wygaśnięciem tokenu dostępu.

        Wzorzec „refresh ahead of expiry” z aplikacji OAuth: odświeżamy ~8-12 min przed exp, a nie w losowej chwili
        - wtedy JS Vinted wymienia token refresh-tokenem, zanim stary przestanie działać.
        """
        delay = pacer.next_seconds()
        left = await self.token_expires_in()
        if left is not None:
            ahead = DelayConfig.pick(self.delays.refresh_ahead_min) * 60
            delay = min(delay, max(60.0, left - ahead))
        return delay

    async def verify_session(self):
        """Podtrzymanie z ponowieniami: sesja „padła” dopiero po N nieudanych sprawdzeniach Z RZĘDU.

        Pojedyncza porażka (strona w trakcie odświeżania tokenu, przerwane zapytanie, chwilowy błąd sieci) to nie
        wylogowanie - wcześniej jedna taka porażka od razu wstrzymywała auto-zakup i wysyłała mail.
        """
        attempts = max(1, int(self.delays.session_fail_checks))
        for i in range(1, attempts + 1):
            try:
                ok = await self.refresh_and_check()
                reason = self.last_reason
            except Exception as exc:
                ok, reason = False, f"błąd: {str(exc)[:200]}"
            left = await self._safe_token_left()
            if ok:
                if i > 1:
                    log.info("[KONTO] Sesja potwierdzona w próbie %d/%d - poprzednia porażka była chwilowa.", i, attempts)
                if left is not None:
                    log.info("[KONTO] Token konta ważny jeszcze ~%.0f min.", left / 60)
                self.journal.checked_ok(self.username or "konto", left)
                return True
            log.warning("[KONTO] Sprawdzenie sesji nieudane (%d/%d): %s", i, attempts, reason or "?")
            self.journal.check_failed(i, attempts, reason or "?", left)
            if i < attempts:
                await self._pause(self.delays.session_retry_s)
        return False

    async def _safe_token_left(self):
        try:
            return await self.token_expires_in()
        except Exception:
            return None

    async def _ensure_single_tab(self):
        """Jedna karta w oknie bota. Każda karta Vinted odświeża token sama, a Vinted rotuje refresh token -
        dwa odświeżenia tym samym refresh tokenem (dwie karty naraz) to w OAuth sygnał kradzieży i unieważnienie sesji.
        """
        ctx = self.context
        pages = list(getattr(ctx, "pages", None) or [])
        if not pages and ctx is None:
            return
        closed = getattr(self.page, "is_closed", None)
        if self.page is None or (closed and closed()):
            self.page = pages[0] if pages else await ctx.new_page()
        extra = [p for p in pages if p is not self.page]
        for p in extra:
            try:
                await p.close()
            except Exception:
                pass
        if extra:
            log.info("[KONTO] Zamknąłem %d dodatkowych kart w oknie bota (jedna karta = jedno odświeżanie tokenu).",
                     len(extra))

    async def _passive_check(self):
        """Sprawdzenie BEZ przeładowania strony (czekamy, aż zalogujesz się ręcznie).

        1. /api/v2/banners z obecnej strony - nazwa konta = zalogowany.
        2. Brak widocznego „Zaloguj się” + ciastko access_token_web = zalogowany.
        3. Ciastko access_token_web ZMIENIŁO się od poprzedniego sprawdzenia = logowanie się zakończyło (Vinted
           wystawia nowy token) -> teraz bezpiecznie jedno sprawdzenie z przeładowaniem strony. Pomaga, gdy strona
           po zalogowaniu nie przerysowała nagłówka albo logowałeś się w innej karcie okna bota.
        Co ~60 s linia w logu z powodem, dlaczego jeszcze nie widzę logowania.
        """
        import time as _t
        token = await self._account_token()
        token_changed = self._last_token is not None and token and token != self._last_token
        self._last_token = token
        if token_changed:
            log.info("[KONTO] Nowy token konta w przeglądarce (koniec logowania?) - sprawdzam z przeładowaniem strony.")
            return await self.refresh_and_check(navigate=True)

        url = self.page.url or ""
        status, name, login_button = None, None, None
        if "vinted." in url and not self.is_session_refresh(url):
            result = await self.page.evaluate(_BANNERS_FETCH, BANNERS_PATH)
            status, body = result.get("status"), result.get("body") or ""
            _, name = detect_banners(body)
            if name:
                self.username = name
                log.info("[KONTO] Zalogowany jako: %s", name)
                return True
            if status == 200 and '"code":0' in body:
                login_button = await self._login_button_visible()
                if not login_button and token:
                    log.info("[KONTO] Sesja aktywna (banner bez nazwy, bez „Zaloguj się”, z tokenem konta).")
                    return True
        self.username = None
        now = _t.monotonic()
        if now - self._last_diag >= 60:
            self._last_diag = now
            log.info("[KONTO] Czekam na logowanie w oknie bota - jeszcze nie widzę sesji: URL %s | banners %s | "
                     "„Zaloguj się” na stronie: %s | token konta: %s | kart w oknie bota: %d",
                     url[:80] or "-", status if status is not None else "nie sprawdzono (strona spoza Vinted)",
                     {True: "TAK", False: "nie", None: "?"}[login_button], "jest" if token else "BRAK",
                     len(getattr(self.context, "pages", []) or []))
        return False

    async def _login_button_visible(self):
        """True = na stronie widać „Zaloguj się” (gość). None = nie da się sprawdzić."""
        import re as _re
        try:
            try:
                await self.page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            button = self.page.get_by_text(_re.compile(r"Zaloguj się", _re.I))
            count = await button.count()
            for i in range(min(count, 5)):
                if await button.nth(i).is_visible():
                    return True
            return False
        except Exception:
            return None

    async def _has_account_token(self):
        """Czy w przeglądarce jest ciastko konta access_token_web? None = nie da się sprawdzić."""
        if self.context is None:
            return None
        try:
            cookies = await self.context.cookies("https://www.vinted.pl")
        except Exception:
            return None
        return any(c.get("name") == "access_token_web" and c.get("value") for c in cookies)

    @staticmethod
    def is_session_refresh(url):
        return "session-refresh" in (url or "")

    async def _stuck_on_session_refresh(self):
        if not self.is_session_refresh(self.page.url):
            return False
        try:
            # /session-refresh to właśnie odświeżanie tokenu przez Vinted - dajemy mu czas (było 15 s).
            await self.page.wait_for_url(lambda u: not self.is_session_refresh(u), timeout=45000)
            return False
        except Exception:
            return self.is_session_refresh(self.page.url)

    async def open_item(self, url):
        """Otwiera ogłoszenie na zalogowanym koncie."""
        log.info("[KONTO] Otwieram ofertę: %s", url)
        await self.page.goto(url, wait_until="domcontentloaded")
        await self._dismiss_consent()
        return self.page.url

    async def _dismiss_consent(self):
        """Zamyka baner zgody na ciastka."""
        for selector in ("#onetrust-accept-btn-handler", "#didomi-notice-agree-button",
                          'button:has-text("Akceptuj")', 'button:has-text("Zgadzam")'):
            try:
                button = self.page.locator(selector)
                if await button.count() and await button.first.is_visible():
                    await button.first.click(timeout=3000)
                    log.info("[KONTO] Zamknąłem baner ciastek (%s).", selector)
                    await self._pause(self.delays.click_s)
                    return
            except Exception:
                pass

    async def open(self, url):
        return await self.open_item(url)

    async def focus(self):
        try:
            await self.page.bring_to_front()
        except Exception:
            pass

    @staticmethod
    def _is_checkout_url(url):
        return "/api/v2/purchases/" in (url or "") and "/checkout" in (url or "")

    async def buy_now_and_get_checkout(self):
        """Klika 'Kup teraz', czeka na stronę płatności i zwraca JSON z /checkout ('Zapłać' = finalize_purchase)."""
        import time as _t
        captured = []

        def on_response(response):
            if self._is_checkout_url(response.url):
                captured.append(response)

        self.context.on("response", on_response)
        pages_before = set(self.context.pages)

        def reacted():
            return bool(captured) or "/checkout" in (self.page.url or "") \
                or any(p not in pages_before for p in self.context.pages)

        try:
            try:
                await self.page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            for attempt in range(1, 4):
                found = await self._click_buy_now()
                log.info("[KONTO] Klik 'Kup teraz' (próba %d, dopasowań: %d) - czekam na reakcję...", attempt, found)
                for _ in range(16):
                    if reacted():
                        break
                    await asyncio.sleep(0.5)
                if reacted():
                    break
                log.warning("[KONTO] Klik bez reakcji (strona mogła się jeszcze ładować) - ponawiam.")
                await self._pause(self.delays.retry_s)

            new_pages = [p for p in self.context.pages if p not in pages_before]
            target = new_pages[0] if new_pages else self.page
            if new_pages:
                log.info("[KONTO] Zakup otworzył się w NOWEJ karcie: %s", target.url)
                self.page = target

            try:
                await target.wait_for_url(lambda u: "/checkout" in (u or ""), timeout=self.cfg.nav_timeout * 1000)
                log.info("[KONTO] Jestem na ekranie płatności: %s", target.url)
            except Exception:
                await self._dump_failure(target)

            deadline = _t.monotonic() + 20
            while not captured and _t.monotonic() < deadline:
                await asyncio.sleep(0.5)
            if not captured:
                raise RuntimeError(f"nie złapałem odpowiedzi /checkout (URL strony: {target.url})")

            response = captured[-1]
            log.info("[KONTO] Mam dane checkout: HTTP %s", response.status)
            return await response.json()
        finally:
            self.context.remove_listener("response", on_response)

    PAY_SELECTORS = (
        ('[data-testid="single-checkout-order-summary-purchase-button"]', "css"),
        ("zapłać", "role"),
    )

    async def _find_pay_button(self, page):
        import re as _re
        for selector, kind in self.PAY_SELECTORS:
            button = (page.get_by_role("button", name=_re.compile(selector, _re.I))
                      if kind == "role" else page.locator(selector))
            if await button.count():
                return button.first, selector
        return None, None

    async def _wait_for_pay_button(self, page, timeout):
        """Czeka, aż checkout się załaduje: przycisk 'Zapłać' widoczny, aktywny i strona po hydracji."""
        import time as _t
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=timeout * 1000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await self._dismiss_consent()

        deadline = _t.monotonic() + timeout
        while _t.monotonic() < deadline:
            button, selector = await self._find_pay_button(page)
            if button is not None:
                try:
                    if await button.is_visible() and await button.is_enabled():
                        log.info("[AUTO-ZAKUP] Przycisk 'Zapłać' gotowy (selektor: %s).", selector)
                        # Chwila na podpięcie obsługi kliknięcia przez React (hydracja) - jak przy 'Kup teraz'.
                        try:
                            await page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            pass
                        await self._pause(self.delays.click_s)
                        return button
                except Exception:
                    pass
            await asyncio.sleep(0.5)
        raise RuntimeError(f"przycisk 'Zapłać' nie pojawił się / jest nieaktywny po {timeout:.0f} s "
                           f"(URL: {page.url})")

    # Komunikaty walidacji formularza checkout (np. czerwone „Wybierz punkt odbioru” pod sekcją wysyłki).
    _PROBLEM_SELECTORS = ('[role="alert"], [class*="Text__warning"], [class*="Text__error"], '
                          '[class*="Text__danger"], [class*="Validation"], [class*="validation"]')
    # Zapytania, które naprawdę oznaczają start płatności. Reszta POST-ów (analityka, zdarzenia) się nie liczy.
    _PAYMENT_HINTS = ("purchase", "transaction", "payment", "checkout", "pay")
    _TRACKING_HINTS = ("event", "track", "analytic", "metric", "log", "public", "collect", "telemetry")

    async def _checkout_problems(self, page):
        """Widoczne komunikaty błędów formularza checkout (lista tekstów)."""
        try:
            texts = await page.eval_on_selector_all(
                self._PROBLEM_SELECTORS,
                "els => els.filter(e => e.offsetParent !== null)"
                "          .map(e => (e.innerText || '').trim()).filter(Boolean)")
        except Exception:
            texts = []
        return list(dict.fromkeys(texts))

    def _pickup_heading(self, page):
        import re as _re
        return page.locator("h2", has_text=_re.compile(r"^\s*Wybierz punkt odbioru\s*$", _re.I))

    async def _pickup_missing(self, page):
        heading = self._pickup_heading(page)
        try:
            return bool(await heading.count()) and await heading.first.is_visible()
        except Exception:
            return False

    async def _ensure_pickup_point(self, page):
        """Wysyłka do punktu bez wybranego punktu: klik „Wybierz punkt odbioru” -> „Potwierdź” (z ponawianiem).

        Bez tego Vinted po kliku „Zapłać” tylko podświetla błąd „Wybierz punkt odbioru”.
        """
        import re as _re
        import time as _t
        if not await self._pickup_missing(page):
            log.info("[AUTO-ZAKUP] Punkt odbioru wybrany (albo niepotrzebny) - pomijam wybór.")
            return False
        confirm = page.get_by_role("button", name=_re.compile(r"^\s*Potwierdź\s*$", _re.I))
        for attempt in range(1, 4):
            log.info("[AUTO-ZAKUP] Klikam 'Wybierz punkt odbioru' (próba %d)...", attempt)
            await self._pickup_heading(page).first.click(timeout=10000)

            # Okno z mapą/listą punktów doładowuje dane - czekamy na aktywny „Potwierdź”.
            # Okno ma się pokazać w ~8 s (inaczej klik był ślepy); potem do 20 s na aktywny przycisk.
            ready = False
            started = _t.monotonic()
            while _t.monotonic() - started < 20:
                try:
                    shown = bool(await confirm.count()) and await confirm.first.is_visible()
                    if shown and await confirm.first.is_enabled():
                        ready = True
                        break
                except Exception:
                    shown = False
                if not shown and _t.monotonic() - started > 8:
                    break
                await asyncio.sleep(0.5)
            if not ready:
                visible = False
                try:
                    visible = bool(await confirm.count()) and await confirm.first.is_visible()
                except Exception:
                    pass
                if visible:
                    shot = await self._screenshot(page, "pickup_error.png")
                    raise RuntimeError("przycisk 'Potwierdź' w wyborze punktu odbioru jest nieaktywny - "
                                       "prawdopodobnie trzeba najpierw zaznaczyć punkt na liście "
                                       f"(wklej outerHTML punktu z F12){shot}")
                log.warning("[AUTO-ZAKUP] Okno wyboru punktu się nie otworzyło (strona mogła się ładować) - ponawiam.")
                await self._pause(self.delays.retry_s)
                continue

            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            await self._pause(self.delays.click_s)
            log.info("[AUTO-ZAKUP] Klikam 'Potwierdź' (punkt odbioru)...")
            await confirm.first.click(timeout=10000)

            deadline = _t.monotonic() + 10
            while _t.monotonic() < deadline:
                if not await self._pickup_missing(page):
                    log.info("[AUTO-ZAKUP] Punkt odbioru wybrany.")
                    # Checkout przelicza się po zmianie dostawy - poczekaj, zanim klikniemy „Zapłać”.
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    await self._pause(self.delays.click_s)
                    return True
                await asyncio.sleep(0.5)
            log.warning("[AUTO-ZAKUP] Po 'Potwierdź' punkt nadal niewybrany - ponawiam.")
        shot = await self._screenshot(page, "pickup_error.png")
        raise RuntimeError(f"nie udało się wybrać punktu odbioru po 3 próbach{shot}")

    async def _screenshot(self, page, name):
        shot = self.log_dir / name
        try:
            await page.screenshot(path=str(shot), full_page=True)
            return f" (zrzut ekranu: {shot})"
        except Exception:
            return ""

    @classmethod
    def _is_payment_request(cls, url):
        path = url.split("?")[0].lower()
        return any(h in path for h in cls._PAYMENT_HINTS) and not any(h in path for h in cls._TRACKING_HINTS)

    async def _pay_reacted(self, page, url_before, pages_before, frames_before, requests):
        """Czy płatność ruszyła po kliku 'Zapłać'? Zwraca opis albo None."""
        if requests:
            return "zapytanie " + requests[-1]
        if page.url != url_before:
            return f"zmiana URL -> {page.url}"
        if any(p not in pages_before for p in self.context.pages):
            return "nowa karta"
        if len(page.frames) > frames_before:
            return "nowa ramka (captcha / 3-D Secure)"
        button, _ = await self._find_pay_button(page)
        if button is None:
            return "przycisk 'Zapłać' zniknął"
        try:
            if not await button.is_enabled() or await button.get_attribute("aria-busy") == "true":
                return "przycisk 'Zapłać' w trakcie przetwarzania"
        except Exception:
            pass
        try:
            if await page.locator('[role="dialog"]:visible').count():
                return "okno dialogowe (captcha / potwierdzenie)"
        except Exception:
            pass
        return None

    async def finalize_purchase(self, page=None):
        """Klika 'Zapłać' na ekranie checkout. Zwraca opis reakcji strony albo rzuca RuntimeError.

        Wzorzec jak przy 'Kup teraz': czeka na pełne załadowanie (networkidle + aktywny przycisk), w razie
        potrzeby wybiera punkt odbioru, klika, przez ~10 s sprawdza reakcję i ponawia klik (max 3 razy).
        Gdy Vinted pokaże błąd formularza, NIE uznaje zakupu - rzuca błąd z treścią komunikatu.
        Przeglądarki NIE zamyka - captchę / potwierdzenie banku dokańczasz w otwartym oknie.
        """
        import time as _t
        page = page or self.page
        try:
            await page.wait_for_url(lambda u: "/checkout" in (u or ""), timeout=self.cfg.nav_timeout * 1000)
        except Exception:
            pass
        log.info("[AUTO-ZAKUP] Czekam na załadowanie ekranu płatności: %s", page.url)
        try:
            await page.bring_to_front()
        except Exception:
            pass
        await self._wait_for_pay_button(page, self.cfg.nav_timeout)
        await self._ensure_pickup_point(page)

        requests, other_posts = [], []

        def on_request(request):
            if request.method == "GET" or "vinted" not in request.url:
                return
            line = f"{request.method} {request.url.split('?')[0]}"
            (requests if self._is_payment_request(request.url) else other_posts).append(line)

        async def check_problems(before):
            new = [t for t in await self._checkout_problems(page) if t not in before]
            if new or await self._pickup_missing(page):
                shot = await self._screenshot(page, "checkout_error.png")
                msg = " | ".join(new) or "Wybierz punkt odbioru"
                raise RuntimeError(f"Vinted nie przyjął płatności - komunikat na stronie: „{msg[:200]}”{shot}")

        self.context.on("request", on_request)
        try:
            for attempt in range(1, 4):
                url_before = page.url
                pages_before = set(self.context.pages)
                frames_before = len(page.frames)
                problems_before = await self._checkout_problems(page)
                button, selector = await self._find_pay_button(page)
                if button is None:
                    reaction = await self._pay_reacted(page, url_before, pages_before, frames_before, requests)
                    if reaction:
                        return reaction
                    raise RuntimeError("nie znalazłem przycisku 'Zapłać' na ekranie checkout")
                log.info("[AUTO-ZAKUP] Klikam 'Zapłać' (próba %d, selektor: %s)...", attempt, selector)
                await button.click(timeout=10000)

                deadline = _t.monotonic() + 10
                while _t.monotonic() < deadline:
                    await asyncio.sleep(0.5)
                    await check_problems(problems_before)
                    reaction = await self._pay_reacted(page, url_before, pages_before, frames_before, requests)
                    if reaction:
                        # Walidacja bywa chwilę po kliku - sprawdź jeszcze raz, zanim uznamy płatność.
                        await asyncio.sleep(2.0)
                        await check_problems(problems_before)
                        log.info("[AUTO-ZAKUP] Płatność ruszyła po 'Zapłać': %s", reaction)
                        return reaction
                if other_posts:
                    log.info("[AUTO-ZAKUP] Inne zapytania po kliku (nie liczę jako płatność): %s",
                             ", ".join(dict.fromkeys(other_posts)))
                    other_posts.clear()
                if attempt < 3:
                    log.warning("[AUTO-ZAKUP] Klik 'Zapłać' bez reakcji (strona mogła się jeszcze ładować) - ponawiam.")
                    await self._pause(self.delays.retry_s)
        finally:
            self.context.remove_listener("request", on_request)

        shot = await self._screenshot(page, "checkout_error.png")
        raise RuntimeError(f"klik 'Zapłać' 3 razy bez reakcji strony{shot}")

    async def _dump_failure(self, page):
        """Diagnostyka, gdy 'Kup teraz' nie przeszło do checkoutu."""
        note = await self._page_notice()
        shot = self.log_dir / "buy_debug.png"
        try:
            await page.screenshot(path=str(shot), full_page=True)
        except Exception:
            shot = None
        try:
            modal = await page.eval_on_selector_all(
                '[role="dialog"], [class*="odal"], [class*="rawer"], [class*="heet"]',
                "els => els.map(e => (e.innerText || '').trim()).filter(Boolean)")
        except Exception:
            modal = []
        log.warning("[KONTO] 'Kup teraz' nie przeszło do checkoutu. URL: %s | kart otwartych: %d%s%s%s",
                    page.url, len(self.context.pages),
                    f" | toast: „{note}”" if note else "",
                    f" | modal: „{modal[0][:200]}”" if modal else "",
                    f" | zrzut ekranu: {shot}" if shot else "")

    BUY_NOW_SELECTORS = (
        ('[data-testid="item-buy-button"]', "css"),
        (".details-list--actions button.web_ui__Button__primary", "css"),
        ("kup teraz", "role"),
    )

    async def _page_notice(self):
        try:
            notices = await self.page.eval_on_selector_all(
                '[role="alert"], [class*="otification"], [class*="oast"]',
                "els => els.map(e => (e.innerText || '').trim()).filter(Boolean)")
            return " | ".join(dict.fromkeys(notices))[:300]
        except Exception:
            return ""

    async def _click_buy_now(self):
        import re as _re
        for selector, kind in self.BUY_NOW_SELECTORS:
            button = (self.page.get_by_role("button", name=_re.compile(selector, _re.I))
                      if kind == "role" else self.page.locator(selector))
            found = await button.count()
            if found:
                log.info("[KONTO] Przycisk 'Kup teraz' znaleziony selektorem: %s", selector)
                await button.first.click(timeout=self.cfg.nav_timeout * 1000)
                return found
        raise RuntimeError("nie znalazłem przycisku 'Kup teraz' na stronie oferty")

    async def run_forever(self):
        pacer = KeepalivePacer(self.delays)
        low, high = self.delays.keepalive_min
        log.info("[KONTO] Podtrzymuję sesję co %.0f-%.0f min (losowo, czasem dłuższa pauza). Ctrl+C kończy.",
                 low, high)
        while True:
            await asyncio.sleep(await self.next_keepalive_delay(pacer))
            try:
                self.logged_in = await self.verify_session()
                if not self.logged_in:
                    self.journal.down(self.last_reason, await self._safe_token_left())
                    await self.focus()
                    log.warning("[KONTO] Sesja padła - zaloguj się ponownie w oknie bota, czekam.")
                    if await self.wait_for_login():
                        self.journal.up(self.username or "konto", "wykryto ponowne zalogowanie")
            except Exception:
                log.exception("[KONTO] Błąd podczas podtrzymania sesji - próbuję dalej.")

    def reset_profile(self):
        """Czyści profil bota. Najpierw sprawdza blokadę - nie skasuje profilu, na którym działa inny proces."""
        import shutil
        if self.profile_dir.exists():
            ProfileLock(self.profile_dir).acquire().release()
            shutil.rmtree(self.profile_dir, ignore_errors=True)
            log.info("[KONTO] Wyczyściłem profil %s - trzeba się zalogować od nowa.", self.profile_dir)

    async def close(self):
        try:
            if self.context:
                await self.context.close()
            if self._pw:
                await self._pw.stop()
        finally:
            self.context = self._pw = None
            if self._lock:
                self._lock.release()
                self._lock = None


async def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Utrzymuje sesję konta Vinted 24/7 (bez proxy).")
    parser.add_argument("--reset", action="store_true",
                        help="wyczyść profil przeglądarki przed startem")
    parser.add_argument("--login", action="store_true",
                        help="czysty profil + RĘCZNE logowanie w oknie bota (własna sesja, zalecane)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        return await _run(args)
    except ProfileInUseError as exc:
        print(f"\nBŁĄD: {exc}")
        return 2


async def _run(args):
    cfg = ScoutConfig()
    if args.login:
        account = VintedAccount(cfg.account, cfg.log_dir)
        account.reset_profile()
        try:
            ok = await account.interactive_login()
        finally:
            await account.close()
        if ok:
            print("\nGotowe - bot ma własne logowanie. Uruchom teraz: python -m sniper")
            print("Nie wylogowuj się „ze wszystkich urządzeń” w Vinted - to zakończyłoby też sesję bota.")
        return 0 if ok else 1
    if not cfg.account.enabled:
        print("Sesja konta wyłączona. Ustaw SNIPER_ACCOUNT_ENABLED=true w sniper/.env")
        return 1
    account = VintedAccount(cfg.account, cfg.log_dir)
    if args.reset:
        account.reset_profile()
    try:
        await account.start()
        if account.logged_in:
            account.journal.up(account.username or "konto", "start programu")
        else:
            account.journal.down("start programu: profil niezalogowany - " + (account.last_reason or "?"))
            print("\n=== ZALOGUJ SIĘ W OKNIE BOTA ===\n" + LOGIN_HELP +
                  "2. Nic tu nie naciskaj - bot sam wykryje logowanie i zacznie podtrzymywać sesję.")
            if await account.wait_for_login():
                account.journal.up(account.username or "konto", "wykryto zalogowanie w oknie bota")
        await account.run_forever()
    except KeyboardInterrupt:
        log.info("[KONTO] Zatrzymano ręcznie.")
    finally:
        await account.close()
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(asyncio.run(main(sys.argv[1:])))