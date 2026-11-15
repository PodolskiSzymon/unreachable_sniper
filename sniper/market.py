"""Reguły rynku po ocenie AI (2026-11-15, doświadczenia użytkownika z zakupów z alertów):

1. Płynność - nie kupujemy rzeczy, których rynek jest mały („reMarkable 2 z klawiaturą folio” dostał 9-10/10, a
   nikt go nie chce kupić). Miara z `comparables.db`: ile ofert tego modelu (ten sam stan) pojawia się na miesiąc,
   z najogólniejszego poziomu median. Kopia bazy 07.11 (używane, ~27 dni): reMarkable 2 ~44, Galaxy Tab A 10.1 ~26,
   Kindle ~6, a Galaxy A17 ~121, MatePad 11 ~109, S23 ~580, iPhone 12 ~3500. Rynek mniejszy niż
   `SNIPER_MIN_MARKET_MONTH` (domyślnie 60 / mies.) = okazja tylko przy cenie ≤ `SNIPER_NICHE_MAX_SHARE` (domyślnie
   0,5) szacunku sprzedaży - wtedy da się sprzedać szybko z dużym zapasem.
2. Rotacja - udział ofert, które zniknęły (sprzedane / usunięte), w całym rynku modelu. Szybko schodzące w bazie:
   iPad A16 62%, Steam Deck 56%, S23 36%; wolne: Xiaomi 12T 21%, Huawei P30 20%, MatePad 23%, Xperia 22% (pokrywa się
   z zakupami użytkownika: iPad A16 sprzedany w 24 h, S23 szybko, MatePad wolniej). Poniżej `SNIPER_MIN_TURNOVER`
   (0,25) - ostrzeżenie i ocena o 1 niżej (bez odbierania okazji).
3. Pudełko - na kopii bazy (używane) oferty z wyraźnym „bez pudełka” są tańsze o ~10-20% (iPhone 13: 753 vs 884 zł,
   S23: 917 vs 1101, Switch OLED: 925 vs 1041), a „z pudełkiem” ≈ bez informacji, ale schodzą szybciej (S23: 52% vs
   34% zniknęło, iPad 9/10: 50% vs 38%). Oferta „bez pudełka” → wycena × `SNIPER_NO_BOX_FACTOR` (0,88).
   Z pudełkiem / kompletem - informacja dla AI i w alercie (szybsza sprzedaż), bez podnoszenia ceny.
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


def niche_limit(levels, offer, cfg, span_days=None):
    """Mały rynek → (max cena zakupu, opis) dla bramki przed AI; None, gdy rynek wystarczający / brak danych."""
    minimum = getattr(cfg, "min_market_month", 60.0)
    share = getattr(cfg, "niche_max_share", 0.5)
    market = liquidity(levels, span_days)
    if not minimum or not share or market is None or market["month"] >= minimum:
        return None
    estimates = [lv["estimate"] for lv in levels if lv.get("estimate")]
    if not estimates:
        return None
    estimate = max(estimates) * no_box_factor(offer, cfg)
    return (round(estimate * share),
            f"mały rynek: ~{market['month']:.0f} ofert tego modelu na miesiąc (próg {minimum:g}) - okazja tylko przy "
            f"cenie ≤ {share * 100:.0f}% szacunku sprzedaży ({estimate:.0f} zł)")


def market_note(levels, offer, cfg, span_days=None):
    """Dodatek do <mediany> dla AI: wielkość rynku, rotacja, pudełko, dodatki."""
    lines = []
    market = liquidity(levels, span_days)
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

    market = liquidity(levels, span_days)
    if market is None:
        return ev
    ev["market"] = {"month": round(market["month"]), "turnover_pct": round(market["turnover"] * 100),
                    "box": box_status(offer)}
    flags = ev.setdefault("red_flags", [])
    limit = niche_limit(levels, offer, cfg, span_days)
    cost = purchase_cost(offer)
    if limit and ev.get("is_deal") and cost is not None and cost > limit[0]:
        ev["score_ai"] = ev.get("score_ai", ev["score"])
        ev.update(is_deal=False, score=min(ev["score"], 6.0))
        flags.append(f"Program: {limit[1]}; cena {cost:.0f} zł > {limit[0]} zł - trudno sprzedać, nie okazja")
    minimum_turnover = getattr(cfg, "min_turnover", 0.25)
    if minimum_turnover and market["turnover"] < minimum_turnover and ev.get("score", 0) > 1:
        ev["score_ai"] = ev.get("score_ai", ev["score"])
        ev["score"] = max(1.0, ev["score"] - 1)
        flags.append(f"Program: wolna rotacja - zniknęło tylko {market['turnover'] * 100:.0f}% ofert tego modelu "
                     f"(próg {minimum_turnover * 100:.0f}%), sprzedaż może potrwać; ocena -1")
    return ev
