"""Wytyczne per kategoria (D5, 2026-10-25): `<SNIPER_GUIDES_DIR>/<kategoria>.md` z analizatora rynku.

* `GuideBook.text(kategoria)` - tekst wytycznych dla modelu AI (komentarze HTML wycięte, plik wczytywany ponownie po
  zmianie - analizator podmienia go co 2 dni bez restartu Snipera). Laptopy bez `guides/laptop.md` i oferty bez
  kategorii (dawny tryb jednego katalogu) → dotychczasowy `SNIPER_AI_GUIDELINES` (guidelines.md).
* `GuideBook.match(kategoria, tytuł)` - „karta rynku”: wiersze tabeli cen pasujące do tytułu (lokalnie, zero kosztów).
  Wiersz pasuje, gdy wszystkie jego słowa są w tytule, a tytuł nie ma słowa wariantu, którego wiersz nie ma
  (iPhone 13 **Pro** nie dostanie progu zwykłego iPhone 13). Najbardziej szczegółowy wiersz wygrywa.
* Bramka ceny: cena łączna > `max zakup` pasującego wiersza × `SNIPER_AI_PRICE_GATE` (domyślnie 1,25) → oferta nie
  idzie do AI (na pewno nie okazja, a przy 7 katalogach to większość ruchu). Brak dopasowania → idzie do AI.
"""
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("sniper.guides")

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Słowa, które zmieniają model / cenę - gdy są w tytule, a nie w wierszu, wiersz nie pasuje. Laptopy: wiersze to karty
# graficzne, więc tylko warianty karty („Legion 5 Pro” to nadal RTX 4060).
_GPU_VARIANTS = {"ti", "super", "xt", "xtx"}
_MODEL_VARIANTS = {"pro", "max", "plus", "ultra", "mini", "air", "slim", "digital", "oled", "lite", "fe", "s", "e",
                   "a", "fold", "flip", "edge", "neo", "x", "xl", "xs", "xr"}
VARIANT_WORDS = {"laptop": _GPU_VARIANTS, "gpu": _GPU_VARIANTS, "ram": set(), "storage": set()}
# Słowa z nazw wierszy bez znaczenia dla dopasowania.
_NOISE = {"gb", "tb", "ram", "zintegrowana", "grafika", "laptop", "pc", "intel", "core", "amd", "ryzen", "apple"}


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

    @property
    def words(self):
        # Laptopy ze zintegrowaną grafiką (linia CPU + RAM) - za luźne do dopasowania po tytule (i7 vs „5 lat”).
        if "zintegrowana" in self.name.lower():
            return []
        return [t for t in tokens(self.name) if t not in _NOISE]

    @property
    def core_words(self):
        """Słowa bez pojemności (128 GB, 1 TB) - do luźnego dopasowania, gdy tytuł nie podaje pamięci."""
        raw = tokens(self.name)
        sizes = {i for i, t in enumerate(raw[:-1]) if t.isdigit() and raw[i + 1] in ("gb", "tb")}
        return [t for i, t in enumerate(raw) if i not in sizes and t not in _NOISE] if self.words else []


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
                columns = (lower.index("max zakup"), next(i for i, c in enumerate(lower) if c.startswith("sprzedam")))
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        buy_i, sale_i = columns
        if len(cells) > max(buy_i, sale_i):
            rows.append(Row(cells[0], _num(cells[sale_i]), _num(cells[buy_i]), line))
    return rows


def match(rows, title, category=None, relaxed=False):
    """Pasujące wiersze, od najbardziej szczegółowego. relaxed - bez wymogu pojemności (iPhone 13 bez „128 GB”
    pasuje do każdego iPhone 13 … GB, ale nie do iPhone 13 Pro)."""
    words = set(tokens(title))
    variants = VARIANT_WORDS.get(category, _MODEL_VARIANTS | _GPU_VARIANTS)
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

    def text(self, category):
        """Tekst dla modelu. Laptopy z guides/laptop.md (dane rynku) + ręczne guidelines.md użytkownika jako uzupełnienie
        (notatki o modelach, stan, czerwone flagi, karty spoza tabeli rynku)."""
        text = self._file(category).text
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

    def gate(self, category, title, price, factor):
        """Powód pominięcia oferty przed AI (za droga wg tabeli) albo None."""
        if not factor or price is None:
            return None
        rows = [r for r in self.match(category, title) if r.max_buy]
        if rows:
            limit, name = rows[0].max_buy, rows[0].name
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
