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
  AI w commitach/kodzie. Testy: `python -m pytest sniper/tests -q` (326 + 1 pominięty).

## Moduły (`sniper/`)

| plik | rola |
|---|---|
| `__main__.py`, `scout.py` | pętle Zwiadowcy – jedna na proxy (`SNIPER_PROXY_<N>_HOST/_AUTH/_KATEGORIE`, `Lane`, osobne sesje): katalogi po kolei (wagi), nowe ID, przepełnienie strony (brak ofert poprzedniego skanu) → TEN katalog częściej o `SNIPER_POLL_STEP` s (do `SNIPER_POLL_MIN`), po `SNIPER_FAST_MINUTES` spokoju o krok wolniej, szczegóły przez to samo proxy, `emit` → evaluator; heartbeat |
| `categories.py` | katalogi z `guides/catalogs.json` (od 31.10 podzespoły z własnych katalogów 3602/3603/3607/3599/3600/3601; ogólne 3582/3583/3584 pomijane), `apply_floor` (`SNIPER_PRICE_FLOOR`), `schedule`, sprzęt do testu |
| `session.py`, `proxy_relay.py`, `traffic.py` | httpx przez proxy, odświeżanie ciastek Playwrightem, licznik transferu |
| `fx.py` | kursy walut (wysyłka → zł) z pliku prebid, którego używa Vinted; co `SNIPER_FX_REFRESH_H` h, bez proxy |
| `extractor.py` | JSON → `Offer`; wysyłka w zł (`FX_TO_PLN`), kraj z waluty wysyłki |
| `config.py` | całe `.env` (`ScoutConfig`, `AiConfig` z `SNIPER_RULE_*`, `MODEL_PRICES`) |
| `evaluator.py` | `submit` (`route` - wiersze rodziny i kategoria z tytułu, już widziana, słowa, bramka tabeli i podobnych wg reguły zysku, bez danych = bez AI, limit $) → ocena dwuetapowa (szybka: myślenie low + zdjęcia medium; pełna tylko dla okazji / ≥ 6) przez Gemini/Anthropic/OpenRouter (`Task`) → `enforce_profit_rule`, PL +1 → logi, raport, mail, ntfy |
| `experiment.py` | tryby `mediany` (`MEDIANS_TASK`, `finish_medians`, `cheap_guard`) i `model` (`formula_score`); CLI eksperymentu |
| `specs.py` | specyfikacja laptopa (karta / procesor + generacja / RAM / dysk / MacBook) z tytułu, opisu i formularza - te same reguły co `analyse_data/analyzer/extract.py` + `comparables.laptop_spec` (test `SPEC_PARITY` w obu repo); `spec_from_ai` (AI odczytuje specyfikację, gdy tekst jej nie ma) |
| `guides.py`, `comparables.py` | tabela wytycznych + dopasowanie po słowach; `route` / `family_rows` (31.10): do AI tylko wiersze rodziny oferty (model + warianty, opis, rodzina / marka, sąsiednie kategorie), `rules_text` (stała legenda); `comparables.db` (FTS5): `levels`, `lookup` (bramka „za drogo” w `evaluator.gate`), od 01.11 `spec_levels` (laptopy po specyfikacji, tabela `laptop_specs`) |
| `market.py` | reguły rynku po AI. **Od 16.11 popyt** (`analyse_data/POPYT.md`, plik `comparables.db` z flagami popytu z analizatora): szacunek = od 17.11 **dolne 10% cen aktywnych ofert** poziomu (klasa gorący → dolne 25%, martwy → 5%: `SNIPER_VALUE_SHARE*`; dodatki w tytule - folio, Marker Plus, Pencil, klawiatura, etui - nie wybierają grupy: `comparables.strip_accessories`; pomysł użytkownika; 70% szybkich sprzedaży było po tej cenie albo drożej), przy < 5 aktywnych cena ofert, które zeszły (`comparables.apply_clearing`, tylko telefony / tablety / konsole; nigdy ponad dawny szacunek), klasa `demand` (gorący / normalny / wolny / martwy z udziału nowych ofert znikających w tydzień i liczby takich zniknięć na tydzień, `SNIPER_DEMAND_*`): martwy = okazja tylko ≤ 50% szacunku (też bramka), wolny = zysk × 1,5 i ocena -1, gorący = +0,5 i podpowiedź „wystaw ~X”; wycena AI najwyżej szacunek × 1,2 / 1,1 / 1,0 wg klasy (`value_factor`); od 17.11 zniknięcie ≠ sprzedaż: gorący tylko przy ≥ 2 potwierdzonych sprzedażach, bez żadnej sprzedaży wycena AI ≤ szacunek × 1,0, a ≥ 10 zniknięć bez sprzedaży = wolny (iPad 10 64 GB); wariant `price_factor`: bez pudełka × 0,88, bateria < 80% × 0,95 (telefony), gwarancja / faktura × 1,05; linia „Popyt” w ntfy i mailu. Ze starszym plikiem - dawne reguły rotacji (15.11: nisza, `SNIPER_NICHE_*`, `SNIPER_MIN_TURNOVER`). Czytniki i tablety graficzne (`LOW_DEMAND`) zawsze ≤ `SNIPER_LOW_DEMAND_MAX_SHARE` (0,3) szacunku; blok `<rynek>` dla AI |
| `hunt.py`, `hunt_for_basal.md` | polowanie na kartę pod basala (`SNIPER_HUNT_FOR_GPU=true`, cele w `SNIPER_HUNT_FILE`): każda nowa oferta przed filtrami i AI - wzorzec w tytule, cena ≤ progu, VRAM ≥ minimum, bez uszkodzonych / części / laptopów; trafienie = ntfy priorytet 5 + `logs/hunt.jsonl` |
| `bench.py`, `bench_golden.json`, `basal.py` | porównanie wariantów oceny (jakość / koszt) na złotym zbiorze; tryb basal (nieprzetestowany na żywo) |
| `report.py`, `mirror.py`, `notifier.py`, `push.py` | `oceny.html` (+ `--stats`), kopia logów na Dysk, mail, ntfy |
| `account*.py`, `buyer.py`, `autobuy.py`, `check_detection.py` | konto i auto-zakup (wyłączone) |
| `diagnose.py`, `dedup.py` | diagnostyka zapytań, pamięć ID |

## API Vinted (sprawdzone na żywo)

* Katalog: `GET https://api.vinted.pl/svc-catalogue/items` (`page, per_page, price_from, currency=PLN,
  order=newest_first, attribute_ids[catalog]…`); puste `price_from=` → 400, wysyłać tylko z wartością.
  Nagłówki z cURL przeglądarki (`config.CATALOG_HEADERS`); `cf_clearance`/`datadome` związane z UA.
* Szczegóły: `www.vinted.pl/api/v2/items/{id}/details/sidebar` (`item_closing_action`: `"sold"` / `null`),
  `/api/v2/items/{id}/shipping_details` (cena w walucie sprzedawcy!) – **od 16.11 zwraca 404 (strona HTML „La page
  n'existe pas”) dla wszystkich ofert: czekamy na nowe API wysyłki (cURL z F12 od użytkownika)**; od 17.11 Sniper
  o wysyłkę nie pyta (`SNIPER_FETCH_SHIPPING=false`), koszt = cena + opłata + `SNIPER_SHIPPING_FALLBACK_PLN` (15 zł),
  a alert podaje „Okazja, jeśli wysyłka ≤ X zł” (X = max zakup − cena − opłata). ID nadawane przy tworzeniu – w
  „najnowszych” bywają niższe ID (deduplikacja bez progu).
* Zakup (test 03.10): „Kup teraz” → `/checkout?purchase_id=…` → „Zapłać” → `POST /api/v2/purchases/{id}/checkout/payment`.
