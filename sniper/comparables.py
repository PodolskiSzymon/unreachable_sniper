"""Porównywalne oferty z bazy analizatora (`<SNIPER_GUIDES_DIR>/comparables.db`, budowany przez `python -m analyzer
sniper-guide` i automat co 2 dni) - 2026-10-26, pomysł użytkownika: zamiast zgadywania ceny przez AI sprawdzić
medianę podobnych ofert po słowach z tytułu (jak przy laptopach po karcie graficznej).

Dopasowanie (bez sieci, kilka ms na ofertę, tylko do odczytu):
  * liczby z tytułu (13, 128, 4060, a17 → „17”) muszą wystąpić; gdy za mało wyników - zostaje tylko pierwsza liczba,
  * słowa wariantu (pro, max, ultra, lite, slim…) muszą się zgadzać: „iPhone 13” nie łapie „iPhone 13 Pro”,
  * pozostałe znaczące słowa (rzadkie w bazie - „telefon”, „stan”, „sprzedam” odpadają same) - co najmniej 60%,
  * ta sama kategoria; wynik tylko przy ≥ `min_offers` ofertach.
Szacunek sprzedaży jak w analizatorze: wyższa z (mediana zniknięć przy ≥ 10, środek p25–mediana aktywnych).

Popyt (2026-11-16, analyse_data/POPYT.md - reMarkable 2 kupiony za 1200, sprzedany za 1400; iPad A16 w 24 h): gdy
plik ma flagi popytu (analizator od 16.11, meta `demand`), szacunek = cena, po której oferty NAPRAWDĘ schodzą -
mediana ofert, które zniknęły w ≤ 3 dni albo są potwierdzoną sprzedażą (`apply_clearing`). Mediana aktywnych jest
zawyżona przez oferty wiszące tygodniami: potwierdzone sprzedaże ≤ 7 dni mają cenę 0,82 × mediana aktywnych, szybkie
zniknięcia 0,88, a dawny szacunek ~0,90-0,95 (reMarkable 2: 1375 → ~1100-1180, iPhone 12: 588 → 530, S23 bez
zmian, iPad A16 bez zmian). Do tego liczby do klasy popytu (sniper/market.py: `demand`): świeże oferty, ile z nich
zniknęło w tydzień, ile wisi ≥ 3 tygodnie, okno dni (`fresh_days`).
"""
import json
import logging
import math
import re
import sqlite3
import unicodedata
from pathlib import Path

from .config import LAPTOP_VALUE_SHARE, VALUE_SHARE, VALUE_SHARE_DEAD, VALUE_SHARE_HOT
from .guides import _ALIASES, VARIANT_WORDS, tokens

log = logging.getLogger("sniper.comparables")

_ALL_VARIANTS = set().union(*VARIANT_WORDS.values()) | {"pro", "max", "plus", "ultra", "mini", "air", "slim",
                                                         "digital", "oled", "lite", "fe", "xl"}
# Słowo w > 35% ofert = nic nie mówi. Było 20%: „iphone” (25% całej bazy, 27.10) wypadał i „iPhone 7” szukał
# każdego telefonu z „7” w tytule (mediana 834 zł zamiast ~340). Słowa ogólne odcina GENERIC.
MAX_DF_SHARE = 0.35
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

# 2026-11-03 (Samsung A17 za 377 zł jako „okazja”, przy rynku ~450-550 zł): mediany mieszały nowe z używanymi
# (80% ofert A17 to „Nowy z metką”), wersję 5G z 4G, „A17” z każdym Samsungiem z „17” w tytule (litera „a” odpadała
# jako słowo ogólne) i dawały AI poziomy „cała rodzina samsung” (~830 zł). Teraz: kod modelu jako fraza („a 17”,
# „ddr 4”, „s 23”), 5G / LTE jako wariant, stan (nowy / używany) osobno, przy pojemności tylko oferty z taką samą
# albo mniejszą, bez poziomów, które gubią model.
NEW_PREFIX = "nowy"                # stan Vinted: „Nowy z metką”, „Nowy bez metki”; reszta = używany
_CODE = re.compile(r"(?<![^\W\d_])([^\W\d_]{1,4})\s?(\d{1,4})(?![\d,.])(?!\s?(?:gb|tb|mb|mhz|hz|w\b|mah|\"|”|cal|inch|%))")
_NET5 = re.compile(r"(?<![\d.,])5\s?g(?![^\W\d_])")
_CELLULAR = re.compile(r"(?<![\d.,])[45]\s?g(?![^\W\d_])|\b(?:lte|cellular|celular|sim)\b")
_SIZE = re.compile(r"(?<![\d.,])(\d{1,4})\s?(gb|tb)\b")
_RAM_SLASH = re.compile(r"(?<![\d.,])(\d{1,2})\s?(?:gb)?\s?/\s?(\d{2,4})\s?(?:gb)?\b")
_RAM = re.compile(r"(?<![\d.,])(\d{1,2})\s?gb\s?(?:ram|lpddr\w*)\b|\bram\s?:?\s?(\d{1,2})\s?gb\b")
STORAGE_STEPS = (16, 32, 64, 128, 256, 512, 1024, 2048)
CODE_SKIP = {"gb", "tb", "mb", "x", "v", "ver", "nr", "no", "gen", "mhz", "hz", "w", "mah", "cm", "mm", "kg", "po",
             "za", "od", "ze", "we", "lub", "oraz", "ok", "ram", "rok", "lat", "szt", "sztuk", "gier", "gry", "pady",
             "by", "to", "is", "in", "of", "or"}
# Podzespoły: kodem modelu są tylko te przedrostki („gtx1050”, „rx 570”, „ddr4”); „cl17”, „hx424”, „m2”, „gddr5”
# to parametry / numery części - jako wymóg odcinały prawie wszystkie podobne oferty.
CODE_PREFIXES = {"gpu": {"gtx", "rtx", "rx", "gt", "hd", "r", "arc", "mx", "quadro", "vega"},
                 "ram": {"ddr", "lpddr"}, "storage": set()}
SIZE_CORE = {"ram", "storage", "gpu"}   # pojemność = sam produkt (pamięć 16 GB, dysk 1 TB, karta 8 GB) - zawsze wymagana
NET_CATEGORIES = {"phone": _NET5, "tablet": _CELLULAR}
_NET_PHRASES = {"phone": ['"5 g"'], "tablet": ['"4 g"', '"5 g"', "lte", "cellular", "celular", "sim"]}


def condition_group(condition):
    """Stan z formularza Vinted → "new" / "used" (None = nie wiadomo - bez filtra)."""
    if not condition:
        return None
    return "new" if NEW_PREFIX in str(condition).strip().lower() else "used"


def parse_title(category, title):
    """Rozbiór tytułu na role (2026-11-03): kody modelu („a17” → fraza "a 17"), liczby modelu, pojemność, RAM,
    flaga sieci (5G w telefonie, LTE / 5G w tablecie). → słownik."""
    text = (title or "").lower()
    for pattern, repl in _ALIASES:
        text = pattern.sub(repl, text)
    net_re = NET_CATEGORIES.get(category)
    net = bool(net_re and net_re.search(text))
    if net_re:
        text = net_re.sub(" ", text)
    ram = storage = None
    used_numbers = []
    match = _RAM_SLASH.search(text)
    if match and int(match.group(1)) <= 24 and int(match.group(2)) in STORAGE_STEPS:
        ram, storage = int(match.group(1)), int(match.group(2))
        used_numbers += [match.group(1), match.group(2)]
        text = text[:match.start()] + f" {storage} gb " + text[match.end():]
    match = _RAM.search(text)
    if match and ram is None:
        ram = int(match.group(1) or match.group(2))
        used_numbers.append(str(ram))
        text = text[:match.start()] + " ram " + text[match.end():]
    sizes = []
    for match in _SIZE.finditer(text):
        value = int(match.group(1)) * (1024 if match.group(2) == "tb" else 1)
        sizes.append((match.group(1), match.group(2), value))
    if sizes and storage is None:
        storage = max(v for _n, _u, v in sizes) if category in ("phone", "tablet", "console") else sizes[0][2]
    codes = []
    for match in _CODE.finditer(text):
        letters, digits = match.group(1), match.group(2)
        spaced = match.group(0) != letters + digits
        # Jedna litera tylko bez spacji („a17”, „s23”) - „z 2 padami”, „i 2 gry” to spójniki, nie model.
        allowed = CODE_PREFIXES.get(category)
        if allowed is not None and letters not in allowed:
            continue
        if letters in CODE_SKIP or letters in _ALL_VARIANTS or (spaced and (len(letters) == 1 or _generic(letters))) or \
                (len(letters) > 1 and _generic(letters)):
            continue
        codes.append((letters, digits))
    words = list(dict.fromkeys(tokens(text)))
    size_numbers = {n for n, _u, _v in sizes}
    code_numbers = {d for _l, d in codes}
    model_numbers = [w for w in words if w.isdigit() and w not in size_numbers and w not in code_numbers
                     and w not in used_numbers]
    return {"words": words, "codes": codes, "model_numbers": model_numbers, "sizes": sizes, "storage": storage,
            "ram": ram, "net": net}


def _phrase(*parts):
    return '"' + " ".join(str(p).replace('"', "") for p in parts) + '"'


def _bigger_storage(storage):
    """Frazy pojemności większych niż `storage` - przy poziomie „każda pojemność” odrzucamy droższe wersje."""
    out = []
    for step in STORAGE_STEPS:
        if step > storage:
            out.append(_phrase(step // 1024, "tb") if step >= 1024 else _phrase(step))
    return out


def _plain(word):
    """Jak tokenizer FTS5 (remove_diacritics): „łódź” → „lodz” - do słownika częstości."""
    word = word.replace("ł", "l")
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))


_GENERIC_PLAIN = {_plain(w) for w in GENERIC}


def _generic(word):
    return word in GENERIC or _plain(word) in _GENERIC_PLAIN


def _q(word):
    return '"' + str(word).replace('"', "") + '"'


def _pct(values, q):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


class Comparables:
    def __init__(self, path, min_offers=8, cfg=None):
        self.cfg = cfg                   # progi klas popytu (AiConfig) - wycena „gorący” / „martwy” (apply_clearing)
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
            self._specs = bool(self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='laptop_specs'").fetchone())
            self._desktop = bool(self._conn.execute(                 # komputery (analizator od 2026-11-20)
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='desktop_specs'").fetchone())
            # Stan (nowy / używany) - analizator od 2026-11-03; starszy plik: bez filtra stanu.
            self._cond = "cond" in [c[0] for c in self._conn.execute("SELECT * FROM offers LIMIT 0").description]
            self._spec_cond = self._specs and "cond" in [
                c[1] for c in self._conn.execute("PRAGMA table_info(laptop_specs)")]
            # Popyt (analizator od 2026-11-16): flagi fast / sold / fresh / gone7 / stale + okno świeżych ofert.
            columns = [c[0] for c in self._conn.execute("SELECT * FROM offers LIMIT 0").description]
            self._demand = all(c in columns for c in DEMAND_COLUMNS)
            row = self._conn.execute("SELECT value FROM meta WHERE key = 'fresh_days'").fetchone()
            try:
                self._fresh_days = json.loads(row[0]) if row else {}
            except (ValueError, TypeError):
                self._fresh_days = {}
            log.info("[AI] Porównywalne oferty: %s (%d ofert)", self.path, self._rows)
        return self._conn

    def span_days(self):
        """Z ilu dni są oferty w pliku (analizator od 2026-11-15, meta 'span_days'); None = nie wiadomo."""
        try:
            db = self._db()
            if db is None:
                return None
            row = db.execute("SELECT value FROM meta WHERE key = 'span_days'").fetchone()
            return float(row[0]) if row else None
        except (sqlite3.Error, ValueError, TypeError):
            return None

    def lookup(self, category, title, condition=None):
        """→ słownik ze statystykami podobnych ofert (najdokładniejszy poziom) albo None (brak pliku / danych)."""
        found = self.levels(category, title, condition)
        return found[0] if found else None

    def _df(self, db, word):
        row = db.execute("SELECT doc FROM vocab WHERE term = ?", (_plain(word),)).fetchone()
        return row[0] if row else 0

    def has_specs(self):
        """Czy plik ma tabelę laptopów po specyfikacji (analizator od 2026-11-01)."""
        try:
            return self._db() is not None and getattr(self, "_specs", False)
        except sqlite3.Error:
            return False

    def has_desktop_specs(self):
        """Czy plik ma tabelę komputerów stacjonarnych po specyfikacji (analizator od 2026-11-20)."""
        try:
            return self._db() is not None and getattr(self, "_desktop", False)
        except sqlite3.Error:
            return False

    def code_spec(self, *texts):
        """Baza modeli (analizator od 2026-11-20, tabela model_codes): pierwszy znany kod modelu z tekstów →
        {"cpu", "gpu", "code"} albo None."""
        from .specs import model_codes
        try:
            db = self._db()
            if db is None or not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'model_codes'").fetchone():
                return None
            for text in texts:
                for code in model_codes(text):
                    row = db.execute("SELECT cpu, gpu FROM model_codes WHERE code = ?", (code,)).fetchone()
                    if row:
                        return {"cpu": row[0], "gpu": row[1], "code": code}
        except sqlite3.Error:
            return None
        return None

    def spec_levels(self, spec, condition=None, table="laptop_specs"):
        """Mediany laptopów (albo komputerów: table="desktop_specs") po specyfikacji → lista statystyk jak levels()
        (+ "kind": "spec"); pusta, gdy brak."""
        path = spec_path(spec)
        if table not in ("laptop_specs", "desktop_specs"):
            raise ValueError(table)
        ready = self.has_specs() if table == "laptop_specs" else self.has_desktop_specs()
        if not path or not ready:
            return []
        out, seen = [], set()
        try:
            db = self._db()
            for cols in SPEC_PLANS[path]:
                if not all(spec.get(c) for c in cols):
                    continue
                where = " AND ".join(f"{c} = ?" for c in cols) + (" AND gpu IS NULL" if path == "cpu" else "")
                params = [spec[c] for c in cols]
                group = condition_group(condition) if getattr(self, "_spec_cond", False) else None
                if group:
                    where += " AND cond = ?"
                    params.append(group)
                rows = db.execute(f"SELECT price, gone FROM {table} WHERE {where}", params).fetchall()
                active = [p for p, g in rows if not g]
                gone = [p for p, g in rows if g]
                if len(active) + len(gone) < self.min_offers:
                    continue
                stats = _stats(active, gone)
                key = (stats["n_active"], stats["n_gone"], stats["median"])
                if key in seen:
                    continue
                seen.add(key)
                state = {"new": ", tylko nowe", "used": ", tylko używane"}.get(group, "")
                out.append({**stats, "words": [str(spec[c]) for c in cols], "label": _spec_label(spec, cols) + state,
                            "kind": "spec", "condition": group})
        except sqlite3.Error as exc:
            log.warning("[AI] Mediany po specyfikacji niedostępne: %s", exc)
            return []
        return out

    def levels(self, category, title, condition=None):
        """Mediany od najbardziej szczegółowej do ogólniejszej, ZAWSZE tego samego modelu i wariantu (2026-11-03):
        wszystko z tytułu → bez RAM-u i liczb dodatkowych → (telefon / tablet / konsola) każda pojemność, ale bez
        większych od tej z oferty. Kod modelu („A17”), 5G / LTE, wariant (Pro, Ti, OLED…) i stan (nowy / używany)
        zawsze muszą się zgadzać; poziomów „bez numeru modelu” i „cała rodzina” już nie ma - mieszały modele
        (A17 dostawał medianę wszystkich Samsungów). → lista statystyk + "label"; pusta, gdy brak danych."""
        if not category or category == "skip":
            return []
        try:
            db = self._db()
            if db is None or not self._rows:
                return []
            if category in CLEARING_CATEGORIES:
                bare = strip_accessories(title)
                title = bare if len(tokens(bare)) >= 2 else title     # samo „Apple Pencil 2” - zostaje jak było
            parsed = parse_title(category, title)
            words = parsed["words"]
            code_letters = {l for l, _d in parsed["codes"]}
            variants = [w for w in words if w in _ALL_VARIANTS]
            limit = MAX_DF_SHARE * self._rows
            df = {w: self._df(db, w) for w in words if not w.isdigit() and w not in _ALL_VARIANTS and len(w) >= 2
                  and not _generic(w) and w not in code_letters and w not in ("tb", "ram")}
            informative = [w for w in df if 0 < df[w] <= limit]
            codes = [_phrase(l, d) for l, d in parsed["codes"]]
            model_numbers = [_q(n) for n in parsed["model_numbers"]]
            if not codes and not model_numbers and not informative:
                return []
            sizes = [_phrase(n, "tb") if u == "tb" else _q(n) for n, u, _v in parsed["sizes"]]
            net = _NET_PHRASES.get(category)
            net_must, net_absent = [], []
            if net:
                if parsed["net"]:
                    net_must = ["(" + " OR ".join(net) + ")"]
                else:
                    net_absent = list(net)
            absent = [_q(w) for w in sorted(_ALL_VARIANTS - set(variants))] + net_absent
            ram = []
            if parsed["ram"]:
                # „8/256” w tytułach = kolejne słowa „8 256”; inaczej „8 GB RAM” / „8 RAM”.
                forms = [_phrase(parsed["ram"], "gb", "ram"), _phrase(parsed["ram"], "ram")]
                if parsed["storage"] and parsed["storage"] < 1024:
                    forms.insert(0, _phrase(parsed["ram"], parsed["storage"]))
                ram = ["(" + " OR ".join(forms) + ")"]
            # Model = kody + liczby modelu; gdy tytuł ma ich kilka („iPhone 13 2022”), pierwsza zostaje zawsze.
            model = codes + model_numbers
            # Rdzeń modelu: kody („a 17”, „rtx 3060”, „ddr 4”); bez kodów - pierwsza liczba („iPhone 13”).
            # Liczby obok kodu (rok, MHz, „2 pady”) odpadają na drugim poziomie.
            core = codes or model_numbers[:1]
            what = "model + wariant" + (" + pojemność" if sizes else "")
            plans = [(what + (" + RAM" if ram else "") + (" + liczby z tytułu" if model != core else ""),
                      [*model, *sizes, *ram]),
                     (what, [*core, *sizes])]
            bigger = []
            if sizes and category not in SIZE_CORE and parsed["storage"]:
                bigger = _bigger_storage(parsed["storage"])
            # (etykieta, wymagane, znaczące słowa, dodatkowo wykluczone)
            plans = [(label, req, informative, []) for label, req in plans]
            if bigger:
                plans.append((f"model + wariant, pojemność do {parsed['storage']} GB", list(core), informative,
                              bigger))
            # Ten sam model, ale pozostałe słowa tytułu dowolne („Vând Samsung S22 impecabil”, „iPad Pro M1 + Pencil +
            # Logitech Combo Touch”) - tylko z rdzeniem modelu i słowem rodziny (marka / linia).
            # Podzespół: sam kod / numer + pojemność wystarczą („RTX 3050” dowolnego producenta, „870” 1 TB).
            family = next((w for w in informative if df[w] <= 0.5 * self._rows), None)
            relaxed = [] if category in SIZE_CORE else [family] if family else None
            if core and relaxed is not None and relaxed != informative:
                size_req = [] if bigger else sizes
                plans.append(("ten sam model, pozostałe słowa tytułu dowolne" + (
                    f", pojemność do {parsed['storage']} GB" if bigger else ""),
                    [*core, *size_req], relaxed, bigger))
            group = condition_group(condition) if getattr(self, "_cond", False) else None
            nets = [net_must]
            if net_must:
                # Oferta 5G / LTE: gdy brak danych dla wersji z modemem - wszystkie wersje (ostrożniej, nie drożej;
                # iPhone 12+ ma 5G zawsze, a sprzedawcy rzadko to piszą).
                nets.append([])
            out, seen = [], set()
            for net_req in nets:
                for label, required, info, extra_absent in plans:
                    if not required and not variants and not info:
                        continue
                    found = self._query(db, category, [*required, *net_req], variants, info,
                                        [*absent, *extra_absent], group)
                    if not found:
                        continue
                    key = (found["n_active"], found["n_gone"], found["median"])
                    if key in seen:
                        continue
                    seen.add(key)
                    state = {"new": ", tylko nowe", "used": ", tylko używane"}.get(group, "")
                    note = "" if net_req or not net_must else ", wszystkie wersje sieci (5G / 4G)"
                    out.append({**found, "label": label + note + state, "condition": group})
                if out:
                    break
            if category in CLEARING_CATEGORIES:
                if getattr(self, "_demand", False):
                    days = (getattr(self, "_fresh_days", None) or {}).get(category)
                    for lv in out:
                        lv["fresh_days"] = days
                apply_clearing(out, getattr(self, "cfg", None))
            return out
        except sqlite3.Error as exc:
            log.warning("[AI] Porównywalne oferty niedostępne: %s", exc)
            return []

    def _query(self, db, category, required, variants, informative, absent, group=None):
        """required = gotowe wyrażenia FTS5 (frazy w cudzysłowie albo grupy OR w nawiasie)."""
        must = [*required, *(_q(w) for w in variants)]
        parts = []
        if must:
            parts.append(" AND ".join(must))
        if informative:
            parts.append("(" + " OR ".join(_q(w) for w in informative) + ")")
        if not parts:
            return None
        query = " AND ".join(parts)
        if absent:
            query += " NOT (" + " OR ".join(absent) + ")"
        need = math.ceil(WORD_SHARE * len(informative)) if informative else 0
        plain_info = {_plain(w) for w in informative}
        active, gone = [], []
        with_demand = getattr(self, "_demand", False)
        demand = {"clear": [], "sold": [], "fast": 0, "fresh": 0, "gone7": 0, "stale": 0} if with_demand else None
        columns = "words, price, gone" + (", " + ", ".join(DEMAND_COLUMNS) if with_demand else "")
        sql = f"SELECT {columns} FROM offers WHERE offers MATCH ? AND category = ?"
        params = [query, category]
        if group:
            sql += " AND cond = ?"
            params.append(group)
        for row in db.execute(sql + " ORDER BY rank LIMIT 2000", params):
            words, price, was_gone = row[:3]
            have = {_plain(w) for w in words.split()}
            if len(plain_info & have) < need:
                continue
            (gone if was_gone else active).append(price)
            if demand is not None:
                fast, sold, fresh, gone7, stale = row[3:8]
                if fast or sold:
                    demand["clear"].append(price)
                if sold:
                    demand["sold"].append(price)
                demand["fast"] += bool(fast)
                demand["fresh"] += bool(fresh)
                demand["gone7"] += bool(gone7)
                demand["stale"] += bool(stale)
        if len(active) + len(gone) < self.min_offers:
            return None
        shown = [w.strip('"()') for w in [*required, *variants, *informative]]
        return {"words": shown, **_stats(active, gone, demand)}


def _stats(active, gone, demand=None):
    """Statystyki grupy: p25 / mediana / p75 aktywnych, mediana zniknięć (≥ 10) i szacunek szybkiej sprzedaży
    (wyższa z: mediana zniknięć, środek p25–mediana) - jak w analizatorze. Z flagami popytu (`demand`) dodatkowo
    ceny ofert, które zeszły (szybkie zniknięcia + potwierdzone sprzedaże), i liczby do klasy popytu; ostateczny
    szacunek liczy wtedy `apply_clearing` (potrzebuje wszystkich poziomów)."""
    p25, med, p75 = _pct(active, .25), _pct(active, .5), _pct(active, .75)
    gone_med = _pct(gone, .5) if len(gone) >= 10 else None
    mid = (p25 + med) / 2 if active else None
    estimate = max(x for x in (gone_med, mid) if x is not None) if (gone_med or mid) else None
    out = {"n_active": len(active), "n_gone": len(gone), "p25": p25, "median": med, "p75": p75,
           "gone_median": gone_med, "estimate": estimate, "p_low": _pct(active, VALUE_SHARE),
           "p_hot": _pct(active, VALUE_SHARE_HOT), "p_dead": _pct(active, VALUE_SHARE_DEAD),
           "p_laptop": _pct(active, LAPTOP_VALUE_SHARE)}
    if demand is not None:
        clear = demand["clear"]
        out.update(estimate_old=estimate, n_clear=len(clear), clear_median=_pct(clear, .5),
                   n_sold=len(demand["sold"]), sold_median=_pct(demand["sold"], .5) if len(demand["sold"]) >= 3
                   else None, n_fast=demand["fast"], n_fresh=demand["fresh"], n_gone7=demand["gone7"],
                   n_stale=demand["stale"])
    return out


DEMAND_COLUMNS = ("fast", "sold", "fresh", "gone7", "stale")
# Wycena ceną zejścia i klasy popytu tylko tu (decyzja użytkownika 15.11: podzespoły i laptopy - mało ofert na model,
# a schodzą; laptopy mają własne mediany po specyfikacji).
CLEARING_CATEGORIES = ("phone", "tablet", "console")
MIN_CLEAR = 5            # od tylu ofert, które zeszły, ich mediana jest szacunkiem poziomu
CLEAR_RATIO_LIMITS = (0.5, 1.05)


MIN_ACTIVE_LOW = 5        # od tylu aktywnych ofert szacunek = dolne VALUE_SHARE cen


# 2026-11-17 (reMarkable 2 kupiony za 1200 - „Sniper zobaczył ofertę z Type Folio dużo droższą i wycenił drogo”):
# dodatki w tytule nie wybierają grupy porównawczej. „Marker Plus” robił z „plus” wariant (tylko oferty z „plus” =
# komplety), „Type Folio” / „Pencil” zawężały do kompletów. Szukamy samego urządzenia; dodatki - reguła E dla AI.
ACCESSORIES = re.compile(
    r"(?:\+|&|\bz\b|\bwith\b|\bi\b|\boraz\b)?\s*(?:\b(?:apple\s+)?(?:magic|smart)\s+(?:keyboard|folio)(?:\s+folio)?"
    r"|\b(?:type|book|smart)\s+folio|\bfolio\b|\bmarker(?:\s+plus)?\b|\b(?:apple\s+)?pencil(?:\s+(?:pro|usb[\s-]?c|[12]))?\b"
    r"|\bstylus\b|\brysik\w*|\bkeyboard\b|\bklawiatur\w*|\betui\b|\bpokrowiec\w*|\bcover\b|\bcase\b)", re.I)


def strip_accessories(title):
    """Tytuł bez dodatków (folio, klawiatura, rysik / Pencil / Marker, etui) - do wyszukania podobnych urządzeń."""
    return re.sub(r"\s+", " ", ACCESSORIES.sub(" ", title or "")).strip()


def apply_clearing(levels, cfg=None):
    """Szacunek szybkiej sprzedaży (telefony, tablety, konsole). Od 2026-11-17 (pomysł użytkownika, iPad 10 64 GB:
    wycena 1380 zł, a nie schodzi za 1100) = **dolne 10% cen aktywnych ofert** tego poziomu (`p_low`,
    SNIPER_VALUE_SHARE): kupujący biorą najtańsze sensowne oferty. Kopia 07.11 (potwierdzone sprzedaże ≤ 7 dni, bez
    oszustw / uszkodzonych): po cenie ≥ p10 aktywnych 70% (mediana sprzedaż / p10 = 1,10), ≥ p20 51%, ≥ dawnego
    szacunku 33%; rozkład trójkątny (p5, mediana, p95) na 10% wypadał jak p20 - gorzej niż zwykły kwantyl.
    Mniej niż MIN_ACTIVE_LOW aktywnych: jak 16.11 - mediana ofert, które zeszły (≤ 3 dni / sprzedane), mediana
    aktywnych × współczynnik zejścia albo p25. Nigdy wyżej niż dawny szacunek. → ta sama lista (zmieniona w miejscu)."""
    if not levels:
        return levels
    for lv in levels:
        lv.setdefault("estimate_old", lv.get("estimate"))
    with_data = [lv for lv in levels if "n_clear" in lv]
    ratio = None
    if with_data:
        widest = max(with_data, key=lambda lv: (lv["n_clear"], lv["n_active"] + lv["n_gone"]))
        if widest["n_clear"] >= MIN_CLEAR and widest.get("median") and widest.get("clear_median"):
            low, high = CLEAR_RATIO_LIMITS
            ratio = min(max(widest["clear_median"] / widest["median"], low), high)
    # Klasa popytu (sniper/market.py: demand) zmienia kwantyl: gorący → dolne 25%, martwy → dolne 5% (17.11).
    klass = None
    if with_data and (cfg is None or getattr(cfg, "demand_rules", True)):
        from .market import demand
        klass = (demand(levels, cfg) or {}).get("class")
    key, share = {"gorący": ("p_hot", VALUE_SHARE_HOT), "martwy": ("p_dead", VALUE_SHARE_DEAD)}.get(
        klass, ("p_low", VALUE_SHARE))
    for lv in levels:
        old = lv.get("estimate_old")
        if lv.get("n_active", 0) >= MIN_ACTIVE_LOW and lv.get(key):
            estimate, basis = lv[key], f"dolne {share * 100:.0f}% cen aktywnych ofert" + (
                f" - popyt {klass}" if key != "p_low" else "")
        elif lv.get("n_clear", 0) >= MIN_CLEAR and lv.get("clear_median"):
            estimate, basis = lv["clear_median"], f"mediana {lv['n_clear']} ofert, które zeszły"
        else:
            options = [(old, "dawny szacunek (mało danych)")]
            if lv.get("n_clear", 0) >= 3 and lv.get("clear_median"):
                options.append((lv["clear_median"], f"mediana {lv['n_clear']} ofert, które zeszły"))
            if ratio and lv.get("median"):
                options.append((ratio * lv["median"], f"mediana aktywnych × {ratio:.2f} (tyle schodzi ten model)"))
            elif lv.get("p25"):
                options.append((lv["p25"], "p25 aktywnych (mało danych)"))
            options = [o for o in options if o[0]]
            if not options:
                continue
            estimate, basis = min(options, key=lambda o: o[0])
        lv.update(estimate=min(estimate, old) if old else estimate, estimate_basis=basis, clear_ratio=ratio)
    return levels
    widest = max(with_data, key=lambda lv: (lv["n_clear"], lv["n_active"] + lv["n_gone"]))
    ratio = None
    if widest["n_clear"] >= MIN_CLEAR and widest.get("median") and widest.get("clear_median"):
        low, high = CLEAR_RATIO_LIMITS
        ratio = min(max(widest["clear_median"] / widest["median"], low), high)
    for lv in with_data:
        old = lv.get("estimate_old")
        if lv["n_clear"] >= MIN_CLEAR and lv.get("clear_median"):
            estimate, basis = lv["clear_median"], f"mediana {lv['n_clear']} ofert, które zeszły"
        else:
            options = [(old, "dawny szacunek (mało danych o sprzedaży)")]
            if lv["n_clear"] >= 3 and lv.get("clear_median"):
                options.append((lv["clear_median"], f"mediana {lv['n_clear']} ofert, które zeszły"))
            if ratio and lv.get("median"):
                options.append((ratio * lv["median"], f"mediana aktywnych × {ratio:.2f} (tyle schodzi ten model)"))
            elif lv.get("p25"):
                options.append((lv["p25"], "p25 aktywnych (mało danych o sprzedaży)"))
            options = [o for o in options if o[0]]
            if not options:
                continue
            estimate, basis = min(options, key=lambda o: o[0])
        caps = [x for x in (old, lv.get("median")) if x]
        lv.update(estimate=min(estimate, max(caps)) if caps else estimate, estimate_basis=basis, clear_ratio=ratio)
    return levels


def apply_laptop_fast(levels):
    """Laptop po specyfikacji (2026-11-18, użytkownik: „chciałbym patrzeć na szybkie sprzedaże”): szacunek = cena
    szybkiej sprzedaży = dolne LAPTOP_VALUE_SHARE (20%) cen aktywnych ofert poziomu (przy < MIN_ACTIVE_LOW aktywnych -
    p25), nigdy wyżej niż dawny szacunek. Mediana samych szybkich sprzedaży per specyfikacja odpada - w teście tylko 16 z
    88 grup miało ≥ 5 takich ofert. Poziomy dostają "laptop_fast" - market.value_factor: wycena AI ≤ szacunek × 1,1."""
    for lv in levels or []:
        old = lv.setdefault("estimate_old", lv.get("estimate"))
        if lv.get("n_active", 0) >= MIN_ACTIVE_LOW and lv.get("p_laptop"):
            estimate, basis = lv["p_laptop"], (f"dolne {LAPTOP_VALUE_SHARE * 100:.0f}% cen aktywnych ofert - po tyle "
                                               "laptopy schodzą w ≤ 3 dni")
        elif lv.get("p25"):
            estimate, basis = lv["p25"], "p25 aktywnych (mało ofert)"
        else:
            estimate, basis = None, None
        if estimate:
            lv.update(estimate=min(estimate, old) if old else estimate, estimate_basis=basis)
        lv["laptop_fast"] = True
    return levels


def apply_old_laptop(levels, reason):
    """Stary laptop (market.old_laptop, 2026-11-18): szacunek = dolne VALUE_SHARE cen aktywnych ofert poziomu (przy
    < MIN_ACTIVE_LOW aktywnych - p25), nigdy wyżej niż dawny szacunek; poziomy dostają "old_laptop" (= opis) - po nim
    market.value_factor przycina wycenę AI do szacunku × 1,0. → ta sama lista (zmieniona w miejscu)."""
    for lv in levels or []:
        old = lv.setdefault("estimate_old", lv.get("estimate"))
        if lv.get("n_active", 0) >= MIN_ACTIVE_LOW and lv.get("p_low"):
            estimate, basis = lv["p_low"], f"dolne {VALUE_SHARE * 100:.0f}% cen aktywnych ofert - {reason}"
        elif lv.get("p25"):
            estimate, basis = lv["p25"], f"p25 aktywnych (mało ofert) - {reason}"
        else:
            estimate, basis = None, None
        if estimate:
            lv.update(estimate=min(estimate, old) if old else estimate, estimate_basis=basis)
        lv["old_laptop"] = reason
    return levels


def describe_levels(levels):
    """Tekst dla modelu (<mediany>) - kilka median od szczegółowej do ogólnej."""
    if not levels:
        return ("\n\n<mediany>\nBRAK podobnych ofert w bazie rynku (żadna mediana).\n</mediany>")
    zl = lambda v: "-" if v is None else f"{v:,.0f}".replace(",", " ")  # noqa: E731
    lines = [f"{i}. {lv['label']} ({'specyfikacja' if lv.get('kind') == 'spec' else 'słowa'}: "
             f"{' '.join(lv['words'])}): aktywnych {lv['n_active']} - p25 "
             f"{zl(lv['p25'])}, mediana {zl(lv['median'])}, p75 {zl(lv['p75'])} zł; zniknęło {lv['n_gone']} "
             f"(mediana {zl(lv['gone_median'])} zł)" + _clear_text(lv, zl) +
             f"; szacunek szybkiej sprzedaży ~{zl(lv['estimate'])} zł"
             + (f" ({lv['estimate_basis']})" if lv.get("estimate_basis") else "") for i, lv in enumerate(levels, 1)]
    return "\n\n<mediany>\n" + "\n".join(lines) + "\n</mediany>"


# Mediany laptopów po specyfikacji (2026-11-01): od najdokładniejszej do samej karty / chipu / procesora.
SPEC_PLANS = {
    "gpu": (("gpu", "ram", "ssd"), ("gpu", "ram"), ("gpu",)),
    "mac": (("line", "chip", "ram", "ssd"), ("line", "chip", "ram"), ("line", "chip")),
    "cpu": (("cpu_line", "cpu_gen", "ram", "ssd"), ("cpu_line", "cpu_gen", "ram"), ("cpu_line", "cpu_gen")),
}


def spec_path(spec):
    if not spec:
        return None
    if spec.get("line") and spec.get("chip"):
        return "mac"                     # 20.11: MacBook najpierw (Intel z Radeon Pro nie miesza się z Windowsami)
    if spec.get("gpu"):
        return "gpu"
    return "cpu" if spec.get("cpu_line") and spec.get("cpu_gen") else None


def _spec_label(spec, cols):
    from .specs import describe
    shown = {k: spec.get(k) for k in cols}
    if "gpu" not in cols:
        shown["gpu"] = None
    text = describe({**{k: None for k in spec}, **shown})
    rest = [name for key, name in (("ram", "RAM"), ("ssd", "dysk")) if key not in cols]
    suffix = f" (każdy {' i '.join(rest)})" if rest else ""
    graphics = " (zintegrowana grafika)" if cols[0] == "cpu_line" else ""
    return text + graphics + suffix


def _clear_text(lv, zl):
    """Fragment opisu poziomu: ile ofert zeszło (≤ 3 dni / potwierdzona sprzedaż) i po ile."""
    if "n_clear" not in lv:
        return ""
    sold = f", w tym potwierdzonych sprzedaży {lv['n_sold']}" if lv.get("n_sold") else ""
    return (f"; zeszło (≤ 3 dni od wystawienia albo potwierdzona sprzedaż) {lv['n_clear']}{sold}"
            + (f" - mediana {zl(lv['clear_median'])} zł" if lv.get("clear_median") else ""))


def describe(found):
    """Tekst dla modelu (<porownywalne>) - liczby z bazy, nie zgadywane."""
    if not found:
        return ""
    zl = lambda v: "-" if v is None else f"{v:,.0f}".replace(",", " ")  # noqa: E731
    return (f"\n\n<porownywalne>\nPodobne oferty z bazy rynku (słowa: {' '.join(found['words'])}): aktywnych "
            f"{found['n_active']} (p25 {zl(found['p25'])}, mediana {zl(found['median'])}, p75 {zl(found['p75'])} zł), "
            f"zniknęło {found['n_gone']} (mediana {zl(found['gone_median'])} zł){_clear_text(found, zl)} → szacunek "
            f"szybkiej sprzedaży "
            f"~{zl(found['estimate'])} zł. Dopasowanie po słowach tytułu - sprawdź, czy to ten sam model i wariant; "
            f"bez wyraźnego powodu nie wyceniaj wyżej.\n</porownywalne>")
