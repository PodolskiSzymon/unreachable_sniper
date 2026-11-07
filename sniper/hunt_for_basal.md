# Polowanie na kartę pod basala (`SNIPER_HUNT_FOR_GPU=true`)

Cel: karta 3–4× szybsza od RX 580 8 GB w llama.cpp (basal 4.5B), co najmniej 8 GB VRAM (4.5B Q8_0 ≈ 4,8 GB +
kontekst), cena do 500 zł. Szybkość to szacunek z mocy obliczeniowej (rdzenie tensor / CUDA w RTX), nie pomiar
basala. Progi poniżej to „bardzo atrakcyjne” ceny - sporo pod typowym rynkiem (mediany ofert z bazy 07.11 i ceny
rynkowe) - więc alarm ma być rzadki, ale wart natychmiastowej reakcji.

Sniper sprawdza każdą nową ofertę (niezależnie od oceny AI): wzorzec karty w TYTULE, cena przedmiotu ≤ „maks. zł”,
VRAM nie mniejszy niż „min. VRAM” (z `!` = VRAM musi być podany w tytule albo opisie, bo model ma też wersję
z mniejszą pamięcią). Odrzuca: uszkodzone / na części / brak obrazu, „kupię / zamienię”, same radiatory / bloki
wodne, laptopy i całe komputery, wersje mobilne. Alarm: ntfy (priorytet 5) + wpis w `logs/hunt.jsonl`.

Wiersze tabeli można zmieniać (ceny, nowe karty) - Sniper czyta plik przy starcie. Kolumna „wzorzec” to regex
na tytule małymi literami, bez polskich znaków.

| karta | wzorzec | maks. zł | min. VRAM | szybkość vs RX 580 (szacunek) | rynek (orientacyjnie) |
|---|---|---|---|---|---|
| RTX 2060 12 GB | `2060(?!\s?(?:s|super))` | 400 | 12! | ok. 3× | 6 GB ~550–600 zł (6 GB odpada) |
| RTX 2060 Super 8 GB | `2060\s?(?:s\b|super)` | 400 | 8 | ok. 3× | ~450–550 zł |
| RTX 2070 8 GB | `2070(?!\s?(?:s\b|super))` | 420 | 8 | ok. 3–3,5× | ~500–600 zł |
| RTX 2070 Super 8 GB | `2070\s?(?:s\b|super)` | 450 | 8 | ok. 3,5× | ~550–650 zł |
| RTX 2080 / 2080 Super 8 GB | `2080(?!\s?ti)` | 480 | 8 | ok. 3,5–4× | ~650–800 zł |
| RTX 2080 Ti 11 GB | `2080\s?ti` | 500 | 11 | ok. 4–5× | ~900–1100 zł |
| RTX 3060 12 GB | `3060(?!\s?ti)` | 500 | 8 | ok. 3–3,5× | ~900–950 zł |
| RTX 3060 Ti 8 GB | `3060\s?ti` | 500 | 8 | ok. 4× | ~1000–1200 zł |
| RTX 3070 / 3070 Ti 8 GB | `3070` | 500 | 8 | ok. 4–5× | ~1100–1400 zł |
| RTX 3080 10 / 12 GB | `3080` | 500 | 10 | ok. 5×+ | ~1500+ zł |
| RTX 4060 8 GB | `4060(?!\s?ti)` | 500 | 8 | ok. 4× | ~1000 zł |
| RTX 4060 Ti 8 / 16 GB | `4060\s?ti` | 500 | 8 | ok. 4–5× | ~1300+ zł |
| RTX 4070 i wyżej / RTX 50 | `40[789]0|50[6-9]0` | 500 | 8 | ok. 5×+ | ~2000+ zł (prawie na pewno oszustwo albo uszkodzona) |
