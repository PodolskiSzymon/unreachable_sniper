<!--
  Wytyczne oceny ofert dla modelu AI (sniper/evaluator.py).
  Edytuj śmiało - Zwiadowca wczytuje ten plik ponownie przy następnej ocenie po zmianie (bez restartu).
  Inny plik wskażesz przez SNIPER_AI_GUIDELINES w sniper/.env.
  Ten komentarz HTML nie jest wysyłany do modelu.
-->

ANALIZA CENOWA I STRATEGIA FLIPOWANIA LAPTOPÓW (ręczne wytyczne użytkownika, poprawione 2026-10-25)

REGUŁA ZYSKU (decyzja użytkownika 2026-10-26, ta sama co w wytycznych z danych rynku):
- Zysk = realna cena sprzedaży - koszt zakupu (cena + wysyłka + opłata Vinted) - 46 zł kosztów (Podbicie
  międzynarodowe 7 dni ~26 zł + wysyłka ~20 zł). Marża = zysk / realna cena sprzedaży.
- Zysk zawsze co najmniej 300 zł; przy niższej marży więcej: ≥ 40% → 300 zł, 35% → 400 zł, 30% → 500 zł,
  25% → 600 zł, 20% → 700 zł (+20 zł na każdy punkt marży poniżej 40%); marża < 20% → NIE kupujemy.
- Nie kupuję uszkodzonych (na części, blokada BIOS / konta, zbita matryca, nie włącza się) - nigdy okazja.
- „Maksymalna cena zakupu” w tabeli niżej = najwyższa cena łączna, przy której reguła jest spełniona dla podanej
  ceny sprzedaży (policzona, nie zgadywana). „Zysk przy progu” = zysk przy zakupie dokładnie za próg.
- Realna cena sprzedaży (market_value_pln) sztuki w dobrym stanie = „Cena sprzedaży do wzoru”, a gdy jej nie ma -
  „Szacowana cena sprzedaży” (przy widełkach: dolna granica). Mocniejsza konfiguracja (więcej RAM, lepszy ekran,
  gwarancja) → wyżej, słabsza → niżej; wtedy policz próg regułą od nowa.
- Gdy wyżej jest tabela z danych rynku (laptop.md z analizatora): ceny i progi z niej mają pierwszeństwo (są
  świeższe); ten plik daje notatki o modelach, zasady stanu i czerwone flagi oraz progi dla kart, których tam nie ma.

Karta spoza tabeli: oszacuj realną cenę sprzedaży i policz próg regułą zysku.

--- ZESTAWIENIE MODELI I WIDEŁKI CENOWE ---

1. RTX 3050 / 3050 Ti
- Szacowana cena sprzedaży: 1800 - 2200 zł
- Zysk przy progu: ~524 zł
- Maksymalna cena zakupu: 1230 zł
- Notatki: Bardzo niska rentowność. Rynkowo na OLX wiszą za ok. 1570 zł. Kupować tylko przy skrajnych okazjach.

2. RTX 4050
- Szacowana cena sprzedaży: 2400 - 2500 zł
- Cena sprzedaży do wzoru: 2500 zł
- Zysk przy progu: ~614 zł
- Maksymalna cena zakupu: 1840 zł
- Notatki: Świetna budżetowa karta z DLSS 3. Gwarancja bardzo szybkiej płynności sprzedaży.

3. RTX 3060
- Szacowana cena sprzedaży: 2500 zł
- Zysk przy progu: ~614 zł
- Maksymalna cena zakupu: 1840 zł
- Notatki: Wymaga większego dyskonta niż 4050, by skompensować klientowi brak DLSS 3 i starszą architekturę.

4. RTX 4060
- Szacowana cena sprzedaży: 3150 zł
- Cena sprzedaży do wzoru: 3150 zł
- Zysk przy progu: ~674 zł
- Maksymalna cena zakupu: 2430 zł
- Notatki: Obecnie najbardziej pożądana karta na rynku. Idealny materiał na błyskawiczny flip.

5. RTX 3070 / 3070 Ti
- Szacowana cena sprzedaży: 3600 zł
- Zysk przy progu: ~724 zł
- Maksymalna cena zakupu: 2830 zł
- Notatki: Mocna w rasteryzacji, rynek wycenia ją stabilnie. Bezpieczna marża przy zachowaniu dyscypliny cenowej przy zakupie.

6. RTX 4070
- Szacowana cena sprzedaży: 4500 zł
- Zysk przy progu: ~904 zł
- Maksymalna cena zakupu: 3550 zł
- Notatki: Klienci w tym segmencie są bardziej wymagający.

7. RTX 3080 (16 GB VRAM)
- Szacowana cena sprzedaży: 4600 - 6000 zł
- Cena sprzedaży do wzoru: 5000 zł
- Zysk przy progu: ~1004 zł
- Maksymalna cena zakupu: 3950 zł
- Notatki: Główna dźwignia to 16 GB VRAM. Właściwe reklamowanie i pozycjonowanie na tle nowszego modelu 4070 (ograniczonego do 8 GB) pozwala na domykanie sprzedaży w górnych widełkach, wersja 8 GB VRAM: sprzedaż ~4100 zł → maksymalna cena zakupu 3230 zł.

8. RTX 5060
- Szacowana cena sprzedaży: 3800 zł
- Zysk przy progu: ~764 zł
- Maksymalna cena zakupu: 2990 zł

9. RTX 5070
- Szacowana cena sprzedaży: 4800 - 5200 zł
- Cena sprzedaży do wzoru: 5200 zł
- Zysk przy progu: ~1044 zł
- Maksymalna cena zakupu: 4110 zł

10. RTX 4080
- Szacowana cena sprzedaży: 6000 - 7500 zł
- Cena sprzedaży do wzoru: 6750 zł
- Zysk przy progu: ~1354 zł
- Maksymalna cena zakupu: 5350 zł

11. RTX 4090
- Szacowana cena sprzedaży: 9000 zł
- Zysk przy progu: ~1804 zł
- Maksymalna cena zakupu: 7150 zł

12. RTX 5050
- Szacowana cena sprzedaży: 3500 zł
- Zysk przy progu: ~704 zł
- Maksymalna cena zakupu: 2750 zł

13. RTX 5070 Ti
- Szacowana cena sprzedaży: 6000 - 7000 zł
- Cena sprzedaży do wzoru: 6500 zł
- Zysk przy progu: ~1304 zł
- Maksymalna cena zakupu: 5150 zł

14. RTX 5080
- Szacowana cena sprzedaży: 9000 – 10 000 zł
- Zysk przy progu: ~1804 zł
- Maksymalna cena zakupu: 7150 zł

15. RTX 5090
- Szacowana cena sprzedaży: 17 000 – 20 000 zł
- Zysk przy progu: ~3404 zł
- Maksymalna cena zakupu: 13550 zł

Inna karta: oszacuj realną cenę sprzedaży i policz próg regułą zysku (wyżej).

--- ZASADY DODATKOWE ---

1. KOREKTA WYDAJNOŚCIOWA: Laptopy z układami starszych generacji (seria 3000) sprzedają się wolniej niż nowsze
z DLSS 3 - ich ceny sprzedaży w tabeli już to uwzględniają. Nie odejmuj niczego od progu.
2. POLSKA (PŁYNNOŚĆ): laptop od sprzedawcy z Polski dochodzi szybciej, więc krócej mrożę gotówkę - program sam
dodaje +1 do oceny okazji z Polski (nie dodawaj tego sam). NIE podnoś maksymalnej ceny zakupu ponad regułę zysku
(dawna premia +50…+500 zł łamała minimalną marżę).
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
