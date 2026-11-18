"""Reguły rynku po ocenie AI (2026-11-15, doświadczenia użytkownika z zakupów z alertów).

Miary z `comparables.db` (najogólniejszy poziom median: ten sam model i stan):
  * rotacja = zniknięte (sprzedane / usunięte) / wszystkie oferty modelu,
  * rynek = ofert tego modelu na miesiąc (meta `span_days` z analizatora).
Kopia bazy 07.11, używane, rotacja: iPad A16 50%, Steam Deck 39%, Galaxy A17 31%, S23 25%, reMarkable 24%,
iPhone 12 18%, MatePad 11%, Xiaomi 12T 11%, iPhone 8 7%, tablety graficzne (Wacom, Huion…) 1% - pokrywa się
z zakupami użytkownika (iPad A16 w 24 h, S23 szybko, iPhone 12 słabo, MatePad wolno). Sama liczba ofert NIE mówi
o popycie (Pixel 10a ~9 / mies. i 38% rotacji, iPhone 8 ~520 / mies. i 7%), dlatego:

1. Nisza - rotacja < `SNIPER_NICHE_TURNOVER` (0,10) przy ≥ 15 ofertach albo rynek < `SNIPER_MIN_MARKET_MONTH`
   (10 / mies.) i rotacja < `SNIPER_MIN_TURNOVER` → okazja tylko przy cenie ≤ `SNIPER_NICHE_MAX_SHARE` (0,5)
   szacunku sprzedaży (też bramka przed AI). Tylko kategorie z `SNIPER_MARKET_CATEGORIES` (telefony, tablety -
   także czytniki i tablety graficzne -, konsole); podzespoły i laptopy (mało ofert na model, a schodzą) - nie.
2. Wolna rotacja < `SNIPER_MIN_TURNOVER` (0,20) - ostrzeżenie i ocena o 1 niżej (bez odbierania okazji).
3. Pudełko - używane z wyraźnym „bez pudełka” ~10-20% taniej (iPhone 13: 753 vs 884 zł, S23: 917 vs 1101, Switch
   OLED: 925 vs 1041), „z pudełkiem” ≈ bez informacji, ale schodzi szybciej (S23: 52% vs 34% zniknęło) → oferta
   „bez pudełka” = wycena × `SNIPER_NO_BOX_FACTOR` (0,88).
4. Czytniki i tablety graficzne (LOW_DEMAND) - bez mierzenia rotacją: zawsze okazja tylko przy cenie ≤
   `SNIPER_LOW_DEMAND_MAX_SHARE` (0,3) szacunku (decyzja użytkownika: znikome zapotrzebowanie; reMarkable 2 + folio
   dostał 9-10/10 i wisi). Do tego reguła E dla AI: dodatki (folio, klawiatura) nie podnoszą wyceny ponad medianę.
5. (2026-11-16, analyse_data/POPYT.md) Popyt zamiast rotacji, gdy comparables.db ma flagi popytu (analizator od
   16.11): rotacja mieszała sprzedaże z usunięciami (reMarkable 24% = S23 25%). `demand` - z ofert modelu wystawionych
   w oknie danych: ile zniknęło w tydzień (udział) i ile takich zniknięć na tydzień (kupujący), ile wisi ≥ 3 tyg.,
   ile potwierdzonych sprzedaży → klasa gorący / normalny / wolny / martwy (kopia 07.11: iPad A16 i PS5 gorące,
   S23, iPhone 12, Steam Deck normalne, Tab S6 Lite wolny, MatePad 11 i tablety graficzne martwe). Skutki: martwy =
   okazja tylko ≤ `SNIPER_DEMAND_DEAD_MAX_SHARE` (0,5) szacunku (też bramka), wolny = zysk × 1,5 i ocena -1, gorący
   = ocena +0,5 i podpowiedź ceny wystawienia; wycena AI przycinana do szacunku × 1,2 / 1,1 / 1,0 wg klasy
   (`value_factor`). Wariant: bateria < 80% × 0,95 (telefony), gwarancja / faktura × 1,05 (`price_factor`).
"""
import re
import unicodedata

# Stopka tekstu ogłoszenia: „bez pudełka”, „no box”, „fără cutie”… - pierwszeństwo przed wzmianką o pudełku.
NO_BOX = re.compile(
    r"bez\s+(?:oryginalnego\s+|org\.?\s+)?(?:pude[łl]k|opakowani|kartonu|boxa?\b)|\bno\s+box\b|without\s+(?:the\s+)?box"
    r"|(?:nie\s+mam|nie\s+posiadam|brak|brakuje)\s+(?:oryginalnego\s+)?(?:pude[łl]k|opakowani|kartonu)"
    r"|ohne\s+(?:ovp|verpackung|karton)|f[aă]r[aă]\s+cutie|bez\s+krabice|doboz\s+n[ée]lk[üu]l|nincs\s+doboz"
    r"|be\s+d[ėe][žz]ut|utan\s+(?:kartong|förpackning|låda)", re.I)
BOX = re.compile(
    r"pude[łl]k|\bbox(?:ie|em)?\b|full\s?box|karton|oryginalne\s+opakowanie|w\s+opakowaniu|\bkomplet\b|\bovp\b"
    r"|originalverpackung|\bcutie\b|krabic|doboz|d[ėe][žz]ut|kartong|förpackning|scatola|emballage", re.I)

def box_status(offer):
    """'bez' (wyraźnie bez pudełka), 'jest' (pudełko / komplet), None (nie wiadomo) - z tytułu i opisu."""
    text = f"{getattr(offer, 'title', '') or ''}\n{getattr(offer, 'description', '') or ''}"
    if NO_BOX.search(text):
        return "bez"
    if BOX.search(text):
        return "jest"
    return None


def liquidity(levels, span_days=None):
    """Rynek modelu z najogólniejszego poziomu median (ten sam model i stan) → {'month', 'turnover', 'n'} albo None."""
    if not levels or not span_days or span_days <= 0:
        return None              # stary comparables.db bez okresu danych - liczby ofert nie da się porównać z progiem
    general = max(levels, key=lambda lv: (lv.get("n_active") or 0) + (lv.get("n_gone") or 0))
    n = (general.get("n_active") or 0) + (general.get("n_gone") or 0)
    if n <= 0:
        return None
    return {"n": n, "month": n * 30.0 / span_days, "turnover": (general.get("n_gone") or 0) / n}


def no_box_factor(offer, cfg):
    factor = getattr(cfg, "no_box_factor", 0.88)
    return factor if factor and factor < 1 and box_status(offer) == "bez" else 1.0


def _norm(text):
    text = unicodedata.normalize("NFKD", (text or "").lower().replace("ł", "l"))
    return re.sub(r"\s+", " ", "".join(ch for ch in text if not unicodedata.combining(ch)))


# Jak analyse_data/analyzer/extract.py: BATTERY (kondycja baterii w tytule / opisie).
BATTERY_WORDS = r"(?:bateri\w*|battery|akku\w*|akum\w*|baterie|baterija|akkumulator\w*|kondycj\w*|health|zdravi\w*|" \
                r"kapacit\w*|capacit\w*|stan baterii|zdrowie)"
BATTERY = re.compile(BATTERY_WORDS + r"[^0-9%\n]{0,25}?(\d{2,3})\s*%|(\d{2,3})\s*%[^\n]{0,15}?" + BATTERY_WORDS)
WARRANTY = re.compile(r"gwarancj|warranty|garanti|garanzia|zaruk|faktur|paragon|dowod zakupu|receipt|invoice")
NO_WARRANTY = re.compile(r"(?:bez|brak|nie ma|nie posiada\w*|po)\s+(?:\w+\s+)?gwarancj|gwarancj\w*\s+(?:sie\s+)?"
                         r"(?:skonczy|wygas)|no warranty|out of warranty|without (?:a )?(?:warranty|receipt)"
                         r"|(?:bez|brak|nie mam)\s+(?:\w+\s+)?(?:faktur|paragon)")


def battery_pct(offer):
    """Kondycja baterii (40-100%) z tytułu albo opisu; None = brak informacji."""
    for part in (getattr(offer, "title", ""), getattr(offer, "description", "")):
        match = BATTERY.search(_norm(part))
        if match:
            pct = int(match.group(1) or match.group(2))
            if 40 <= pct <= 100:
                return pct
    return None


def has_warranty(offer):
    """„Gwarancja” / faktura / paragon w ogłoszeniu (bez „bez gwarancji”, „po gwarancji”, „brak paragonu”)."""
    text = _norm(f"{getattr(offer, 'title', '') or ''}\n{getattr(offer, 'description', '') or ''}")
    return bool(WARRANTY.search(text)) and not NO_WARRANTY.search(text)


def price_factor(offer, cfg):
    """(mnożnik wyceny, opisy) z wariantu ogłoszenia: bez pudełka, słaba bateria (telefon), gwarancja / faktura."""
    factor, notes = 1.0, []
    box = no_box_factor(offer, cfg)
    if box < 1:
        factor *= box
        notes.append(f"bez pudełka - wycena × {box:g}")
    low = getattr(cfg, "low_battery_factor", 0.95)
    pct = battery_pct(offer) if getattr(offer, "category", None) == "phone" and low and low < 1 else None
    if pct is not None and pct < getattr(cfg, "low_battery_pct", 80.0):
        factor *= low
        notes.append(f"bateria {pct}% - schodzi wolniej i taniej, wycena × {low:g}")
    plus = getattr(cfg, "warranty_factor", 1.05)
    if plus and plus > 1 and has_warranty(offer):
        factor *= plus
        notes.append(f"gwarancja / faktura - schodzi szybciej, wycena × {plus:g}")
    return factor, notes


MIN_TRUSTED = 15          # od tylu ofert modelu rotacja jest wiarygodna

# 2026-11-15 (użytkownik): czytników i tabletów graficznych NIE mierzymy rotacją - zapotrzebowanie na nie jest
# znikome (reMarkable 2 z folio wisi, choć cena „jak inne”). Zawsze okazja tylko przy cenie ≤
# SNIPER_LOW_DEMAND_MAX_SHARE (0,3) szacunku sprzedaży - reMarkable 2 przy ~1500 zł → ~450 zł.
LOW_DEMAND = re.compile(
    r"remarkable|kindle|\bkobo\b|pocketbook|\bboox\b|\bonyx\b|tolino|czytni[kc]|\be-?book|\be-?reader|\be-?ink\b"
    r"|\bwacom\b|cintiq|intuos|\bhuion\b|xp-?pen|kamvas|tablet\s+graficzny|graphic\s+tablet|drawing\s+tablet",
    re.I)


def low_demand(offer):
    """Czytnik / tablet graficzny (tytuł) - towar o znikomym popycie. 2026-11-18: „e-book” tylko od początku słowa -
    „Notebook” i „Chromebook” łapały się jako czytnik (laptop tracił okazję poza ceną ≤ 30% szacunku)."""
    return bool(LOW_DEMAND.search(getattr(offer, "title", "") or ""))


# 2026-11-18 (decyzja użytkownika: dolne 10% cen „tylko jeśli chodzi o wiek, bo te stare MacBooki słabo schodzą”).
# Kopia 07.11, używane laptopy bez osobnej karty - nowa oferta znika w tydzień: Intel ≤ 5 gen. 5%, 6-7 gen. 11%,
# MacBook z Intelem ≤ 2015 16%, 2016-17 23%; dla porównania MacBook M1 38%, M2+ 40%, Intel 8-9 gen. 23%, 12+ gen. 25%.
# Do tego 35-73% ofert tych roczników kosztuje < 700 zł - poniżej progu crawla analizatora (laptopy od 700 zł), więc
# mediany z bazy pokazują tylko droższą część rynku (MacBook ≤ 2015: mediana ≥ 700 zł 889 zł, całego rynku 535 zł).
# Stary = Intel do 7. generacji bez osobnej karty (także MacBooki z Intelem do 2017 - mają procesory 5-7 gen.).
# Laptopy z kartą (GTX 9xx / 10xx też) schodzą jak nowsze (28% w tydzień) - bez zmian.
OLD_CPU_GEN = "do 7 gen."
APPLE_SILICON = re.compile(r"\bm[1-5]\b")
MAC_YEAR = re.compile(r"\b(20(?:0[6-9]|1\d|2[0-5]))\b")


def old_laptop(offer, spec=None, cfg=None):
    """Stary laptop, który schodzi wolno (wycena = dolne 10% cen aktywnych ofert) → opis albo None."""
    if cfg is not None and not getattr(cfg, "old_laptop_rules", True):
        return None
    if getattr(offer, "category", None) != "laptop":
        return None
    spec = spec or {}
    if spec.get("gpu") or spec.get("chip"):
        return None
    if spec.get("cpu_gen") == OLD_CPU_GEN:
        return "MacBook z Intelem do 2017" if spec.get("line") else "laptop z procesorem Intel do 7. generacji"
    from .specs import norm
    title = norm(getattr(offer, "title", ""))
    if re.search(r"mac\s*book", title) and not APPLE_SILICON.search(title):
        years = [int(y) for y in MAC_YEAR.findall(title)]
        if years and max(years) <= 2017:
            return f"MacBook z Intelem ({max(years)})"
    return None


def old_reason(levels):
    """Opis starego laptopa z poziomów median (comparables.apply_old_laptop) albo None."""
    return next((lv["old_laptop"] for lv in levels or [] if lv.get("old_laptop")), None)


def in_scope(offer, cfg):
    scope = getattr(cfg, "market_categories", ("phone", "tablet", "console"))
    return getattr(offer, "category", None) in (scope or ())


CLASSES = ("gorący", "normalny", "wolny", "martwy")
CLASS_TEXT = {"gorący": "schodzi szybko", "normalny": "schodzi normalnie, przy cenie jak oferty, które zeszły",
              "wolny": "mało kupujących - sprzedaż potrwa", "martwy": "prawie nikt nie kupuje"}


def demand_on(offer, cfg):
    return bool(getattr(cfg, "demand_rules", True)) and in_scope(offer, cfg)


def demand(levels, cfg=None):
    """Popyt modelu z poziomu median z największą liczbą świeżych ofert (flagi z analizatora od 16.11) → słownik
    {class, share, week, stale, n_fresh, n_sold, n_clear, clear_median, median, estimate} albo None (stary plik).
    share = udział świeżych ofert zniknionych w ≤ 7 dni, week = takich zniknięć na tydzień (~kupujący),
    stale = udział aktywnych wiszących ≥ 3 tyg."""
    with_data = [lv for lv in levels or [] if "n_fresh" in lv]
    if not with_data:
        return None
    level = max(with_data, key=lambda lv: (lv["n_fresh"], lv["n_active"] + lv["n_gone"]))
    fresh, gone7, days = level["n_fresh"], level["n_gone7"], level.get("fresh_days")
    share = gone7 / fresh if fresh else None
    week = gone7 / days * 7 if days else None
    stale = level["n_stale"] / level["n_active"] if level["n_active"] else None
    get = lambda name, default: getattr(cfg, name, default)  # noqa: E731
    klass = None
    if fresh >= get("demand_min_fresh", 6) and week is not None:
        if share >= get("demand_hot_share", 0.5) and week >= get("demand_hot_week", 3.0):
            klass = "gorący"
        elif week < get("demand_dead_week", 0.7) or share < get("demand_dead_share", 0.10):
            klass = "martwy"
        elif week < get("demand_slow_week", 2.0) or share < get("demand_slow_share", 0.15):
            klass = "wolny"
        else:
            klass = "normalny"
    elif level["n_active"] >= 8 and stale is not None and stale >= 0.7:
        klass = "martwy"                      # prawie nic nowego się nie pojawia, a stare oferty wiszą
    sold = level.get("n_sold", 0)
    note = None
    if klass == "martwy" and sold >= 3:
        klass = "wolny"                       # potwierdzone sprzedaże = ktoś jednak kupuje
    # 2026-11-17 (iPad 10 64 GB kupiony za ~900 zł nie schodzi nawet za 1100, choć 8 z 12 nowych ofert „zniknęło”
    # w tydzień - wszystkie usunięte / ukryte, ZERO potwierdzonych sprzedaży): zniknięcie to nie sprzedaż. U
    # użytkownika dobre zakupy (S23, iPad A16) miały potwierdzone sprzedaże, złe (reMarkable, MatePad, iPad 10) - żadnej.
    if klass == "gorący" and sold < get("demand_hot_min_sold", 2):
        klass, note = "normalny", f"„gorący” tylko przy ≥ {get('demand_hot_min_sold', 2):g} potwierdzonych sprzedażach"
    if klass in ("gorący", "normalny") and sold == 0 and level.get("n_clear", 0) >= get("demand_no_sales_clear", 10):
        klass, note = "wolny", "znikają, ale bez żadnej potwierdzonej sprzedaży (usunięcia / oszustwa / poza Vinted)"
    return {"class": klass, "note": note, "share": share, "week": week, "stale": stale, "n_fresh": fresh, "n_sold": sold,
            "n_clear": level.get("n_clear", 0), "clear_median": level.get("clear_median"),
            "median": level.get("median"), "estimate": level.get("estimate")}


def value_factor(levels, offer, cfg):
    """Do ilu × szacunku z danych program przepuszcza wycenę AI: gorący = SNIPER_AI_COMPARABLE_FACTOR (1,2), normalny
    i bez klasy = SNIPER_DEMAND_NORMAL_FACTOR (1,1), wolny / martwy / czytnik = SNIPER_DEMAND_SLOW_FACTOR (1,0).
    Stary plik (bez flag popytu) i kategorie poza zasięgiem - jak dawniej."""
    base = getattr(cfg, "comparable_factor", 1.2)
    if not base:
        return base
    slow = min(base, getattr(cfg, "demand_slow_factor", 1.0) or base)
    if low_demand(offer) and getattr(cfg, "demand_rules", True):
        return slow
    if old_reason(levels):
        return slow               # stary laptop (2026-11-18): wycena AI najwyżej dolne 10% cen ofert
    if any(lv.get("laptop_fast") for lv in levels or []):
        return min(base, getattr(cfg, "laptop_value_factor", 1.1) or base)   # laptop: cena szybkiej sprzedaży × 1,1
    found = demand(levels, cfg) if demand_on(offer, cfg) else None
    if found is None:
        return base
    klass = found["class"]
    if not found.get("n_sold"):
        return slow               # bez potwierdzonej sprzedaży AI nie wycenia ponad dane (2026-11-17, iPad 10)
    if klass == "gorący":
        return base
    if klass in ("wolny", "martwy"):
        return slow
    return min(base, getattr(cfg, "demand_normal_factor", 1.1) or base)


def demand_text(found):
    """Jedna linia o popycie (alert, <rynek>)."""
    if not found:
        return ""
    parts = []
    if found.get("share") is not None:
        parts.append(f"w tydzień schodzi {found['share'] * 100:.0f}% nowych ofert")
    if found.get("week") is not None:
        parts.append(f"~{found['week']:.1f} / tydz.".replace(".", ",", 1))
    if found.get("stale") is not None:
        parts.append(f"{found['stale'] * 100:.0f}% aktywnych wisi ≥ 3 tyg.")
    parts.append(f"potwierdzonych sprzedaży {found.get('n_sold', 0)}")
    klass = found.get("class")
    head = f"{klass} ({CLASS_TEXT[klass]})" if klass else "za mało nowych ofert, by ocenić"
    if found.get("note"):
        parts.append(found["note"])
    return f"{head}: " + ", ".join(parts)


def niche_reason(market, cfg):
    """Opis niszy albo None. Nisza = bardzo niska rotacja przy wiarygodnej próbce, albo prawie brak ofert
    i niska rotacja."""
    turnover_floor = getattr(cfg, "niche_turnover", 0.10)
    slow = getattr(cfg, "min_turnover", 0.20)
    minimum = getattr(cfg, "min_market_month", 10.0)
    if turnover_floor and market["n"] >= MIN_TRUSTED and market["turnover"] < turnover_floor:
        return (f"nisza: zniknęło tylko {market['turnover'] * 100:.0f}% ofert tego modelu "
                f"(~{market['month']:.0f} / mies.) - schodzi bardzo wolno")
    if minimum and market["month"] < minimum and market["turnover"] < (slow or 1.0):
        return (f"nisza: ~{market['month']:.0f} ofert tego modelu na miesiąc i rotacja "
                f"{market['turnover'] * 100:.0f}% - mało kupujących")
    return None


def niche_limit(levels, offer, cfg, span_days=None):
    """Nisza → (max cena zakupu, opis) dla bramki przed AI; None, gdy rynek wystarczający / brak danych /
    kategoria poza zasięgiem reguły."""
    found = demand(levels, cfg) if demand_on(offer, cfg) else None
    if low_demand(offer) and getattr(cfg, "low_demand_max_share", 0.3):
        share = getattr(cfg, "low_demand_max_share", 0.3)
        reason = "czytnik / tablet graficzny - znikome zapotrzebowanie"
    elif found is not None:
        # Plik z flagami popytu: limit tylko dla klasy „martwy” (rotacja nie decyduje).
        share = getattr(cfg, "demand_dead_max_share", 0.5)
        if found["class"] != "martwy" or not share:
            return None
        reason = "martwy popyt - " + demand_text(found)
    else:
        share = getattr(cfg, "niche_max_share", 0.5)
        market = liquidity(levels, span_days)
        if not share or market is None or not in_scope(offer, cfg):
            return None
        reason = niche_reason(market, cfg)
        if not reason:
            return None
    estimates = [lv["estimate"] for lv in levels if lv.get("estimate")]
    if not estimates:
        return None
    estimate = max(estimates) * price_factor(offer, cfg)[0]
    return (round(estimate * share),
            f"{reason}; okazja tylko przy cenie ≤ {share * 100:.0f}% szacunku sprzedaży ({estimate:.0f} zł)")


def market_note(levels, offer, cfg, span_days=None):
    """Dodatek do <mediany> dla AI: wielkość rynku, rotacja, pudełko, dodatki."""
    lines = []
    found = demand(levels, cfg) if demand_on(offer, cfg) and not low_demand(offer) else None
    market = liquidity(levels, span_days) if in_scope(offer, cfg) and not low_demand(offer) and found is None \
        else None
    if low_demand(offer):
        lines.append("Czytnik / tablet graficzny: znikome zapotrzebowanie na rynku - sprzedaż trwa długo; okazja tylko "
                     "przy cenie dużo niższej od mediany.")
    old = old_reason(levels)
    if not old and any(lv.get("laptop_fast") for lv in levels or []):
        lines.append("Laptop: szacunek w medianach = cena szybkiej sprzedaży (dolne 20% cen aktywnych ofert tej "
                     "specyfikacji - po tyle laptopy schodzą w ≤ 3 dni; połowa szybkich sprzedaży była po tej cenie albo "
                     "drożej, tylko co czwarta ≥ 1,1 × tyle). Wyżej najwyżej o 10% i tylko z wyraźnym powodem (lepsza "
                     "konfiguracja niż w grupie, gwarancja, bardzo dobry stan z pudełkiem).")
    if old:
        lines.append(f"Stary laptop ({old}): takie schodzą wolno (nowa oferta znika w tydzień w 5-23% przypadków, "
                     "MacBook z M1 / M2 w ~40%), a większość z nich kosztuje poniżej 700 zł - progu, od którego baza "
                     "zbiera laptopy, więc mediany pokazują tylko droższą część rynku. Szacunek w medianach = dolne "
                     "10% cen aktywnych ofert - nie wyceniaj wyżej.")
    if found:
        lines.append("Popyt na ten model: " + demand_text(found) + ". Szacunek szybkiej sprzedaży w medianach to cena "
                     "ofert, które zeszły (≤ 3 dni od wystawienia albo potwierdzona sprzedaż), a nie mediana aktywnych "
                     "(te często wiszą za drogo) - nie wyceniaj wyżej bez wyraźnego powodu.")
    if market:
        lines.append(f"Rynek tego modelu: ~{market['month']:.0f} ofert na miesiąc, zniknęło (sprzedane / usunięte) "
                     f"{market['turnover'] * 100:.0f}% - im mniej ofert i niższa rotacja, tym dłużej się sprzedaje.")
    box = box_status(offer)
    if box == "bez":
        lines.append("Ogłoszenie: BEZ pudełka - używane bez pudełka sprzedają się ~10-20% taniej niż mediana; program "
                     f"liczy wycenę × {getattr(cfg, 'no_box_factor', 0.88):g}.")
    elif box == "jest":
        lines.append("Ogłoszenie: z pudełkiem / kompletem - zwykle szybsza sprzedaż, cena jak mediana (nie wyżej).")
    for note in price_factor(offer, cfg)[1]:
        if not note.startswith("bez pudełka"):
            lines.append("Ogłoszenie: " + note + ".")
    lines.append("Dodatki (etui, klawiatura / folio, ładowarka, folia, rysik do czytnika) nie podnoszą ceny odsprzedaży "
                 "ponad medianę - najwyżej przyspieszają sprzedaż. Wyjątek: oryginalny rysik / klawiatura Apple do "
                 "iPada (osobno sprzedawalne) - najwyżej +10% mediany.")
    return "\n\n<rynek>\n" + "\n".join(lines) + "\n</rynek>"


def apply_market_rules(ev, offer, cfg, levels, span_days=None):
    """Po regule zysku: mały rynek bez dużego zapasu → nie okazja; wolna rotacja → ostrzeżenie i ocena -1."""
    from .extractor import purchase_cost

    limit = niche_limit(levels, offer, cfg, span_days)
    cost = purchase_cost(offer)
    flags = ev.setdefault("red_flags", [])
    if limit and ev.get("is_deal") and cost is not None and cost > limit[0]:
        ev["score_ai"] = ev.get("score_ai", ev["score"])
        ev.update(is_deal=False, score=min(ev["score"], 6.0))
        flags.append(f"Program: {limit[1]}; cena {cost:.0f} zł > {limit[0]} zł - trudno sprzedać, nie okazja")
    old = old_reason(levels)
    if old:
        flags.append(f"Program: stary laptop ({old}) - takie schodzą wolno, wycena z dolnych 10% cen ofert")
    found = demand(levels, cfg) if demand_on(offer, cfg) and not low_demand(offer) else None
    if found is not None:
        return _apply_demand(ev, offer, cfg, levels, found, cost)
    market = liquidity(levels, span_days)
    if market is None or not in_scope(offer, cfg) or low_demand(offer):
        return ev
    ev["market"] = {"month": round(market["month"]), "turnover_pct": round(market["turnover"] * 100),
                    "box": box_status(offer)}
    minimum_turnover = getattr(cfg, "min_turnover", 0.20)
    if (minimum_turnover and market["n"] >= MIN_TRUSTED and market["turnover"] < minimum_turnover
            and ev.get("score", 0) > 1):
        ev["score_ai"] = ev.get("score_ai", ev["score"])
        ev["score"] = max(1.0, ev["score"] - 1)
        flags.append(f"Program: wolna rotacja - zniknęło tylko {market['turnover'] * 100:.0f}% ofert tego modelu "
                     f"(próg {minimum_turnover * 100:.0f}%), sprzedaż może potrwać; ocena -1")
    return ev


def list_price(levels, ev, offer, cfg, found):
    """Podpowiedź ceny wystawienia przy odsprzedaży: gorący - do mediany aktywnych (schodzi i tak), inne - szacunek
    (cena ofert, które zeszły). Z poziomu wybranego przez AI (median_used), × wariant ogłoszenia."""
    used = ev.get("median_used")
    comps = levels[used - 1] if isinstance(used, int) and 1 <= used <= len(levels) else (levels[0] if levels else {})
    estimate = comps.get("estimate")
    if not estimate:
        return None
    price = max(estimate, comps.get("median") or 0) if found.get("class") == "gorący" else estimate
    return round(price * price_factor(offer, cfg)[0] / 10) * 10


def _apply_demand(ev, offer, cfg, levels, found, cost):
    """Skutki klasy popytu po regule zysku (martwy obsłużył już niche_limit)."""
    from .evaluator import rule_required_profit

    flags = ev.setdefault("red_flags", [])
    klass = found["class"]
    ev["market"] = {"class": klass, "share7_pct": None if found["share"] is None else round(found["share"] * 100),
                    "week": None if found["week"] is None else round(found["week"], 1),
                    "stale_pct": None if found["stale"] is None else round(found["stale"] * 100),
                    "sold": found["n_sold"], "box": box_status(offer), "text": demand_text(found),
                    "list_price": list_price(levels, ev, offer, cfg, found)}
    if klass == "wolny":
        times = getattr(cfg, "demand_slow_profit", 1.5) or 1.0
        value, profit = ev.get("market_value_pln"), ev.get("potential_profit_pln")
        required = None
        if value and profit is not None:
            required = rule_required_profit(profit / value, cfg, value)
        if ev.get("is_deal") and times > 1 and required is not None and profit < required * times:
            ev["score_ai"] = ev.get("score_ai", ev["score"])
            ev.update(is_deal=False, score=min(ev["score"], 6.0))
            flags.append(f"Program: wolny popyt ({demand_text(found)}) - wymagany zysk × {times:g} = "
                         f"{required * times:.0f} zł, a jest {profit:.0f} zł - nie okazja")
        elif ev.get("score", 0) > 1:
            ev["score_ai"] = ev.get("score_ai", ev["score"])
            ev["score"] = max(1.0, ev["score"] - 1)
            flags.append(f"Program: wolny popyt ({demand_text(found)}) - sprzedaż potrwa; ocena -1")
    elif klass == "gorący" and ev.get("is_deal"):
        bonus = getattr(cfg, "demand_hot_bonus", 0.5) or 0
        if bonus:
            ev["score_ai"] = ev.get("score_ai", ev["score"])
            ev["score"] = min(10.0, ev["score"] + bonus)
    return ev
