# Vinted Sniper – nowe oferty → ocena AI → powiadomienie o okazji

**Stan, cel, reguły, ustawienia, na co czekamy i następne kroki całego projektu (Sniper + analizator):
`PodolskiSzymon/analyse_data` → `STAN_PROJEKTU.md` – czytaj najpierw.** Historia Snipera (sesja konta, auto-zakup,
proxy, logi użytkownika do 25.10): `sniper/HISTORIA_SNIPERA.md` – tylko po szczegóły (`grep`). `sniper/CONTEXT.md` =
stara kopia, nie czytać.

## Zasady

* Po polsku (także komentarze w kodzie); konkretnie, z wynikami testów.
* Claude nie ma dostępu do vinted.pl – Sniper chodzi na serwerze użytkownika (Windows, 4 GB RAM,
  `C:\Users\szymo\unreachable_sniper`, `.venv`). Zmiany testujemy atrapami (`httpx.MockTransport`, atrapy API AI).
* Zapytania do Vinted tylko z prawdziwego ruchu przeglądarki (cURL z F12) – bez zgadywania. Nie obchodzić wykrywania
  botów (auto-zakup wyłączony, `SNIPER_BUY_ENABLED=false`, bo Vinted wylogowuje konto bota).
* Nie commitować sekretów (`.env`, klucze, hasła, tokeny, temat ntfy, `logs/`, `session.json`, profil przeglądarki).
* Gałąź jak w `analyse_data` (stan: `claude/gifted-volta-x1764p`), commit + push, bez PR. Bez identyfikatora modelu
  AI w commitach/kodzie. Testy: `python -m pytest sniper/tests -q` (245 + 1 pominięty).

## Moduły (`sniper/`)

| plik | rola |
|---|---|
| `__main__.py`, `scout.py` | pętle Zwiadowcy – jedna na proxy (`SNIPER_PROXY_<N>_HOST/_AUTH/_KATEGORIE`, `Lane`, osobne sesje): katalogi po kolei (wagi), nowe ID, przepełnienie strony (brak ofert poprzedniego skanu) → TEN katalog częściej o `SNIPER_POLL_STEP` s (do `SNIPER_POLL_MIN`), po `SNIPER_FAST_MINUTES` spokoju o krok wolniej, szczegóły przez to samo proxy, `emit` → evaluator; heartbeat |
| `categories.py` | katalogi z `guides/catalogs.json`, `apply_floor` (`SNIPER_PRICE_FLOOR`), `schedule`, kategoria z tytułu, sprzęt do testu |
| `session.py`, `proxy_relay.py`, `traffic.py` | httpx przez proxy, odświeżanie ciastek Playwrightem, licznik transferu |
| `fx.py` | kursy walut (wysyłka → zł) z pliku prebid, którego używa Vinted; co `SNIPER_FX_REFRESH_H` h, bez proxy |
| `extractor.py` | JSON → `Offer`; wysyłka w zł (`FX_TO_PLN`), kraj z waluty wysyłki |
| `config.py` | całe `.env` (`ScoutConfig`, `AiConfig` z `SNIPER_RULE_*`, `MODEL_PRICES`) |
| `evaluator.py` | `submit` (już widziana, słowa, bramka, limit $) → backend Gemini/Anthropic/OpenRouter (`Task`) → `enforce_profit_rule`, PL +1 → logi, raport, mail, ntfy |
| `experiment.py` | tryby `mediany` (`MEDIANS_TASK`, `finish_medians`, `cheap_guard`) i `model` (`formula_score`); CLI eksperymentu |
| `guides.py`, `comparables.py` | tabela wytycznych + dopasowanie po słowach; `comparables.db` (FTS5): `levels`, `lookup` (bramka „za drogo” w `evaluator.gate`) |
| `bench.py`, `bench_golden.json`, `basal.py` | porównanie wariantów oceny (jakość / koszt) na złotym zbiorze; tryb basal (nieprzetestowany na żywo) |
| `report.py`, `mirror.py`, `notifier.py`, `push.py` | `oceny.html` (+ `--stats`), kopia logów na Dysk, mail, ntfy |
| `account*.py`, `buyer.py`, `autobuy.py`, `check_detection.py` | konto i auto-zakup (wyłączone) |
| `diagnose.py`, `dedup.py` | diagnostyka zapytań, pamięć ID |

## API Vinted (sprawdzone na żywo)

* Katalog: `GET https://api.vinted.pl/svc-catalogue/items` (`page, per_page, price_from, currency=PLN,
  order=newest_first, attribute_ids[catalog]…`); puste `price_from=` → 400, wysyłać tylko z wartością.
  Nagłówki z cURL przeglądarki (`config.CATALOG_HEADERS`); `cf_clearance`/`datadome` związane z UA.
* Szczegóły: `www.vinted.pl/api/v2/items/{id}/details/sidebar` (`item_closing_action`: `"sold"` / `null`),
  `/api/v2/items/{id}/shipping_details` (cena w walucie sprzedawcy!). ID nadawane przy tworzeniu – w „najnowszych”
  bywają niższe ID (deduplikacja bez progu).
* Zakup (test 03.10): „Kup teraz” → `/checkout?purchase_id=…` → „Zapłać” → `POST /api/v2/purchases/{id}/checkout/payment`.
