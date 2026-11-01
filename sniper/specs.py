"""Specyfikacja laptopa (2026-11-01, pomysł użytkownika: „dla laptopów nie patrzeć na modele, a na specyfikacje - za
to ludzie płacą: grafika, procesor, RAM, dysk”).

`laptop_spec(tytuł, opis, atrybuty)` wyciąga kartę graficzną, procesor (linia + generacja), chip i linię MacBooka,
RAM i dysk TYMI SAMYMI regułami co analizator (analyzer/extract.py: laptop, analyzer/comparables.py: laptop_spec) -
inaczej klucze nie trafiłyby w tabelę `laptop_specs` w comparables.db. Zmieniasz regułę tu → zmień też tam.

Mediany po specyfikacji (`sniper/comparables.py: spec_levels`) idą od najdokładniejszej (karta + RAM + dysk) do samej
karty / procesora. Gdy tekst nie podaje specyfikacji, AI odczytuje ją z opisu / zdjęć / numeru modelu
(`spec_from_ai`) i program liczy mediany dla niej.
"""
import re
import unicodedata


def norm(text):
    text = unicodedata.normalize("NFKD", (text or "").lower().replace("ł", "l"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


def _gb(text):
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(tb|gb|mb)\b", norm(text))
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    if match.group(2) == "tb":
        return int(round(value * 1000))
    return None if match.group(2) == "mb" else int(round(value))


CPU_PATTERNS = (
    re.compile(r"\b(?:intel\s*)?(?:core\s*)?ultra\s*([579])\s*-?\s*(\d{3}[a-z]{0,2})\b"),
    re.compile(r"\b(?:intel\s*)?(?:core\s*)?(i[3579])\s*-?\s*(\d{4,5}(?:[a-z]{1,2}\d?)?)\b"),
    re.compile(r"\b(?:amd\s*)?ryzen\s*(?:ai\s*)?([3579])\s*(?:pro\s*)?-?\s*(\d{3,4}[a-z]{0,2})\b"),
    re.compile(r"\b(?:apple\s*)?(m[1345](?![.\d])|m2(?![.\d])(?!\s*(?:\d{3}|ssd|nvme|slot|sata|dysk|disk)))"
               r"(?:\s*(pro|max|ultra))?\b"),
    re.compile(r"\b(celeron|pentium|athlon|snapdragon x\s*(?:elite|plus))\s*-?\s*([a-z]?\d{3,5}[a-z]?)?\b"),
)
CPU_FAMILY = re.compile(r"\b(?:intel\s*)?(?:core\s*)?(i[3579])\b(?![-\s]*\d)|\bryzen\s*([3579])\b(?!\s*\d{3})")
GPU_PATTERNS = (
    re.compile(r"\b(rtx)\s*(a\d{3,4}|\d{4})\s*(ti|super)?\b"),
    re.compile(r"\b(gtx)\s*(\d{3,4})\s*(ti|super)?\b"),
    re.compile(r"\b(rx)\s*(\d{3,4})\s*(xt|xtx|m|s)?\b"),
    re.compile(r"\b(quadro)\s*([a-z]?\d{3,4}[a-z]?)\b"),
    re.compile(r"\b(mx)\s*(\d{3})\b"),
    re.compile(r"\b(arc)\s*(a\d{3}m?)\b"),
)
INTEGRATED = re.compile(r"\b(?:iris\s*xe|iris plus|intel (?:uhd|hd) graphics|uhd graphics|radeon (?:graphics|vega)|"
                        r"780m|680m|890m|integrated graphics|zintegrowan\w*)\b")
RAM = re.compile(r"\b(\d{1,2})\s*gb\s*(?:ram|ddr\d?|lpddr\d?\w*|pamie\w*|memory|memoria|memorie|operacn\w*|"
                 r"arbeitsspeicher)\b|"
                 r"\b(?:ram|pamiec\w*|memory|memoria|memorie|arbeitsspeicher|operacni pamet)\s*(?:merete|de|size)?"
                 r"\s*:?\s*(\d{1,2})\s*gb\b")
STORAGE_BEFORE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(tb|gb)\s*(?:ssd|nvme|m\.?2|hdd|emmc|dysk\w*|disk\w*|storage)\b")
STORAGE_AFTER = re.compile(r"\b(?:ssd|nvme|dysk|disk)(?:\s*(?::|m\.?2|nvme|pcie|sata|ssd))*\s*(\d+(?:[.,]\d+)?)\s*"
                           r"(tb|gb)\b")
RAM_SIZES = (2, 4, 6, 8, 12, 16, 20, 24, 32, 36, 48, 64, 96, 128)
_GPU_WITH_VRAM = re.compile(r"\b(?:rtx|gtx|rx|quadro|mx|arc)\s*a?\d{3,4}\s*(?:ti|super|xt|m)?\s*"
                            r"(?:(?:2|3|4|6|8|12)\s*gb\b)?")
MACBOOK = re.compile(r"\bmac\s*book\s*(air|pro)?")
DEDICATED_GPU = re.compile(r"(rtx|gtx|rx|arc|quadro)\b")
SSD_BUCKETS = ((160, 128), (300, 256), (600, 512), (1200, 1000), (2500, 2000))


def _cpu(text):
    apple = re.search(r"\b(?:macbook|apple|imac)\b", text)
    for i, pattern in enumerate(CPU_PATTERNS):
        if i == 3 and not apple:
            continue
        match = pattern.search(text)
        if match:
            parts = [p for p in match.groups() if p]
            if i == 0:
                return f"ultra {parts[0]} {parts[1]}"
            if i == 1:
                return f"{parts[0]}-{parts[1]}"
            if i == 2:
                return f"ryzen {parts[0]} {parts[1]}"
            if i == 3:
                return "apple " + " ".join(parts)
            return " ".join(parts).replace("  ", " ").strip()
    return None


def _gpu(text):
    for pattern in GPU_PATTERNS:
        match = pattern.search(text)
        if match:
            return " ".join(p for p in match.groups() if p)
    return "integrated" if INTEGRATED.search(text) else None


def _ram(text, loose=False):
    match = RAM.search(text)
    if match:
        value = int(match.group(1) or match.group(2))
        if value in RAM_SIZES:
            return value
    if loose:
        clean = _GPU_WITH_VRAM.sub(" ", text)
        sizes = [(int(m.group(1)), m.end()) for m in re.finditer(r"\b(\d{1,4})\s*gb\b", clean)]
        small = [v for v, end in sizes if v in RAM_SIZES and v <= 64
                 and not re.match(r"\s*(?:ssd|nvme|hdd|emmc|m\.?2|dysk|disk|storage|vram)", clean[end:])]
        if small and any(v >= 128 for v, _ in sizes):
            return small[0]
        if small and len(sizes) == 1 and re.search(r"\b(?:macbook|apple)\b", text) and re.search(r"\bm[1-5]\b", text):
            return small[0]
    return None


def _storage(text, loose=False):
    found = sorted((m.start(), _gb(f"{m.group(1)} {m.group(2)}")) for pattern in (STORAGE_BEFORE, STORAGE_AFTER)
                   for m in pattern.finditer(text))
    values = [v for _pos, v in found if v]
    if values:
        return next((v for v in values if v >= 64), values[0])
    if loose:
        # tytuł „i7 16GB 512GB” - obok RAM-u (≤ 64 GB) większa liczba to dysk
        clean = _GPU_WITH_VRAM.sub(" ", text)
        sizes = [_gb(m.group(0)) for m in re.finditer(r"\b\d+(?:[.,]\d+)?\s*(?:tb|gb)\b", clean)]
        big = [v for v in sizes if v and v >= 120]
        if big and any(v and v <= 64 for v in sizes):
            return big[0]
    return None


def ssd_bucket(gb):
    if not gb or gb < 32:
        return None
    return next((value for limit, value in SSD_BUCKETS if gb <= limit), 4000)


def cpu_line_of(cpu, cpu_line=None):
    """Jak analyzer/analysis.py: cpu_line_of („i5-8350u” → „intel core i5”, „apple m1 pro” → „apple m1”)."""
    if cpu_line:
        return str(cpu_line).lower()
    if not cpu:
        return None
    cpu = str(cpu).lower()
    for pattern, fmt in ((r"(i[3579])-", "intel core {}"), (r"ultra ([579])", "intel core ultra {}"),
                         (r"ryzen ([3579])", "amd ryzen {}"), (r"apple (m\d)", "apple {}")):
        match = re.match(pattern, cpu)
        if match:
            return fmt.format(match.group(1))
    return cpu.split()[0]


def cpu_generation(cpu):
    """Jak analyzer/guide.py: cpu_generation („i5-8350u” → „8–9 gen.”, „ryzen 5 5500u” → „4000–5000”)."""
    cpu = (cpu or "").lower()
    m = re.match(r"i[3579]-(\d{3,5})", cpu)
    if m:
        d = m.group(1)
        gen = int(d[:2]) if len(d) == 5 or (len(d) == 4 and d[0] == "1") else int(d[0]) if len(d) == 4 else 1
        return "do 7 gen." if gen <= 7 else "8–9 gen." if gen <= 9 else "10–11 gen." if gen <= 11 else "12+ gen."
    m = re.match(r"ryzen [3579] (?:pro )?(\d)\d{3}", cpu)
    if m:
        series = int(m.group(1))
        return "1000–3000" if series <= 3 else "4000–5000" if series <= 5 else "6000+"
    return None


def normalize(model, cpu, cpu_line, gpu, ram_gb, storage_gb):
    """Jak analyzer/comparables.py: laptop_spec → słownik albo None (za mało danych do median)."""
    gpu = (gpu or "").lower().strip()
    cpu = (cpu or "").lower().strip()
    model = (model or "").lower()
    chip = re.search(r"apple (m\d(?: (?:pro|max|ultra))?)", cpu)
    line = ("macbook air" if "air" in model else "macbook pro" if "pro" in model else None) \
        if "macbook" in model or chip else None
    spec = {"gpu": gpu if DEDICATED_GPU.match(gpu) else None,
            "cpu_line": cpu_line_of(cpu or None, cpu_line), "cpu_gen": cpu_generation(cpu) if cpu else None,
            "chip": chip.group(1) if chip else None, "line": line,
            "ram": int(ram_gb) if ram_gb else None, "ssd": ssd_bucket(storage_gb)}
    if spec["cpu_line"] and spec["cpu_line"].startswith("intel core ultra"):
        spec["cpu_gen"] = "core ultra"
    if spec["gpu"] or (spec["chip"] and spec["line"]) or (spec["cpu_line"] and spec["cpu_gen"]):
        return spec
    return None


def laptop_spec(title, description="", attributes=None):
    """Specyfikacja z tytułu, opisu i formularza Vinted (pierwsze źródło wygrywa, jak w analizatorze) albo None."""
    attrs = attributes or {}
    t, d = norm(title), norm(description)
    model = norm(attrs.get("model_nav") or attrs.get("model") or "")
    if not model:
        match = MACBOOK.search(t)
        model = " ".join(p for p in ("macbook", match.group(1)) if p) if match else ""
    cpu = _cpu(t) or _cpu(d)
    cpu_line = attrs.get("computer_cpu_line")
    if not cpu and not cpu_line:
        for text in (t, d):
            match = CPU_FAMILY.search(text)
            if match:
                cpu_line = f"intel core {match.group(1)}" if match.group(1) else f"amd ryzen {match.group(2)}"
                break
    gpu = _gpu(t) or _gpu(d)
    ram = _gb(attrs["computer_ram"]) if attrs.get("computer_ram") else None
    ram = ram if ram and ram <= 128 else (_ram(t, loose=True) or _ram(d))
    disk = _gb(attrs["computer_storage_capacity"]) if attrs.get("computer_storage_capacity") else None
    disk = disk if disk and disk >= 32 else (_storage(t, loose=True) or _storage(d))
    return normalize(model, cpu, cpu_line, gpu, ram, disk)


def spec_from_ai(found, title=""):
    """Specyfikacja odczytana przez AI ({gpu, cpu, ram_gb, ssd_gb, source}) → słownik jak laptop_spec albo None."""
    if not isinstance(found, dict) or (found.get("source") or "brak") == "brak":
        return None
    t = norm(title)
    gpu_text, cpu_text = norm(found.get("gpu") or ""), norm(found.get("cpu") or "")
    if re.match(r"m[1-5]\b", cpu_text):
        cpu_text = "apple " + cpu_text
    gpu = _gpu(gpu_text) or ("integrated" if "zintegrowan" in gpu_text or "integrated" in gpu_text else None)
    cpu = _cpu(cpu_text if "apple" in cpu_text else cpu_text.replace("apple", "")) or _cpu(f"{t} {cpu_text}")
    cpu_line = None
    if not cpu:
        match = CPU_FAMILY.search(cpu_text)
        cpu_line = (f"intel core {match.group(1)}" if match.group(1) else f"amd ryzen {match.group(2)}") \
            if match else None
    match = MACBOOK.search(t)
    model = " ".join(p for p in ("macbook", match.group(1)) if p) if match else ""

    def number(key, low, high):
        try:
            value = int(found.get(key) or 0)
        except (TypeError, ValueError):
            return None
        return value if low <= value <= high else None
    return normalize(model, cpu, cpu_line, gpu, number("ram_gb", 2, 128), number("ssd_gb", 32, 8000))


def merge(text_spec, ai_spec):
    """Tekst ma pierwszeństwo, AI uzupełnia braki; dedykowana karta z AI (np. naklejka na zdjęciu) zastępuje brak."""
    if not ai_spec:
        return text_spec
    if not text_spec:
        return ai_spec
    out = dict(text_spec)
    for key, value in ai_spec.items():
        if out.get(key) is None and value is not None:
            out[key] = value
    return out


SPEC_KEYS = ("gpu", "line", "chip", "cpu_line", "cpu_gen", "ram", "ssd")


def same(a, b):
    return all((a or {}).get(k) == (b or {}).get(k) for k in SPEC_KEYS)


def significant(before, after):
    """Czy specyfikacja od AI zmienia wycenę: inna karta, MacBook (linia / chip) albo procesor (linia / generacja).
    Sam RAM / dysk - nie (AI widzi już ogólniejsze mediany i koryguje sama; ponowna ocena to dodatkowy koszt)."""
    return any((before or {}).get(k) != (after or {}).get(k) for k in ("gpu", "line", "chip", "cpu_line", "cpu_gen"))


def gpu_name(gpu):
    return gpu.upper().replace(" TI", " Ti").replace(" SUPER", " Super")


def describe(spec):
    """„RTX 4060 · 16 GB RAM · 512 GB” - do logu i dla modelu."""
    if not spec:
        return "-"
    parts = [gpu_name(spec["gpu"]) if spec.get("gpu") else None,
             " ".join(x for x in (spec.get("line"), (spec.get("chip") or "").upper()) if x).replace("macbook", "MacBook")
             .replace("air", "Air").replace("pro", "Pro") if spec.get("line") else None,
             (spec.get("cpu_line") or "").title().replace("Amd", "AMD") + (f" {spec['cpu_gen']}" if spec.get("cpu_gen")
                                                                         else "") if spec.get("cpu_line") else None,
             f"{spec['ram']} GB RAM" if spec.get("ram") else None, ssd_label(spec.get("ssd"))]
    return " · ".join(p for p in parts if p)


def ssd_label(ssd):
    if not ssd:
        return None
    return {1000: "1 TB", 2000: "2 TB", 4000: "4 TB+"}.get(ssd, f"{ssd} GB")
