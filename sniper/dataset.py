"""Zbiór danych do wyszkolenia YOLO (2026-10-26, pomysł użytkownika): przy okazji oceny AI zapisujemy zdjęcia oferty
z ramkami obiektów, które zwrócił model (`objects`: urządzenie, pady, zasilacz, pudełko, gry, uszkodzenia…).

Układ (Ultralytics szuka etykiet, podmieniając `/images/` na `/labels/` w ścieżce):
    <SNIPER_DATASET_DIR>/data.yaml, classes.txt               - klasy = evaluator.OBJECT_LABELS (kolejność = numer)
    <kategoria>/<marka_model>/images/<id>_<n>.webp            - zdjęcie 1:1 z CDN Vinted
    <kategoria>/<marka_model>/labels/<id>_<n>.txt             - YOLO: klasa cx cy w h (0-1); pusty = tło
    <kategoria>/<marka_model>/<id>.json                       - tytuł, identyfikacja AI, ramki w pikselach i 0-1000
Ramki od AI są przybliżone - przed treningiem przejrzeć (np. Label Studio / Roboflow). Zdjęcia pobierane z domowego
IP bez proxy (jak SNIPER_AI_PHOTOS=download), w tle - nie opóźniają oceny ani powiadomień. Bez danych sprzedawcy.
`SNIPER_DATASET_MAX_PER_MODEL` ofert na model = równe klasy i ograniczone miejsce na dysku.
"""
import asyncio
import json
import logging
import re
import struct
import unicodedata
from pathlib import Path

import httpx

log = logging.getLogger("sniper.dataset")

_EXTS = (".webp", ".jpg", ".jpeg", ".png")


def slug(text, limit=60):
    """'Sony PlayStation 4 Pro' → 'sony_playstation_4_pro' (nazwa folderu bez polskich znaków)."""
    text = unicodedata.normalize("NFKD", (text or "").replace("ł", "l").replace("Ł", "L"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:limit].strip("_")


def image_size(data):
    """(szerokość, wysokość) z nagłówka PNG / JPEG / WebP bez dodatkowych bibliotek; None gdy nie rozpoznano."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if chunk == b"VP8L":
                b = data[21:25]
                return 1 + (((b[1] & 0x3F) << 8) | b[0]), 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
            if chunk == b"VP8X":
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + length
    except (struct.error, IndexError):
        return None
    return None


def _ext(url):
    path = url.split("?", 1)[0].lower()
    return next((ext for ext in _EXTS if path.endswith(ext)), ".jpg")


def yolo_line(obj, labels):
    """Ramka [ymin, xmin, ymax, xmax] 0-1000 → 'klasa cx cy w h' (YOLO, 0-1)."""
    y1, x1, y2, x2 = (v / 1000 for v in obj["box_2d"])
    return (f"{labels.index(obj['label'])} {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {x2 - x1:.6f} "
            f"{y2 - y1:.6f}")


class DatasetWriter:
    def __init__(self, root, labels, max_per_model=30, max_photos=6, http=None):
        self.root = Path(root)
        self.labels = list(labels)
        self.max_per_model = max_per_model
        self.max_photos = max_photos
        self._http = http
        self.saved = 0

    def folder(self, category, evaluation):
        name = slug(" ".join(x for x in (evaluation.get("brand"), evaluation.get("model")) if x)) or "nieznany"
        return self.root / slug(category or "laptop") / name

    def _write_meta(self):
        self.root.mkdir(parents=True, exist_ok=True)
        classes = "\n".join(self.labels) + "\n"
        path = self.root / "classes.txt"
        if not path.exists() or path.read_text(encoding="utf-8") != classes:
            path.write_text(classes, encoding="utf-8")
            names = "\n".join(f"  {i}: {name}" for i, name in enumerate(self.labels))
            (self.root / "data.yaml").write_text(
                f"# Ultralytics: train/val = listy folderów .../images (podziel ręcznie, np. 80/20 modeli)\n"
                f"path: {self.root.as_posix()}\ntrain: .\nval: .\nnames:\n{names}\n", encoding="utf-8")

    async def _download(self, urls):
        if self._http is None:
            # Bezpośrednio z domowego IP (trust_env=False = bez proxy ze zmiennych systemowych) - nie przez IPRoyal.
            self._http = httpx.AsyncClient(trust_env=False, timeout=20.0, follow_redirects=True)

        async def one(url):
            try:
                response = await self._http.get(url)
                response.raise_for_status()
                return response.content
            except httpx.HTTPError as exc:
                log.info("[DANE] Nie pobrałem zdjęcia %s: %r", url, exc)
                return None

        return await asyncio.gather(*(one(url) for url in urls))

    async def save(self, offer, record):
        """→ folder z zapisaną ofertą albo None (bez zdjęć / limit modelu / już jest). Nigdy nie rzuca."""
        try:
            return await self._save(offer, record)
        except Exception as exc:  # zbiór danych nie może przeszkadzać Sniperowi
            log.warning("[DANE] Nie zapisałem %s do zbioru: %r", getattr(offer, "id", "?"), exc)
            return None

    async def _save(self, offer, record):
        evaluation = record.get("evaluation") or {}
        sent = record.get("photos_sent") or 0
        urls = list(offer.photo_urls or [])[:sent]
        # Numer zdjęcia w ramkach = kolejność przesłania; gdy część zdjęć nie poszła (błąd pobrania) - nie wiadomo,
        # które to były, więc pomijamy ofertę zamiast zapisać złe ramki.
        if not sent or not evaluation.get("photos_seen") or sent != min(len(offer.photo_urls or []), self.max_photos):
            return None
        folder = self.folder(getattr(offer, "category", None), evaluation)
        meta_path = folder / f"{offer.id}.json"
        if meta_path.exists():
            return None
        if folder.exists() and sum(1 for _ in folder.glob("*.json")) >= self.max_per_model:
            return None
        images = await self._download(urls)
        self._write_meta()
        (folder / "images").mkdir(parents=True, exist_ok=True)
        (folder / "labels").mkdir(parents=True, exist_ok=True)
        photos = []
        for n, (url, data) in enumerate(zip(urls, images), 1):
            if not data:
                continue
            name = f"{offer.id}_{n}"
            (folder / "images" / f"{name}{_ext(url)}").write_bytes(data)
            size = image_size(data)
            objects = [o for o in evaluation.get("objects") or [] if o.get("photo") == n]
            (folder / "labels" / f"{name}.txt").write_text(
                "".join(yolo_line(o, self.labels) + "\n" for o in objects), encoding="utf-8")
            boxes = []
            for o in objects:
                y1, x1, y2, x2 = o["box_2d"]
                px = [round(x1 * size[0] / 1000), round(y1 * size[1] / 1000), round(x2 * size[0] / 1000),
                      round(y2 * size[1] / 1000)] if size else None
                boxes.append({"label": o["label"], "box_2d": o["box_2d"], "box_px_xyxy": px})
            photos.append({"file": f"images/{name}{_ext(url)}", "width": size[0] if size else None,
                           "height": size[1] if size else None, "objects": boxes})
        if not photos:
            return None
        meta = {"id": offer.id, "url": offer.url, "title": offer.title, "category": getattr(offer, "category", None),
                "price": offer.price, "brand": evaluation.get("brand"), "model": evaluation.get("model"),
                "variant": evaluation.get("variant"), "specs": evaluation.get("specs"),
                "accessories": evaluation.get("accessories"), "score": evaluation.get("score"),
                "red_flags": evaluation.get("red_flags"), "photo_notes": evaluation.get("photo_notes"),
                "detected_at": getattr(offer, "detected_at", None), "photos": photos}
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        self.saved += 1
        log.info("[DANE] %s → %s (%d zdjęć, %d ramek)", offer.id, folder.relative_to(self.root), len(photos),
                 sum(len(p["objects"]) for p in photos))
        return folder

    async def close(self):
        if self._http is not None:
            await self._http.aclose()
