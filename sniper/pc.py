"""Komputery stacjonarne (2026-11-20, prośba użytkownika): wycena po SPECYFIKACJI jak laptopy i - osobno - na CZĘŚCI.

Dawniej PC szły do median po słowach tytułu („Komputer gamingowy”, „PC do gier”) - nic nie trafiało, więc Sniper
pomijał ofertę przed AI („brak danych rynku”) i w ocenach nie było żadnego komputera.

1. Specyfikacja (`desktop_spec`) - karta / procesor + generacja / RAM / dysk, TYMI SAMYMI regułami co analizator
   (analyzer/extract.py: desktop + comparables.laptop_spec; tabela `desktop_specs` w comparables.db). Zmieniasz regułę
   tu → zmień też tam (test DESKTOP_PARITY w obu repo).
2. Części (`part_values`) - karta, procesor, RAM, dysk SSD (mediany z bazy podzespołów: cena szybkiej sprzedaży =
   dolne 20% cen aktywnych), HDD, płyta (po chipsecie z bazy, inaczej stała), zasilacz (wg mocy), obudowa (stała).
   Użytkownik: „obudowa ~100-300 zł, zasilacz ~200-300 zł zależnie od mocy” → SNIPER_PC_CASE_PLN,
   SNIPER_PC_PSU_* (stałe niżej).
3. Wybór (`routes`): domyślnie kupujemy cały komputer i sprzedajemy w całości. Na części tylko, gdy zysk z części jest
   o co najmniej SNIPER_PC_PARTS_EXTRA (200 zł) większy niż zysk ze sprzedaży całości - każda część to osobne
   ogłoszenie (promowanie + wysyłka = koszty reguły zysku za KAŻDĄ część) i dużo więcej pracy. Bez wyceny całości
   (brak median) - zysk z części musi spełniać regułę zysku + te 200 zł.
"""
import re

from .specs import (_GPU_WITH_VRAM, CPU_FAMILY, _cpu, _gb, _gpu, _ram, _storage, cpu_gen_text, gpu_name, norm,
                    normalize)

# ------------------------------------------------------------------------------- specyfikacja (jak analizator)
# Kopie wzorów z analyzer/extract.py (desktop_cpu, CHIPSET_SOCKET, PSU, HDD, DESKTOP_RAM, DESKTOP_DISK).
DESKTOP_CPU = (
    re.compile(r"\b(?:intel\s*)?(?:core\s*)?ultra\s*([579])\s*-?\s*(\d{3}[a-z]{0,2})\b"),
    re.compile(r"\b(?:intel\s*)?(?:core\s*)?(i[3579])\s*-?\s*(\d{4,5})\s*([a-z]{0,2})\b"),
    re.compile(r"\b(?:amd\s*)?ryzen\s*([3579])\s*(?:pro\s*)?-?\s*(\d{4})\s*(x3d|xt|x|ge|g|af|f)?\b"),
    re.compile(r"\bxeon\s*(?:([ew][357]?)\s*-?\s*)?(\d{4})\s*(v\d)?\b"),
    re.compile(r"\b(pentium|celeron)\s*(g\d{3,4}|[gj]?\d{4}[a-z]?)\b"),
)
CHIPSET_SOCKET = {
    **dict.fromkeys(("a320", "b350", "x370", "b450", "x470", "a520", "b550", "x570"), "AM4"),
    **dict.fromkeys(("a620", "b650", "x670", "b850", "x870"), "AM5"),
    **dict.fromkeys(("h610", "b660", "h670", "z690", "b760", "z790"), "LGA1700"),
    **dict.fromkeys(("h410", "b460", "h470", "z490", "h510", "b560", "z590"), "LGA1200"),
    **dict.fromkeys(("h110", "b150", "h170", "z170", "b250", "h270", "z270", "h310", "b360", "b365", "h370", "z370",
                     "z390"), "LGA1151"),
    **dict.fromkeys(("h81", "b85", "h87", "z87", "h97", "z97"), "LGA1150"),
    **dict.fromkeys(("h61", "b75", "h77", "z77", "z68", "p67"), "LGA1155"),
    "x99": "LGA2011-3", "x299": "LGA2066", "x79": "LGA2011",
}
CHIPSET = re.compile(r"(?<![a-z0-9])(?:ga-?)?(" + "|".join(sorted(CHIPSET_SOCKET, key=len, reverse=True)) +
                     r")(?:m|n|i)?(?![0-9])")
PSU = re.compile(r"(?:zasilacz\w*|psu|power\s*supply|netzteil|sursa)[^0-9\n]{0,30}?(\d{3,4})\s*w\b"
                 r"|(\d{3,4})\s*w\b[^\n]{0,20}?(?:zasilacz|psu|80\s*\+|80\s*plus|bronze|silver|gold|platinum)")
HDD = re.compile(r"\b(\d{1,2}(?:[.,]\d)?\s*tb|\d{3,4}\s*gb)\s*(?:dysk\s*)?hdd\b|\bhdd\s*(\d{1,2}\s*tb|\d{3,4}\s*gb)\b")
DESKTOP_RAM = re.compile(r"\b(8|16|32|64|128)\s*gb(?!\s*(?:vram|gddr))\b")
DESKTOP_DISK = re.compile(r"\b(?:ssd|nvme|m\.?2|dysk)\s*(?:nvme\s*)?(\d{3,4})\s*(?:gb)?\b|\b(\d{3,4})\s*gb\s*(?:ssd|nvme|m\.?2)\b"
                          r"|\b(\d(?:[.,]\d)?)\s*tb\b")
DDR = re.compile(r"\bddr\s?([345])\b")


def desktop_cpu(text):
    """Model procesora („i5-9400f”, „ryzen 5 2600x”, „xeon e5-2680 v4”, „pentium g4600”) albo None."""
    text = norm(text)
    for n, pattern in enumerate(DESKTOP_CPU):
        match = pattern.search(text)
        if not match:
            continue
        g = match.groups()
        if n == 0:
            return f"ultra {g[0]} {g[1]}"
        if n == 1:
            return f"{g[0]}-{g[1]}{g[2] or ''}"
        if n == 2:
            return f"ryzen {g[0]} {g[1]}{g[2] or ''}"
        if n == 3:
            return " ".join(x for x in ("xeon", f"{g[0]}-{g[1]}" if g[0] else g[1], g[2]) if x)
        return f"{g[0]} {g[1]}"
    return None


def parts(title, description="", attributes=None):
    """Podzespoły z ogłoszenia PC → słownik (cpu, cpu_line, gpu, ram_gb, ssd_gb, hdd_gb, psu_w, chipset, ram_type).
    Kolejność jak analyzer/extract.py: desktop - pierwsze źródło wygrywa (tytuł przed opisem)."""
    attrs = attributes or {}
    t, d = norm(title), norm(description)
    out = {}

    def put(key, value):
        if value is not None and value != "" and key not in out:
            out[key] = value
    for text in (t, d):
        put("cpu", desktop_cpu(text))
        bare = _GPU_WITH_VRAM.sub(" ", text)
        match = DESKTOP_RAM.search(bare)
        if match:
            put("ram_gb", int(match.group(1)))
        match = DESKTOP_DISK.search(HDD.sub(" ", bare))
        if match:
            put("ssd_gb", int(float(match.group(3).replace(",", ".")) * 1000) if match.group(3)
                else int(match.group(1) or match.group(2)))
    # dalej jak laptop (analyzer/extract.py: laptop) - procesor mobilny / generacja słownie, linia, karta, RAM, dysk
    for text in (t, d):
        put("cpu", _cpu(text))
    for text in (t, d):
        put("cpu", cpu_gen_text(text))
    put("cpu_line", attrs.get("computer_cpu_line"))
    if not out.get("cpu"):
        for text in (t, d):
            match = CPU_FAMILY.search(text)
            if match:
                put("cpu_line", f"intel core {match.group(1)}" if match.group(1) else f"amd ryzen {match.group(2)}")
    for text in (t, d):
        put("gpu", _gpu(text))
    ram = _gb(attrs["computer_ram"]) if attrs.get("computer_ram") else None
    put("ram_gb", ram if ram and ram <= 128 else None)
    for text, loose in ((t, True), (d, False)):
        put("ram_gb", _ram(text, loose=loose))
    disk = _gb(attrs["computer_storage_capacity"]) if attrs.get("computer_storage_capacity") else None
    put("ssd_gb", disk if disk and disk >= 32 else None)
    for text, loose in ((t, True), (d, False)):
        put("ssd_gb", _storage(text, loose=loose))
    for text in (t, d):
        match = CHIPSET.search(text)
        if match:
            put("chipset", match.group(1))
        match = PSU.search(text)
        if match:
            watts = int(match.group(1) or match.group(2))
            if 200 <= watts <= 2000:
                put("psu_w", watts)
        match = HDD.search(text)
        if match:
            put("hdd_gb", _gb(match.group(1) or match.group(2)))
        match = DDR.search(text)
        if match:
            put("ram_type", f"ddr{match.group(1)}")
    return out


def desktop_spec(title, description="", attributes=None):
    """Specyfikacja PC do median (jak laptop_spec, bez MacBooków) albo None."""
    p = parts(title, description, attributes)
    return normalize("", p.get("cpu"), p.get("cpu_line"), p.get("gpu"), p.get("ram_gb"), p.get("ssd_gb"))


# ------------------------------------------------------------------------------- części
PSU_STEPS = ((450, 80.0), (600, 150.0), (750, 220.0))   # < 450 W: 80 zł, < 600 W: 150, < 750 W: 220, więcej: 300
PSU_BIG = 300.0


def psu_value(watts, cfg=None):
    if not watts:
        return float(getattr(cfg, "pc_psu_unknown", 120.0))
    return next((v for limit, v in PSU_STEPS if watts < limit), PSU_BIG)


def ram_type(p):
    """DDR4 / DDR5 z tekstu albo z platformy (Ryzen 7000+ i Core Ultra → DDR5, starsze → DDR4); 12-14 gen. Intela
    bywa jednym i drugim → DDR4 (ostrożniej, taniej)."""
    if p.get("ram_type"):
        return p["ram_type"]
    cpu = p.get("cpu") or ""
    if re.match(r"ryzen [3579] [7-9]\d{3}|ultra", cpu) or (p.get("chipset") or "")[:1] in ("a6", "b6", "x6", "b8", "x8"):
        return "ddr5"
    return "ddr4"


def _part_price(levels):
    """Cena szybkiej sprzedaży części z poziomów median (najdokładniejszy): dolne 20% aktywnych (≥ 5 ofert), inaczej
    niższa z (szacunek, p25). → (cena, opis) albo (None, None)."""
    for lv in levels or []:
        if lv.get("n_active", 0) >= 5 and lv.get("p_laptop"):
            return lv["p_laptop"], f"dolne 20% z {lv['n_active']} ofert"
        options = [x for x in (lv.get("estimate"), lv.get("p25")) if x]
        if options:
            return min(options), f"{lv['n_active'] + lv['n_gone']} ofert (mało)"
    return None, None


def part_values(p, comparables, cfg=None, condition="Bardzo dobry"):
    """Wartość części PC → lista {"part", "label", "value", "basis"} (części bez wyceny pomijane - ostrożnie)."""
    out = []

    def add(part, label, value, basis):
        if value and value > 0:
            out.append({"part": part, "label": label, "value": round(value), "basis": basis})

    def lookup(category, text):
        if comparables is None:
            return None, None
        return _part_price(comparables.levels(category, text, condition))
    gpu = p.get("gpu")
    if gpu and gpu != "integrated":
        add("gpu", gpu_name(gpu), *lookup("gpu", gpu))
    if p.get("cpu") and re.match(r"(i[3579]-\d|ryzen|ultra|xeon|pentium|celeron)", p["cpu"]):
        add("cpu", p["cpu"].upper() if p["cpu"].startswith("i") else p["cpu"].title(), *lookup("cpu", p["cpu"]))
    if p.get("ram_gb"):
        kind = ram_type(p)
        add("ram", f"{p['ram_gb']} GB {kind.upper()}", *lookup("ram", f"{kind} {p['ram_gb']} gb"))
    if p.get("ssd_gb"):
        size = p["ssd_gb"]
        add("ssd", f"SSD {size // 1000} TB" if size >= 1000 else f"SSD {size} GB",
            *lookup("storage", f"ssd {size // 1000} tb" if size >= 1000 else f"ssd {size} gb"))
    if p.get("hdd_gb"):
        tb = max(1, round(p["hdd_gb"] / 1000))
        add("hdd", f"HDD {tb} TB", min(tb * float(getattr(cfg, "pc_hdd_tb", 50.0)), 150.0), "stała (HDD tanio)")
    if p.get("chipset"):
        value, basis = lookup("motherboard", p["chipset"])
        add("motherboard", f"płyta {p['chipset'].upper()}", value or float(getattr(cfg, "pc_mobo_unknown", 150.0)),
            basis or "stała (brak median)")
    else:
        add("motherboard", "płyta (chipset nieznany)", float(getattr(cfg, "pc_mobo_unknown", 150.0)), "stała")
    add("psu", f"zasilacz {p['psu_w']} W" if p.get("psu_w") else "zasilacz (moc nieznana)",
        psu_value(p.get("psu_w"), cfg), "stała wg mocy")
    add("case", "obudowa", float(getattr(cfg, "pc_case_pln", 150.0)), "stała")
    return out


def _required(value, cfg):
    from .evaluator import rule_min_margin
    return max(getattr(cfg, "rule_min_profit", 300.0), rule_min_margin(value, cfg) * value)


def routes(parts_list, cost, cfg, whole_value=None):
    """Całość czy części → słownik: parts_value, n_listings, parts_profit, whole_profit, extra (o ile części lepsze),
    parts_ok (opłaca się kupić NA CZĘŚCI), parts_max_buy (najwyższy koszt zakupu, przy którym części są okazją)."""
    costs = getattr(cfg, "rule_costs", 46.0)
    extra = float(getattr(cfg, "pc_parts_extra", 200.0))
    # Bez żadnej wycenionej z bazy części głównej (karta / procesor / RAM / dysk) same stałe (płyta, zasilacz,
    # obudowa) nic nie mówią - brak wyceny na części.
    if not any(x["part"] in ("gpu", "cpu", "ram", "ssd") for x in parts_list):
        parts_list = []
    value = sum(x["value"] for x in parts_list)
    n = len(parts_list)
    out = {"parts_value": round(value), "n_listings": n, "parts": parts_list, "parts_ok": False,
           "parts_max_buy": None, "parts_profit": None, "whole_profit": None, "better_by": None}
    if not value:
        return out
    required = _required(value, cfg)
    # Różnica części - całość nie zależy od ceny zakupu: (wartość części - n × koszty) - (wartość całości - koszty).
    better_by = (value - n * costs) - (whole_value - costs) if whole_value else None
    out["better_by"] = round(better_by) if better_by is not None else None
    if better_by is None or better_by >= extra:
        out["parts_max_buy"] = round(value - n * costs - required - (extra if better_by is None else 0))
    if cost is not None:
        profit = value - cost - n * costs
        out["parts_profit"] = round(profit)
        if whole_value:
            out["whole_profit"] = round(whole_value - cost - costs)
        out["parts_ok"] = out["parts_max_buy"] is not None and cost <= out["parts_max_buy"]
    return out


def parts_text(r):
    """„RTX 3060 ~1100 + I5-9400F ~300 + … = 2370 zł (7 ogłoszeń)”."""
    items = " + ".join(f"{x['label']} ~{x['value']}" for x in r["parts"])
    return f"{items} = {r['parts_value']} zł ({r['n_listings']} ogłoszeń)"


DAMAGED = re.compile(r"\b(?:uszkodz\w*|nie\s+dzia\w*|na\s+cz[eę][sś]ci|nie\s+w[lł][aą]cza|spalon\w*|bez\s+obrazu|"
                     r"for\s+parts|defekt\w*)\b", re.I)


def apply_parts(ev, offer, cfg, route):
    """Po regule zysku (komputer): części opłacalniejsze o ≥ SNIPER_PC_PARTS_EXTRA niż całość → okazja „NA CZĘŚCI”
    (ocena co najmniej SNIPER_RULE_DEAL_SCORE); inaczej tylko informacja. Uszkodzony PC (tekst) - bez okazji z części."""
    if not route or not route.get("parts_value"):
        return ev
    flags = ev.setdefault("red_flags", [])
    ev["pc_parts"] = {k: route[k] for k in ("parts_value", "n_listings", "parts_profit", "whole_profit", "better_by",
                                            "parts_max_buy")}
    text = f"{getattr(offer, 'title', '')} {getattr(offer, 'description', '') or ''}"
    if route.get("parts_ok") and DAMAGED.search(text):
        flags.append(f"Program: na części ~{route['parts_value']} zł, ale w ogłoszeniu usterka - które części działają? "
                     "Bez okazji, sprawdź ręcznie")
        return ev
    if route.get("parts_ok"):
        if not ev.get("is_deal"):
            ev["score_ai"] = ev.get("score_ai", ev.get("score"))
            ev["is_deal"] = True
            ev["score"] = max(ev.get("score") or 0, getattr(cfg, "rule_deal_score", 8.0))
            ev["route"] = "części"
            ev["potential_profit_pln"] = route["parts_profit"]
            ev["max_buy_price_pln"] = route["parts_max_buy"]
        flags.append(f"Program: OKAZJA NA CZĘŚCI - zysk ~{route['parts_profit']} zł"
                     + (f" (całość ~{route['whole_profit']} zł)" if route.get("whole_profit") is not None else "")
                     + f": {parts_text(route)}")
    else:
        flags.append(f"Program: na części ~{route['parts_value']} zł w {route['n_listings']} ogłoszeniach"
                     + (f", zysk ~{route['parts_profit']} zł" if route.get("parts_profit") is not None else "")
                     + " - nie opłaca się bardziej niż sprzedaż całości (próg +"
                     f"{getattr(cfg, 'pc_parts_extra', 200.0):.0f} zł)")
    return ev
