"""Porównanie wariantów oceny AI: jakość i koszt na tych samych ofertach (2026-10-27, prośba użytkownika: „jak
najlepsza jakość przy jak najniższym koszcie, priorytet jakość”). Bez maili, Sniper bez zmian.

    python -m sniper.bench --lista                     # opis wariantów
    python -m sniper.bench                             # domyślne warianty, 100 ostatnich ocen (najpierw koszt)
    python -m sniper.bench --warianty obecny,mediany,niska_rozdz --last 60
    python -m sniper.bench --wlasny "moj:model=gemini-3.1-flash-lite,max_photos=4,tryb=mediany"

Wariant = zmiana ustawień AI (model, liczba i rozdzielczość zdjęć, głębokość myślenia) + tryb oceny:
`zwykly` (jak Sniper), `mediany` / `model` (sniper/experiment.py), `kaskada` (najpierw tani model bez zdjęć, pełna
ocena tylko dla ofert z etapu 1 ≥ KASKADA_PROG albo okazji). Oferty: ostatnie oceny z logs/evaluations.jsonl + oferty
ze złotego zbioru, ta sama bramka co w Sniperze (próg ceny, prefiltr, tabela, podobne oferty) - odcięte nie kosztują.

Jakość:
  1. złoty zbiór `sniper/bench_golden.json` - oferty z ustaloną odpowiedzią (okazja tak / nie, zakres ceny sprzedaży):
     okazje trafione / przegapione / FAŁSZYWE, cena sprzedaży (S) w zakresie,
  2. zgodność z wariantem odniesienia (`--ref`, domyślnie pierwszy): ta sama decyzja, ocena ±1, S ±15%.
Koszt: $ na ofertę (z tokenów i cen MODEL_PRICES), tokeny, czas. Wynik: tabela + logs/bench_<czas>.jsonl.
"""
import argparse
import asyncio
import json
import logging
import time
from dataclasses import fields, replace
from pathlib import Path

from .evaluator import STATUS_EVALUATED, OfferEvaluator, _offer_price, _Stats, prefilter

log = logging.getLogger("sniper.bench")

LITE_MODEL = "gemini-3.1-flash-lite"
KASKADA_PROG = 4.0                   # etap 1 (tani, bez zdjęć) ≥ tyle albo okazja → pełna ocena
MODES = ("zwykly", "mediany", "model", "kaskada", "basal")
VARIANTS = {
    "obecny": ({}, "ustawienia z sniper/.env (jak Sniper, także SNIPER_EVAL_MODE)"),
    "mediany": ({"tryb": "mediany"}, "AI wycenia z kilku median z bazy, może poprawić model (eksperyment)"),
    "model": ({"tryb": "model"}, "AI tylko rozpoznaje, program wycenia gorszy wariant (eksperyment)"),
    "zdjecia3": ({"max_photos": 3}, "najwyżej 3 zdjęcia"),
    "bez_zdjec": ({"max_photos": 0}, "sam tekst"),
    "srednia_rozdz": ({"media_resolution": "medium"}, "zdjęcia w średniej rozdzielczości (~połowa tokenów)"),
    "niska_rozdz": ({"media_resolution": "low"}, "zdjęcia w niskiej rozdzielczości (~ćwierć tokenów)"),
    "mysl_niska": ({"effort": "low"}, "krótsze myślenie (mniej tokenów wyjścia)"),
    "lite": ({"model": LITE_MODEL}, f"tańszy model {LITE_MODEL}"),
    "lite_mediany": ({"model": LITE_MODEL, "tryb": "mediany"}, f"{LITE_MODEL} + mediany"),
    "mediany_srednia": ({"tryb": "mediany", "media_resolution": "medium"}, "mediany + średnia rozdzielczość"),
    "kaskada": ({"tryb": "kaskada"}, f"etap 1 {LITE_MODEL} bez zdjęć, pełna ocena tylko gdy ≥ {KASKADA_PROG:g}/10"),
}
# Dzień testowy 28.10 (użytkownik): tryby mediany / model / basal × Gemini 3.8 Flash / 2.5 Flash Lite × 3 / 6 zdjęć,
# myślenie medium → 12 wariantów na tych samych ofertach.
TEST_MODELS = {"38": "gemini-3.8-flash", "25l": "gemini-2.5-flash-lite"}
TEST_MODES = {"med": "mediany", "mod": "model", "bas": "basal"}
for _m, _mode in TEST_MODES.items():
    for _k, _model in TEST_MODELS.items():
        for _photos in (3, 6):
            VARIANTS[f"{_m}_{_k}_z{_photos}"] = (
                {"tryb": _mode, "model": _model, "max_photos": _photos, "effort": "medium"},
                f"{_mode}, {_model}, {_photos} zdjęć, myślenie medium" +
                (" (basal rozpoznaje → mediany → Gemini)" if _mode == "basal" else ""))
SETS = {"dzien_testowy": ",".join(f"{m}_{k}_z{p}" for m in TEST_MODES for k in TEST_MODELS for p in (3, 6))}
DEFAULT_VARIANTS = "obecny,mediany,zdjecia3,srednia_rozdz,niska_rozdz,mysl_niska,lite_mediany,kaskada"
GOLDEN_FILE = Path(__file__).with_name("bench_golden.json")

# Szacunek kosztu przed uruchomieniem (tokeny z ocen 26-27.10: ~13,6 tys. wejścia przy 6-12 zdjęciach).
TEXT_TOKENS, OUT_TOKENS = 4000, 1400
PHOTO_TOKENS = {"": 1100, "high": 1100, "medium": 560, "low": 280}


def parse_custom(text):
    """'moj:model=x,max_photos=4,tryb=mediany' → ('moj', {...})."""
    name, _, spec = text.partition(":")
    out = {}
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        key, _, value = part.partition("=")
        key, value = key.strip(), value.strip()
        out[key] = int(value) if value.isdigit() else value
    return name.strip(), out


def split_overrides(overrides):
    """→ (tryb, nadpisania AiConfig, model etapu 1 kaskady). Nieznane klucze = błąd (literówka w --wlasny)."""
    allowed = {f.name for f in fields_of_ai()}
    mode = overrides.get("tryb", "zwykly")
    if mode not in MODES:
        raise SystemExit(f"Nieznany tryb {mode!r} (dozwolone: {', '.join(MODES)})")
    cfg = {k: v for k, v in overrides.items() if k not in ("tryb", "etap1")}
    bad = sorted(set(cfg) - allowed)
    if bad:
        raise SystemExit(f"Nieznane ustawienia wariantu: {', '.join(bad)} (np. model, max_photos, media_resolution, "
                         f"effort)")
    return mode, cfg, overrides.get("etap1", LITE_MODEL)


def fields_of_ai():
    from .config import AiConfig
    return fields(AiConfig)


def load_offers(log_dir, last, golden_ids=()):
    """Ostatnie `last` różnych ocenionych ofert + oferty ze złotego zbioru → [(Offer, stara ocena)]."""
    from .extractor import Offer
    path = Path(log_dir) / "evaluations.jsonl"
    latest = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            offer = record.get("offer") or {}
            if record.get("status") == STATUS_EVALUATED and record.get("evaluation") and offer.get("id") \
                    and not str(record.get("source") or "").startswith(("experiment", "bench")):
                latest.pop(offer["id"], None)
                latest[offer["id"]] = record
    ids = list(latest)[-last:] if last else []
    ids += [i for i in latest if i in set(golden_ids) and i not in ids]
    return [(Offer.from_dict(latest[i]["offer"]), latest[i]["evaluation"]) for i in ids]


def load_golden(path=GOLDEN_FILE):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {int(k): v for k, v in (data.get("offers") or {}).items()}


def call_cost(evaluator, usage):
    stats = _Stats()
    for key, value in (usage or {}).items():
        if key in stats:
            stats[key] += value or 0
    return stats.cost_usd(evaluator._price_in, evaluator._price_out)


def _price(model, kind):
    from .config import MODEL_PRICES
    return MODEL_PRICES.get(model, (0.0, 0.0))[0 if kind == "in" else 1]


def estimate(base, variants, n_offers):
    """Szacunek kosztu całego porównania (USD) - przed pytaniem o zgodę."""
    total = 0.0
    for _name, (mode, over, stage1) in variants.items():
        cfg = replace(base, **over)
        tokens_in = TEXT_TOKENS + max(cfg.max_photos, 0) * PHOTO_TOKENS.get(cfg.media_resolution or "", 1100)
        one = (tokens_in * _price(cfg.model, "in") + OUT_TOKENS * _price(cfg.model, "out")) / 1e6
        if mode == "kaskada":
            one = (TEXT_TOKENS * _price(stage1, "in") + 1000 * _price(stage1, "out")) / 1e6 + 0.4 * one
        total += one * n_offers
    return total


class Bench:
    """Jedna instancja OfferEvaluator na wariant (ten sam kod co Sniper), wspólny limit równoległych wywołań."""

    def __init__(self, base, variants, concurrency=10, client=None, basal_client=None):
        self.variants = variants
        self.basal_client = basal_client
        self.evaluators = {}
        for name, (mode, over, stage1) in variants.items():
            cfg = replace(base, max_concurrent=concurrency, **over)
            ev = OfferEvaluator(cfg, notifier=None, log_dir=None, client=client)
            first = None
            if mode == "kaskada":
                first = OfferEvaluator(replace(cfg, model=stage1, max_photos=0), notifier=None, log_dir=None,
                                       client=client)
            self.evaluators[name] = (mode, ev, first)
        self.slots = asyncio.Semaphore(max(concurrency, 1))

    async def one(self, name, offer):
        mode, ev, first = self.evaluators[name]
        started = time.monotonic()
        async with self.slots:
            costs, usages, stage2 = [], [], True
            if mode == "kaskada":
                result, usage, error = await _zwykly(first, offer)
                costs.append(call_cost(first, usage))
                usages.append(usage)
                stage2 = error is None and (result["is_deal"] or result["score"] >= KASKADA_PROG)
                if not stage2:
                    return _row(name, offer, result, sum(costs), started, error, False, _merge(usages))
            if mode == "basal":
                from .basal import run as run_basal
                result, usage, _photos, error, info = await run_basal(ev, offer, client=self.basal_client)
                row = _row(name, offer, result, call_cost(ev, usage), started, error, True, usage)
                row["basal"] = info
                return row
            if mode in ("zwykly", "kaskada"):
                result, usage, error = await _zwykly(ev, offer)
            else:
                from .experiment import run_one
                result, usage, _photos, error = await run_one(ev, mode, offer)
            costs.append(call_cost(ev, usage))
            usages.append(usage)
            return _row(name, offer, result, sum(costs), started, error, stage2, _merge(usages))

    async def close(self):
        for _mode, ev, first in self.evaluators.values():
            await ev.close()
            if first:
                await first.close()


async def _zwykly(evaluator, offer):
    record = await evaluator.evaluate(offer)
    if record["status"] != STATUS_EVALUATED:
        return None, record.get("usage"), record.get("error") or record["status"]
    return record["evaluation"], record.get("usage"), None


def _merge(usages):
    out = {}
    for usage in usages:
        for key, value in (usage or {}).items():
            out[key] = out.get(key, 0) + (value or 0)
    return out


def _row(name, offer, ev, cost, started, error, stage2=True, usage=None):
    return {"variant": name, "id": offer.id, "evaluation": ev, "cost": cost, "seconds": time.monotonic() - started,
            "error": error, "stage2": stage2, "usage": usage}


def gate_reason(evaluator, offer, floor):
    """Powód pominięcia przed AI - to samo co Sniper (próg ceny katalogu, prefiltr, tabela, podobne oferty)."""
    price = getattr(offer, "price", None)
    if floor and price is not None and price < floor:
        return f"cena < {floor:g} zł (SNIPER_PRICE_FLOOR)"
    return prefilter(offer, evaluator.cfg) or evaluator.gate(offer)


async def run_bench(base, offers, variants, golden, concurrency=10, client=None, floor=0.0, use_gate=True,
                    progress=None, basal_client=None):
    """→ (wiersze wyników, pominięte przed AI {id: powód})."""
    bench = Bench(base, variants, concurrency, client, basal_client)
    say = progress or (lambda _t: None)
    try:
        gatekeeper = next(iter(bench.evaluators.values()))[1]
        skipped = {o.id: gate_reason(gatekeeper, o, floor) for o, _old in offers} if use_gate else {}
        skipped = {k: v for k, v in skipped.items() if v}
        todo = [(name, o) for name in variants for o, _old in offers if o.id not in skipped]
        rows, done = [], 0

        async def job(name, offer):
            nonlocal done
            row = await bench.one(name, offer)
            done += 1
            say(f"  [{done}/{len(todo)}] {name:<16} {offer.id} {(offer.title or '')[:40]}")
            return row

        rows = await asyncio.gather(*(job(n, o) for n, o in todo))
        return list(rows), skipped
    finally:
        await bench.close()


def _is_deal(row):
    return bool(row and row["evaluation"] and row["evaluation"].get("is_deal"))


def summary(rows, skipped, offers, golden, ref, variants):
    """Linie tabeli: koszt, okazje, złoty zbiór, zgodność z odniesieniem + lista spornych okazji."""
    by = {}
    for row in rows:
        by.setdefault(row["variant"], {})[row["id"]] = row
    ids = [o.id for o, _old in offers]
    titles = {o.id: (o.title or "", _offer_price(o)) for o, _old in offers}
    gold = {i: g for i, g in golden.items() if i in ids}
    lines = [f"Oferty: {len(ids)}, do AI {len(ids) - len(skipped)} (bramka odcięła {len(skipped)}), złoty zbiór "
             f"{len(gold)} (okazji {sum(1 for g in gold.values() if g.get('deal'))}). Odniesienie: {ref}.",
             "",
             "wariant           $/ofertę  $ razem  tok.we/wy   s/of. błędy okazje 8+ | złote: trafione / przegap. / "
             "FAŁSZYWE  S w zakr. | vs ref: decyzja  ±1  S±15%"]
    for name in variants:
        mine = by.get(name, {})
        ok = [r for r in mine.values() if r["evaluation"]]
        n = max(len(mine), 1)
        cost = sum(r["cost"] for r in mine.values())
        tin = sum(_tokens_in(r["usage"]) for r in mine.values()) / n
        tout = sum((r["usage"] or {}).get("output_tokens", 0) for r in mine.values()) / n
        secs = sum(r["seconds"] for r in mine.values()) / n
        deals = sum(1 for r in ok if _is_deal(r))
        high = sum(1 for r in ok if r["evaluation"]["score"] >= 8)
        tp = fn = fp = 0
        in_range = with_range = 0
        for i, g in gold.items():
            r = mine.get(i)
            predicted = _is_deal(r)
            if g.get("deal") and predicted:
                tp += 1
            elif g.get("deal"):
                fn += 1
            elif predicted:
                fp += 1
            sale = r and r["evaluation"] and r["evaluation"].get("market_value_pln")
            if g.get("sale") and sale:
                with_range += 1
                in_range += g["sale"][0] <= sale <= g["sale"][1]
        agree = _agreement(mine, by.get(ref, {})) if name != ref else None
        lines.append(
            f"{name:<16} {cost / n:>8.4f} {cost:>8.3f} {tin:>6.0f}/{tout:<5.0f} {secs:>5.1f} {len(mine) - len(ok):>5} "
            f"{deals:>6} {high:>2} | {tp:>14} / {fn:>8} / {fp:>8}  {_frac(in_range, with_range):>9} | "
            + ("        (odniesienie)" if agree is None else f"{agree[0]:>14} {agree[1]:>4} {agree[2]:>5}"))
    basal_rows = [r for r in rows if r.get("basal")]
    if basal_rows:
        times = sorted(r["basal"].get("latency_ms") or 0 for r in basal_rows)
        other = sum(1 for r in basal_rows if not r["basal"].get("choice"))
        lines += ["", f"basal: {len(basal_rows)} rozpoznań, mediana czasu {times[len(times) // 2]:.0f} ms, "
                      f"„inny / nie wiadomo” {other} ({other / len(basal_rows):.0%})"]
    disputed = [i for i in ids if any(_is_deal(by.get(v, {}).get(i)) for v in variants)
                or (gold.get(i) or {}).get("deal")]
    if disputed:
        lines += ["", "Okazje (✓ = okazja wg wariantu; złote: T/N = ustalona odpowiedź):"]
        for i in disputed:
            title, price = titles[i]
            marks = " ".join(
                f"{v}:{by[v][i]['evaluation']['score']:g}{'✓' if _is_deal(by[v][i]) else ''}"
                if i in by.get(v, {}) and by[v][i]["evaluation"] else f"{v}:-" for v in variants)
            g = gold.get(i)
            lines.append(f"  {i} {title[:34]:<34} {price or 0:>6.0f} zł  złote {('T' if g['deal'] else 'N') if g else '?'}"
                         f" | {marks}")
    return lines


def _tokens_in(usage):
    u = usage or {}
    return u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)


def _frac(a, b):
    return f"{a}/{b}" if b else "-"


def _agreement(mine, ref):
    both = [i for i in mine if i in ref and mine[i]["evaluation"] and ref[i]["evaluation"]]
    if not both:
        return "-", "-", "-"
    same = sum(1 for i in both if _is_deal(mine[i]) == _is_deal(ref[i]))
    near = sum(1 for i in both if abs(mine[i]["evaluation"]["score"] - ref[i]["evaluation"]["score"]) <= 1)
    priced = [i for i in both if mine[i]["evaluation"].get("market_value_pln") and
              ref[i]["evaluation"].get("market_value_pln")]
    close = sum(1 for i in priced if abs(mine[i]["evaluation"]["market_value_pln"] /
                                         ref[i]["evaluation"]["market_value_pln"] - 1) <= 0.15)
    pct = lambda a, b: f"{a / b * 100:.0f}%" if b else "-"  # noqa: E731
    return pct(same, len(both)), pct(near, len(both)), pct(close, len(priced))


def resolve_variants(names, custom=()):
    out = {}
    names = ",".join(SETS.get(n.strip(), n.strip()) for n in names.split(","))
    for name in [n.strip() for n in names.split(",") if n.strip()]:
        if name not in VARIANTS:
            raise SystemExit(f"Nieznany wariant {name!r} - lista: python -m sniper.bench --lista")
        out[name] = split_overrides(VARIANTS[name][0])
    for text in custom:
        name, over = parse_custom(text)
        out[name] = split_overrides(over)
    return out


async def basal_alive():
    import httpx
    from .basal import BASAL_URL
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            return (await client.get(f"{BASAL_URL}/health")).status_code == 200
    except httpx.HTTPError:
        return False


async def _cli(argv=None):
    from .config import ScoutConfig

    parser = argparse.ArgumentParser(description="Porównanie wariantów oceny AI: jakość i koszt.")
    parser.add_argument("--warianty", default=DEFAULT_VARIANTS)
    parser.add_argument("--wlasny", action="append", default=[], help='np. "moj:model=x,max_photos=4,tryb=mediany"')
    parser.add_argument("--ref", default=None, help="wariant odniesienia (domyślnie pierwszy)")
    parser.add_argument("--last", type=int, default=100, help="ile ostatnich ocenionych ofert (domyślnie 100)")
    parser.add_argument("--rownolegle", type=int, default=10, help="wywołań AI naraz (domyślnie 10)")
    parser.add_argument("--bez-bramki", action="store_true", help="wszystkie oferty do AI (bez bramki Snipera)")
    parser.add_argument("--tak", action="store_true", help="bez pytania o zgodę na koszt")
    parser.add_argument("--lista", action="store_true", help="opis wariantów")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if args.lista:
        for name, (over, text) in VARIANTS.items():
            print(f"  {name:<16} {text}")
        for name, value in SETS.items():
            print(f"  zestaw {name}: {value}")
        return 0
    cfg = ScoutConfig()
    if not cfg.ai.api_key:
        print("Brak klucza: ustaw SNIPER_AI_API_KEY (albo GEMINI_API_KEY) w sniper/.env.")
        return 1
    variants = resolve_variants(args.warianty, args.wlasny)
    if any(mode == "basal" for mode, _o, _s in variants.values()) and not await basal_alive():
        from .basal import BASAL_URL
        print(f"⚠ Serwer basal nie odpowiada ({BASAL_URL}/health) - warianty basal pominięte. Uruchom basal-serve "
              f"albo ustaw SNIPER_BASAL_URL.")
        variants = {k: v for k, v in variants.items() if v[0] != "basal"}
        if not variants:
            return 1
    ref = args.ref or next(iter(variants))
    golden = load_golden()
    offers = load_offers(cfg.log_dir, args.last, golden)
    if not offers:
        print(f"Brak ocen w {Path(cfg.log_dir) / 'evaluations.jsonl'}.")
        return 1
    probe = OfferEvaluator(cfg.ai, notifier=None, log_dir=None)
    if probe.comparables.path is None or not probe.comparables.path.exists():
        print(f"⚠ Brak {probe.comparables.path} - bramka i mediany bez danych (uruchom w analizatorze sniper-guide).")
    floor = getattr(cfg, "price_floor", 0.0)
    passing = len(offers) if args.bez_bramki else sum(1 for o, _ in offers if not gate_reason(probe, o, floor))
    await probe.close()
    cost = estimate(cfg.ai, variants, passing)
    print(f"{len(offers)} ofert (złotych {sum(1 for o, _ in offers if o.id in golden)}), do AI ~{passing} × "
          f"{len(variants)} wariantów: {', '.join(variants)}. Szacunek kosztu ~${cost:.2f}.")
    if not args.tak and input("Uruchomić? [t/N] ").strip().lower() not in ("t", "tak", "y"):
        return 0
    started = time.monotonic()
    rows, skipped = await run_bench(cfg.ai, offers, variants, golden, args.rownolegle, floor=floor,
                                    use_gate=not args.bez_bramki, progress=lambda t: print(t, flush=True))
    lines = summary(rows, skipped, offers, golden, ref, variants)
    print("\n" + "\n".join(lines))
    stamp = time.strftime("%Y%m%d_%H%M")
    out = Path(cfg.log_dir) / f"bench_{stamp}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps({**row, "source": "bench"}, ensure_ascii=False, default=str) + "\n")
    (out.with_suffix(".txt")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nSzczegóły: {out} (+ .txt z tabelą - wyślij Claude). Koszt ~${sum(r['cost'] for r in rows):.2f}, "
          f"czas {(time.monotonic() - started) / 60:.1f} min.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_cli()))
