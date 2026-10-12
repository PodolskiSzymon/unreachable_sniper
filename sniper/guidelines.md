<!--
  Wytyczne oceny ofert dla modelu AI (sniper/evaluator.py).
  Edytuj śmiało - Zwiadowca wczytuje ten plik ponownie przy następnej ocenie po zmianie (bez restartu).
  Inny plik wskażesz przez SNIPER_AI_GUIDELINES w sniper/.env.
  Ten komentarz HTML nie jest wysyłany do modelu.
-->

ANALIZA CENOWA I STRATEGIA FLIPOWANIA LAPTOPÓW
Cel: zysk proporcjonalny do ceny laptopa - minimum 500 zł, 1000 zł przy laptopie za 3500 zł, powyżej tego zysk
rośnie wolniej (żeby droższe laptopy nadal dało się kupić).

WYMAGANY ZYSK (S = realna cena szybkiej sprzedaży):
- S do 3500 zł: zysk = 28,5% z S, ale nie mniej niż 500 zł
  (np. S 1800 zł -> 500 zł, S 2400 zł -> 700 zł, S 3000 zł -> 850 zł, S 3500 zł -> 1000 zł)
- S powyżej 3500 zł: zysk = 1000 zł + 15% z nadwyżki ponad 3500 zł
  (np. S 4300 zł -> 1100 zł, S 6000 zł -> 1400 zł, S 9000 zł -> 1850 zł, S 17 000 zł -> 3000 zł)
Kwoty zaokrąglone do 50 zł.

GŁÓWNY WZÓR:
Maksymalna Cena Zakupu = Realna Cena Sprzedaży - Wymagany Zysk - 150 zł
(150 zł na prowizje, koszty przesyłki i margines negocjacyjny). Realna cena sprzedaży = DOLNA granica widełek
(szybka sprzedaż), dla RTX 4070 - 4300 zł. Progi w tabeli niżej są już policzone tym wzorem i zawierają korektę
dla serii 3000 (-250 zł) - NIE odejmuj jej drugi raz.

--- ZESTAWIENIE MODELI I WIDEŁKI CENOWE ---

1. RTX 3050 / 3050 Ti
- Szacowana cena sprzedaży: 1800 - 2200 zł
- Wymagany zysk: 500 zł
- Maksymalna cena zakupu: poniżej 900 zł
- Notatki: Bardzo niska rentowność. Rynkowo na OLX wiszą za ok. 1570 zł. Kupować tylko przy skrajnych okazjach.

2. RTX 4050
- Szacowana cena sprzedaży: 2400 - 2500 zł
- Wymagany zysk: 700 zł
- Maksymalna cena zakupu: poniżej 1550 zł
- Notatki: Świetna budżetowa karta z DLSS 3. Gwarancja bardzo szybkiej płynności sprzedaży.

3. RTX 3060
- Szacowana cena sprzedaży: 2400 - 2500 zł
- Wymagany zysk: 700 zł
- Maksymalna cena zakupu: poniżej 1300 zł
- Notatki: Wymaga większego dyskonta niż 4050, by skompensować klientowi brak DLSS 3 i starszą architekturę.

4. RTX 4060
- Szacowana cena sprzedaży: 3050 - 3150 zł
- Wymagany zysk: 850 zł
- Maksymalna cena zakupu: poniżej 2050 zł
- Notatki: Obecnie najbardziej pożądana karta na rynku. Idealny materiał na błyskawiczny flip.

5. RTX 3070 / 3070 Ti
- Szacowana cena sprzedaży: 3400 - 3600 zł
- Wymagany zysk: 950 zł
- Maksymalna cena zakupu: poniżej 2050 zł
- Notatki: Mocna w rasteryzacji, rynek wycenia ją stabilnie. Bezpieczna marża przy zachowaniu dyscypliny cenowej przy zakupie.

6. RTX 4070
- Szacowana cena sprzedaży: 4000 - 4500 zł (realna szybka sprzedaż ok. 4300 zł)
- Wymagany zysk: 1100 zł
- Maksymalna cena zakupu: poniżej 3050 zł
- Notatki: Klienci w tym segmencie są bardziej wymagający.

7. RTX 3080 (16 GB VRAM)
- Szacowana cena sprzedaży: 4600 - 6000 zł
- Wymagany zysk: 1150 zł
- Maksymalna cena zakupu: poniżej 3050 zł
- Notatki: Główna dźwignia to 16 GB VRAM. Właściwe reklamowanie i pozycjonowanie na tle nowszego modelu 4070 (ograniczonego do 8 GB) pozwala na domykanie sprzedaży w górnych widełkach.

8. RTX 5060
- Szacowana cena sprzedaży: 3700 - 3800 zł
- Wymagany zysk: 1050 zł
- Maksymalna cena zakupu: poniżej 2500 zł

9. RTX 5070
- Szacowana cena sprzedaży: 4800 - 5200 zł
- Wymagany zysk: 1200 zł
- Maksymalna cena zakupu: poniżej 3450 zł

10. RTX 4080
- Szacowana cena sprzedaży: 6000 - 7500 zł
- Wymagany zysk: 1400 zł
- Maksymalna cena zakupu: poniżej 4450 zł

11. RTX 4090
- Szacowana cena sprzedaży: 9000 zł
- Wymagany zysk: 1850 zł
- Maksymalna cena zakupu: poniżej 7000 zł

12. RTX 5050
- Szacowana cena sprzedaży: 3150 - 3400 zł
- Wymagany zysk: 900 zł
- Maksymalna cena zakupu: poniżej 2100 zł

13. RTX 5070 Ti
- Szacowana cena sprzedaży: 6000 - 7000 zł
- Wymagany zysk: 1400 zł
- Maksymalna cena zakupu: poniżej 4450 zł

14. RTX 5080
- Szacowana cena sprzedaży: 9000 – 10 000 zł
- Wymagany zysk: 1850 zł
- Maksymalna cena zakupu: poniżej 7000 zł

15. RTX 5090
- Szacowana cena sprzedaży: 17 000 – 20 000 zł
- Wymagany zysk: 3000 zł
- Maksymalna cena zakupu: poniżej 13 850 zł

Inna karta albo inna cena sprzedaży niż w tabeli: policz próg tym samym wzorem.

--- ZASADY DODATKOWE ---

1. KOREKTA WYDAJNOŚCIOWA: Laptopy z układami starszych generacji (seria 3000) sprzedają się wolniej niż nowsze
z DLSS 3 - dlatego ich progi w tabeli są już obniżone o 250 zł. Nie odejmuj tego ponownie.
2. PREMIA ZA POLSKĘ (PŁYNNOŚĆ): Laptop od sprzedawcy z Polski (kraj sprzedawcy: Polska / PL) dochodzi szybko,
więc szybciej go odsprzedam - taki laptop może kosztować więcej niż próg wejścia. Do „Maksymalnej ceny zakupu”
dodaj premię zależną od wysokości tego progu:
- próg poniżej 1000 zł: +50 zł
- próg 1000 - 1999 zł: +150 zł
- próg 2000 - 2999 zł: +250 zł
- próg 3000 - 4999 zł: +350 zł
- próg 5000 zł i więcej: +500 zł
Przykład: RTX 4060 z Polski - próg 2050 zł + 250 zł = 2300 zł. Laptop zza granicy (albo gdy kraj sprzedawcy
jest nieznany) - bez premii.
3. STAN WIZUALNY - NIE PRZESADZAJ: to sprzęt używany, odsprzedaję go po wyczyszczeniu.
- NIE są czerwoną flagą ani powodem do obniżenia oceny: kurz, włosy, sierść, okruchy, odciski palców, smugi,
  odbicia światła i refleksy na zdjęciu, lekkie przetarcia obudowy, drobne ryski na klapie/spodzie, błyszczące
  od używania klawisze, naklejki.
- Na zdjęciu nie da się odróżnić włosa, kurzu czy odbicia od rysy - w razie wątpliwości przyjmij, że to
  zabrudzenie, i najwyżej wspomnij o tym w photo_notes. Rysę na matrycy uznaj tylko wtedy, gdy jest jednoznaczna
  (widoczna na kilku zdjęciach albo opisana przez sprzedawcę).
- Wyraźne wgniecenia, pęknięcia obudowy, uszkodzone zawiasy, brak klawiszy - to obniża wartość (uwzględnij
  w market_value_pln), ale czerwoną flagą jest tylko wtedy, gdy laptop wymaga naprawy.

4. CZERWONE FLAGI (OMIJAC): 
- Pęknięta albo wyraźnie uszkodzona matryca (pęknięcia, plamy, martwe pasy, wyraźne głębokie rysy WIDOCZNE
  na włączonym ekranie) w droższych modelach.
- Próby finalizowania transakcji poza bezpiecznym systemem Vinted/OLX.
- Napisane w opisie że tylko odbiór osobisty/ że to nie jest przedmiot na sprzedaż

Rynkowa minimalna wartość:
Najniższe odnotowane ceny rynkowe dla poszczególnych modeli kart graficznych na podstawie dostarczonego zestawienia ofert:

* **RTX 3050:** 1400 zł (Allegro)

* **RTX 3050 Ti:** 1800 zł (OLX)

* **RTX 3060:** 2200 zł (Vinted)

* **RTX 3070:** 3050 zł (Vinted - modele Asus TUF)

* **RTX 3070 Ti:** 3570 zł (OLX)

* **RTX 3080:** 3900 zł (Vinted)

* **RTX 3080 Ti:** 6000 zł (Vinted)

* **RTX 4050:** 2200 zł (Allegro)

* **RTX 4060:** 3050 zł (Allegro)

* **RTX 4070:** 4020 zł (OLX)

* **RTX 4080:** 6000 zł (Vinted)

* **RTX 4090:** 9000 zł (Allegro)

* **RTX 5050:** 3150 zł (Vinted)

* **RTX 5060:** 3700 zł (Allegro)

* **RTX 5070:** 4800 zł (Vinted)

* **RTX 5070 Ti:** 6000 zł (Allegro)

* **RTX 5080:** 9000 zł (Allegro)

* **RTX 5090:** 17 000 zł (Allegro)
