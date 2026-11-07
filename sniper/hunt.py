"""Polowanie na kartę graficzną pod basala (2026-11-07, `SNIPER_HUNT_FOR_GPU=true`, cele w `SNIPER_HUNT_FILE`,
domyślnie sniper/hunt_for_basal.md).

Każda nowa oferta - niezależnie od filtrów i oceny AI - jest sprawdzana z tabelą celów: wzorzec karty w tytule,
cena ≤ progu, VRAM ≥ minimum (z „!” - VRAM musi być podany), bez uszkodzonych / części / laptopów / „kupię”.
Trafienie: ntfy z priorytetem 5 i wpis w logs/hunt.jsonl. Każda oferta alarmuje raz.
"""
import asyncio
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger("sniper.hunt")

DEFAULT_FILE = Path(__file__).with_name("hunt_for_basal.md")
SKIP_CATEGORIES = {"laptop", "phone", "tablet", "console", "bundle"}
BAD = re.compile(r"\b(?:na czesci|uszkodzon\w*|nie dziala\w*|niesprawn\w*|brak obrazu|artefakt\w*|for parts|broken|"
                 r"not working|defekt\w*|do naprawy|nie wykrywa\w*|nefunk\w*|piese|hibas|kaputt|defect)\b")
NOT_A_CARD = re.compile(r"\b(?:kupie|szukam|zamienie|zamiana|wtb|radiator|heatsink|waterblock|blok wodny|backplate|"
                        r"chlodzenie|wentylator\w*|laptop\w*|notebook|komputer|zestaw pc|pc gaming\w*|stacjonarn\w*|"
                        r"mobile|max-?q|pudelko|box only|samo pudelko)\b")
NEGATED = re.compile(r"\b(?:bez|brak|nie ma|zadnych|no|not)\W+(?:\w+\W+){0,2}$")


def norm(text):
    text = unicodedata.normalize("NFKD", (text or "").lower().replace("ł", "l"))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


@dataclass
class Target:
    name: str
    pattern: re.Pattern
    max_price: float
    min_vram: int
    vram_required: bool
    speed: str


def load_targets(path=None):
    """Wiersze tabeli markdown: | karta | `regex` | maks. zł | min. VRAM (np. 12!) | szybkość | rynek |."""
    path = Path(path) if path else DEFAULT_FILE
    targets = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = _cells(line) if line.strip().startswith("|") else []
        if len(cells) < 5 or not cells[1].startswith("`"):
            continue
        try:
            vram = cells[3].strip()
            targets.append(Target(cells[0], re.compile(cells[1].strip("`")), float(cells[2].replace(",", ".")),
                                  int(vram.rstrip("!")), vram.endswith("!"), cells[4]))
        except (ValueError, re.error) as exc:
            log.warning("[HUNT] Pomijam wiersz %r: %s", line[:80], exc)
    return targets


def _cells(line):
    """Komórki wiersza tabeli markdown; „|” wewnątrz `...` (regex) nie dzieli komórki."""
    cells, cur, code = [], "", False
    for ch in line.strip().strip("|"):
        if ch == "`":
            code = not code
        if ch == "|" and not code:
            cells.append(cur.strip())
            cur = ""
        else:
            cur += ch
    cells.append(cur.strip())
    return cells


def _vram(text):
    return {int(n) for n in re.findall(r"(?<![\d.,])(\d{1,2})\s?(?:gb|g)\b", text) if 2 <= int(n) <= 48}


def _bad(pattern, text):
    for m in pattern.finditer(text):
        if not NEGATED.search(text[max(0, m.start() - 30):m.start()]):
            return True
    return False


def match(targets, offer):
    """Cel, który oferta spełnia, albo None (z powodem w logu DEBUG)."""
    if getattr(offer, "category", None) in SKIP_CATEGORIES:
        return None
    title, desc = norm(offer.title), norm(getattr(offer, "description", "") or "")
    price = offer.price
    if price is None or not re.search(r"rtx|geforce|nvidia|gtx|\b[2-5]0[5-9]0\b", title):
        return None
    if NOT_A_CARD.search(title) or _bad(BAD, f"{title}\n{desc}"):
        return None
    for t in targets:
        if not t.pattern.search(title) or price > t.max_price:
            continue
        sizes = _vram(title) or _vram(desc)
        if sizes and max(sizes) < t.min_vram:
            continue
        if t.vram_required and not sizes:
            continue
        return t
    return None


class Hunter:
    def __init__(self, path=None, log_dir=None):
        self.path = Path(path) if path else DEFAULT_FILE
        self.targets = load_targets(self.path)
        self.log_dir = Path(log_dir) if log_dir else None
        self.alerted = set()
        self._tasks = set()
        log.info("[HUNT] Polowanie na kartę pod basala: %d celów z %s.", len(self.targets), self.path.name)

    def check(self, offer, push=None):
        if offer.id in self.alerted:
            return None
        target = match(self.targets, offer)
        if target is None:
            return None
        self.alerted.add(offer.id)
        from .extractor import purchase_cost
        cost = purchase_cost(offer)
        log.warning("[HUNT] TRAFIENIE: %s - %s za %.0f zł (z wysyłką i opłatą ~%s zł) %s", target.name, offer.title,
                    offer.price, f"{cost:.0f}" if cost else "?", offer.url)
        self._save(offer, target, cost)
        if push is not None:
            task = asyncio.create_task(push.send(self.message(offer, target, cost, push.cfg.topic)), name="hunt")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        else:
            log.warning("[HUNT] Brak SNIPER_NTFY_TOPIC - trafienie tylko w logu i logs/hunt.jsonl.")
        return target

    @staticmethod
    def message(offer, target, cost, topic):
        lines = [f"{offer.price:.0f} zł" + (f" (razem ~{cost:.0f} zł)" if cost else ""),
                 f"Cel: {target.name}, próg {target.max_price:.0f} zł, szybkość vs RX 580: {target.speed}",
                 "Sprawdź: czy karta kopała, zdjęcie z GPU-Z, test w grze / FurMark."]
        msg = {"topic": topic, "title": f"POLOWANIE: {offer.title}"[:120], "message": "\n".join(lines),
               "priority": 5, "tags": ["dart", "video_game"]}
        if offer.url:
            msg["click"] = offer.url
            msg["actions"] = [{"action": "view", "label": "Otwórz ofertę", "url": offer.url, "clear": True}]
        if getattr(offer, "photo_urls", None):
            msg["attach"] = offer.photo_urls[0]
            msg["filename"] = "karta.jpg"
        return msg

    def _save(self, offer, target, cost):
        if not self.log_dir:
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with open(self.log_dir / "hunt.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "target": target.name,
                                    "id": offer.id, "title": offer.title, "price": offer.price, "cost": cost,
                                    "url": offer.url}, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("[HUNT] Nie zapisałem logs/hunt.jsonl: %r", exc)
