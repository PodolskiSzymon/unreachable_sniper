"""Wytyczne per kategoria (D5, 2026-10-25): `<SNIPER_GUIDES_DIR>/<kategoria>.md` z analizatora rynku.

* `GuideBook.text(kategoria)` - tekst wytycznych dla modelu AI (komentarze HTML wycięte, plik wczytywany ponownie po
  zmianie - analizator podmienia go co 2 dni bez restartu Snipera). Laptopy bez `guides/laptop.md` i oferty bez
  kategorii (dawny tryb jednego katalogu) → dotychczasowy `SNIPER_AI_GUIDELINES` (guidelines.md).
* `GuideBook.match(kategoria, tytuł)` - „karta rynku”: wiersze tabeli cen pasujące do tytułu (lokalnie, zero kosztów).
  Wiersz pasuje, gdy wszystkie jego słowa są w tytule, a tytuł nie ma słowa wariantu, którego wiersz nie ma
  (iPhone 13 **Pro** nie dostanie progu zwykłego iPhone 13). Najbardziej szczegółowy wiersz wygrywa.
* Bramka ceny: cena łączna > `max zakup` pasującego wiersza × `SNIPER_AI_PRICE_GATE` (domyślnie 1,25) → oferta nie
  idzie do AI (na pewno nie okazja, a przy 7 katalogach to większość ruchu). Brak dopasowania → idzie do AI.
* Rodziny (31.10, prośba użytkownika: koszt AI to głównie to, co wysyłamy): `GuideBook.route(oferta)` dobiera
  wiersze tabeli dla TEJ oferty - pasujące do tytułu + inne warianty tego samego modelu (AI może poprawić wariant po
  zdjęciach), a gdy tytuł nie podaje modelu: z opisu, potem cała rodzina / marka (najliczniejsze wiersze). Sprawdza
  też sąsiednie kategorie (telefon ↔ tablet ↔ laptop, procesor ↔ płyta ↔ zestaw): iPad w katalogu telefonów dostaje
  wiersze tabletów i zmienia kategorię, „Huawei” bez modelu - telefony i tablety, „Huawei MatePad” / „tab” - tylko
  tablety. Do AI idzie kilkanaście wierszy zamiast całej tabeli (telefony: ~350 grup, ~14 tys. tokenów).
"""
import logging
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

log = logging.getLogger("sniper.guides")

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Słowa, które zmieniają model / cenę - gdy są w tytule, a nie w wierszu, wiersz nie pasuje. Laptopy: wiersze to karty
# graficzne, więc tylko warianty karty („Legion 5 Pro” to nadal RTX 4060).
_GPU_VARIANTS = {"ti", "super", "xt", "xtx"}
_MODEL_VARIANTS = {"pro", "max", "plus", "ultra", "mini", "air", "slim", "digital", "oled", "lite", "fe", "s", "e",
                   "a", "fold", "flip", "edge", "neo", "x", "xl", "xs", "xr"}
VARIANT_WORDS = {"laptop": _GPU_VARIANTS, "gpu": _GPU_VARIANTS, "ram": set(), "storage": set()}
# Słowa z nazw wierszy bez znaczenia dla dopasowania („Płyta B550 (AM4)”, „Zestaw z Intel Core i5-9600KF”).
_NOISE = {"gb", "tb", "ram", "zintegrowana", "grafika", "laptop", "pc", "intel", "core", "amd", "ryzen", "apple",
          "płyta", "plyta", "zestaw", "z"}
_PARENS = re.compile(r"\([^)]*\)")


# Skróty z tytułów → nazwy jak w tabelach analizatora („PS5 Slim” → playstation 5 slim).
_ALIASES = ((re.compile(r"\bps\s?([345])\b"), r"playstation \1"), (re.compile(r"\bgameboy\b"), "game boy"))


def tokens(text):
    """'iPhone13 Pro 128GB' → ['iphone', '13', 'pro', '128', 'gb'] (litery i cyfry rozdzielone)."""
    text = (text or "").lower()
    for pattern, repl in _ALIASES:
        text = pattern.sub(repl, text)
    # Litery z każdego języka (RO „vând”, SE „säljer”, CZ „prodám”) - samo [a-ząćęłńóśźż] cięło słowa na kawałki.
    text = re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", " ", text)
    return re.findall(r"[^\W\d_]+|\d+", text)


def _num(text):
    digits = re.sub(r"[^\d,.-]", "", text or "").replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return None


@dataclass(frozen=True)
class Row:
    name: str
    sale: float | None          # „sprzedam za ~”
    max_buy: float | None       # „max zakup”
    line: str                   # wiersz tabeli 1:1 (dla modelu)
    n: int = 0                  # „ofert” (liczność grupy - kolejność w rodzinie)
    cells: tuple = ()           # komórki wiersza (zwięzła tabela dla modelu)

    @cached_property
    def _raw(self):
        # Bez nawiasów: rok („(2024)”), gniazdo („(AM4)”), rodzaj RAM („(laptop)”) rzadko są w tytule.
        return tokens(_PARENS.sub(" ", self.name))

    @cached_property
    def words(self):
        # Laptopy ze zintegrowaną grafiką (linia CPU + RAM) - za luźne do dopasowania po tytule (i7 vs „5 lat”);
        # dopasowuje je osobno laptop_rows (procesor z tytułu / opisu).
        if "zintegrowana" in self.name.lower():
            return []
        return [t for t in self._raw if t not in _NOISE]

    @cached_property
    def core_words(self):
        """Słowa bez pojemności (128 GB, 1 TB) - do luźnego dopasowania, gdy tytuł nie podaje pamięci."""
        raw = self._raw
        sizes = {i for i, t in enumerate(raw[:-1]) if t.isdigit() and raw[i + 1] in ("gb", "tb")}
        return [t for i, t in enumerate(raw) if i not in sizes and t not in _NOISE] if self.words else []

    @cached_property
    def family(self):
        """Słowa przed pierwszą liczbą: „iphone 13 pro” → (iphone,), „galaxy s22” → (galaxy, s), „RTX 3060” → (rtx,)."""
        out = []
        for word in self.core_words:
            if word.isdigit():
                break
            out.append(word)
        return tuple(out)

    @cached_property
    def number(self):
        """Numer modelu: najdłuższa liczba poza pojemnością („iphone 13 128 GB” → 13, „Ryzen 5 5600X” → 5600)."""
        numbers = [w for w in self.core_words if w.isdigit()]
        return max(numbers, key=len) if numbers else None

    @property
    def model_key(self):
        return (self.family, self.number)


def parse_header(text):
    """Komórki nagłówka tabeli cen („grupa | ofert | … | max zakup | …”) albo ()."""
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        lower = [c.lower() for c in cells]
        if line.strip().startswith("|") and "max zakup" in lower and any(c.startswith("sprzedam") for c in lower):
            return tuple(cells)
    return ()


def parse_rows(text):
    """Wiersze tabeli cen z pliku analizatora (kolumny po nagłówku: grupa … sprzedam za ~ … max zakup …)."""
    rows, columns = [], None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            columns = None if not line else columns
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if columns is None:
            lower = [c.lower() for c in cells]
            if "max zakup" in lower and any(c.startswith("sprzedam") for c in lower):
                columns = (lower.index("max zakup"), next(i for i, c in enumerate(lower) if c.startswith("sprzedam")),
                           lower.index("ofert") if "ofert" in lower else None)
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        buy_i, sale_i, n_i = columns
        if len(cells) > max(buy_i, sale_i):
            count = _num(cells[n_i]) if n_i is not None and n_i < len(cells) else None
            rows.append(Row(cells[0], _num(cells[sale_i]), _num(cells[buy_i]), line, int(count or 0), tuple(cells)))
    return rows


def match(rows, title, category=None, relaxed=False):
    """Pasujące wiersze, od najbardziej szczegółowego. relaxed - bez wymogu pojemności (iPhone 13 bez „128 GB”
    pasuje do każdego iPhone 13 … GB, ale nie do iPhone 13 Pro)."""
    return match_words(rows, set(tokens(title)), category, relaxed)


# Warianty tylko do dopasowania wierszy (nie do comparables.py - tam „x” / „g” psułyby telefony „5G” / „2x”):
# płyty - „Tomahawk Max”, „B550-A PRO” to nazwy płyt, nie inny chipset; procesory - 3600 vs 3600X, 9400 vs 9400F.
_ROW_VARIANTS = {"motherboard": set(), "bundle": {"x", "xt"},
                 "cpu": {"x", "xt", "g", "ge", "f", "k", "kf", "ks", "t", "pro"}}


def match_words(rows, words, category=None, relaxed=False):
    variants = _ROW_VARIANTS.get(category, VARIANT_WORDS.get(category, _MODEL_VARIANTS | _GPU_VARIANTS))
    found = []
    for row in rows:
        need = row.core_words if relaxed else row.words
        if not need or not set(need) <= words:
            continue
        if (words & variants) - set(tokens(row.name)):
            continue
        found.append(row)
    found.sort(key=lambda r: -len(r.words))
    return found


# ---------------------------------------------------------------------------- rodziny (31.10)

# Sąsiednie kategorie: oferta wystawiona w złym katalogu (iPad wśród telefonów, zestaw wśród płyt głównych).
NEIGHBORS = {"phone": ("tablet",), "tablet": ("phone", "laptop"), "laptop": ("tablet",),
             "cpu": ("bundle", "motherboard"), "motherboard": ("bundle", "cpu"), "bundle": ("cpu", "motherboard")}
PARTS = ("cpu", "motherboard", "bundle")
# Słowa rozstrzygające kategorię przy słabym dopasowaniu („Huawei tab” → tylko tablety).
CATEGORY_WORDS = {
    "tablet": {"tab", "tablet", "tableta", "tablette", "pad", "matepad", "mediapad", "ipad"},
    "phone": {"telefon", "smartfon", "smartphone", "phone", "iphone", "komórka", "komorka"},
    "laptop": {"laptop", "notebook", "macbook", "ultrabook", "chromebook", "laptopa"},
}
LEVELS = {1: "tytuł - model", 2: "tytuł - numer modelu", 3: "opis", 4: "rodzina", 5: "marka", 0: "brak"}
# Słowa kategorii, które nie są rodziną (iphone / ipad / macbook / matepad - są).
_GENERIC_FAMILY = {"tab", "tablet", "tableta", "tablette", "pad", "telefon", "smartfon", "smartphone", "phone",
                   "komórka", "komorka", "laptop", "laptopa", "notebook", "ultrabook"}
FAMILY_LIMIT = 12
DESCRIPTION_CHARS = 1500

# Laptopy ze zintegrowaną grafiką: wiersze „intel core i5 10–11 gen. · 16 GB RAM (zintegrowana grafika)” dopasowane
# po procesorze i RAM-ie z tytułu / opisu (te same przedziały generacji co analyzer/guide.py: cpu_generation).
_INTEGRATED_ROW = re.compile(r"^(intel core i[3579]|amd ryzen [3579]) (.+?) · (\d+) gb ram", re.I)
_INTEL_CPU = re.compile(r"\bi([3579])[\s-]?(\d{4,5})(?:[a-z]{1,2}\d?)?\b", re.I)
_RYZEN_CPU = re.compile(r"\bryzen\s?([3579])\s?(?:pro\s)?(\d)\d{3}", re.I)
_RAM_GB = re.compile(r"\b(\d{1,2})\s?gb\s?(?:ram|ddr\d?|pami[eę]ci)\b|\bram[:\s-]*(\d{1,2})\s?gb", re.I)


def laptop_cpu(text):
    """(linia, przedział generacji, RAM w GB albo None) z tekstu oferty albo None."""
    text = text or ""
    m = _INTEL_CPU.search(text)
    if m:
        digits = m.group(2)
        gen = int(digits[:2]) if len(digits) == 5 or digits[0] == "1" else int(digits[0])
        bucket = "do 7 gen." if gen <= 7 else "8–9 gen." if gen <= 9 else "10–11 gen." if gen <= 11 else "12+ gen."
        line = f"intel core i{m.group(1)}"
    else:
        m = _RYZEN_CPU.search(text)
        if not m:
            return None
        series = int(m.group(2))
        bucket = "1000–3000" if series <= 3 else "4000–5000" if series <= 5 else "6000+"
        line = f"amd ryzen {m.group(1)}"
    ram = _RAM_GB.search(text)
    size = int(ram.group(1) or ram.group(2)) if ram else None
    return line, bucket, size if size and size >= 2 else None


def laptop_rows(rows, text):
    """Wiersze zintegrowanej grafiki dla procesora z tekstu → (wiersze, z RAM-em?)."""
    cpu = laptop_cpu(text)
    if not cpu:
        return [], False
    line, bucket, ram = cpu
    same = []
    for row in rows:
        m = _INTEGRATED_ROW.match(row.name.lower())
        if m and m.group(1) == line and m.group(2).strip() == bucket:
            same.append((row, int(m.group(3))))
    exact = [r for r, gb in same if gb == ram]
    rest = sorted((r for r, gb in same if gb != ram), key=lambda r: -r.n)
    return exact + rest, bool(exact)


def _siblings(rows, found):
    """Inne warianty tych samych modeli (pojemności, Pro / mini / Max, rok) - od najliczniejszych."""
    keys = {r.model_key for r in found if r.number}
    prefixes = [r.core_words for r in found if not r.number and r.core_words]
    out = []
    for row in rows:
        if row in found:
            continue
        core = row.core_words
        if (row.number and row.model_key in keys) or any(
                core and (core[:len(p)] == p or p[:len(core)] == core) for p in prefixes):
            out.append(row)
    return sorted(out, key=lambda r: -r.n)


def _by_model(rows, words):
    """Rodzina + numer modelu z tekstu (bez sprawdzania wariantu): „iPhone 13 Pro Max” bez takiego wiersza → wszystkie
    iPhone'y 13."""
    found = [r for r in rows if r.number and r.number in words and set(r.family) <= words]
    return sorted(found, key=lambda r: -r.n)


def family_rows(rows, title, description="", category=None, limit=FAMILY_LIMIT, title_only=False):
    """Wiersze tabeli dla oferty → (wiersze, poziom 0-5, dopasowane do tytułu). Poziomy: 1 - tytuł podaje model
    (wiersz pasujący + inne warianty tego modelu), 2 - tytuł podaje rodzinę i numer modelu, 3 - model z opisu,
    4 - sama rodzina („iPhone”), 5 - sama marka (pierwsze słowo nazw wierszy, np. „huawei”), 0 - nic."""
    title_words = set(tokens(title))
    texts = [(title_words, title, 1)]
    if not title_only and description:
        desc = (description or "")[:DESCRIPTION_CHARS]
        texts.append((title_words | set(tokens(desc)), f"{title}\n{desc}", 3))
    for words, text, level in texts:
        exact = match_words(rows, words, category)
        found = exact + [r for r in match_words(rows, words, category, relaxed=True) if r not in exact]
        if category == "laptop" and not found:
            found, with_ram = laptop_rows(rows, text)
            if found:
                return found[:limit], (level if with_ram or level == 3 else 2), found[:1] if with_ram else []
        if found:
            # ✓ (dopasowane) - dokładne; bez nich te bez pojemności („iPhone 13” → każdy iPhone 13 … GB)
            return (found + _siblings(rows, found))[:limit], level, exact or found
        found = _by_model(rows, words)
        if found:
            return found[:limit], (2 if level == 1 else 3), []
    if title_only:
        return [], 0, []
    # Rodzina / marka tylko ze słów ≥ 2 liter, które nie są nazwą kategorii („b” z „B550”, „i” z „i5-9400F”, „tab”
    # z „Tab 10 Plus” to nie rodzina).
    def own_word(word):
        return len(word) >= 2 and not word.isdigit() and word not in _GENERIC_FAMILY

    for level, pick in ((4, lambda r, w: any(own_word(x) for x in r.family) and set(r.family) <= w),
                        (5, lambda r, w: r.core_words and own_word(r.core_words[0]) and r.core_words[0] in w)):
        for words, _text, _lvl in texts:
            found = sorted((r for r in rows if pick(r, words)), key=lambda r: -r.n)
            if found:
                return found[:limit], level, []
    return [], 0, []


@dataclass
class Route:
    """Wiersze tabeli dla oferty: kategoria (może być inna niż katalog), wiersze, poziom dopasowania, uwaga."""
    category: str | None
    rows: list
    level: int = 0
    matched: list = None
    note: str = ""
    extra: list = None                 # [(kategoria, wiersze)] - dodatkowe tabele (sąsiednie kategorie)

    @property
    def has_data(self):
        return bool(self.rows or self.extra)


TABLE_LEGEND = """## Tabela cen (dane rynku z Vinted)
W wiadomości <wiersze_tabeli> to wiersze tabeli cen dobrane do oferty (ten sam model i jego warianty, a gdy tytuł \
nie podaje modelu - rodzina albo marka); ✓ = wiersz dopasowany po tytule - sprawdź, czy naprawdę pasuje.
Wiersz = grupa SPRAWNYCH ofert z Vinted (PL + zagranica). ofert = ile wystawionych, p25 / mediana / p75 = ich ceny, \
zniknęło (sprzed.) = ile zniknęło (w tym potwierdzonych sprzedaży), sprzedam za ~ = realna cena sprzedaży w ≤ 7 dni \
(mocniejsza konfiguracja, komplet, gwarancja → bliżej p75; słabsza → bliżej p25; p75 = górna granica), max zakup = \
najwyższy KOSZT ZAKUPU spełniający regułę zysku przy „sprzedam za ~”. ⚠ przy grupie = ponad 10% ofert już kosztuje \
≤ max zakup - grupa miesza tańsze i droższe wersje, więc okazja tylko wtedy, gdy TA wersja jest wyraźnie tańsza od \
podobnych, inaczej score ≤ 6. Ceny to ceny wystawienia (z „Zaproponuj cenę” bywa taniej). Brak pasującego wiersza \
→ oceniaj ostrożnie i uzasadnij cenę sprzedaży.
Koszty sprzedaży: {costs} zł (Podbicie ~26 zł + wysyłka ~20 zł)."""

_MANUAL_RULES = re.compile(r"^-+\s*ZASADY DODATKOWE\s*-+\s*$(.*?)(?=^Rynkowa minimalna|^-{3}|\Z)", re.M | re.S)
_MANUAL_MODEL = re.compile(r"^\d+\.\s+(RTX[^\n]*)\n(.*?)(?=^\d+\.\s|^Inna karta|^-{3}|\Z)", re.M | re.S)
_MANUAL_MIN = re.compile(r"\*\*(RTX[^:*]+):\*\*\s*([^\n]+)")


def manual_rules(text):
    """Sekcja „ZASADY DODATKOWE” ręcznych wytycznych laptopów (stan wizualny, czerwone flagi) - bez starej reguły
    zysku i progów z 26.10, które zastąpiła tabela z danych rynku (marża 28% → 25%)."""
    m = _MANUAL_RULES.search(text or "")
    return m.group(1).strip() if m else ""


def manual_notes(text, rows):
    """Notatki użytkownika i minimalna cena rynkowa dla kart graficznych z wierszy oferty (guidelines.md)."""
    numbers = {r.number for r in rows if r.family == ("rtx",) and r.number}
    if not numbers or not text:
        return []
    out = []
    for heading, body in _MANUAL_MODEL.findall(text):
        if numbers & set(re.findall(r"\d{4}", heading)):
            note = next((line.split(":", 1)[1].strip() for line in body.splitlines() if "Notatki:" in line), "")
            if note:
                out.append(f"- {heading.strip()}: {note}")
    for name, price in _MANUAL_MIN.findall(text):
        if numbers & set(re.findall(r"\d{4}", name)):
            out.append(f"- najniższa cena rynkowa {name.strip()}: {price.strip()}")
    return out


def cap_table(text, max_rows):
    """Tabela cen skrócona do `max_rows` pierwszych wierszy (reszta tekstu bez zmian)."""
    if not max_rows:
        return text
    out, in_table, kept, dropped = [], False, 0, 0
    for line in text.splitlines():
        if line.startswith("| grupa"):
            in_table, kept = True, -2                   # nagłówek i |---| nie liczą się
        elif in_table and not line.startswith("|"):
            in_table = False
            if dropped:
                out.append(f"(… i {dropped} mniej licznych grup)")
                dropped = 0
        if in_table:
            kept += 1
            if kept > max_rows:
                dropped += 1
                continue
        out.append(line)
    if dropped:
        out.append(f"(… i {dropped} mniej licznych grup)")
    return "\n".join(out)


class _File:
    """Plik wczytywany ponownie, gdy zmieni się data modyfikacji."""

    def __init__(self, path):
        self.path = Path(path)
        self._mtime = None
        self.text = ""
        self.rows = []

    def get(self):
        mtime = self.path.stat().st_mtime              # FileNotFoundError → wołający decyduje
        if mtime != self._mtime:
            text = _HTML_COMMENT.sub("", self.path.read_text(encoding="utf-8")).strip()
            if self._mtime is not None:
                log.info("[AI] Wczytano zmienione wytyczne z %s", self.path)
            self._mtime, self.text, self.rows = mtime, text, parse_rows(text)
        return self


class GuideBook:
    def __init__(self, guides_dir, fallback_file):
        self.dir = Path(guides_dir) if guides_dir else None
        self.fallback = _File(fallback_file)
        self._files = {}

    def path(self, category):
        """Plik wytycznych dla kategorii (None → brak wytycznych dla tej kategorii)."""
        if category and self.dir is not None:
            own = self.dir / f"{category}.md"
            if own.exists():
                return own
        if category in (None, "laptop"):
            return self.fallback.path
        return None

    def _file(self, category):
        path = self.path(category)
        if path is None:
            raise FileNotFoundError(f"brak wytycznych {self.dir / f'{category}.md'} - uruchom w analizatorze "
                                    f"sniper-guide (automat kopiuje je do SNIPER_GUIDES_DIR)")
        if path == self.fallback.path:
            return self.fallback.get()
        return self._files.setdefault(path, _File(path)).get()

    def text(self, category, max_rows=None):
        """Tekst dla modelu. Laptopy z guides/laptop.md (dane rynku) + ręczne guidelines.md użytkownika jako uzupełnienie
        (notatki o modelach, stan, czerwone flagi, karty spoza tabeli rynku). max_rows - najwyżej tyle wierszy tabeli
        cen (najliczniejsze; tryb „wszystkie”)."""
        text = cap_table(self._file(category).text, max_rows)
        if category == "laptop" and self.path(category) != self.fallback.path:
            try:
                manual = self.fallback.get().text
            except OSError:
                manual = ""
            if manual:
                text += "\n\n## Uzupełnienie: ręczne wytyczne użytkownika (laptopy)\n\n" + manual
        return text

    def short_text(self, category, title, limit=8):
        """Wytyczne bez pełnej tabeli: zasady (do „## Ceny”) + nagłówek tabeli + wiersze pasujące do tytułu
        (SNIPER_AI_GUIDE_ROWS=dopasowane - mniej tokenów; dzień testowy 29.10)."""
        text = self.text(category)
        cut = text.find("\n## Ceny")
        if cut < 0:
            return text
        lines = text.splitlines()
        header = next((i for i, line in enumerate(lines) if line.startswith("| grupa")), None)
        rows = self.match(category, title) or self.match(category, title, relaxed=True)
        out = [text[:cut].rstrip(), "", "## Ceny – tylko wiersze pasujące do tytułu (pozostałe pominięte; dane z bazy "
               "są w <mediany> / <porownywalne>)"]
        if header is not None and rows:
            out += ["", lines[header], lines[header + 1]] + [r.line for r in rows[:limit]]
        else:
            out += ["", "Brak pasującego wiersza w tabeli."]
        return "\n".join(out)

    def match(self, category, title, relaxed=False):
        try:
            return match(self._file(category).rows, title, category, relaxed)
        except OSError:
            return []

    def rows(self, category):
        try:
            return self._file(category).rows
        except OSError:
            return []

    def header(self, category):
        try:
            return parse_header(self._file(category).text)
        except OSError:
            return ()

    def route(self, category, title, description="", limit=FAMILY_LIMIT):
        """Wiersze tabeli dla oferty (rodzina) + ewentualna zmiana kategorii, gdy tytuł wskazuje sąsiednią kategorię
        (oferta w złym katalogu). Kolejność rozstrzygania - patrz opis modułu."""
        if not category:
            return Route(category, [], 0, [])
        rows, level, matched = family_rows(self.rows(category), title, description, category, limit)
        own = Route(category, rows, level, matched)
        neighbors = [c for c in NEIGHBORS.get(category, ()) if self.rows(c)]
        if not neighbors:
            return own
        words = set(tokens(title))
        hinted = {c for c, w in CATEGORY_WORDS.items() if w & words}
        title_only = {c: family_rows(self.rows(c), title, "", c, limit, title_only=True) for c in neighbors}

        def strength(level_, matched_):
            return (level_ if level_ in (1, 2) else 9, -max((len(r.words) for r in matched_), default=0))

        if category in PARTS:
            # Procesor i chipset płyty w jednym tytule = zestaw (także wystawiony jako procesor / płyta).
            parts = {**title_only, category: family_rows(rows, title, "", category, limit, title_only=True)}
            strong = {c: v for c, v in parts.items() if v[1] in (1, 2)}
            if "motherboard" in strong and {"cpu", "bundle"} & set(strong):
                main = "bundle" if "bundle" in strong else category
                main_rows, _lvl, main_matched = strong.get(main, (rows, level, matched))
                note = "" if main == category else "tytuł ma procesor i płytę główną - oceniam jako zestaw"
                return Route(main, main_rows[:limit // 2 or 1], 1, main_matched, note,
                             [(c, v[0][:limit // 3 or 1]) for c, v in strong.items() if c != main])
        best = None
        for c, (rows_c, level_c, matched_c) in title_only.items():
            if level_c not in (1, 2):
                continue
            better = strength(level_c, matched_c) < strength(level, matched)
            hint = c in hinted and category not in hinted
            if (better or hint) and (best is None or strength(level_c, matched_c) < strength(best[2], best[3])):
                best = (c, rows_c, level_c, matched_c)
        if best:
            return Route(best[0], best[1], best[2], best[3], f"oferta z katalogu „{category}”, tytuł pasuje do "
                                                          f"kategorii „{best[0]}” - oceniam w tej kategorii")
        if level in (1, 2, 3):
            return own
        # Słabe dopasowanie (rodzina / marka / nic): słowa kategorii rozstrzygają, inaczej wiersze z obu tabel
        # („Huawei” bez modelu - telefony i tablety; „Huawei tab” - tylko tablety).
        weak = {c: family_rows(self.rows(c), title, description, c, limit) for c in neighbors}
        hinted_other = sorted((c for c in neighbors if c in hinted), key=lambda c: not weak[c][0])
        if hinted_other and category not in hinted:
            # Nawet bez wierszy: kategoria wskazana słowami tytułu (dane do oceny dadzą mediany z bazy tej kategorii).
            c = hinted_other[0]
            return Route(c, weak[c][0], weak[c][1], weak[c][2], f"oferta z katalogu „{category}”, słowa tytułu "
                                                                f"wskazują kategorię „{c}”")
        extra = [(c, v[0][:limit // 2 or 1]) for c, v in weak.items() if v[0] and v[1] in (3, 4, 5)
                 and (not hinted or c in hinted)]
        if extra and not (category in hinted and level):
            return Route(category, rows[:limit // 2 or 1], level, matched, "", extra)
        return own

    def family_text(self, route, columns=("grupa", "ofert", "p25", "mediana", "p75", "zniknęło", "sprzedam",
                                          "max zakup")):
        """Wiersze rodziny dla modelu (<wiersze_tabeli>): zwięzła tabela (bez kolumn PL / zagr., zysk / marża,
        % ofert ≤ max), ✓ = pasuje do tytułu."""
        from .categories import CATEGORY_NAMES

        def table(category, rows, matched=()):
            header = self.header(category)
            keep = [i for i, h in enumerate(header) if any(h.lower().startswith(c) for c in columns)] if header else []
            if not keep:
                return [r.line for r in rows]
            out = ["| " + " | ".join(header[i] for i in keep) + " |", "|" + "---|" * len(keep)]
            for r in rows:
                cells = [r.cells[i] if i < len(r.cells) else "" for i in keep]
                if r in matched:
                    cells[0] = "✓ " + cells[0]
                out.append("| " + " | ".join(cells) + " |")
            return out

        lines = ["<wiersze_tabeli>"]
        if route.note:
            lines.append(f"Uwaga: {route.note}.")
        if not route.has_data:
            lines.append("Brak pasujących wierszy w tabeli cen (model spoza danych rynku) - oceniaj ostrożnie.")
        if route.rows:
            name = CATEGORY_NAMES.get(route.category, route.category)
            lines.append(f"Tabela: {name} (dopasowanie: {LEVELS.get(route.level, '?')}; ✓ = pasuje do tytułu)")
            lines += table(route.category, route.rows, route.matched or ())
        for category, rows in route.extra or ():
            lines.append(f"Tabela: {CATEGORY_NAMES.get(category, category)} (też możliwe)")
            lines += table(category, rows)
        if route.category == "laptop" and route.rows:
            notes = manual_notes(self._manual_text(), route.rows)
            if notes:
                lines += ["Notatki użytkownika o kartach:"] + notes
        lines.append("</wiersze_tabeli>")
        return "\n\n" + "\n".join(lines)

    def _manual_text(self):
        try:
            return self.fallback.get().text
        except OSError:
            return ""

    def rules_text(self, category, costs=46.0):
        """Stała część dla modelu w trybie „rodzina” (w instrukcji systemowej - ta sama dla wszystkich ofert kategorii,
        więc trafia w pamięć podręczną dostawcy AI): legenda tabeli; laptopy - zasady dodatkowe użytkownika."""
        text = TABLE_LEGEND.format(costs=f"{costs:g}")
        if category in (None, "laptop"):
            manual = self._manual_text()
            rules = manual_rules(manual)
            if category is None or not self.rows("laptop"):
                rules = manual                 # bez tabeli z danych rynku - całe ręczne wytyczne, jak dawniej
            if rules:
                text += "\n\n## Zasady użytkownika (laptopy)\n\n" + rules
        return text

    def gate(self, category, title, price, factor):
        """Powód pominięcia oferty przed AI (za droga wg tabeli) albo None."""
        if not factor or price is None:
            return None
        rows = [r for r in self.match(category, title) if r.max_buy]
        if rows:
            # Kilka równie dokładnych wierszy (iPad Air (2020) / (2022) przy tytule bez roku) → najwyższy próg.
            top = [r for r in rows if len(r.words) == len(rows[0].words)]
            best = max(top, key=lambda r: r.max_buy)
            limit, name = best.max_buy, best.name
        else:
            # Tytuł bez pojemności: próg NAJWYŻSZY spośród wariantów pamięci (na pewno nie odrzucimy okazji).
            rows = [r for r in self.match(category, title, relaxed=True) if r.max_buy]
            if not rows:
                return None
            best = max(rows, key=lambda r: r.max_buy)
            limit, name = best.max_buy, f"{best.name}, najwyższy z {len(rows)} wariantów"
        if price > limit * factor:
            return f"za drogo wg tabeli: {price:.0f} zł > max zakup {limit:.0f} zł × {factor:g} ({name})"
        return None
