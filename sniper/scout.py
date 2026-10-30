"""Zwiadowca (Scout) - nieskończona, asynchroniczna pętla skanująca katalog Vinted.

Ewolucja main_vinted.py:
  * requests -> httpx.AsyncClient za rotacyjnym proxy,
  * PostgreSQL -> deque w RAM (RecentIds),
  * zero pobierania zdjęć na dysk - tylko URL-e,
  * weryfikacja item_closing_action (sold -> ignoruj) i koszt wysyłki,
  * alert e-mail w tle (aiosmtplib).
"""
import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .categories import (AI_ENABLED_NAMES, CatalogSource, CatalogSpec, apply_floor, apply_weights, category_for,
                         parse_weights, schedule, wanted_specs)
from .config import (CATALOG_ONLY_HEADERS, CATALOG_URL, MAIN_PROXY, SHIPPING_URL, SIDEBAR_URL, ScoutConfig,
                     get_catalog_params, parse_proxy_routes, proxy_poll_interval)
from .dedup import RecentIds
from .extractor import build_offer, inactive_reason, item_url, unwrap_sidebar
from .notifier import EmailNotifier
from .proxy_relay import PROXY_AUTH_HELP
from .session import RateLimited, SessionExpired, VintedSession

log = logging.getLogger("sniper.scout")

IDLE_RECHECK_S = 60.0         # pętla proxy bez katalogów sprawdza co tyle sekund, czy jakieś doszły (catalogs.json)


def catalog_params(cfg, spec=None, page=1):
    """Najnowsze - parametry 1:1 z działającego zapytania do svc-catalogue (patrz config.get_catalog_params).
    spec (CatalogSpec) - katalog i zakres cen z SNIPER_CATALOGS; None = SNIPER_CATALOG / SNIPER_PRICE_FROM/TO.
    page > 1 tylko przy dociąganiu stron, gdy od poprzedniego skanu przyszło więcej ofert, niż mieści strona."""
    return get_catalog_params(
        category=str(spec.catalog_id) if spec else cfg.category,
        page=page,
        order='newest_first',
        search_text=cfg.search_text,
        price_from=spec.price_from if spec else cfg.price_from,
        price_to=spec.price_to if spec else cfg.price_to,
        per_page=cfg.per_page,
    )


def min_dedup(per_page):
    """Pamięć ID = co najmniej 5 stron katalogu (i nie mniej niż 100)."""
    return max(100, 5 * per_page)


def _catalog_price(item):
    price = item.get("price")
    if isinstance(price, dict):
        return f"{price.get('amount', '?')} {price.get('currency_code', '')}".strip()
    return str(price) if price is not None else "?"


def _to_float(value):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


@dataclass
class Lane:
    """Pętla skanu przez jedno proxy (2026-10-30): własna sesja Vinted (ciastka, tokeny), swoje katalogi
    (SNIPER_PROXY_ROUTES) i tempo; przyspiesza, gdy ofert przybywa szybciej, niż mieści strona."""
    name: str
    session: object
    interval: float               # zwykły odstęp między skanami (s)
    fast_interval: float          # odstęp po przepełnieniu (s)
    cycle: list = field(default_factory=list)      # kolejka skanów bieżącego cyklu (categories.schedule)
    fast_until: float = 0.0       # time.monotonic(), do kiedy przyspieszone
    gap_pages: int = 0            # dociągnięte strony od ostatniego heartbeatu

    @property
    def label(self):
        return f"proxy {self.name}"

    def current_interval(self, now=None):
        return self.fast_interval if self.fast_until > (time.monotonic() if now is None else now) else self.interval


class Scout:
    def __init__(self, cfg: ScoutConfig, session: VintedSession, notifier: EmailNotifier, evaluator=None, buyer=None,
                 sessions=None):
        """session - główne proxy (SNIPER_PROXY_HOST / _AUTH); sessions - {nazwa: VintedSession} dodatkowych proxy
        z SNIPER_PROXY_ROUTES (każde = osobna pętla skanu)."""
        self.cfg = cfg
        self.session = session
        self.notifier = notifier
        # OfferEvaluator (sniper/evaluator.py) albo None = mail o każdej ofercie, jak przed modułem AI.
        self.evaluator = evaluator
        # AutoBuyer (sniper/autobuy.py) albo None - tu tylko do heartbeatu (okazje dostaje od evaluatora).
        self.buyer = buyer
        single = CatalogSpec(cfg.catalog_id, cfg.price_from, cfg.price_to)
        guides_dir = getattr(cfg.ai, "guides_dir", None)
        self.catalogs = CatalogSource(cfg.catalogs, guides_dir, single)
        self.routes = parse_proxy_routes(getattr(cfg, "proxy_routes", ""))
        self.lanes = {MAIN_PROXY: self._lane(MAIN_PROXY, session)}
        for name, extra in (sessions or {}).items():
            if name != MAIN_PROXY:
                self.lanes[name] = self._lane(name, extra)
        missing = sorted(set(self.routes.values()) - set(self.lanes))
        if missing:
            log.warning("[SCOUT] SNIPER_PROXY_ROUTES: brak działającego proxy %s - jego kategorie idą przez główne.",
                        ", ".join(missing))
        self._last_ids = {}       # katalog i zakres cen → najnowsze ID z poprzedniego skanu (przepełnienie strony)
        self._warm = set()        # katalogi po rozgrzewce
        # Wspólna pamięć ID dla wszystkich katalogów - 5 stron na katalog.
        floor = min_dedup(cfg.per_page) * max(1, len(self.catalogs.get()))
        if cfg.dedup_size < floor:
            log.warning("[SCOUT] SNIPER_DEDUP_SIZE=%d to za mało przy stronie %d ofert - używam %d.",
                        cfg.dedup_size, cfg.per_page, floor)
        self.seen = RecentIds(max(cfg.dedup_size, floor))
        # Kolejka "złapanych" ofert dla przyszłego modułu AI (słowniki z Offer.to_dict()).
        self.offers = asyncio.Queue(maxsize=200)
        self._detail_slots = asyncio.Semaphore(cfg.max_concurrent_details)
        self._tasks = set()
        self._first_batch = cfg.skip_initial_batch
        self._stats = {"polls": 0, "errors": 0, "new": 0, "caught": 0, "last_size": 0}
        self._skipped = {}        # powód -> liczba (od ostatniego heartbeatu)
        self._per_catalog = {}    # katalog -> [skany, nowe, wysłane do oceny, pełne strony] (od ostatniego heartbeatu)
        self._top_items = []      # pierwsze oferty z ostatniego skanu, w kolejności Vinted
        self._last_heartbeat = time.monotonic()

    # ------------------------------------------------------------------ katalog
    def active_specs(self):
        """Katalogi do skanowania - bez tych, w których wszystkie kategorie są wyłączone (SNIPER_CATEGORIES)."""
        specs = self.catalogs.get()
        enabled = getattr(self.cfg, "categories", ())
        unknown = [c for c in enabled if c not in AI_ENABLED_NAMES]
        if unknown and not getattr(self, "_warned_categories", False):
            self._warned_categories = True
            log.warning("[SCOUT] Nieznane kategorie w SNIPER_CATEGORIES: %s (dozwolone: %s)", ", ".join(unknown),
                        ", ".join(AI_ENABLED_NAMES))
        chosen = wanted_specs(specs, enabled)
        if not chosen:
            log.warning("[SCOUT] SNIPER_CATEGORIES=%s wyłącza wszystkie katalogi - skanuję wszystkie.",
                        ",".join(enabled))
            chosen = specs
        floored = apply_floor(chosen, getattr(self.cfg, "price_floor", None),
                              exact=self.catalogs.setting.lower() == "auto")
        if floored != chosen and not getattr(self, "_floor_logged", False):
            self._floor_logged = True
            log.info("[SCOUT] Minimalna cena %s zł (SNIPER_PRICE_FLOOR) w katalogach: %s", self.cfg.price_floor,
                     "; ".join(s.label for s in floored))
        return apply_weights(floored, parse_weights(getattr(self.cfg, "catalog_weights", "")))

    # ------------------------------------------------------------------ proxy (pętle skanu)
    def _lane(self, name, session):
        interval = proxy_poll_interval(name, self.cfg.poll_interval)
        fast = getattr(self.cfg, "poll_fast", 0.0) or max(1.5, interval / 2)
        return Lane(name, session, interval, min(fast, interval))

    def lane_for(self, spec):
        """Pętla (proxy) katalogu: wpis SNIPER_PROXY_ROUTES dla numeru katalogu albo jednej z jego kategorii (katalog
        mieszany - pierwsza z przypisanym proxy), inaczej główne proxy."""
        if spec is not None and self.routes:
            for key in (str(spec.catalog_id), *spec.categories):
                lane = self.lanes.get(self.routes.get(key))
                if lane is not None:
                    return lane
        return self.lanes[MAIN_PROXY]

    def next_spec(self, lane=None):
        """Następny katalog do skanu w pętli danego proxy (domyślnie głównego); None = ta pętla nie ma katalogów."""
        lane = lane or self.lanes[MAIN_PROXY]
        if not lane.cycle:
            lane.cycle = schedule([s for s in self.active_specs() if self.lane_for(s) is lane])
        return lane.cycle.pop(0) if lane.cycle else None

    def _speed_up(self, lane):
        """Przepełnienie strony → pętla tego proxy skanuje co `fast_interval` przez `fast_minutes` od ostatniego."""
        minutes = getattr(self.cfg, "fast_minutes", 0.0)
        if not minutes or lane.fast_interval >= lane.interval:
            return False
        now = time.monotonic()
        if lane.fast_until <= now:
            log.info("[SCOUT] %s: przyspieszam - skan co %g s (zwykle %g s) przez %g min od ostatniego przepełnienia.",
                     lane.label, lane.fast_interval, lane.interval, minutes)
        lane.fast_until = now + minutes * 60
        return True

    def _interval(self, lane):
        now = time.monotonic()
        if lane.fast_until and lane.fast_until <= now:
            lane.fast_until = 0.0
            log.info("[SCOUT] %s: spokojniej - skan znów co %g s.", lane.label, lane.interval)
        return lane.current_interval(now)

    # ------------------------------------------------------------------ katalog
    async def _fetch_page(self, lane, spec, page=1):
        return await lane.session.get_json(
            CATALOG_URL, params=catalog_params(self.cfg, spec, page),
            referer="https://www.vinted.pl/", extra_headers=CATALOG_ONLY_HEADERS,  # jak w cURL z przeglądarki
        )

    async def poll_catalog(self, spec=None, lane=None):
        if spec is None:
            lane = lane or self.lanes[MAIN_PROXY]
            spec = self.next_spec(lane)
            if spec is None:
                return
        lane = lane or self.lane_for(spec)
        data = await self._fetch_page(lane, spec)
        items = (data.get("items") if isinstance(data, dict) else None) or []
        self._stats["polls"] += 1
        self._stats["last_size"] = len(items)
        self._top_items = items[:5]
        self._top_label = spec.label
        counts = self._per_catalog.setdefault(spec.label, [0, 0, 0, 0])
        counts[0] += 1
        if not items:
            log.warning("[SCOUT] Pusty katalog (klucze odpowiedzi: %s) - możliwy soft-ban. Odświeżam sesję.",
                        ", ".join(data) if isinstance(data, dict) else type(data).__name__)
            await lane.session.refresh()
            return

        key = (spec.catalog_id, spec.price_from, spec.price_to)
        previous = self._last_ids.get(key)
        self._last_ids[key] = [it["id"] for it in items[:max(1, getattr(self.cfg, "overlap_ids", 5))]]
        # Rosnąco po ID, żeby RecentIds wypychał najstarsze oferty jako pierwsze.
        fresh = [it for it in sorted(items, key=lambda it: it["id"]) if self.seen.add(it["id"])]

        if self._first_batch and spec.catalog_id not in self._warm:
            self._warm.add(spec.catalog_id)
            log.info("[SCOUT] Rozgrzewka %s: zapamiętano %d ofert bez alertów.", spec.label, len(fresh))
            return

        self._handle_fresh(spec, fresh, counts)
        if previous and not set(previous) & {it["id"] for it in items}:
            # Żadnej z najnowszych ofert poprzedniego skanu = od tamtej pory przyszło więcej, niż mieści strona.
            counts[3] += 1
            await self._overflow(lane, spec, previous, counts, len(items))

    async def _overflow(self, lane, spec, previous, counts, page_size):
        """Przepełnienie strony (prośba użytkownika 2026-10-30): dociągnij kolejne strony aż do oferty z poprzedniego
        skanu (SNIPER_GAP_PAGES, żeby nic nie umknęło) i przyspiesz pętlę tego proxy (SNIPER_POLL_FAST)."""
        pages = max(0, int(getattr(self.cfg, "gap_pages", 0)))
        faster = self._speed_up(lane)
        log.warning("[SCOUT] %s: brak żadnej z %d ofert poprzedniego skanu - cała strona (%d) to nowe oferty%s%s.",
                    spec.label, len(previous), page_size,
                    f", dociągam kolejne strony (najwyżej {pages})" if pages else " - część mogła umknąć",
                    f"; skan co {lane.fast_interval:g} s" if faster else "")
        wanted = set(previous)
        for page in range(2, 2 + pages):
            data = await self._fetch_page(lane, spec, page)
            items = (data.get("items") if isinstance(data, dict) else None) or []
            lane.gap_pages += 1
            self._handle_fresh(spec, [it for it in sorted(items, key=lambda it: it["id"]) if self.seen.add(it["id"])],
                               counts)
            if not items or wanted & {it["id"] for it in items}:
                return
        if pages:
            log.warning("[SCOUT] %s: także na %d dociągniętych stronach brak ofert z poprzedniego skanu - część mogła "
                        "umknąć (zwiększ SNIPER_GAP_PAGES albo SNIPER_PER_PAGE).", spec.label, pages)

    def _handle_fresh(self, spec, fresh, counts):
        """Nowe oferty ze strony katalogu → szczegóły i ocena (w tle); w mieszanych bez naszej kategorii - pomijane."""
        self._stats["new"] += len(fresh)
        counts[1] += len(fresh)
        wanted = []
        for item in fresh:
            category = category_for(spec.catalog_id, item.get("title"), getattr(self.cfg, "categories", ()))
            if category == "skip":
                # Katalog mieszany: nie karta / RAM / dysk - bez szczegółów (oszczędza proxy) i bez AI.
                log.debug("[SCOUT] Pomijam %s - inna kategoria: %s", item["id"], item.get("title"))
                self._skip("inna kategoria")
                continue
            wanted.append(item)
            counts[2] += 1
            self._spawn(self.inspect(item, spec, category))
        for it in wanted:
            log.info("[SCOUT] Nowe ogłoszenie %s | %s | %s | %s",
                     it["id"], it.get("title", "?"), _catalog_price(it), item_url(it["id"], it))

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------ detale
    async def inspect(self, item, spec=None, category=None):
        item_id = item["id"]
        referer = item_url(item_id, item)  # jak date_verification.get_sidebar_info: Referer = item['url']
        session = self.lane_for(spec).session         # szczegóły przez to samo proxy (i ciastka) co katalog

        async with self._detail_slots:
            sidebar, shipping = await asyncio.gather(
                session.get_json(SIDEBAR_URL.format(item_id=item_id), referer=referer),
                session.get_json(SHIPPING_URL.format(item_id=item_id), referer=referer),
                return_exceptions=True,
            )

        if isinstance(sidebar, Exception):
            log.error("[SCOUT] Brak detali dla %s: %r", item_id, sidebar)
            self._skip("błąd detali")
            return
        if isinstance(shipping, Exception):
            log.warning("[SCOUT] Brak shipping_details dla %s: %r", item_id, shipping)
            shipping = None

        sidebar = unwrap_sidebar(sidebar)
        reason = inactive_reason(sidebar)
        if reason:
            log.info("[SCOUT] Pomijam %s - status: %s", item_id, reason)
            self._skip(f"status {reason}")
            return

        offer = build_offer(item_id, sidebar, shipping, catalog_item=item)
        offer.category = category
        # Druga linia obrony: gdyby API zignorowało filtr ceny, odsiewamy tutaj.
        price_from, price_to = (spec.price_from, spec.price_to) if spec else (self.cfg.price_from, self.cfg.price_to)
        low, high = _to_float(price_from), _to_float(price_to)
        if offer.price is not None and ((low is not None and offer.price < low) or
                                        (high is not None and offer.price > high)):
            log.info("[SCOUT] Pomijam %s - cena %.2f poza zakresem %s-%s", item_id, offer.price,
                     price_from or "0", price_to or "∞")
            self._skip("cena poza zakresem")
            return
        self.emit(offer)

    def _skip(self, reason):
        self._skipped[reason] = self._skipped.get(reason, 0) + 1

    def emit(self, offer):
        payload = offer.to_dict()
        log.info("[ZŁAPANO] %s%s | %s %s | %s", f"[{offer.category}] " if offer.category else "", offer.title,
                 offer.price, offer.currency, offer.url)
        log.debug(json.dumps(payload, ensure_ascii=False))

        self._stats["caught"] += 1
        self._save_offer(payload)
        if self.offers.full():
            self.offers.get_nowait()  # nikt jeszcze nie konsumuje - wyrzucamy najstarszą
        self.offers.put_nowait(payload)
        if self.evaluator:
            self.evaluator.submit(offer)   # ocena AI w tle; mail wysyła evaluator po ocenie
        else:
            self.notifier.notify(offer)

    def _save_traffic(self, row, lane=None):
        """Wiersz do <log_dir>/traffic.csv (dodatkowe proxy: traffic_<nazwa>.csv) - do porównania ustawień per_page /
        tempa skanów."""
        if not self.cfg.log_dir:
            return
        lane = lane or self.lanes[MAIN_PROXY]
        row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "per_page": self.cfg.per_page,
               "poll_interval": lane.interval, **row}
        try:
            name = "traffic.csv" if lane.name == MAIN_PROXY else f"traffic_{lane.name}.csv"
            path = Path(self.cfg.log_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            new = not path.exists()
            with path.open("a", encoding="utf-8") as f:
                if new:
                    f.write(";".join(row) + "\n")
                f.write(";".join(str(v) for v in row.values()) + "\n")
        except OSError as exc:
            log.warning("[SCOUT] Nie zapisałem traffic.csv: %s", exc)

    def _save_offer(self, payload):
        """Dopisuje ofertę jako jedną linię JSON do <log_dir>/offers.jsonl (mikrosekundy, nie blokuje pętli)."""
        if not self.cfg.log_dir:
            return
        try:
            path = Path(self.cfg.log_dir) / "offers.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("[SCOUT] Nie zapisałem oferty do offers.jsonl: %s", exc)

    # ------------------------------------------------------------------ pętla
    async def run(self):
        specs = self.active_specs()
        log.info("=== ZWIADOWCA START | %s | %d ofert/skan, skan co %.0fs (katalogi po kolei) | proxy=%s ===",
                 "; ".join(f"{s.label}{f' ×{s.share}' if s.share > 1 else ''}" for s in specs),
                 self.cfg.per_page, self.cfg.poll_interval, "TAK" if self.cfg.proxy_url else "NIE")
        lanes = list(self.lanes.values())
        if len(lanes) > 1:
            shared = asyncio.Lock()          # przeglądarki (odświeżanie sesji) po kolei - serwer ma 4 GB RAM
            for lane in lanes:
                lane.session.browser_lock = shared
        if len(lanes) > 1 or self.routes:
            for lane in lanes:
                mine = [s for s in specs if self.lane_for(s) is lane]
                log.info("[SCOUT] %s: skan co %g s (przy przepełnieniu co %g s): %s", lane.label, lane.interval,
                         lane.fast_interval, "; ".join(s.label for s in mine) or "brak katalogów")
        # Pętle dodatkowych proxy startują z losowym przesunięciem - bez równoczesnych zapytań co cykl.
        await asyncio.gather(*(self._run_lane(lane, 0.0 if lane.name == MAIN_PROXY else random.uniform(0, lane.interval))
                               for lane in lanes))

    async def _run_lane(self, lane, first_delay=0.0):
        """Nieskończona pętla skanu jednego proxy: katalogi po kolei (wagi), odstęp lane.interval (po przepełnieniu
        lane.fast_interval), własny backoff - błąd jednego proxy nie zatrzymuje pozostałych."""
        backoff = 0.0
        # Sesja z poprzedniego uruchomienia oszczędza wizytę przeglądarki; jeśli wygasła, pierwszy skan
        # dostanie 401/403 i get_json sam ją odświeży.
        needs_refresh = not lane.session.load_state(self.cfg.session_max_age_min * 60)
        if first_delay:
            await asyncio.sleep(first_delay)
        idle = False
        while True:
            started = time.monotonic()
            spec = None
            try:
                spec = self.next_spec(lane)
                if spec is None:
                    if not idle:
                        log.info("[SCOUT] %s: brak katalogów do skanowania (SNIPER_PROXY_ROUTES / SNIPER_CATEGORIES) - "
                                 "sprawdzam co %.0f s.", lane.label, IDLE_RECHECK_S)
                    idle = True
                else:
                    idle = False
                    if needs_refresh:
                        await lane.session.refresh()
                        needs_refresh = False
                    await self.poll_catalog(spec, lane)
                backoff = 0.0
            except RateLimited:
                backoff = min(max(backoff * 2, 10.0), 120.0)
                log.warning("[SCOUT] %s429 Too Many Requests - czekam %.0fs.", self._tag(lane), backoff)
            except SessionExpired as exc:
                backoff = self.cfg.refresh_backoff
                log.error("[SCOUT] %s%s - czekam %.0fs.", self._tag(lane), exc, backoff)
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
                # Błąd proxy/sieci/JSON - przy rotacyjnym proxy następne żądanie pójdzie z innego IP.
                backoff = min(max(backoff * 2, 2.0), 30.0)
                if isinstance(exc, httpx.ProxyError) and "407" in str(exc):
                    backoff = self.cfg.refresh_backoff
                    log.error("[SCOUT] %s%s Ponawiam za %.0fs.", self._tag(lane), PROXY_AUTH_HELP, backoff)
                else:
                    log.warning("[SCOUT] %sBłąd skanu: %r - ponawiam za %.0fs.", self._tag(lane), exc, backoff)
                self._stats["errors"] += 1
            except Exception:
                # Nieprzewidziany błąd nie może zatrzymać pętli - logujemy pełny traceback i jedziemy dalej.
                backoff = min(max(backoff * 2, 5.0), 60.0)
                log.exception("[SCOUT] %sNieoczekiwany błąd - ponawiam za %.0fs.", self._tag(lane), backoff)
                self._stats["errors"] += 1

            self._heartbeat()
            if backoff:
                await asyncio.sleep(backoff)
            elif spec is None:
                await asyncio.sleep(IDLE_RECHECK_S)
            else:
                # Stałe tempo liczone od startu skanu: czas zapytania nie wydłuża odstępu.
                interval = self._interval(lane)
                jitter = min(self.cfg.poll_jitter, interval / 3)
                target = interval + random.uniform(-jitter, jitter)
                await asyncio.sleep(max(0.0, target - (time.monotonic() - started)))

    def _tag(self, lane):
        """Przedrostek logu z nazwą proxy - tylko gdy pętli jest kilka."""
        return f"{lane.label}: " if len(self.lanes) > 1 else ""

    def _heartbeat(self):
        """Co heartbeat_interval sekund podsumowanie - żeby cisza w logu nie wyglądała na zawieszenie."""
        interval = self.cfg.heartbeat_interval
        if not interval or time.monotonic() - self._last_heartbeat < interval:
            return
        s = self._stats
        skipped = ", ".join(f"{k}: {v}" for k, v in self._skipped.items()) or "0"
        log.info("[SCOUT] Żyję (%.0fs): %d skanów, %d błędów | nowych %d, złapanych %d, pominiętych: %s | "
                 "maile od startu: wysłane %s, błędy %d | katalog: %d ofert",
                 interval, s["polls"], s["errors"], s["new"], s["caught"], skipped,
                 self.notifier.summary() if hasattr(self.notifier, "summary") else self.notifier.sent,
                 self.notifier.failed, s["last_size"])
        if self._per_catalog:
            log.info("[SCOUT] Katalogi (skany / nowe / do oceny[ / przepełnienia]): %s | strona %d ofert", " | ".join(
                f"{label.split(' ', 2)[0]} {label.split(' ', 2)[1]} {c[0]}/{c[1]}/{c[2]}" + (f"/{c[3]}!" if c[3] else "")
                for label, c in self._per_catalog.items()), self.cfg.per_page)
        now = time.monotonic()
        lanes = list(self.lanes.values())
        if len(lanes) > 1 or any(lane.fast_until > now or lane.gap_pages for lane in lanes):
            log.info("[SCOUT] Proxy (tempo skanu): %s", " | ".join(
                f"{lane.name} co {lane.current_interval(now):g} s"
                + (f" PRZYSPIESZONE jeszcze {(lane.fast_until - now) / 60:.0f} min" if lane.fast_until > now else "")
                + (f", dociągnięte strony {lane.gap_pages}" if lane.gap_pages else "") for lane in lanes))
            for lane in lanes:
                lane.gap_pages = 0
        if self._top_items:
            log.info("[SCOUT] Pierwsze oferty w ostatnio skanowanym katalogu %s (kolejność Vinted):",
                     getattr(self, "_top_label", ""))
            for it in self._top_items:
                log.info("    %s | %s | %s | %s", it.get("id"), it.get("title", "?"), _catalog_price(it),
                         item_url(it.get("id"), it))
        if self.evaluator:
            log.info("[SCOUT] %s", self.evaluator.window_report())
        if self.buyer:
            log.info("[SCOUT] %s", self.buyer.report())
        for lane in lanes:
            text, row = lane.session.traffic.window_report()
            log.info("[SCOUT] %s%s", self._tag(lane), text)
            self._save_traffic(row, lane)
        for key in ("polls", "errors", "new", "caught"):
            s[key] = 0
        self._skipped = {}
        self._per_catalog = {}
        self._last_heartbeat = time.monotonic()

    async def shutdown(self):
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
