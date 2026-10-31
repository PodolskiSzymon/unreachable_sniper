"""Wiele kategorii naraz (D5, 2026-10-25): katalogi Vinted, zakresy cen z analizy rynku i nasza kategoria oferty.

`SNIPER_CATALOGS` (sniper/.env):
  * `auto` - katalogi i zakresy cen z `<SNIPER_GUIDES_DIR>/catalogs.json`, który generuje analizator
    (`python -m analyzer sniper-guide` / automat co 2 dni; zmiana pliku działa bez restartu),
  * lista `3580:250-6100*3, 3661:100-3600, 3602` - katalog[:cena_od-cena_do][*waga] (bez zakresu =
    SNIPER_PRICE_FROM/TO; waga = ile razy częściej skanowany niż katalog z wagą 1, domyślnie laptopy 3, reszta 1),
  * puste - jak dawniej: jeden katalog `SNIPER_CATALOG` z `SNIPER_PRICE_FROM` / `SNIPER_PRICE_TO`.

Nasza kategoria (do wyboru wytycznych `guides/<kategoria>.md`) wynika wprost z katalogu. 31.10 (lista
użytkownika): podzespoły mają własne katalogi - RAM 3603, dyski wewnętrzne 3607, karty graficzne 3602, procesory
3599, płyty główne 3600, zestawy płyta + procesor 3601 - zamiast ogólnych 3582 (części i podzespoły), 3583 (nośniki)
i 3584 (akcesoria), w których kategorię zgadywaliśmy z tytułu. Wycofane katalogi są pomijane także wtedy, gdy są
jeszcze w starym `catalogs.json` albo w SNIPER_CATALOGS (ostrzeżenie w logu).
"""
import json
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path

log = logging.getLogger("sniper.categories")

CATEGORY_OF_CATALOG = {3580: "laptop", 3661: "phone", 3728: "tablet", 3025: "console", 3603: "ram", 3607: "storage",
                       3602: "gpu", 3599: "cpu", 3600: "motherboard", 3601: "bundle"}
# Ogólne katalogi sprzed 31.10 (części i podzespoły, nośniki, akcesoria) - nie skanujemy.
RETIRED_CATALOGS = (3582, 3583, 3584)
AI_ENABLED_NAMES = ("laptop", "phone", "tablet", "console", "gpu", "ram", "storage", "cpu", "motherboard",
                    "bundle")                                                          # SNIPER_CATEGORIES
CATEGORY_NAMES = {"laptop": "laptopy", "phone": "telefony", "tablet": "tablety", "console": "konsole",
                  "gpu": "karty graficzne", "ram": "pamięci RAM", "storage": "dyski", "cpu": "procesory",
                  "motherboard": "płyty główne", "bundle": "zestawy płyta + procesor"}

# Zapasowe zakresy, gdy brak catalogs.json (analiza rynku 2026-10-25; podzespoły z dawnych katalogów ogólnych);
# aktualne liczy analizator do catalogs.json (procesory / płyty / zestawy tylko stamtąd - tanie części rzadko
# spełniają regułę zysku 300 zł).
ANALYSIS_CATALOGS = "3580:250-6100,3661:100-3600,3728:100-3200,3025:100-4400,3602:150-2700,3603:150-1400,3607:300-1400"
# Katalogi skanowane są po kolei (jeden skan co SNIPER_POLL_INTERVAL - transfer proxy jak przy jednym katalogu);
# laptopy (największe zyski, najszybciej znikają) domyślnie co 3. skan.
DEFAULT_WEIGHTS = {3580: 3}


@dataclass(frozen=True)
class CatalogSpec:
    catalog_id: int
    price_from: str = ""
    price_to: str = ""
    weight: int = 0                                  # 0 = domyślna (DEFAULT_WEIGHTS, inaczej 1)

    @property
    def share(self):
        return max(1, self.weight or DEFAULT_WEIGHTS.get(self.catalog_id, 1))

    @property
    def categories(self):
        """Nasze kategorie w tym katalogu ([] = katalog spoza listy, dawny tryb jednego katalogu)."""
        try:
            cid = int(self.catalog_id)
        except (TypeError, ValueError):
            return []
        return [CATEGORY_OF_CATALOG[cid]] if cid in CATEGORY_OF_CATALOG else []

    @property
    def label(self):
        names = "/".join(CATEGORY_NAMES.get(c, c) for c in self.categories) or "katalog"
        return f"{self.catalog_id} ({names}) {self.price_from or '0'}-{self.price_to or '∞'} zł"


def parse_weights(text):
    """'laptop:3, phone:3, 3602:2' → {'laptop': 3, 'phone': 3, '3602': 2} (błędne wpisy pomijane)."""
    out = {}
    for part in (p.strip() for p in (text or "").split(",")):
        key, _, value = part.partition(":")
        try:
            out[key.strip().lower()] = max(1, int(value))
        except ValueError:
            if part:
                log.warning("[KATEGORIE] Pomijam błędny wpis SNIPER_CATALOG_WEIGHTS: %r", part)
    return out


def apply_weights(specs, weights):
    """Wagi z SNIPER_CATALOG_WEIGHTS (kategoria albo numer katalogu) zastępują te z catalogs.json / listy."""
    if not weights:
        return list(specs)
    out = []
    for spec in specs:
        found = [weights[k] for k in [str(spec.catalog_id), *spec.categories] if k in weights]
        out.append(replace(spec, weight=max(found)) if found else spec)
    return out


def apply_floor(specs, floor, exact=False):
    """Minimalna cena każdego katalogu (SNIPER_PRICE_FLOOR, domyślnie 300 zł) - prośba użytkownika 2026-10-27:
    najczęściej dodawane tanie oferty (catalogs.json od 50-100 zł) to śmieci, a każda nowa oferta kosztuje szczegóły
    przez proxy i często wywołanie AI. Przy regule zysku (≥ 300 zł, marża ≥ 30-40%) zakup poniżej 300 zł byłby okazją
    tylko przy sprzedaży za ≥ ~650 zł, czyli cenie < ~45% wartości - to zwykle blokada albo oszustwo.
    Katalog z `price_to` poniżej progu (świadomie tani zakres) zostaje bez zmian. exact=True (tryb auto,
    catalogs.json) - dolna granica DOKŁADNIE równa progowi także wtedy, gdy analizator policzył wyższą (28.10:
    użytkownik widział 400 zł, a chce 300 zł)."""
    floor = _to_float(floor)
    if not floor or floor <= 0:
        return list(specs)
    out = []
    for spec in specs:
        low, high = _to_float(spec.price_from), _to_float(spec.price_to)
        if (low is None or low < floor or exact) and (high is None or high >= floor):
            spec = replace(spec, price_from=_price(floor))
        out.append(spec)
    return out


def wanted_specs(specs, enabled):
    """Katalogi z co najmniej jedną włączoną kategorią (SNIPER_CATEGORIES; puste = wszystkie). Katalog spoza listy
    (tryb jednego katalogu) zostaje zawsze."""
    if not enabled:
        return list(specs)
    return [s for s in specs if not s.categories or set(s.categories) & set(enabled)]


def category_for(catalog_id, title=None, enabled=None):
    """Nasza kategoria oferty, "skip" (kategoria wyłączona w SNIPER_CATEGORIES albo wycofany katalog ogólny - bez
    szczegółów i bez AI) albo None (katalog spoza listy - dawny tryb jednego katalogu, ogólne wytyczne)."""
    try:
        catalog_id = int(catalog_id)
    except (TypeError, ValueError):
        return None
    if catalog_id in CATEGORY_OF_CATALOG:
        category = CATEGORY_OF_CATALOG[catalog_id]
        return category if not enabled or category in enabled else "skip"
    if catalog_id in RETIRED_CATALOGS:
        return "skip"
    return None


def drop_retired(specs):
    """Bez wycofanych katalogów ogólnych (3582 / 3583 / 3584) → (katalogi, pominięte numery)."""
    kept, dropped = [], []
    for spec in specs:
        try:
            retired = int(spec.catalog_id) in RETIRED_CATALOGS
        except (TypeError, ValueError):
            retired = False
        (dropped if retired else kept).append(spec if not retired else spec.catalog_id)
    return kept, dropped


def _to_float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _price(value):
    if value in (None, ""):
        return ""
    value = float(value)
    return str(int(value)) if value == int(value) else str(value)


def parse_catalogs(text, default_from="", default_to=""):
    """'3580:250-6100, 3661, 3602:150-2700' → [CatalogSpec]. Błędny wpis - pomijany z ostrzeżeniem."""
    specs = []
    for part in (p.strip() for p in (text or "").split(",")):
        if not part:
            continue
        part, _, weight = part.partition("*")
        catalog, _, prices = part.partition(":")
        try:
            catalog_id = int(catalog)
            weight = int(weight) if weight.strip() else 0
            low, high = default_from, default_to
            if prices:
                lo, _, hi = prices.partition("-")
                low, high = _price(lo.strip() or None), _price(hi.strip() or None)
            specs.append(CatalogSpec(catalog_id, low, high, weight))
        except ValueError:
            log.warning("[KATEGORIE] Pomijam błędny wpis SNIPER_CATALOGS: %r", part)
    return specs


def load_catalogs_json(path):
    """catalogs.json z analizatora → [CatalogSpec] (pusta lista, gdy brak / błąd)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("[KATEGORIE] Nie wczytałem %s: %s", path, exc)
        return []
    specs = []
    for row in data.get("catalogs") or []:
        try:
            specs.append(CatalogSpec(int(row["catalog_id"]), _price(row.get("price_from")), _price(row.get("price_to")),
                                     int(row.get("weight") or 0)))
        except (KeyError, TypeError, ValueError):
            continue
    return specs


def schedule(specs):
    """Kolejność skanów w jednym cyklu - równomiernie wg wag (laptopy*3 + 2 inne → L a L b L)."""
    current = {i: 0 for i in range(len(specs))}
    total = sum(s.share for s in specs)
    order = []
    for _ in range(total):
        for i, spec in enumerate(specs):
            current[i] += spec.share
        best = max(current, key=lambda i: (current[i], -i))
        current[best] -= total
        order.append(specs[best])
    return order


class CatalogSource:
    """Aktualna lista katalogów. Tryb `auto` czyta catalogs.json ponownie, gdy plik się zmieni (bez restartu)."""

    def __init__(self, setting, guides_dir, single):
        self.setting = (setting or "").strip()
        self.path = Path(guides_dir) / "catalogs.json" if guides_dir else None
        self.single = single                         # CatalogSpec z SNIPER_CATALOG / SNIPER_PRICE_FROM/TO
        self._mtime = None
        self._specs = None
        self._warned_retired = set()

    def get(self):
        specs, dropped = drop_retired(self._get())
        new = set(dropped) - self._warned_retired
        if new:
            self._warned_retired |= new
            log.warning("[KATEGORIE] Pomijam wycofane katalogi ogólne %s (od 31.10 podzespoły mają własne katalogi; "
                        "nowy catalogs.json da `python -m analyzer auto --force`).", ", ".join(map(str, sorted(new))))
        return specs

    def _get(self):
        if self.setting.lower() == "auto":
            try:
                mtime = self.path.stat().st_mtime
            except (OSError, AttributeError):
                if self._specs is None:
                    log.warning("[KATEGORIE] SNIPER_CATALOGS=auto, ale brak %s - używam zakresów z analizy "
                                "z 2026-10-25 (uruchom: python -m analyzer sniper-guide).", self.path)
                    self._specs = parse_catalogs(ANALYSIS_CATALOGS)
                return self._specs
            if mtime != self._mtime:
                specs = load_catalogs_json(self.path)
                if specs:
                    if self._mtime is not None:
                        log.info("[KATEGORIE] Nowe zakresy cen z %s: %s", self.path, "; ".join(s.label for s in specs))
                    self._specs = specs
                elif self._specs is None:
                    self._specs = parse_catalogs(ANALYSIS_CATALOGS)
                self._mtime = mtime
            return self._specs
        if self.setting:
            if self._specs is None:
                self._specs = parse_catalogs(self.setting, self.single.price_from, self.single.price_to) \
                    or [self.single]
            return self._specs
        return [self.single]


# Czym sprawdzić kupioną rzecz (2026-10-25, prośba użytkownika: w powiadomieniu o okazji „czego potrzebuję”).
# Pełna lista z cenami i programami: analyse_data/SPRZET_DO_TESTOW.md. Uszkodzonych nie kupujemy - laptop / telefon /
# tablet sprawdza się sam (włącz, ekran, porty, bateria).
BENCH_GPU = "stanowisko A (AM4: B450 + Ryzen 5 3600 + DDR4 + zasilacz 850 W ATX 3.x + SSD z Windows)"
BENCH_DDR5 = "stanowisko B (LGA1700 DDR5: B660/B760 + Pentium G7400, zasilacz i SSD ze stanowiska A)"
TEST_KITS = {
    "laptop": "nic - włącz, ekran, klawiatura, porty, bateria (ładowarka w zestawie?)",
    "phone": "nic - ładowarka / kabel; sprawdź blokady (iCloud / Google), baterię, aparat",
    "tablet": "nic - ładowarka / kabel; sprawdź blokady (iCloud / Google), baterię",
    "console": "telewizor / monitor HDMI + pad (jeśli nie ma w zestawie)",
    "gpu": BENCH_GPU + "; GPU-Z, FurMark / OCCT 30 min",
    "cpu": ("płyta z tym gniazdem + RAM + zasilacz (AM4: stanowisko A, LGA1700: stanowisko B); POST, Cinebench / "
            "OCCT 15 min, temperatury; sprawdź piny / pady"),
    "motherboard": ("procesor + RAM pasujące do gniazda (AM4: Ryzen 5 3600 + DDR4 ze stanowiska A, LGA1700: Pentium "
                    "G7400 ze stanowiska B) + zasilacz; POST, BIOS, wszystkie sloty RAM i M.2, piny gniazda"),
    "bundle": ("zasilacz + SSD ze stanowiska A (+ RAM, jeśli brak w zestawie); POST, BIOS, Cinebench / OCCT 15 min, "
               "temperatury"),
}


# Klucze sprzętu (SNIPER_TEST_HAVE w .env = co już masz, np. „gpu,ddr4,sodimm_ddr4,nvme,sata”) i co trzeba
# dokupić, gdy go brak. Puste SNIPER_TEST_HAVE = bez oznaczeń ✓ / ⚠ (tylko opis sprzętu).
EQUIPMENT_MISSING = {
    "gpu": "stanowisko z PCIe x16 + zasilacz 850 W ATX 3.x",
    "ddr4": "płyta główna z DDR4 (np. B250 / Z270 + i5-7400)",
    "ddr5": "płyta główna z DDR5 (np. H610M / B660M DDR5 + Pentium G7400)",
    "sodimm_ddr4": "przejściówka SO-DIMM→DIMM DDR4 (do płyty DDR4) albo mini PC z DDR4 SO-DIMM",
    "sodimm_ddr5": "laptop / mini PC z DDR5 SO-DIMM (albo płyta DDR5 + przejściówka DDR5 SO-DIMM→DIMM)",
    "nvme": "gniazdo M.2 na płycie albo obudowa M.2 NVMe → USB-C",
    "sata": "przejściówka SATA → USB",
}


def test_requirement(category, title=""):
    """Klucz potrzebnego sprzętu (EQUIPMENT_MISSING) albo None - laptop / telefon / tablet / konsola nic nie wymagają."""
    text = (title or "").lower()
    if category == "gpu":
        return "gpu"
    if category == "ram":
        ddr5, ddr4 = "ddr5" in text, "ddr4" in text
        laptop = bool(re.search(r"so-?dimm|sodimm|laptop|notebook", text))
        if not (ddr4 or ddr5):
            return None                  # typ nieznany z tytułu - AI dopisze w test_needs
        return ("sodimm_" if laptop else "") + ("ddr5" if ddr5 else "ddr4")
    if category == "storage":
        return "nvme" if re.search(r"nvme|m\.2|m2\b|pcie", text) else "sata"
    return None


def _have():
    import os
    return {x.strip().lower() for x in os.getenv("SNIPER_TEST_HAVE", "").split(",") if x.strip()}


# Konsole przenośne mają własny ekran - telewizor niepotrzebny (Switch: tylko do sprawdzenia stacji dokującej).
_HANDHELD = re.compile(r"\b(?:ds\s?lite|dsi|nds|nintendo ds|[23]ds|switch\s?lite|psp|vita|game\s?boy|gba|"
                       r"steam\s?deck|rog\s?ally|legion\s?go|playstation portal)\b", re.I)
HANDHELD_KIT = ("nic - ładowarka (DS / 3DS / PSP: oryginalna, często brak w zestawie); sprawdź oba ekrany i dotyk, "
                "zawiasy, przyciski, baterię, slot na gry")
SWITCH_KIT = ("nic w trybie przenośnym; telewizor HDMI tylko do sprawdzenia stacji; Joy-Cony: kalibracja gałek "
              "(dryf), ładowarka USB-C")


def test_kit(category, title=""):
    """Krótki opis sprzętu do testu (do powiadomienia) albo None. Z SNIPER_TEST_HAVE: „⚠ BRAK SPRZĘTU: …”, gdy
    potrzebnego sprzętu nie masz, albo „✓ masz sprzęt” na początku."""
    text = (title or "").lower()
    if category == "ram":
        laptop = bool(re.search(r"so-?dimm|sodimm|laptop|notebook", text))
        ddr5 = "ddr5" in text
        if laptop:
            kit = ("laptop / mini PC z DDR5 SO-DIMM (np. Twój laptop na sprzedaż); MemTest86" if ddr5 else
                   "przejściówka SO-DIMM→DIMM na płycie DDR4 albo mini PC z DDR4 SO-DIMM; MemTest86")
        else:
            kit = (BENCH_DDR5 if ddr5 else "płyta DDR4 (stanowisko A)") + "; MemTest86"
    elif category == "storage":
        nvme = bool(re.search(r"nvme|m\.2|m2\b|pcie", text))
        kit = ("gniazdo M.2 na płycie albo obudowa M.2 NVMe → USB-C" if nvme else "przejściówka SATA → USB") + \
            "; CrystalDiskInfo (stan, godziny, zapisane TB) + h2testw (prawdziwa pojemność)"
    elif category == "console" and _HANDHELD.search(text):
        kit = HANDHELD_KIT
    elif category == "console" and re.search(r"\bswitch\b", text):
        kit = SWITCH_KIT
    else:
        kit = TEST_KITS.get(category)
    need, have = test_requirement(category, title), _have()
    if kit and need and have:
        if need in have:
            return "✓ masz sprzęt: " + kit
        return f"⚠ BRAK SPRZĘTU – do kupna potrzebujesz: {EQUIPMENT_MISSING[need]}"
    return kit
