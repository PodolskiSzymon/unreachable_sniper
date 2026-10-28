"""Tryb „basal” (dzień testowy 2026-10-28, pomysł użytkownika): mały lokalny model decyzyjny rozpoznaje ofertę,
program dobiera z tego mediany z bazy, a dopiero Gemini wystawia ocenę.

basal (https://github.com/rkinas/basal, np. basal-omni-pico 0,8B na CPU) NIE generuje tekstu - zwraca
prawdopodobieństwa odpowiedzi, które mu podamy. Dlatego „zapytania do bazy” generuje program: kandydaci = wiersze
tabeli wytycznych kategorii (guides/<kategoria>.md) najbardziej podobne do tytułu i opisu, plus „inny”. basal wybiera
kandydata (choice) i odpowiada tak / nie na pytania o akcesorium, uszkodzenie i kontakt poza Vinted (noul). Program:
wybrany wiersz → `comparables.levels()` (mediany od szczegółowej do rodziny) → Gemini z trybem „mediany”
(MEDIANS_TASK) + rozpoznanie basal jako wskazówka → `finish_medians` (reguła zysku, przycięcie, PL, „bardzo tanio”).

API wg dokumentacji projektu (docs/API.md, v2.4.1), NIEPRZETESTOWANE na żywo: POST {url}/v1/systemone
  {"state": "...", "questions": {"model": {"type": "choice", "instructions": "...", "criteria": {klucz: opis}},
                                 "akcesorium": {"type": "noul", "instructions": "..."}}}
  → {"answers": {"model": {"choice": klucz, "probabilities": {...}, "confidence": p},
                 "akcesorium": {"noul": p_tak, ...}}, "usage": {"latency_ms": t}}
Serwer: `basal-serve ... --port 8000` (adres: SNIPER_BASAL_URL, domyślnie http://127.0.0.1:8000).
"""
import os
import time

import httpx

from .guides import tokens

BASAL_URL = os.getenv("SNIPER_BASAL_URL", "http://127.0.0.1:8000").rstrip("/")
MAX_CANDIDATES = 15                  # tryb ollama przyjmuje najwyżej 20 opcji na pytanie
DESCRIPTION_CHARS = 1500
OTHER = "inny"
FLAGS = {
    "akcesorium": "Czy sprzedawany jest tylko dodatek, część albo akcesorium (pad, etui, gra, pudełko, ładowarka, "
                  "kabel, obudowa) zamiast całego urządzenia?",
    "uszkodzony": "Czy z ogłoszenia wynika, że urządzenie jest uszkodzone, na części, nie działa albo jest zablokowane "
                  "(iCloud, konto Google, simlock)?",
    "poza_vinted": "Czy sprzedawca chce kontaktu, płatności albo wysyłki poza Vinted (telefon, WhatsApp, przelew, "
                   "tylko kurier wskazany przez niego)?",
}


def candidates(rows, offer, limit=MAX_CANDIDATES):
    """Wiersze tabeli wytycznych najbardziej podobne do tytułu i początku opisu → [(klucz, Row)]."""
    text = set(tokens(f"{offer.title or ''} {(offer.description or '')[:300]}"))
    scored = []
    for row in rows:
        core = row.core_words or tokens(row.name)
        if not core:
            continue
        hit = len(set(core) & text) / len(set(core))
        if hit > 0:
            scored.append((hit, len(core), row))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [(f"m{i + 1}", row) for i, (_h, _n, row) in enumerate(scored[:limit])]


def state_text(offer):
    price = f"{offer.price:.0f} zł" if offer.price is not None else "?"
    return (f"Ogłoszenie z Vinted.\nTytuł: {offer.title or ''}\nCena: {price}\nStan (formularz): "
            f"{offer.condition or '?'}\nOpis: {(offer.description or '')[:DESCRIPTION_CHARS]}")


def build_request(offer, cands):
    questions = {}
    if cands:                        # choice wymaga ≥ 2 opcji - bez kandydatów pytamy tylko o flagi
        criteria = {key: row.name for key, row in cands}
        criteria[OTHER] = "żaden z powyższych / nie da się ustalić"
        questions["model"] = {"type": "choice", "instructions": "Który model (i wariant pamięci) jest sprzedawany?",
                              "criteria": criteria}
    for key, text in FLAGS.items():
        questions[key] = {"type": "noul", "instructions": text}
    return {"state": state_text(offer), "questions": questions}


async def ask(offer, cands, url=BASAL_URL, client=None):
    """→ (odpowiedzi basal, czas w ms). Rzuca httpx.HTTPError przy niedostępnym serwerze."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=60.0, trust_env=False)
    started = time.monotonic()
    try:
        response = await client.post(f"{url}/v1/systemone", json=build_request(offer, cands))
        response.raise_for_status()
        data = response.json()
    finally:
        if own:
            await client.aclose()
    latency = (data.get("usage") or {}).get("latency_ms") or (time.monotonic() - started) * 1000
    return data.get("answers") or {}, latency


def describe(answers, cands):
    """Tekst dla Gemini (<rozpoznanie_basal>) + (wybrany Row albo None, flagi {nazwa: p_tak})."""
    rows = dict(cands)
    model = answers.get("model") or {}
    probs = model.get("probabilities") or {}
    chosen = model.get("choice")
    flags = {k: float((answers.get(k) or {}).get("noul") or 0.0) for k in FLAGS}
    top = sorted(probs.items(), key=lambda kv: -kv[1])[:3]
    lines = ["Wstępne rozpoznanie małym modelem decyzyjnym (może się mylić - sprawdź ze zdjęciami i opisem):",
             "  model: " + ", ".join(f"{rows[k].name if k in rows else 'inny / nie wiadomo'} ({p:.0%})" for k, p in top)]
    lines += [f"  {name.replace('_', ' ')}: {p:.0%}" for name, p in flags.items()]
    return "\n\n<rozpoznanie_basal>\n" + "\n".join(lines) + "\n</rozpoznanie_basal>", rows.get(chosen), flags


async def run(evaluator, offer, url=BASAL_URL, client=None):
    """Cały tryb dla jednej oferty → (ocena, usage, zdjęcia, błąd, info basal)."""
    from .comparables import describe_levels
    from .evaluator import market_card
    from .experiment import MEDIANS_TASK, finish_medians
    category = getattr(offer, "category", None)
    try:
        rows = evaluator.guides._file(category).rows
    except OSError as exc:
        return None, None, 0, f"brak wytycznych: {exc}", {}
    cands = candidates(rows, offer)
    try:
        answers, latency = await ask(offer, cands, url, client)
    except (httpx.HTTPError, ValueError) as exc:
        return None, None, 0, f"basal niedostępny ({url}): {exc!r}", {}
    hint_basal, chosen, flags = describe(answers, cands)
    query = chosen.name if chosen else offer.title
    levels = evaluator.comparables.levels(category, query)
    hint = market_card([chosen] if chosen else evaluator.guides.match(category, offer.title)) + \
        describe_levels(levels) + hint_basal
    info = {"choice": chosen.name if chosen else None, "query": query, "flags": flags, "latency_ms": latency,
            "candidates": [r.name for _k, r in cands]}
    try:
        async with evaluator._slots:
            ev, usage, _attempts, photos = await evaluator._call_model(offer, hint=hint, task=MEDIANS_TASK)
    except Exception as exc:
        return None, None, 0, f"{type(exc).__name__}: {exc}", info
    ev["levels"] = [{k: lv[k] for k in ("label", "n_active", "n_gone", "median", "estimate")} for lv in levels]
    ev["basal"] = info
    return finish_medians(ev, offer, evaluator.cfg, levels), usage, photos, None, info
