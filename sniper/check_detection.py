"""Ręczna kontrola wykrywalności przeglądarki bota - ta sama konfiguracja co sesja konta (Patchright + Chrome,
stały profil SCRAPER_PROFILE_DIR, bez proxy, domyślne ustawienia).

Otwiera strony testów wykrywania botów, czeka na ich załadowanie i zostawia okno otwarte do Twojej oceny.
Niczego nie zmienia na koncie Vinted (nie wchodzi na Vinted). Enter w konsoli zamyka przeglądarkę.

    python -m sniper.check_detection

Nie uruchamiaj w tym czasie Zwiadowcy z auto-zakupem ani sniper.account_session - ten sam profil (blokada).
"""
import asyncio
import logging
import sys

from .account_session import ProfileInUseError, launch_profile
from .config import ScoutConfig

log = logging.getLogger("sniper.check_detection")

TEST_PAGES = (
    "https://www.browserscan.net/bot-detection",
    "https://bot.sannysoft.com",
)


async def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    cfg = ScoutConfig().account
    try:
        pw, ctx, page, lock = await launch_profile(cfg.profile_dir, cfg.nav_timeout)
    except ProfileInUseError as exc:
        print(f"\nBŁĄD: {exc}")
        return 2
    try:
        for i, url in enumerate(TEST_PAGES):
            tab = page if i == 0 else await ctx.new_page()
            log.info("Otwieram %s ...", url)
            try:
                await tab.goto(url, wait_until="load")
                try:
                    # Testy liczą się w JS już po „load” - chwila na dokończenie (bez błędu, gdy strona nie ucichnie).
                    await tab.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass
                log.info("Załadowane: %s", url)
            except Exception as exc:
                log.warning("Nie załadowałem %s: %s", url, exc)
        print("\nSprawdź wyniki w obu kartach okna Chrome. Enter tutaj zamyka przeglądarkę.")
        await asyncio.get_running_loop().run_in_executor(None, input)
    finally:
        try:
            await ctx.close()
        finally:
            await pw.stop()
            lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
