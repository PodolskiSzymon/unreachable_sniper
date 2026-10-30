"""Porównywalne oferty z bazy analizatora (`<SNIPER_GUIDES_DIR>/comparables.db`, budowany przez `python -m analyzer
sniper-guide` i automat co 2 dni) - 2026-10-26, pomysł użytkownika: zamiast zgadywania ceny przez AI sprawdzić
medianę podobnych ofert po słowach z tytułu (jak przy laptopach po karcie graficznej).

Dopasowanie (bez sieci, kilka ms na ofertę, tylko do odczytu):
  * liczby z tytułu (13, 128, 4060, a17 → „17”) muszą wystąpić; gdy za mało wyników - zostaje tylko pierwsza liczba,
  * słowa wariantu (pro, max, ultra, lite, slim…) muszą się zgadzać: „iPhone 13” nie łapie „iPhone 13 Pro”,
  * pozostałe znaczące słowa (rzadkie w bazie - „telefon”, „stan”, „sprzedam” odpadają same) - co najmniej 60%,
  * ta sama kategoria; wynik tylko przy ≥ `min_offers` ofertach.
Szacunek sprzedaży jak w analizatorze: wyższa z (mediana zniknięć przy ≥ 10, środek p25–mediana aktywnych).
"""
import logging
import math
import re
import sqlite3
import unicodedata
from pathlib import Path

from .guides import VARIANT_WORDS, tokens

log = logging.getLogger("sniper.comparables")

_ALL_VARIANTS = set().union(*VARIANT_WORDS.values()) | {"pro", "max", "plus", "ultra", "mini", "air", "slim",
                                                         "digital", "oled", "lite", "fe", "xl"}
# Słowo w > 35% ofert = nic nie mówi. Było 20%: „iphone” (25% całej bazy, 27.10) wypadał i „iPhone 7” szukał
# każdego telefonu z „7” w tytule (mediana 834 zł zamiast ~340). Słowa ogólne odcina GENERIC.
MAX_DF_SHARE = 0.35
FAMILY_DF_SHARE = 0.5              # słowo rodziny w trybie „mediany” może być częste
GENERIC = {"sprzedam", "sprzedaje", "nowy", "nowa", "nowe", "stan", "okazja", "telefon", "smartfon", "smartphone",
           "konsola", "laptop", "notebook", "tablet", "karta", "sprawny", "sprawna", "uzywany", "używany", "pamiec",
           "pamięć", "dysk", "zestaw", "komplet", "super", "idealny", "bdb", "jak", "the", "and", "with", "do", "na",
           "z", "w", "i", "bez", "gratis", "gb",
           # „sprzedam / na sprzedaż / telefon / konsola” w językach sprzedawców (tytuły RO/HU/SK/CZ/LT/SE/DE/HR, 27.10)
           "vând", "vand", "vinde", "vanzare", "telefonul", "consola", "tableta", "eladó", "elado", "telefon",
           "konzol", "konzola", "predám", "predam", "prodám", "prodam", "parduodu", "säljer", "saljer", "verkaufe",
           "verkauf", "prodajem", "sell", "selling", "sale", "for", "en", "de", "si", "és", "es", "a", "na", "un",
           "o", "cu", "mit", "und", "nou", "noi", "folii"}
WORD_SHARE = 0.6                   # ile znaczących słów musi mieć podobna oferta


def _plain(word):
    """Jak tokenizer FTS5 (remove_diacritics): „łódź” → „lodz” - do słownika częstości."""
    word = word.replace("ł", "l")
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))


_GENERIC_PLAIN = {_plain(w) for w in GENERIC}


def _generic(word):
    return word in GENERIC or _plain(word) in _GENERIC_PLAIN


def _q(word):
    return '"' + word.replace('"', "") + '"'


def _pct(values, q):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


class Comparables:
    def __init__(self, path, min_offers=8):
        self.path = Path(path) if path else None
        self.min_offers = min_offers
        self._conn = None
        self._mtime = None
        self._rows = 0

    def _db(self):
        if self.path is None or not self.path.exists():
            return None
        mtime = self.path.stat().st_mtime
        if self._conn is None or mtime != self._mtime:
            if self._conn is not None:
                self._conn.close()
            self._conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
            row = self._conn.execute("SELECT value FROM meta WHERE key = 'rows'").fetchone()
            self._rows, self._mtime = int(row[0]) if row else 0, mtime
            log.info("[AI] Porównywalne oferty: %s (%d ofert)", self.path, self._rows)
        return self._conn

    def lookup(self, category, title):
        """→ słownik ze statystykami podobnych ofert albo None (brak pliku / za mało podobnych)."""
        if not category or category == "skip":
            return None
        try:
            db = self._db()
            if db is None or not self._rows:
                return None
            return self._lookup(db, category, title)
        except sqlite3.Error as exc:
            log.warning("[AI] Porównywalne oferty niedostępne: %s", exc)
            return None

    def _df(self, db, word):
        row = db.execute("SELECT doc FROM vocab WHERE term = ?", (_plain(word),)).fetchone()
        return row[0] if row else 0

    def _lookup(self, db, category, title):
        words = list(dict.fromkeys(tokens(title)))
        numbers = [w for w in words if w.isdigit()]
        variants = [w for w in words if w in _ALL_VARIANTS]
        limit = MAX_DF_SHARE * self._rows
        informative = [w for w in words if not w.isdigit() and w not in _ALL_VARIANTS and len(w) >= 2
                       and not _generic(w) and 0 < self._df(db, w) <= limit]
        if not numbers and not informative:
            return None
        absent = sorted(_ALL_VARIANTS - set(variants))
        for required in ([*numbers], numbers[:1]) if len(numbers) > 1 else ([*numbers],):
            found = self._query(db, category, required, variants, informative, absent)
            if found:
                return found
        return None

    def levels(self, category, title):
        """Mediany od najbardziej szczegółowej do ogólnej (eksperyment „mediany”, sniper/experiment.py):
        wszystkie liczby + warianty + słowa → pierwsza liczba → bez liczb → sama rodzina (najczęstsze znaczące słowo,
        np. „ipad”). → lista statystyk jak lookup() + "label"; pusta, gdy brak pliku / danych."""
        if not category or category == "skip":
            return []
        try:
            db = self._db()
            if db is None or not self._rows:
                return []
            words = list(dict.fromkeys(tokens(title)))
            numbers = [w for w in words if w.isdigit()]
            variants = [w for w in words if w in _ALL_VARIANTS]
            limit = MAX_DF_SHARE * self._rows
            df = {w: self._df(db, w) for w in words if not w.isdigit() and w not in _ALL_VARIANTS and len(w) >= 2
                  and not _generic(w)}
            informative = [w for w in df if 0 < df[w] <= limit]
            absent = sorted(_ALL_VARIANTS - set(variants))
            plans = [("model + liczby + wariant", numbers, variants, informative, absent),
                     ("model + 1. liczba + wariant", numbers[:1], variants, informative, absent),
                     ("model + wariant, bez liczb", [], variants, informative, absent)]
            # Rodzina = pierwsze znaczące słowo tytułu (zwykle marka / linia: „iphone”, „apple”, „ps4”), także
            # częste w bazie (do 50% ofert) - zapytanie i tak jest w obrębie kategorii.
            family = next((w for w in df if 0 < df[w] <= FAMILY_DF_SHARE * self._rows), None)
            if family:
                plans.append((f"cała rodzina „{family}”", [], [], [family], []))
            out, seen = [], set()
            for label, required, var, info, abs_ in plans:
                if not required and not var and not info:
                    continue
                found = self._query(db, category, required, var, info, abs_)
                if not found:
                    continue
                key = (found["n_active"], found["n_gone"], found["median"])
                if key in seen:
                    continue
                seen.add(key)
                out.append({**found, "label": label})
            return out
        except sqlite3.Error as exc:
            log.warning("[AI] Porównywalne oferty niedostępne: %s", exc)
            return []

    def _query(self, db, category, required, variants, informative, absent):
        must = [_q(w) for w in [*required, *variants]]
        parts = []
        if must:
            parts.append(" AND ".join(must))
        if informative:
            parts.append("(" + " OR ".join(_q(w) for w in informative) + ")")
        if not parts:
            return None
        query = " AND ".join(parts)
        if absent:
            query += " NOT (" + " OR ".join(_q(w) for w in absent) + ")"
        need = math.ceil(WORD_SHARE * len(informative)) if informative else 0
        plain_info = {_plain(w) for w in informative}
        active, gone = [], []
        for words, price, was_gone in db.execute(
                "SELECT words, price, gone FROM offers WHERE offers MATCH ? AND category = ? ORDER BY rank LIMIT 2000",
                (query, category)):
            have = {_plain(w) for w in words.split()}
            if len(plain_info & have) < need:
                continue
            (gone if was_gone else active).append(price)
        if len(active) + len(gone) < self.min_offers:
            return None
        p25, med, p75 = _pct(active, .25), _pct(active, .5), _pct(active, .75)
        gone_med = _pct(gone, .5) if len(gone) >= 10 else None
        mid = (p25 + med) / 2 if active else None
        estimate = max(x for x in (gone_med, mid) if x is not None) if (gone_med or mid) else None
        return {"words": [*required, *variants, *informative], "n_active": len(active), "n_gone": len(gone),
                "p25": p25, "median": med, "p75": p75, "gone_median": gone_med, "estimate": estimate}


def describe_levels(levels):
    """Tekst dla modelu (<mediany>) - kilka median od szczegółowej do ogólnej."""
    if not levels:
        return ("\n\n<mediany>\nBRAK podobnych ofert w bazie rynku (żadna mediana).\n</mediany>")
    zl = lambda v: "-" if v is None else f"{v:,.0f}".replace(",", " ")  # noqa: E731
    lines = [f"{i}. {lv['label']} (słowa: {' '.join(lv['words'])}): aktywnych {lv['n_active']} - p25 "
             f"{zl(lv['p25'])}, mediana {zl(lv['median'])}, p75 {zl(lv['p75'])} zł; zniknęło {lv['n_gone']} "
             f"(mediana {zl(lv['gone_median'])} zł); szacunek szybkiej sprzedaży ~{zl(lv['estimate'])} zł"
             for i, lv in enumerate(levels, 1)]
    return "\n\n<mediany>\n" + "\n".join(lines) + "\n</mediany>"


def describe(found):
    """Tekst dla modelu (<porownywalne>) - liczby z bazy, nie zgadywane."""
    if not found:
        return ""
    zl = lambda v: "-" if v is None else f"{v:,.0f}".replace(",", " ")  # noqa: E731
    return (f"\n\n<porownywalne>\nPodobne oferty z bazy rynku (słowa: {' '.join(found['words'])}): aktywnych "
            f"{found['n_active']} (p25 {zl(found['p25'])}, mediana {zl(found['median'])}, p75 {zl(found['p75'])} zł), "
            f"zniknęło {found['n_gone']} (mediana {zl(found['gone_median'])} zł) → szacunek szybkiej sprzedaży "
            f"~{zl(found['estimate'])} zł. Dopasowanie po słowach tytułu - sprawdź, czy to ten sam model i wariant; "
            f"bez wyraźnego powodu nie wyceniaj wyżej.\n</porownywalne>")
