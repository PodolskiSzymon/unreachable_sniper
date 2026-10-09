# Instalacja sniper na nowym komputerze (Windows)

## 1. Python
Zainstaluj **Python 3.12** z https://www.python.org/downloads/ i zaznacz w instalatorze
**„Add python.exe to PATH”**. Sprawdzenie w nowym oknie konsoli:
```
python --version
```

## 2. Folder programu
Skopiuj cały folder (np. `C:\Users\Szymek\sniper`) na laptopa **razem z plikami, których nie ma na GitHubie**
(są w `.gitignore`, więc `git clone` ich nie przyniesie):

| Plik / folder | Co to | Potrzebne? |
|---|---|---|
| `sniper\.env` | Twoje ustawienia: proxy, hasło SMTP, klucz AI | **tak** – bez niego program nie ruszy |
| `sniper\guidelines.md` | wytyczne dla AI (jeśli zmieniałeś) | tak |
| `sniper\logs\` | logi, `evaluations.*`, `oceny.html`, `bought.jsonl`, `session.json` | zalecane (historia, rejestr zakupów) |
| `profiles\scraper\` | profil przeglądarki bota (zalogowana sesja konta) | opcjonalnie – zamiast tego `--login` (krok 3) |

Przenoś je pendrive'em / dyskiem, **nie przez GitHub ani maila** – `.env` zawiera hasła i klucze.

Folder `.venv` (jeśli masz) **nie kopiuj** – utwórz go na laptopie od nowa (krok 3).

## 3. Biblioteki
W konsoli, w folderze programu:
```
cd C:\Users\Szymek\sniper
python -m venv .venv
.venv\Scripts\activate
pip install -r sniper\requirements.txt
python -m playwright install chromium
patchright install chrome
```
`patchright install chrome` instaluje Google Chrome (przeglądarka konta / auto-zakup). Jeśli Chrome już jest
zainstalowany, użyje istniejącego.

Pierwsze logowanie bota (tylko przy auto-zakupie, raz na komputer / nowy profil):
```
python -m sniper.account_session --login
```
Otworzy się okno Chrome z osobnym profilem bota (`profiles\scraper`) – zaloguj się RĘCZNIE e-mailem i hasłem
Vinted, potem naciśnij Enter w konsoli. Program nie wpisuje loginu ani hasła.
Możesz też pominąć ten krok: `python -m sniper` przy braku sesji sam otworzy okno bota na stronie głównej
Vinted – zaloguj się w nim, a bot wykryje to w kilka sekund (bez Entera i restartu).
Lista bibliotek: `sniper\requirements.txt`. Do testów dodatkowo: `pip install -r sniper\requirements-dev.txt`.

Za każdym razem przed uruchomieniem w nowym oknie konsoli: `.venv\Scripts\activate`.

## 4. Sprawdzenie
```
python -m pytest sniper\tests          # tylko jeśli zainstalowałeś requirements-dev.txt
python -m sniper.notifier              # mail testowy
python -m sniper                       # Zwiadowca
python -m sniper.check_detection       # okno z testami wykrywania bota (do ręcznej oceny, Enter zamyka)
```

## Uwagi
* Nie uruchamiaj programu **jednocześnie** na obu komputerach – zdublujesz ruch przez proxy (koszt transferu)
  i maile.
* Laptop nie może zasypiać w trakcie pracy (Ustawienia → System → Zasilanie → „Uśpij” = Nigdy, przy zasilaniu
  z sieci) – inaczej Zwiadowca stoi.
