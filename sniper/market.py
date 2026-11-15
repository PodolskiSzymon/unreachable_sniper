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
"""
import re

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


MIN_TRUSTED = 15          # od tylu ofert modelu rotacja jest wiarygodna

# 2026-11-15 (użytkownik): czytników i tabletów graficznych NIE mierzymy rotacją - zapotrzebowanie na nie jest
# znikome (reMarkable 2 z folio wisi, choć cena „jak inne”). Zawsze okazja tylko przy cenie ≤
# SNIPER_LOW_DEMAND_MAX_SHARE (0,3) szacunku sprzedaży - reMarkable 2 przy ~1500 zł → ~450 zł.
LOW_DEMAND = re.compile(
    r"remarkable|kindle|\bkobo\b|pocketbook|\bboox\b|\bonyx\b|tolino|czytni[kc]|e-?book|e-?reader|e-?ink"
    r"|\bwacom\b|cintiq|intuos|\bhuion\b|xp-?pen|kamvas|tablet\s+graficzny|graphic\s+tablet|drawing\s+tablet",
    re.I)


def low_demand(offer):
    """Czytnik / tablet graficzny (tytuł) - towar o znikomym popycie."""
    return bool(LOW_DEMAND.search(getattr(offer, "title", "") or ""))


def in_scope(offer, cfg):
    scope = getattr(cfg, "market_categories", ("phone", "tablet", "console"))
    return getattr(offer, "category", None) in (scope or ())


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
    if low_demand(offer) and getattr(cfg, "low_demand_max_share", 0.3):
        share = getattr(cfg, "low_demand_max_share", 0.3)
        reason = "czytnik / tablet graficzny - znikome zapotrzebowanie"
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
    estimate = max(estimates) * no_box_factor(offer, cfg)
    return (round(estimate * share),
            f"{reason}; okazja tylko przy cenie ≤ {share * 100:.0f}% szacunku sprzedaży ({estimate:.0f} zł)")


def market_note(levels, offer, cfg, span_days=None):
    """Dodatek do <mediany> dla AI: wielkość rynku, rotacja, pudełko, dodatki."""
    lines = []
    market = liquidity(levels, span_days) if in_scope(offer, cfg) and not low_demand(offer) else None
    if low_demand(offer):
        lines.append("Czytnik / tablet graficzny: znikome zapotrzebowanie na rynku - sprzedaż trwa długo; okazja tylko "
                     "przy cenie dużo niższej od mediany.")
    if market:
        lines.append(f"Rynek tego modelu: ~{market['month']:.0f} ofert na miesiąc, zniknęło (sprzedane / usunięte) "
                     f"{market['turnover'] * 100:.0f}% - im mniej ofert i niższa rotacja, tym dłużej się sprzedaje.")
    box = box_status(offer)
    if box == "bez":
        lines.append("Ogłoszenie: BEZ pudełka - używane bez pudełka sprzedają się ~10-20% taniej niż mediana; program "
                     f"liczy wycenę × {getattr(cfg, 'no_box_factor', 0.88):g}.")
    elif box == "jest":
        lines.append("Ogłoszenie: z pudełkiem / kompletem - zwykle szybsza sprzedaż, cena jak mediana (nie wyżej).")
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
