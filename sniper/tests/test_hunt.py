"""Polowanie na kartę pod basala (sniper/hunt.py, hunt_for_basal.md)."""
import asyncio
from types import SimpleNamespace

from sniper import hunt


def offer(title, price, desc="", category="gpu", oid=1):
    return SimpleNamespace(id=oid, title=title, price=price, description=desc, category=category, url="https://x/1",
                           photo_urls=["https://x/1.jpg"], total_price=None)


def test_targets_file_parses():
    targets = hunt.load_targets()
    names = [t.name for t in targets]
    assert len(targets) >= 12 and "RTX 3060 12 GB" in names
    t2060 = next(t for t in targets if t.name == "RTX 2060 12 GB")
    assert t2060.vram_required and t2060.min_vram == 12 and t2060.max_price == 400
    assert next(t for t in targets if t.name.startswith("RTX 4070")).pattern.search("rtx 5070 ti")


def test_matches_and_rejects():
    t = hunt.load_targets()
    assert hunt.match(t, offer("Gigabyte RTX 3060 12GB Eagle", 480)).name == "RTX 3060 12 GB"
    assert hunt.match(t, offer("MSI RTX 3060 Ti Ventus 2X", 500)).name == "RTX 3060 Ti 8 GB"
    assert hunt.match(t, offer("RTX 3060 12GB", 650)) is None                         # za drogo
    assert hunt.match(t, offer("RTX 2060 6GB", 350)) is None                          # za mało VRAM
    assert hunt.match(t, offer("RTX 2060", 350)) is None                              # VRAM musi być podany
    assert hunt.match(t, offer("RTX 2060 12GB", 390)).name == "RTX 2060 12 GB"
    assert hunt.match(t, offer("RTX 2060 Super", 390)).name == "RTX 2060 Super 8 GB"
    assert hunt.match(t, offer("RTX 3070", 300, "Brak obrazu, na części")) is None
    assert hunt.match(t, offer("RTX 3070", 450, "Bez artefaktów, w pełni sprawna")).name.startswith("RTX 3070")
    assert hunt.match(t, offer("Radiator RTX 3080", 100)) is None
    assert hunt.match(t, offer("Laptop MSI RTX 3060", 450)) is None
    assert hunt.match(t, offer("Kupię RTX 3070", 400)) is None
    assert hunt.match(t, offer("RTX 3060 12GB", 400, category="laptop")) is None
    assert hunt.match(t, offer("RX 580 8GB", 150)) is None


def test_hunter_alerts_once(tmp_path):
    sent = []

    class Push:
        cfg = SimpleNamespace(topic="t")

        async def send(self, msg):
            sent.append(msg)
            return True

    async def run():
        h = hunt.Hunter(log_dir=tmp_path)
        o = offer("Zotac RTX 3060 12GB", 450, oid=7)
        assert h.check(o, Push()) is not None and h.check(o, Push()) is None
        await asyncio.sleep(0)
    asyncio.run(run())
    assert len(sent) == 1 and sent[0]["priority"] == 5 and sent[0]["title"].startswith("POLOWANIE")
    assert '"target": "RTX 3060 12 GB"' in (tmp_path / "hunt.jsonl").read_text(encoding="utf-8")
