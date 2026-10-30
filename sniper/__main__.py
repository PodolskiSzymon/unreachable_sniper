"""Uruchomienie: python -m sniper  (z katalogu głównego repozytorium)."""
import asyncio
import logging
import logging.handlers
from pathlib import Path
from urllib.parse import urlsplit

from .config import MAIN_PROXY, ScoutConfig, extra_proxy_url, proxy_env_names, require_proxy_url
from .notifier import EmailNotifier
from .scout import Scout
from .session import VintedSession


def log_proxy(name, proxy_url):
    """Adres proxy w logu - bez loginu i hasła (tylko czy są)."""
    proxy = urlsplit(proxy_url or "")
    logging.getLogger("sniper").info(
        "Proxy %s: %s:%s | login: %s | hasło: %s", name, proxy.hostname, proxy.port,
        "TAK" if proxy.username else "BRAK", "TAK" if proxy.password else "BRAK")


def build_session(cfg, proxy_url, state_name="session.json"):
    """Sesja Vinted przez jedno proxy; ciastka i tokeny zapisywane osobno dla każdego proxy (state_name)."""
    return VintedSession(
        proxy_url=proxy_url,
        timeout=cfg.request_timeout,
        browser_wait_ms=cfg.browser_wait_ms,
        refresh_attempts=cfg.refresh_attempts,
        refresh_retry_delay=cfg.refresh_retry_delay,
        refresh_timeout=cfg.refresh_timeout,
        browser_light=cfg.browser_light,
        state_file=Path(cfg.log_dir) / state_name if cfg.log_dir else None,
    )


def build_extra_sessions(cfg):
    """Dodatkowe proxy (SNIPER_PROXY_<N>_KATEGORIE) → {nazwa: VintedSession}. Proxy bez adresu w .env - błąd w logu,
    a jego kategorie idą przez główne proxy (Sniper działa dalej)."""
    sessions = {}
    for name in sorted(set(dict(cfg.proxy_routes).values()) - {MAIN_PROXY}):
        url = extra_proxy_url(name)
        if not url:
            logging.getLogger("sniper").error(
                "[PROXY] Proxy %s ma kategorie, ale w sniper/.env brak %s - jego kategorie idą przez główne proxy.",
                name, proxy_env_names(name))
            continue
        log_proxy(name, url)
        sessions[name] = build_session(cfg, url, f"session_{name}.json")
    return sessions


def setup_logging(log_dir):
    """Konsola: INFO. Plik sniper/logs/sniper.log: DEBUG (m.in. pełny JSON złapanych ofert), nowy plik co północ."""
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    handlers = [console]
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        to_file = logging.handlers.TimedRotatingFileHandler(
            Path(log_dir) / "sniper.log", when="midnight", backupCount=30, encoding="utf-8")
        to_file.setLevel(logging.DEBUG)
        to_file.setFormatter(fmt)
        handlers.append(to_file)
    logging.basicConfig(level=logging.DEBUG, handlers=handlers, force=True)
    for noisy in ("httpx", "httpcore", "httpx2", "anthropic", "asyncio", "aiosmtplib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _quiet_connection_resets(loop, context):
    """Zerwane połączenie od proxy (np. WinError 10054 na Windowsie) to nie błąd programu.

    Pętla Proactor na Windowsie loguje je jako ERROR z tracebackiem przy zamykaniu gniazda,
    chociaż żądanie i tak jest ponawiane. Zapisujemy je tylko w pliku logu (DEBUG).
    """
    exc = context.get("exception")
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        logging.getLogger("sniper.net").debug("[NET] Zdalna strona zerwała połączenie: %r", exc)
        return
    loop.default_exception_handler(context)


def build_evaluator(cfg, notifier):
    """Moduł oceny AI albo None (wtedy mail o każdej ofercie, jak dotąd)."""
    log = logging.getLogger("sniper")
    if not cfg.ai.enabled:
        log.info("[AI] Ocena AI wyłączona (SNIPER_AI_ENABLED=false) - mail o każdej ofercie.")
        return None
    if not cfg.ai.api_key:
        log.warning("[AI] Brak klucza SNIPER_AI_API_KEY (GEMINI_API_KEY / ANTHROPIC_API_KEY) - ocena AI wyłączona, "
                    "mail o każdej ofercie.")
        return None
    try:
        from .evaluator import OfferEvaluator
    except ImportError as exc:
        log.error("[AI] Brak biblioteki AI (%s) - uruchom: pip install -r sniper/requirements.txt. "
                  "Ocena AI wyłączona, mail o każdej ofercie.", exc)
        return None
    try:
        evaluator = OfferEvaluator(cfg.ai, notifier, log_dir=cfg.log_dir)
    except (ImportError, ValueError) as exc:
        log.error("[AI] Nie mogę uruchomić oceny AI: %s - pip install -r sniper/requirements.txt / sprawdź "
                  "SNIPER_AI_PROVIDER. Mail o każdej ofercie.", exc)
        return None
    evaluator.check_guidelines()
    log.info("[AI] Ocena AI włączona: %s", evaluator.describe())
    return evaluator


async def build_buyer(cfg, notifier, evaluator):
    """AutoBuyer (zalogowana przeglądarka konta) albo None. Wymaga oceny AI - bez niej nie wiemy, co jest okazją."""
    log = logging.getLogger("sniper")
    if not cfg.buyer.enabled:
        log.info("[AUTO-BUY] Auto-zakup wyłączony (SNIPER_BUY_ENABLED=false) - okazje tylko mailem.")
        if cfg.ai.mail_only_purchases:
            log.warning("[MAIL] SNIPER_MAIL_ONLY_PURCHASES=true nie działa bez auto-zakupu - maile jak zwykle.")
        return None
    if evaluator is None:
        log.warning("[AUTO-BUY] SNIPER_BUY_ENABLED=true, ale ocena AI nie działa - auto-zakup WYŁĄCZONY.")
        return None
    from .autobuy import AutoBuyer
    buyer = AutoBuyer(cfg, notifier)
    if not await buyer.start():
        await buyer.shutdown()
        return None
    evaluator.buyer = buyer
    if cfg.ai.mail_only_purchases:
        log.info("[MAIL] SNIPER_MAIL_ONLY_PURCHASES=true - maile tylko o kupionych / próbowanych okazjach.")
    return buyer


async def main():
    asyncio.get_running_loop().set_exception_handler(_quiet_connection_resets)
    cfg = ScoutConfig()
    setup_logging(cfg.log_dir)
    logging.getLogger("sniper").info("Logi zapisuję do: %s", Path(cfg.log_dir).resolve())
    require_proxy_url()  # bez proxy Zwiadowca w ogóle nie startuje (SNIPER_REQUIRE_PROXY)
    log_proxy(MAIN_PROXY, cfg.proxy_url)
    session = build_session(cfg, cfg.proxy_url)
    extra = build_extra_sessions(cfg)
    notifier = EmailNotifier(cfg.smtp, log_dir=cfg.log_dir)
    evaluator = build_evaluator(cfg, notifier)
    from .push import build_push
    push = build_push(cfg)
    if evaluator is not None:
        evaluator.push = push
    elif push is not None:
        logging.getLogger("sniper").warning("[PUSH] Powiadomienia na telefon wymagają oceny AI (okazja = ocena AI) - "
                                            "bez niej nic nie zostanie wysłane.")
    buyer = await build_buyer(cfg, notifier, evaluator)
    scout = Scout(cfg, session, notifier, evaluator, buyer, sessions=extra)
    from .mirror import build_mirror
    mirror = build_mirror(cfg)
    mirror_task = asyncio.create_task(mirror.run(), name="log-mirror") if mirror else None
    from . import fx
    fx_task = asyncio.create_task(fx.run(cfg.fx_refresh_h), name="fx-rates") if cfg.fx_refresh_h > 0 else None
    try:
        await scout.run()
    finally:
        if mirror_task:
            mirror_task.cancel()
        if fx_task:
            fx_task.cancel()
        await scout.shutdown()
        if evaluator:
            await evaluator.drain()      # oceny w locie -> ich maile trafiają do notifier (albo do buyera)
            await evaluator.close()
        if buyer:
            await buyer.shutdown()       # dokończ zakup w toku, zamknij przeglądarkę konta
        await notifier.drain()
        if push:
            await push.drain()
        await session.close()
        for other in extra.values():
            await other.close()
        if mirror:
            mirror.sync_once()           # ostatnia kopia przy zamknięciu


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger("sniper").info("=== ZWIADOWCA ZATRZYMANY RĘCZNIE ===")
    except Exception:
        # Pełny traceback trafia też do pliku logu, nie tylko na konsolę.
        logging.getLogger("sniper").exception("=== ZWIADOWCA PRZERWANY BŁĘDEM ===")
        raise
