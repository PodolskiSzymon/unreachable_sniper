"""Konfiguracja Zwiadowcy - wszystko z zmiennych środowiskowych (lub pliku sniper/.env)."""
import logging
import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:  # python-dotenv jest opcjonalny
    pass


def _env(name, default=""):
    return os.getenv(name, default).strip()


def _env_int(name, default):
    value = _env(name)
    return int(value) if value else default


def _env_float(name, default):
    value = _env(name)
    return float(value) if value else default


def _env_bool(name, default):
    value = _env(name).lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "tak", "on")


def build_proxy_url():
    """Buduje URL proxy według oficjalnego wzorca IPRoyal: http://{proxy_auth}@{proxy}.

    Priorytet:
      1. SNIPER_PROXY_HOST (np. geo.iproyal.com:12321) + SNIPER_PROXY_AUTH (LOGIN:HASLO_country-pl)
      2. SNIPER_PROXY_URL  (gotowy http://LOGIN:HASLO_country-pl@geo.iproyal.com:12321)
    Zwraca "" gdy proxy nie jest skonfigurowane.
    """
    return _proxy_url("SNIPER_PROXY")


def _proxy_url(prefix):
    """URL proxy z <prefix>_HOST + <prefix>_AUTH albo gotowego <prefix>_URL ("" = brak)."""
    proxy = _env(f"{prefix}_HOST")
    proxy_auth = _env(f"{prefix}_AUTH")
    if proxy:
        proxy = proxy.split("://", 1)[-1]          # tolerujemy wpisanie z http://
        if not proxy_auth:
            return f"http://{proxy}"
        login, _, password = proxy_auth.partition(":")
        # quote() zmienia tylko znaki specjalne (@ : / itd.) - dla zwykłych loginów/haseł
        # wynik jest identyczny z f'http://{proxy_auth}@{proxy}'.
        return f"http://{quote(login, safe='')}:{quote(password, safe='')}@{proxy}"
    return _env(f"{prefix}_URL")


# ---------------------------------------------------------------------------
# Kilka proxy naraz (2026-10-30, prośba użytkownika): kategorie przypisane do proxy w SNIPER_PROXY_ROUTES, każde proxy
# = osobna sesja Vinted (ciastka, tokeny) i osobna pętla skanu z własnym tempem. Główne proxy (SNIPER_PROXY_HOST /
# _AUTH / _URL) ma nazwę MAIN_PROXY; kategorie bez wpisu idą przez nie.
# ---------------------------------------------------------------------------
MAIN_PROXY = "glowne"


def _proxy_key(name):
    """Nazwa proxy z SNIPER_PROXY_ROUTES → część nazwy zmiennej: „dataimpulse” → „DATAIMPULSE”."""
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def parse_proxy_routes(text):
    """'phone:di, console:di, 3582:web' → {'phone': 'di', 'console': 'di', '3582': 'web'} (klucz: kategoria albo
    numer katalogu, wartość: nazwa proxy; małe litery). Błędny wpis pomijany z ostrzeżeniem."""
    routes = {}
    for part in (p.strip() for p in (text or "").split(",")):
        if not part:
            continue
        key, sep, name = (x.strip().lower() for x in part.partition(":"))
        if not sep or not key or not re.fullmatch(r"[a-z0-9_]+", name):
            logging.getLogger("sniper.config").warning("[PROXY] Pomijam błędny wpis SNIPER_PROXY_ROUTES: %r "
                                                       "(wzór: kategoria:nazwa_proxy)", part)
            continue
        routes[key] = name
    return routes


def extra_proxy_url(name):
    """URL dodatkowego proxy: SNIPER_PROXY_<NAZWA>_HOST + SNIPER_PROXY_<NAZWA>_AUTH albo SNIPER_PROXY_<NAZWA>_URL
    ("" = brak). Główne (MAIN_PROXY) - jak dotąd z SNIPER_PROXY_HOST / _AUTH / _URL."""
    return build_proxy_url() if name == MAIN_PROXY else _proxy_url(f"SNIPER_PROXY_{_proxy_key(name)}")


def proxy_poll_interval(name, default):
    """Odstęp między skanami pętli danego proxy (s): SNIPER_PROXY_<NAZWA>_POLL, inaczej `default`."""
    if name == MAIN_PROXY:
        return default
    return _env_float(f"SNIPER_PROXY_{_proxy_key(name)}_POLL", default)


def proxy_env_names(name):
    """Nazwy zmiennych dodatkowego proxy - do komunikatu, gdy czegoś brakuje."""
    key = _proxy_key(name)
    return f"SNIPER_PROXY_{key}_HOST + SNIPER_PROXY_{key}_AUTH (albo SNIPER_PROXY_{key}_URL)"


class ProxyNotConfigured(RuntimeError):
    """Brak proxy w .env, a SNIPER_REQUIRE_PROXY=true (domyślnie) - nie wolno wyjść bezpośrednio."""


def require_proxy_url():
    """URL proxy albo wyjątek. Gwarantuje, że żaden ruch nie wyjdzie z pominięciem IPRoyal.

    Ustaw SNIPER_REQUIRE_PROXY=false tylko świadomie (np. testy lokalne) - wtedy brak proxy = ruch bezpośredni.
    """
    proxy_url = build_proxy_url()
    if not proxy_url and _env_bool("SNIPER_REQUIRE_PROXY", True):
        raise ProxyNotConfigured(
            "Brak proxy: ustaw SNIPER_PROXY_HOST + SNIPER_PROXY_AUTH (lub SNIPER_PROXY_URL) w sniper/.env"
        )
    return proxy_url


def requests_proxies(proxy_url=None):
    """Słownik proxies dla requests: {'http': ..., 'https': ...} (używa go sniper.diagnose)."""
    proxy_url = require_proxy_url() if proxy_url is None else proxy_url
    if not proxy_url:
        return {}
    return {"http": proxy_url, "https": proxy_url}


BASE_URL = "https://www.vinted.pl"
# Opłata Vinted płacona przez KUPUJĄCEGO (ochrona kupującego): % ceny + stała kwota (2026-10-26: 416,80 zł → 23,74 zł
# = 5% + 2,90 zł). Wchodzi w koszt zakupu przy ocenie okazji, bramce ceny i powiadomieniach.
BUYER_FEE_PCT = _env_float("SNIPER_BUYER_FEE_PCT", 5.0)
BUYER_FEE_FIXED = _env_float("SNIPER_BUYER_FEE_FIXED", 2.90)

# Kursy do przeliczenia wysyłki: shipping_details podaje cenę w walucie sprzedawcy (np. 799 HUF ≈ 9 zł), a była
# doliczana jak złotówki - oferty z Węgier miały koszt zawyżony o ~790 zł (eksperyment 2026-10-27). Kursy
# przybliżone (X 2026) - wysyłka to kilka-kilkanaście zł, więc błąd kursu jest pomijalny.
# Nadpisanie / dodanie waluty: SNIPER_FX_RATES="EUR:4.25,HUF:0.0108".
FX_TO_PLN = {"PLN": 1.0, "EUR": 4.25, "HUF": 0.0108, "RON": 0.84, "CZK": 0.17, "SEK": 0.39, "DKK": 0.57,
             "GBP": 4.95, "USD": 3.70, "BGN": 2.17, "CHF": 4.55, "NOK": 0.37}
for _part in _env("SNIPER_FX_RATES").split(","):
    _code, _, _rate = _part.partition(":")
    try:
        FX_TO_PLN[_code.strip().upper()] = float(_rate)
    except ValueError:
        pass
# Wysyłka w walucie spoza tabeli - przyjmujemy typową kwotę zamiast surowej liczby.
UNKNOWN_SHIPPING_PLN = 20.0
# Nowy endpoint katalogu (Vinted przeniósł listę z www.vinted.pl/api/v2/catalog/items)
CATALOG_URL = "https://api.vinted.pl/svc-catalogue/items"
SIDEBAR_URL = BASE_URL + "/api/v2/items/{item_id}/details/sidebar"
SHIPPING_URL = BASE_URL + "/api/v2/items/{item_id}/shipping_details"

# ---------------------------------------------------------------------------
# 1:1 z działającego projektu (session_management.py / cookies_management.py).
# NIE zmieniać parametr po parametrze - to jest sprawdzony zestaw.
# ---------------------------------------------------------------------------

# UA identyczny z działającym zapytaniem (cURL z przeglądarki) - cf_clearance/datadome są wiązane z UA,
# więc Playwright (który zdobywa te ciastka) i httpx muszą się przedstawiać tak samo.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0"
)

# Nagłówki 1:1 z działającego zapytania do api.vinted.pl/svc-catalogue/items (cURL z przeglądarki),
# bez ciastek i tokenów (x-csrf-token / x-anon-id dochodzą z Playwrighta).
CATALOG_HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'pl,en;q=0.9,en-GB;q=0.8,en-US;q=0.7',
    'locale': 'pl-PL',
    'origin': 'https://www.vinted.pl',
    'platform': 'web',
    'priority': 'u=1, i',
    'referer': 'https://www.vinted.pl/',
    'sec-ch-ua': '"Chromium";v="154", "Microsoft Edge";v="154", "Not A(Brand";v="99"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-site',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0',
    'x-next-app': 'marketplace-web',
}

# Zapytania do www.vinted.pl/api/v2/... (sidebar, shipping_details) to dla przeglądarki ta sama domena:
# bez 'origin', z 'sec-fetch-site: same-origin'.
BASE_HEADERS = {k: v for k, v in CATALOG_HEADERS.items() if k != 'origin'}
BASE_HEADERS['sec-fetch-site'] = 'same-origin'
CATALOG_ONLY_HEADERS = {'origin': CATALOG_HEADERS['origin'], 'sec-fetch-site': CATALOG_HEADERS['sec-fetch-site']}

# session_management.py -> categories
categories = {
    'karty_pamieci': 3063,
    'elektronika': 2994,
}
CATEGORIES = categories


def get_catalog_params(category, order='newest_first', page=1, search_text='', brand_ids='', brand_collection_ids='', status_ids='', price_from='', price_to='', per_page=96):
    """Kopia session_management.get_catalog_params - parametry 1:1 z działającego zapytania (cURL z przeglądarki).

    Jedyna różnica: kategoria spoza słownika (np. "3580") jest używana wprost jako attribute_ids[catalog],
    zamiast rzucać KeyError.
    """
    catalog_ids = categories.get(category, category)
    params = {
        'page': page,
        'per_page': per_page,
        'search_text': search_text,
        'price_from': price_from,
        'price_to': price_to,
        'currency': 'PLN',
        'order': order,
        'attribute_ids[catalog]': catalog_ids,
        'attribute_ids[brand]': brand_ids,
        'attribute_ids[brand_collection]': brand_collection_ids,
        'attribute_ids[status]': status_ids,
    }
    # Vinted odrzuca puste price_from= (400 INVALID_REQUEST) - ceny wysyłamy tylko z wartością,
    # tak jak przeglądarka. Pozostałe puste pola (search_text, attribute_ids[...]) są akceptowane.
    for key in ('price_from', 'price_to'):
        if params[key] in ('', None):
            del params[key]
    return params


def make_main_loop_referer(page=1):
    """Kopia session_management.make_main_loop_referer."""
    if page == 1:
        return "https://www.vinted.pl/catalog"
    else:
        return f"https://www.vinted.pl/catalog?page={page}"


@dataclass(frozen=True)
class SmtpConfig:
    host: str = _env("SNIPER_SMTP_HOST", "smtp.poczta.onet.pl")
    port: int = _env_int("SNIPER_SMTP_PORT", 465)
    username: str = _env("SNIPER_SMTP_USER")          # np. twoj_login@onet.pl
    password: str = _env("SNIPER_SMTP_PASSWORD")      # <-- TUTAJ hasło do Onetu (przez .env!)
    sender: str = _env("SNIPER_EMAIL_FROM") or _env("SNIPER_SMTP_USER")
    recipient: str = _env("SNIPER_EMAIL_TO") or _env("SNIPER_SMTP_USER")
    timeout: float = _env_float("SNIPER_SMTP_TIMEOUT", 20.0)

    @property
    def enabled(self):
        return bool(self.username and self.password and self.recipient)


def _env_list(name, default=""):
    """Lista z przecinkami: 'RTX, 4060 ,' -> ['rtx', '4060'] (małe litery, bez pustych)."""
    return tuple(part.strip().lower() for part in _env(name, default).split(",") if part.strip())


# Nasze kategorie (sniper/categories.py) - do list słów per kategoria: SNIPER_AI_KEYWORDS_PHONE itd.
AI_CATEGORIES = ("laptop", "phone", "tablet", "console", "gpu", "ram", "storage")


def _env_category_lists(prefix):
    """{kategoria: lista} z SNIPER_AI_..._<KATEGORIA> - tylko ustawione (puste = brak filtra słów)."""
    return {cat: _env_list(f"{prefix}_{cat.upper()}") for cat in AI_CATEGORIES if os.getenv(f"{prefix}_{cat.upper()}")}


DEFAULT_AI_KEYWORDS = ("rtx,3050,3060,3070,3080,4050,4060,4070,4080,4090,"
                       "5050,5060,5070,5080,5090")


def _ai_provider():
    """SNIPER_AI_PROVIDER albo zgadnięty z tego, który klucz jest w .env (domyślnie gemini)."""
    provider = _env("SNIPER_AI_PROVIDER").lower()
    if provider:
        return provider
    if _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"):
        return "gemini"
    if _env("ANTHROPIC_API_KEY"):
        return "anthropic"
    if _env("OPENROUTER_API_KEY"):
        return "openrouter"
    return "gemini"


def _ai_api_key(provider):
    specific = {"gemini": _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"), "anthropic": _env("ANTHROPIC_API_KEY"),
                "openrouter": _env("OPENROUTER_API_KEY")}.get(provider, "")
    return _env("SNIPER_AI_API_KEY") or specific


# openrouter: jeden klucz, setki modeli różnych firm (openrouter.ai/models, filtr „image input”); model podajesz
# w SNIPER_AI_MODEL w formacie z ich strony, np. qwen/…, z-ai/…, mistralai/…, google/gemma-…
AI_DEFAULT_MODELS = {"gemini": "gemini-3.8-flash", "anthropic": "claude-opus-5-5", "openrouter": ""}
_AI_PROVIDER = _ai_provider()


@dataclass(frozen=True)
class AiConfig:
    """Ocena ofert przez model AI (sniper/evaluator.py). Wywołania idą bezpośrednio, NIE przez proxy IPRoyal."""
    enabled: bool = _env_bool("SNIPER_AI_ENABLED", True)
    # gemini (Google AI Studio) albo anthropic (Claude).
    provider: str = _AI_PROVIDER
    # Klucz: SNIPER_AI_API_KEY albo standardowe GEMINI_API_KEY / GOOGLE_API_KEY / ANTHROPIC_API_KEY.
    api_key: str = _ai_api_key(_AI_PROVIDER)
    model: str = _env("SNIPER_AI_MODEL") or AI_DEFAULT_MODELS.get(_AI_PROVIDER, "")
    # Tylko openrouter (i inne API zgodne z OpenAI): adres API.
    base_url: str = _env("SNIPER_AI_BASE_URL", "https://openrouter.ai/api/v1")
    # Głębokość myślenia: low / medium / high (Gemini: thinking_level, Claude: effort). Puste = domyślna modelu.
    effort: str = _env("SNIPER_AI_EFFORT", "medium").lower()
    # Tylko Claude: zapasowy model po stronie API, gdy główny odmówi odpowiedzi (fallbacks: "default").
    fallback: bool = _env_bool("SNIPER_AI_FALLBACK", True)
    max_tokens: int = _env_int("SNIPER_AI_MAX_TOKENS", 8000)
    max_photos: int = _env_int("SNIPER_AI_MAX_PHOTOS", 6)
    # Tylko Gemini: url = Google pobiera zdjęcia sam; download = pobieramy je bezpośrednio (domowe IP, bez proxy)
    # i wysyłamy w zapytaniu.
    photos: str = _env("SNIPER_AI_PHOTOS", "url").lower()
    # Tylko Gemini: rozdzielczość zdjęć dla modelu (low / medium / high; puste = domyślna modelu). Zdjęcia to większość
    # tokenów wejścia (~1 100 na zdjęcie przy domyślnej, 27.10: ~13,6 tys. tokenów na ofertę) - porównaj jakość
    # w `python -m sniper.bench` zanim zmienisz.
    media_resolution: str = _env("SNIPER_AI_MEDIA_RESOLUTION").lower()
    # Wytyczne w zapytaniu: wszystkie (cała tabela kategorii, ~4-5 tys. tokenów) albo dopasowane (zasady + tylko
    # wiersze pasujące do tytułu; ceny i tak są w <mediany> / <porownywalne>). Laptopy zawsze całe.
    guide_rows: str = _env("SNIPER_AI_GUIDE_ROWS", "wszystkie").lower()
    guidelines_file: str = _env("SNIPER_AI_GUIDELINES") or str(Path(__file__).with_name("guidelines.md"))
    # Wytyczne per kategoria z analizatora: <folder>/<kategoria>.md (+ catalogs.json z zakresami cen).
    guides_dir: str = _env("SNIPER_GUIDES_DIR") or str(Path(__file__).with_name("guides"))
    # Dzienny limit kosztu AI w USD (szacunek z tokenów, doba wg zegara komputera). Po przekroczeniu oferty do końca
    # doby pomijane przed AI + jedno powiadomienie push. 0 = bez limitu.
    daily_limit_usd: float = _env_float("SNIPER_AI_DAILY_LIMIT_USD", 0.0)
    # Bramka ceny przed AI: cena łączna > max zakup pasującego wiersza tabeli × tyle → bez AI (0 = wyłączona).
    price_gate: float = _env_float("SNIPER_AI_PRICE_GATE", 1.25)
    # Premia do oceny okazji od sprzedawcy z Polski (krótsza dostawa = krócej zamrożona gotówka). 0 = wyłączona.
    pl_bonus: float = _env_float("SNIPER_AI_PL_BONUS", 1.0)
    # Reguła zysku sprawdzana przez PROGRAM po ocenie AI (AI potrafi się pomylić - S25 2026-10-26: 9/10 przy 19% marży).
    # Zysk = wartość rynkowa wg AI - koszt zakupu (z opłatą Vinted) - koszty sprzedaży; okazja tylko gdy marża ≥ min
    # i zysk ≥ min. Inaczej: nie okazja, ocena najwyżej 6. Spełniona reguła = okazja z oceną co najmniej 8.
    # 2026-10-28 (użytkownik): marża min. ~28% przy tanich (sprzedaż ≤ 1 000 zł), ~25% przy drogich (≥ 2 500 zł),
    # pomiędzy liniowo; zysk zawsze ≥ 300 zł. (Wcześniej: min. 30%, 35% → 400 zł, 30% → 500 zł.)
    rule_min_profit: float = _env_float("SNIPER_RULE_MIN_PROFIT", 300.0)
    rule_margin_cheap: float = _env_float("SNIPER_RULE_MARGIN_CHEAP", 28.0)            # %
    rule_margin_expensive: float = _env_float("SNIPER_RULE_MARGIN_EXPENSIVE", 25.0)    # %
    rule_cheap_sale: float = _env_float("SNIPER_RULE_CHEAP_SALE", 1000.0)              # zł - do tylu „tanie”
    rule_expensive_sale: float = _env_float("SNIPER_RULE_EXPENSIVE_SALE", 2500.0)      # zł - od tylu „drogie”
    rule_deal_score: float = _env_float("SNIPER_RULE_DEAL_SCORE", 8.0)                 # ocena okazji spełniającej regułę
    rule_costs: float = _env_float("SNIPER_RULE_COSTS", 46.0)                  # Podbicie 26 + wysyłka 20
    # Sposób oceny: zwykly (AI wycenia z karty rynku + podobnych ofert) albo mediany (AI dostaje kilka median z bazy,
    # od szczegółowej do całej rodziny, i wycenia z nich; może poprawić model - sniper/experiment.py, eksperyment
    # 27.10: 0 fałszywych okazji w złotym zbiorze). Laptopy zawsze zwykle (tabela kart graficznych + guidelines.md).
    eval_mode: str = _env("SNIPER_EVAL_MODE", "zwykly").lower()
    # Ocena najwyżej tyle, gdy modelu nie ma w tabeli cen z analizatora (cena sprzedaży = domysł AI). 0 = bez limitu.
    no_data_max_score: float = _env_float("SNIPER_AI_NO_DATA_MAX_SCORE", 7.0)
    # Wycena AI najwyżej tyle × szacunek z podobnych ofert z bazy (comparables.db). 0 = bez przycinania.
    comparable_factor: float = _env_float("SNIPER_AI_COMPARABLE_FACTOR", 1.2)

    # Mail tylko gdy ocena >= min_score; notify_all=true -> mail o każdej ofercie (z oceną albo bez).
    min_score: float = _env_float("SNIPER_AI_MIN_SCORE", 7.0)
    notify_all: bool = _env_bool("SNIPER_AI_NOTIFY_ALL", False)
    # Mail o ofercie, której AI nie oceniło (błąd API). false = tylko w logu i evaluations.csv (2026-10-26: 12 maili
    # „NIEOCENIONA” w 2 minuty przy błędach API).
    notify_failed: bool = _env_bool("SNIPER_AI_NOTIFY_FAILED", True)
    # Ta sama oferta (ID) wraca do katalogu (podbicie, ponowne wystawienie) - bez ponownej oceny AI i maila, chyba
    # że cena spadła. Pamięć z logs/evaluations.jsonl, także po restarcie.
    skip_seen: bool = _env_bool("SNIPER_AI_SKIP_SEEN", True)
    # true = mail TYLKO o okazjach, które auto-zakup kupił / próbował kupić (zwykłe oferty i błędy AI bez maila).
    # Działa tylko przy włączonym auto-zakupie - bez niego maile idą jak zwykle, żeby nie zgubić okazji.
    mail_only_purchases: bool = _env_bool("SNIPER_MAIL_ONLY_PURCHASES", False)
    # Podgląd ocen w przeglądarce (logs/oceny.html): oferty z oceną POWYŻEJ tej wartości.
    report_above: float = _env_float("SNIPER_AI_REPORT_ABOVE", 5.0)

    # Asynchronicznie: limit równoległych wywołań, limit czasu jednej próby, liczba ponowień.
    # 2026-10-27: 10 zamiast 3 (prośba użytkownika - eksperyment 50 ofert trwał ~4 min przy 3 naraz).
    max_concurrent: int = _env_int("SNIPER_AI_MAX_CONCURRENT", 10)
    timeout: float = _env_float("SNIPER_AI_TIMEOUT", 120.0)
    retries: int = _env_int("SNIPER_AI_RETRIES", 2)
    retry_delay: float = _env_float("SNIPER_AI_RETRY_DELAY", 5.0)

    # Tani filtr przed AI (zero kosztów): cena łączna i słowa kluczowe w tytule/opisie.
    price_min: float | None = _env_float("SNIPER_AI_PRICE_MIN", None)
    price_max: float | None = _env_float("SNIPER_AI_PRICE_MAX", None)
    keywords: tuple = _env_list("SNIPER_AI_KEYWORDS", DEFAULT_AI_KEYWORDS)
    exclude_keywords: tuple = _env_list("SNIPER_AI_EXCLUDE_KEYWORDS")
    # Per kategoria: SNIPER_AI_KEYWORDS_PHONE, SNIPER_AI_EXCLUDE_KEYWORDS_LAPTOP itd. Ogólne SNIPER_AI_KEYWORDS /
    # SNIPER_AI_EXCLUDE_KEYWORDS / SNIPER_AI_PRICE_MIN/MAX dotyczą tylko laptopów (i trybu jednego katalogu).
    category_keywords: dict = field(default_factory=lambda: _env_category_lists("SNIPER_AI_KEYWORDS"))
    category_exclude: dict = field(default_factory=lambda: _env_category_lists("SNIPER_AI_EXCLUDE_KEYWORDS"))
    # Gdzie szukać słów kluczowych: "title" albo "title+description".
    keywords_in: str = _env("SNIPER_AI_KEYWORDS_IN", "title+description").lower()

    # Cena modelu w USD za 1M tokenów (wejście / wyjście) - tylko do szacunku kosztu w heartbeacie.
    # Puste = z tabeli MODEL_PRICES poniżej.
    price_in: float | None = _env_float("SNIPER_AI_PRICE_IN", None)
    price_out: float | None = _env_float("SNIPER_AI_PRICE_OUT", None)

    @property
    def active(self):
        return self.enabled and bool(self.api_key)

    def filters_for(self, category):
        """(słowa kluczowe, wykluczające, cena min, cena max) dla kategorii oferty."""
        legacy = category in (None, "laptop")
        keywords = self.category_keywords.get(category, self.keywords if legacy else ())
        exclude = self.category_exclude.get(category, self.exclude_keywords if legacy else ())
        return keywords, exclude, (self.price_min if legacy else None), (self.price_max if legacy else None)


# USD za 1M tokenów (wejście, wyjście). Gemini 3.x Flash: cena promocyjna do 31.12.2026,
# od 2027 r. 1,50 / 7,50 - wtedy ustaw SNIPER_AI_PRICE_IN / SNIPER_AI_PRICE_OUT. Claude: cennik 2026-09.
MODEL_PRICES = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.7-flash": (0.75, 3.75),
    # Tańsze (ceny z zestawień 2026-10, sprawdź na ai.google.dev/pricing): bez wpisu koszt = 0 i dzienny limit
    # SNIPER_AI_DAILY_LIMIT_USD by nie działał.
    "gemini-3-flash": (0.50, 3.00),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
}


def _env_range(name, default):
    """Zakres "a-b" (np. "4-12") -> (a, b) jako float. Błędny / pusty -> default; odwrócony -> zamieniony."""
    value = _env(name)
    if not value:
        return default
    try:
        low, high = (float(x.replace(",", ".")) for x in value.split("-", 1))
    except ValueError:
        return default
    return (min(low, high), max(low, high)) if low >= 0 else default


@dataclass(frozen=True)
class DelayConfig:
    """WSZYSTKIE losowe przerwy przeglądarki konta w jednym miejscu (zamiast stałych sleep).

    Pętle, które co 0,5 s sprawdzają, czy strona już zareagowała, zostają stałe - to czekanie na wynik,
    nie przerwa. Kliki przy zakupie mają krótkie zakresy celowo: 4-12 s przy „Zapłać” = ktoś kupi szybciej.
    """
    # Podtrzymanie sesji: wejście na stronę co tyle MINUT (losowo z zakresu).
    keepalive_min: tuple = _env_range("SCRAPER_KEEPALIVE_MIN", (15.0, 25.0))
    # Co ile wejść (losowo z zakresu) dłuższa pauza i ile MINUT trwa (zamiast zwykłej przerwy).
    long_pause_every: tuple = _env_range("SCRAPER_LONG_PAUSE_EVERY", (12.0, 18.0))
    long_pause_min: tuple = _env_range("SCRAPER_LONG_PAUSE_MIN", (40.0, 60.0))
    # Między podtrzymaniami: szybkie sprawdzenie BEZ przeładowania strony co tyle SEKUND - wylogowanie widać od razu,
    # a nie dopiero przy następnym podtrzymaniu (było: do 25 min „AKTYWNY” po wylogowaniu, test 2026-10-09).
    session_watch_s: tuple = _env_range("SCRAPER_SESSION_WATCH_S", (60.0, 120.0))
    # Czekanie na Twoje ręczne logowanie w oknie bota: sprawdzanie co tyle SEKUND (bez przeładowania strony).
    login_check_s: tuple = _env_range("SCRAPER_LOGIN_CHECK_S", (5.0, 10.0))
    # Odśwież sesję (wejście na stronę) na tyle MINUT przed wygaśnięciem tokenu dostępu (exp z JWT, żyje 60 min).
    # Log nocny 2026-10-11: JS Vinted wymienia token dopiero, gdy zostało ~<5 min - przy 8-12 min zapasu bot wchodził
    # na stronę co minutę przez ~5 min, zanim token się odświeżył. 2-4 min = jedno wejście wystarcza.
    refresh_ahead_min: tuple = _env_range("SCRAPER_REFRESH_AHEAD_MIN", (2.0, 4.0))
    # Sesja „padła” dopiero po tylu nieudanych sprawdzeniach Z RZĘDU (jedno potknięcie = chwilowy błąd, nie wylogowanie),
    # z przerwą tyle SEKUND między nimi.
    session_fail_checks: int = _env_int("SCRAPER_SESSION_FAIL_CHECKS", 3)
    session_retry_s: tuple = _env_range("SCRAPER_SESSION_RETRY_S", (30.0, 60.0))
    # Krótka pauza przed kliknięciem / po hydracji strony (s) i przed ponowieniem kliku (s).
    click_s: tuple = _env_range("SCRAPER_CLICK_DELAY_S", (0.8, 2.0))
    retry_s: tuple = _env_range("SCRAPER_RETRY_DELAY_S", (1.5, 3.0))

    @staticmethod
    def pick(bounds):
        return random.uniform(*bounds)


class KeepalivePacer:
    """Losowe odstępy podtrzymania sesji: zwykle keepalive_min, co kilkanaście wejść dłuższa pauza."""

    def __init__(self, delays: DelayConfig):
        self.delays = delays
        self.visits = 0
        self._next_long = self._draw_long()

    def _draw_long(self):
        low, high = self.delays.long_pause_every
        return max(1, round(random.uniform(low, high)))

    def next_seconds(self):
        self.visits += 1
        if self.visits >= self._next_long:
            self.visits, self._next_long = 0, self._draw_long()
            return DelayConfig.pick(self.delays.long_pause_min) * 60
        return DelayConfig.pick(self.delays.keepalive_min) * 60


@dataclass(frozen=True)
class NtfyConfig:
    """Powiadomienia push na telefon (sniper/push.py, aplikacja ntfy). Puste SNIPER_NTFY_TOPIC = wyłączone."""
    topic: str = _env("SNIPER_NTFY_TOPIC")                       # długa, losowa nazwa - temat jest „hasłem”
    server: str = _env("SNIPER_NTFY_SERVER", "https://ntfy.sh")
    token: str = _env("SNIPER_NTFY_TOKEN")                       # tylko dla własnego serwera / konta ntfy
    # Od jakiej oceny AI powiadomienie (domyślnie jak mail: SNIPER_AI_MIN_SCORE).
    min_score: float = _env_float("SNIPER_NTFY_MIN_SCORE", _env_float("SNIPER_AI_MIN_SCORE", 8.0))
    priority: int = _env_int("SNIPER_NTFY_PRIORITY", 5)          # 5 = max: osobny kanał = własny dźwięk w Androidzie
    attach_photo: bool = _env_bool("SNIPER_NTFY_PHOTO", True)    # pierwsze zdjęcie oferty w powiadomieniu


@dataclass(frozen=True)
class AccountConfig:
    """Sesja TWOJEGO konta Vinted (auto-zakup) - sniper.account_session, przeglądarka Patchright.

    Idzie z domowego IP, NIGDY przez proxy IPRoyal (sesja konta i ciastka anty-botowe są związane z Twoim IP).
    Osobny, stały profil bota (SCRAPER_PROFILE_DIR) - NIE Twój główny profil Chrome/Edge. Logowanie WYŁĄCZNIE ręczne
    w oknie bota (bez my_headers.txt); stronę odświeża jej własny JS, więc token podtrzymuje się sam.
    """
    enabled: bool = _env_bool("SNIPER_ACCOUNT_ENABLED", False)
    # Folder profilu bota (ciastka sesji konta!). Względny = od folderu, z którego uruchamiasz program.
    profile_dir: str = _env("SCRAPER_PROFILE_DIR", "./profiles/scraper")
    # Strona otwierana przy --login (logujesz się na niej ręcznie).
    login_url: str = _env("SCRAPER_LOGIN_URL", "https://www.vinted.pl/")
    # Limit czasu jednej nawigacji (s).
    nav_timeout: float = _env_float("SNIPER_ACCOUNT_NAV_TIMEOUT", 45.0)
    # Sandbox Chrome (zabezpieczenie). Domyślnie WŁĄCZONY - Playwright sam z siebie dodaje --no-sandbox.
    # false tylko gdy Chrome nie startuje (np. Linux jako root).
    chrome_sandbox: bool = _env_bool("SCRAPER_CHROME_SANDBOX", True)
    # Test diagnostyczny: false = podtrzymanie BEZ przeładowania strony (tylko sprawdzenie z otwartej strony).
    keepalive_navigate: bool = _env_bool("SCRAPER_KEEPALIVE_NAVIGATE", True)
    delays: DelayConfig = field(default_factory=DelayConfig)


@dataclass(frozen=True)
class BuyerConfig:
    """Auto-zakup okazji z konta (sniper.buyer). Z domowego IP przez sesję account_session (bez proxy).

    Bot NIGDY nie płaci: dochodzi do ekranu płatności, sprawdza limity i woła Ciebie - klik 'Zapłać'
    oraz captchę (suwak) robisz Ty. Tu są tylko twarde limity, które muszą przejść, zanim checkout powstanie.
    """
    enabled: bool = _env_bool("SNIPER_BUY_ENABLED", False)
    # Twarde limity bezpieczeństwa:
    max_total_pln: float = _env_float("SNIPER_BUY_MAX_TOTAL", 2500.0)   # maksymalna suma do zapłaty (z wysyłką i opłatą)
    max_per_day: int = _env_int("SNIPER_BUY_MAX_PER_DAY", 2)            # ile zakupów na dobę
    pl_only: bool = _env_bool("SNIPER_BUY_PL_ONLY", True)               # tylko sprzedawca z Polski
    min_score: float = _env_float("SNIPER_BUY_MIN_SCORE", 8.0)          # minimalna ocena AI (zwykle > próg maila)


@dataclass(frozen=True)
class ScoutConfig:
    # Zbudowane z SNIPER_PROXY_HOST + SNIPER_PROXY_AUTH (albo SNIPER_PROXY_URL) - patrz build_proxy_url().
    proxy_url: str = field(default_factory=build_proxy_url)

    # Kategoria: numer catalog_id z Vinted (np. 3580 = laptopy) albo nazwa z CATEGORIES.
    # SNIPER_CATALOG ma pierwszeństwo, SNIPER_CATEGORY zostaje dla zgodności.
    category: str = _env("SNIPER_CATALOG") or _env("SNIPER_CATEGORY", "karty_pamieci")
    search_text: str = _env("SNIPER_SEARCH_TEXT")
    # Wiele katalogów naraz (sniper/categories.py): "auto" = z <SNIPER_GUIDES_DIR>/catalogs.json od analizatora,
    # lista "3580:250-6100, 3661:100-3600*2" (katalog:cena_od-cena_do*waga), puste = tylko SNIPER_CATALOG.
    catalogs: str = _env("SNIPER_CATALOGS")
    # Które kategorie Sniper ocenia (laptop, phone, tablet, console, gpu, ram, storage); puste = wszystkie.
    # Wyłączona kategoria = jej katalog nie jest skanowany (a w mieszanych - oferty bez pobierania szczegółów).
    categories: tuple = _env_list("SNIPER_CATEGORIES")
    # Jak często skanować kategorię względem innych: "laptop:3,phone:3" (waga 3 = 3× częściej niż waga 1).
    catalog_weights: str = _env("SNIPER_CATALOG_WEIGHTS")
    price_from: str = _env("SNIPER_PRICE_FROM", "100")   # minimalna cena w PLN (puste = bez filtra)
    # Dolny próg ceny dla KAŻDEGO katalogu (także z catalogs.json / SNIPER_CATALOGS): price_from w zapytaniu nie
    # niższe niż to. 0 = wyłączone. Domyślnie 300 zł (2026-10-27) - patrz categories.apply_floor.
    price_floor: float = _env_float("SNIPER_PRICE_FLOOR", 300.0)
    price_to: str = _env("SNIPER_PRICE_TO")               # maksymalna cena w PLN (puste = bez filtra)
    # Ofert na jeden skan. svc-catalogue respektuje per_page (test check_per_page.py, 2026-10-02):
    # 96 ofert = ~39 KB transferu na skan, 20 ofert = ~9 KB. 20 to zapas ~1 h przy nowej ofercie co ~3 min.
    per_page: int = _env_int("SNIPER_PER_PAGE", 20)
    # Pamięć ID - musi być kilka razy większa niż strona katalogu; za mała jest podnoszona do 5 x per_page (min. 100).
    dedup_size: int = _env_int("SNIPER_DEDUP_SIZE", 100)

    # Odstęp między STARTAMI kolejnych skanów katalogu: 15 s = 4 skany na minutę (oszczędza transfer proxy)
    poll_interval: float = _env_float("SNIPER_POLL_INTERVAL", 15.0)
    poll_jitter: float = _env_float("SNIPER_POLL_JITTER", 1.0)       # losowe odchylenie +/- jitter sekund
    # Kilka proxy (2026-10-30): „kategoria:nazwa_proxy” po przecinku, np. „phone:di, console:di, laptop:web”; każda
    # nazwa = SNIPER_PROXY_<NAZWA>_HOST + _AUTH (albo _URL) i opcjonalnie _POLL (odstęp skanów tej pętli, s).
    # Kategorie bez wpisu idą przez główne proxy (SNIPER_PROXY_HOST / _AUTH). Puste = jedno proxy, jak dotąd.
    proxy_routes: str = _env("SNIPER_PROXY_ROUTES")
    # Przyspieszanie (2026-10-30, prośba użytkownika): gdy w skanie katalogu nie ma ŻADNEJ z `overlap_ids` najnowszych
    # ofert z poprzedniego skanu tego katalogu, przyszło więcej ofert, niż mieści strona → dociągamy do `gap_pages`
    # kolejnych stron (żeby nic nie umknęło) i pętla tego proxy skanuje co `poll_fast` s przez `fast_minutes` minut
    # od ostatniego takiego skanu. poll_fast 0 = połowa zwykłego odstępu (min. 1,5 s); fast_minutes 0 = bez zmiany tempa.
    overlap_ids: int = _env_int("SNIPER_OVERLAP_IDS", 5)
    gap_pages: int = _env_int("SNIPER_GAP_PAGES", 1)
    poll_fast: float = _env_float("SNIPER_POLL_FAST", 0.0)
    fast_minutes: float = _env_float("SNIPER_FAST_MINUTES", 10.0)
    heartbeat_interval: float = _env_float("SNIPER_HEARTBEAT", 60.0)  # co ile sekund log "żyję" (0 = wyłączony)
    max_concurrent_details: int = _env_int("SNIPER_MAX_CONCURRENT_DETAILS", 5)
    request_timeout: float = _env_float("SNIPER_REQUEST_TIMEOUT", 10.0)
    # Pierwszy skan tylko "zapamiętuje" obecne oferty, bez alertów (żeby nie zalać skrzynki).
    skip_initial_batch: bool = _env_bool("SNIPER_SKIP_INITIAL_BATCH", True)

    browser_wait_ms: int = _env_int("SNIPER_BROWSER_WAIT_MS", 15000)
    # Odświeżanie sesji przez Playwright (przy rotacyjnym proxy każda próba = nowe IP):
    refresh_attempts: int = _env_int("SNIPER_REFRESH_ATTEMPTS", 6)          # prób w jednej serii
    refresh_retry_delay: float = _env_float("SNIPER_REFRESH_RETRY_DELAY", 5.0)  # s między próbami
    refresh_timeout: float = _env_float("SNIPER_REFRESH_TIMEOUT", 90.0)    # s limitu na jedną próbę
    refresh_backoff: float = _env_float("SNIPER_REFRESH_BACKOFF", 30.0)    # s przerwy po nieudanej serii
    # Lekka przeglądarka: bez obrazków/wideo/fontów i skryptów reklamowych (wizyta ~9 MB -> ułamek tego).
    browser_light: bool = _env_bool("SNIPER_BROWSER_LIGHT", True)
    # Sesja (ciastka + tokeny) zapisywana na dysk i używana po restarcie, jeśli młodsza niż tyle minut
    # (0 = zawsze nowa sesja przeglądarką). Gdy wygaśnie, 401/403 i tak wywoła odświeżenie.
    session_max_age_min: float = _env_float("SNIPER_SESSION_MAX_AGE", 360.0)
    # Folder na logi: sniper.log (rotacja co północ, 30 dni) + offers.jsonl (złapane oferty)
    log_dir: str = _env("SNIPER_LOG_DIR") or str(Path(__file__).with_name("logs"))
    # Kopia logów i oceny.html do drugiego folderu, np. na Dysk Google (puste = wyłączone). Patrz sniper/mirror.py.
    mirror_dir: str = _env("SNIPER_MIRROR_DIR")
    mirror_interval_min: float = _env_float("SNIPER_MIRROR_INTERVAL_MIN", 2.0)

    smtp: SmtpConfig = field(default_factory=SmtpConfig)
    ai: AiConfig = field(default_factory=AiConfig)
    account: AccountConfig = field(default_factory=AccountConfig)
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)
    buyer: BuyerConfig = field(default_factory=BuyerConfig)

    @property
    def catalog_id(self):
        return CATEGORIES.get(self.category, self.category)
