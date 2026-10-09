# Vinted Sniper

Asynchroniczny Zwiadowca Vinted (asyncio + httpx + Playwright): co kilka sekund sprawdza najnowsze oferty
w wybranej kategorii przez rotacyjne proxy IPRoyal, odrzuca sprzedane i spoza zakresu ceny, zbiera komplet
danych (opis, zdjęcia jako URL-e, sprzedawca, wysyłka) i wysyła alert e-mail. Dane ofert są gotowe do
przekazania modelowi AI (następny etap).

```bash
pip install -r sniper/requirements.txt
playwright install chromium          # przeglądarka Zwiadowcy (skan przez proxy)
patchright install chrome            # Google Chrome dla przeglądarki konta (auto-zakup)
cp sniper/.env.example sniper/.env   # uzupełnij proxy, SMTP i filtry
python -m sniper.account_session --login   # RAZ: ręczne logowanie w oknie bota, potem Enter w konsoli
python -m sniper                     # zwykłe uruchomienie
```

Przeglądarka konta (to, z czego korzysta auto-zakup) działa na **Patchright** i Google Chrome, w widocznym oknie,
na stałym, osobnym profilu bota `./profiles/scraper` (zmienna `SCRAPER_PROFILE_DIR`; folder jest w `.gitignore`,
bo trzyma ciasteczka sesji konta). Program nigdy nie wpisuje loginu ani hasła - logujesz się sam przy `--login`.
Jeden proces na profil: drugi start dostaje komunikat „profil jest JUŻ UŻYWANY”.
Kontrola wykrywalności tej przeglądarki: `python -m sniper.check_detection` (browserscan + sannysoft, okno zostaje
otwarte do oceny).

* Dokumentacja modułu: [`sniper/README.md`](sniper/README.md)
* Kontekst projektu, ustalenia o API Vinted i plan dalszych prac: [`CLAUDE.md`](CLAUDE.md)
  (ten sam tekst co `sniper/CONTEXT.md`; Claude Code wczytuje go automatycznie)
* Testy: `pip install pytest && python -m pytest sniper/tests`
