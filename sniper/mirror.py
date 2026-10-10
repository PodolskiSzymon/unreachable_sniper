"""Kopia logów i podglądu ocen do drugiego folderu - np. na Dysk Google (Google Drive dla komputerów).

Logi zostają w sniper/logs (tam program pisze na bieżąco - bez ryzyka, że klient Dysku zablokuje plik przy
rotacji logu o północy). Co SNIPER_MIRROR_INTERVAL_MIN minut zmienione pliki są kopiowane do SNIPER_MIRROR_DIR.
Przy zamknięciu programu - jeszcze raz.

Kopiujemy tylko pliki do czytania przez Ciebie. NIE kopiujemy session.json (ciastka sesji Vinted), profilu
przeglądarki ani my_headers.txt.
"""
import asyncio
import logging
import os
from pathlib import Path

log = logging.getLogger("sniper.mirror")

# Pliki z sniper/logs kopiowane do folderu w chmurze (brakujące są pomijane).
MIRROR_FILES = (
    "oceny.html",          # podgląd ocen AI (odświeża się sam co minutę)
    "sniper.log",          # dzisiejszy log
    "evaluations.csv",     # wszystkie oceny (Excel)
    "evaluations.jsonl",
    "offers.jsonl",        # złapane oferty
    "mails.csv",           # wysłane maile
    "session_events.csv",  # dziennik sesji konta
    "traffic.csv",         # transfer przez proxy
    "bought.jsonl",        # rejestr zakupów
)


class LogMirror:
    def __init__(self, log_dir, mirror_dir, interval_min=2.0, files=MIRROR_FILES):
        self.log_dir = Path(log_dir)
        self.mirror_dir = Path(mirror_dir)
        self.interval = max(float(interval_min), 0.25) * 60
        self.files = tuple(files)
        self._seen = {}             # nazwa -> (mtime_ns, rozmiar) ostatnio skopiowanej wersji

    def sync_once(self):
        """Kopiuje zmienione pliki. Zwraca listę skopiowanych nazw. Błędy tylko logujemy - program działa dalej."""
        copied = []
        try:
            self.mirror_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("[KOPIA] Nie mogę utworzyć %s: %s", self.mirror_dir, exc)
            return copied
        for name in self.files:
            src = self.log_dir / name
            try:
                st = src.stat()
            except OSError:
                continue
            stamp = (st.st_mtime_ns, st.st_size)
            if self._seen.get(name) == stamp:
                continue
            dst = self.mirror_dir / name
            tmp = dst.with_name(dst.name + ".tmp")
            try:
                tmp.write_bytes(src.read_bytes())
                os.replace(tmp, dst)          # podmiana na raz - Dysk Google nie wyśle pół pliku
                self._seen[name] = stamp
                copied.append(name)
            except OSError as exc:            # np. plik chwilowo zablokowany przez klienta Dysku
                log.debug("[KOPIA] %s pominięty tym razem: %s", name, exc)
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return copied

    async def run(self):
        log.info("[KOPIA] Logi i oceny.html kopiuję co %.0f min do: %s", self.interval / 60, self.mirror_dir)
        while True:
            await asyncio.to_thread(self.sync_once)
            await asyncio.sleep(self.interval)


def build_mirror(cfg):
    """LogMirror z konfiguracji albo None (SNIPER_MIRROR_DIR puste)."""
    if not getattr(cfg, "mirror_dir", "") or not cfg.log_dir:
        return None
    return LogMirror(cfg.log_dir, cfg.mirror_dir, cfg.mirror_interval_min)
