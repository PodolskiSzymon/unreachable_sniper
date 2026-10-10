"""Kopia logów do folderu w chmurze (sniper/mirror.py) i podsumowanie ocen (report --stats)."""
import json
import os

from sniper import report
from sniper.mirror import LogMirror


def test_mirror_copies_only_changed_whitelisted_files(tmp_path):
    logs, cloud = tmp_path / "logs", tmp_path / "Mój dysk" / "sniper"
    logs.mkdir()
    (logs / "oceny.html").write_text("<html>1</html>", encoding="utf-8")
    (logs / "sniper.log").write_text("linia 1\n", encoding="utf-8")
    (logs / "session.json").write_text('{"tokeny": "SEKRET"}', encoding="utf-8")   # nie do chmury
    m = LogMirror(logs, cloud, interval_min=1)
    assert sorted(m.sync_once()) == ["oceny.html", "sniper.log"]
    assert not (cloud / "session.json").exists() and (cloud / "oceny.html").read_text(encoding="utf-8") == "<html>1</html>"
    assert m.sync_once() == []                                       # nic się nie zmieniło
    (logs / "sniper.log").write_text("linia 1\nlinia 2\n", encoding="utf-8")
    st = (logs / "sniper.log").stat()
    os.utime(logs / "sniper.log", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert m.sync_once() == ["sniper.log"] and "linia 2" in (cloud / "sniper.log").read_text(encoding="utf-8")
    assert not list(cloud.glob("*.tmp"))


def test_mirror_disabled_without_dir(tmp_path):
    from types import SimpleNamespace
    from sniper.mirror import build_mirror
    assert build_mirror(SimpleNamespace(mirror_dir="", log_dir=str(tmp_path))) is None
    assert build_mirror(SimpleNamespace(mirror_dir=str(tmp_path / "c"), log_dir=str(tmp_path),
                                        mirror_interval_min=2)) is not None


def test_stats_explains_empty_report(tmp_path):
    rows = [
        {"status": "odfiltrowana", "prefilter_reason": "słowo wykluczające „thinkpad”", "offer": {"title": "T"}},
        {"status": "odfiltrowana", "prefilter_reason": "słowo wykluczające „thinkpad”", "offer": {"title": "T2"}},
        {"status": "odfiltrowana", "prefilter_reason": "brak słowa kluczowego", "offer": {"title": "X"}},
        {"status": "oceniona", "evaluation": {"score": 4}, "offer": {"title": "Legion RTX 3060", "total_price": 2500}},
        {"status": "oceniona", "evaluation": {"score": 7}, "offer": {"title": "TUF RTX 4060", "total_price": 3000}},
    ]
    path = tmp_path / "evaluations.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    text = report.stats(path, above=5)
    assert "Ofert w evaluations.jsonl: 5" in text and "odfiltrowana 3" in text and "oceniona 2" in text
    assert "W oceny.html (ocena > 5): 1" in text and "2 x słowo wykluczające „thinkpad”" in text
    assert text.index("7/10 | TUF RTX 4060") < text.index("4/10 | Legion RTX 3060")
    assert "Brak pliku" in report.stats(tmp_path / "brak.jsonl", above=5)
