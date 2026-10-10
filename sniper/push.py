"""Powiadomienie push na telefon o okazji - przez ntfy (https://ntfy.sh, darmowa aplikacja Android / iOS).

Program wysyła JSON na serwer ntfy, a aplikacja na telefonie pokazuje wyskakujące powiadomienie z dźwiękiem.
Okazja idzie z najwyższym priorytetem (5 = „max”): w Androidzie to osobny kanał powiadomień, któremu ustawiasz
własny, unikalny dźwięk (instrukcja w README). Dotknięcie powiadomienia otwiera ofertę (aplikacja Vinted
albo przeglądarka).

Wysyłka idzie bezpośrednio z komputera (NIE przez proxy IPRoyal), w tle - nie blokuje Zwiadowcy.
Uwaga: na publicznym ntfy.sh każdy, kto zna nazwę tematu, może go czytać - użyj długiej, losowej nazwy.

Test:  python -m sniper.push   (wysyła przykładowe powiadomienie o okazji)
"""
import asyncio
import logging

import httpx

log = logging.getLogger("sniper.push")


def _pln(value):
    return f"{value:,.0f}".replace(",", " ") + " zł" if isinstance(value, (int, float)) else "?"


def build_deal_message(offer, evaluation, topic, priority=5, attach_photo=True):
    """JSON dla ntfy (publikacja na adres serwera, temat w treści). Czysta funkcja - testowalna bez sieci."""
    ev = evaluation or {}
    score = ev.get("score")
    title = f"Okazja {score:g}/10: {offer.get('title') or '?'}" if isinstance(score, (int, float)) \
        else f"Okazja: {offer.get('title') or '?'}"
    lines = [f"{_pln(offer.get('total_price'))} z wysyłką (cena {_pln(offer.get('price'))})"]
    if ev.get("max_buy_price_pln") is not None or ev.get("potential_profit_pln") is not None:
        lines.append(f"Maks. zakup wg AI: {_pln(ev.get('max_buy_price_pln'))} | zysk ~{_pln(ev.get('potential_profit_pln'))}")
    parts = [p for p in (ev.get("gpu_model"), ev.get("laptop_model")) if p]
    if parts:
        lines.append(" / ".join(parts))
    if ev.get("reasoning"):
        lines.append(str(ev["reasoning"])[:300])
    msg = {
        "topic": topic,
        "title": title[:120],
        "message": "\n".join(lines),
        "priority": int(priority),
        "tags": ["moneybag"],
    }
    url = offer.get("url")
    if url:
        msg["click"] = url
        msg["actions"] = [{"action": "view", "label": "Otwórz ofertę", "url": url, "clear": True}]
    photos = offer.get("photo_urls") or []
    if attach_photo and photos:
        msg["attach"] = photos[0]
        msg["filename"] = "oferta.jpg"
    return msg


class PushNotifier:
    """Nieblokujące powiadomienia ntfy. Błąd wysyłki tylko w logu - nigdy nie zatrzymuje programu."""

    def __init__(self, cfg, client=None):
        self.cfg = cfg
        self._client = client
        self._tasks = set()
        self.sent = 0
        self.failed = 0
        log.info("[PUSH] Powiadomienia na telefon włączone: %s, temat %s…, od oceny %g, priorytet %d.",
                 cfg.server, cfg.topic[:4], cfg.min_score, cfg.priority)

    def wants(self, record):
        ev = (record or {}).get("evaluation") or {}
        score = ev.get("score")
        return record.get("status") == "oceniona" and isinstance(score, (int, float)) and score >= self.cfg.min_score

    def notify_deal(self, record):
        """Planuje wysyłkę (w tle) i od razu wraca."""
        msg = build_deal_message(record.get("offer") or {}, record.get("evaluation"), self.cfg.topic,
                                 self.cfg.priority, self.cfg.attach_photo)
        task = asyncio.create_task(self.send(msg), name="push-deal")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def send(self, msg):
        headers = {"Authorization": f"Bearer {self.cfg.token}"} if self.cfg.token else {}
        try:
            client = self._client or httpx.AsyncClient(timeout=10)
            try:
                r = await client.post(self.cfg.server.rstrip("/"), json=msg, headers=headers)
                r.raise_for_status()
            finally:
                if self._client is None:
                    await client.aclose()
            self.sent += 1
            log.info("[PUSH] Wysłano na telefon: %s", msg.get("title"))
            return True
        except Exception as exc:
            self.failed += 1
            log.error("[PUSH] Nie wysłałem powiadomienia na telefon: %r", exc)
            return False

    async def drain(self, timeout=10.0):
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=timeout)


def build_push(cfg):
    """PushNotifier z konfiguracji albo None (SNIPER_NTFY_TOPIC puste)."""
    ntfy = getattr(cfg, "ntfy", None)
    if ntfy is None or not ntfy.topic:
        return None
    return PushNotifier(ntfy)


async def _test():
    from .config import ScoutConfig
    from .notifier import sample_offer
    cfg = ScoutConfig()
    push = build_push(cfg)
    if push is None:
        print("Ustaw SNIPER_NTFY_TOPIC w sniper/.env (patrz README: Powiadomienia na telefon).")
        return 1
    offer = sample_offer().to_dict()
    evaluation = {"score": 9, "is_deal": True, "max_buy_price_pln": 3200, "potential_profit_pln": 700,
                  "gpu_model": "RTX 4070", "reasoning": "TEST - tak będzie wyglądać powiadomienie o okazji."}
    ok = await push.send(build_deal_message(offer, evaluation, cfg.ntfy.topic, cfg.ntfy.priority,
                                            cfg.ntfy.attach_photo))
    print("Wysłano - sprawdź telefon." if ok else "Nie udało się - szczegóły wyżej.")
    return 0 if ok else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    raise SystemExit(asyncio.run(_test()))
