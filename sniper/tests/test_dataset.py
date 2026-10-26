"""Zbiór danych do YOLO (sniper/dataset.py) + identyfikacja modelu w ocenie AI."""
import asyncio
import io
import json
import struct

import httpx

from sniper.dataset import DatasetWriter, image_size, slug, yolo_line
from sniper.evaluator import OBJECT_LABELS, OfferEvaluator, clean_objects, identity, normalize_evaluation

from sniper.tests.test_evaluator import FakeClient, FakeNotifier, PHOTOS, ai_cfg, laptop, response, submit_and_wait

EVAL = {"is_deal": False, "score": 5, "gpu_model": None, "laptop_model": "PS4 Pro", "brand": "Sony",
        "model": "PlayStation 4 Pro", "variant": "1 TB, CUH-7216B", "specs": "1 TB", "accessories": ["2 pady"],
        "market_value_pln": 700, "max_buy_price_pln": 400, "potential_profit_pln": 100, "photos_seen": 2,
        "photo_notes": "konsola i 2 pady", "reasoning": "x", "red_flags": [],
        "objects": [{"photo": 1, "label": "urzadzenie", "box_2d": [100, 200, 600, 800]},
                    {"photo": 1, "label": "pad", "box_2d": [650, 100, 900, 300]},
                    {"photo": 2, "label": "pad", "box_2d": [0, 0, 1000, 500]}]}


def png(w, h):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"


def jpeg(w, h):
    return (b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
            + b"\xff\xc0" + struct.pack(">HBHH", 17, 8, h, w) + b"\x03" + b"\x00" * 9)


def webp_vp8x(w, h):
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00" * 4 + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def test_image_size_formats():
    assert image_size(png(640, 480)) == (640, 480)
    assert image_size(jpeg(800, 600)) == (800, 600)
    assert image_size(webp_vp8x(1200, 1600)) == (1200, 1600)
    assert image_size(b"nic") is None


def test_image_size_matches_pillow_webp():
    from PIL import Image
    for mode, kw in (("RGB", {"lossless": False}), ("RGB", {"lossless": True}), ("RGBA", {"lossless": False})):
        buf = io.BytesIO()
        Image.new(mode, (321, 123)).save(buf, "WEBP", **kw)
        assert image_size(buf.getvalue()) == (321, 123)


def test_slug_and_yolo_line():
    assert slug("Sony PlayStation 4 Pro") == "sony_playstation_4_pro"
    assert slug("Łódź / Xiaomi Redmi Note 12") == "lodz_xiaomi_redmi_note_12"
    line = yolo_line({"label": "pad", "box_2d": [100, 200, 600, 800]}, OBJECT_LABELS)
    assert line == f"{OBJECT_LABELS.index('pad')} 0.500000 0.350000 0.600000 0.500000"


def test_normalize_identity_and_objects():
    ev = normalize_evaluation(json.dumps({**EVAL, "brand": "null", "objects": [
        *EVAL["objects"], {"photo": 0, "label": "pad", "box_2d": [1, 1, 2, 2]},          # zły numer zdjęcia
        {"photo": 1, "label": "kot", "box_2d": [1, 1, 2, 2]},                            # nieznana klasa
        {"photo": 1, "label": "pad", "box_2d": [500, 1, 400, 2]},                         # odwrócona
        {"photo": 1, "label": "gra", "box_2d": [-5, 10, 1200, 20.4]}]}))                 # przycięta do 0-1000
    assert ev["brand"] is None and ev["model"] == "PlayStation 4 Pro" and ev["accessories"] == ["2 pady"]
    assert ev["objects"][-1] == {"photo": 1, "label": "gra", "box_2d": [0, 10, 1000, 20]}
    assert len(ev["objects"]) == 4
    assert identity(normalize_evaluation(json.dumps(EVAL))) == "Sony PlayStation 4 Pro, 1 TB, CUH-7216B"
    assert normalize_evaluation(json.dumps({"score": 3}))["objects"] == [] and clean_objects(None) == []


def fake_http(images, calls):
    def handler(request):
        calls.append(str(request.url))
        data = images.get(str(request.url))
        return httpx.Response(200, content=data) if data else httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def record(offer, ev=None, sent=2):
    return {"status": "oceniona", "photos_sent": sent, "evaluation": normalize_evaluation(json.dumps(ev or EVAL)),
            "offer": offer.to_dict()}


def test_writer_saves_images_labels_and_meta(tmp_path):
    offer = laptop(photo_urls=PHOTOS[:2], category="console")
    calls = []
    writer = DatasetWriter(tmp_path, OBJECT_LABELS, max_photos=6,
                           http=fake_http({PHOTOS[0]: webp_vp8x(1000, 800), PHOTOS[1]: png(500, 500)}, calls))
    folder = asyncio.run(writer.save(offer, record(offer)))
    assert folder == tmp_path / "console" / "sony_playstation_4_pro"
    assert (folder / "images" / "7001_1.webp").read_bytes()[:4] == b"RIFF"
    labels = (folder / "labels" / "7001_1.txt").read_text().splitlines()
    assert [line.split()[0] for line in labels] == ["0", "1"]
    meta = json.loads((folder / "7001.json").read_text(encoding="utf-8"))
    first = meta["photos"][0]
    assert (first["width"], first["height"]) == (1000, 800)
    assert first["objects"][0]["box_px_xyxy"] == [200, 80, 800, 480]         # x = ×1000/1000, y = ×800/1000
    assert meta["variant"] == "1 TB, CUH-7216B" and "seller" not in meta
    assert (tmp_path / "classes.txt").read_text().split() == OBJECT_LABELS
    assert "names:" in (tmp_path / "data.yaml").read_text()
    # druga próba tej samej oferty - nic nie pobiera
    assert asyncio.run(writer.save(offer, record(offer))) is None and len(calls) == 2


def test_writer_skips_unclear_cases_and_limits(tmp_path):
    offer = laptop(photo_urls=PHOTOS[:3], category="console")
    writer = DatasetWriter(tmp_path, OBJECT_LABELS, max_per_model=1, max_photos=6,
                           http=fake_http({u: png(10, 10) for u in PHOTOS}, []))
    # 2 z 3 zdjęć wysłane (np. błąd pobrania) - numeracja niepewna → pomijamy
    assert asyncio.run(writer.save(offer, record(offer, sent=2))) is None
    # model nie widział zdjęć → pomijamy
    assert asyncio.run(writer.save(offer, record(offer, {**EVAL, "photos_seen": 0}, sent=3))) is None
    assert asyncio.run(writer.save(offer, record(offer, sent=3))) is not None
    other = laptop(id=7002, photo_urls=PHOTOS[:3], category="console")
    assert asyncio.run(writer.save(other, record(other, sent=3))) is None              # limit 1 oferty na model


def test_evaluator_saves_to_dataset_after_evaluation(tmp_path):
    evaluator = OfferEvaluator(ai_cfg(tmp_path, dataset_dir=str(tmp_path / "ds"), max_photos=2), FakeNotifier(),
                               log_dir=tmp_path, client=FakeClient(response({**EVAL, "photos_seen": 2})))
    evaluator.dataset._http = fake_http({u: png(100, 100) for u in PHOTOS}, [])
    asyncio.run(submit_and_wait(evaluator, laptop()))                    # bez kategorii = laptop (guidelines.md)
    saved = list((tmp_path / "ds").rglob("7001.json"))
    assert [p.relative_to(tmp_path / "ds").parent.as_posix() for p in saved] == ["laptop/sony_playstation_4_pro"]
    line = (tmp_path / "evaluations.csv").read_text(encoding="utf-8-sig").splitlines()[-1].split(";")
    assert line[-6:-3] == ["Sony", "PlayStation 4 Pro", "1 TB, CUH-7216B"] and line[-1] == "3"
