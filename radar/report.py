"""Raport dzienny: zbiorcze podsumowanie nowych listowań z ostatnich 24h.

Wysyłany o skonfigurowanej godzinie (czas warszawski) w trybie watch,
albo ręcznie poleceniem `python3 -m radar report [--hours N]`.
"""
from __future__ import annotations

import html
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .alerts import Sink, _fmt_ts
from .config import Config
from .store import Store

log = logging.getLogger("radar")
WARSAW = ZoneInfo("Europe/Warsaw")

# kolejność sekcji w raporcie (etykiety po polsku)
SECTIONS: list[tuple[str, str]] = [
    ("cross_hit", "🎯 CROSS — jednocześnie CoinGecko + SafeTrade"),
    ("st_new_market_live", "🚀 SafeTrade — nowe notowania"),
    ("st_state_change", "⚡ SafeTrade — otwarcie/zmiana notowań"),
    ("st_new_market_pre", "📥 SafeTrade — pre-listingi (przed notowaniami)"),
    ("cg_safe_pair", "🔗 CoinGecko → SafeTrade (nowe pary)"),
    ("cg_new_coin", "🆕 CoinGecko — nowe monety"),
    ("pump_promising", "🔬 Pump.fun — projekty z sygnałami fundamentów"),
]
MAX_PER_SECTION = 12
TG_LIMIT = 3900  # Telegram: maks. 4096 znaków na wiadomość


def _collect(store: Store, since_ts: float) -> dict[str, list]:
    rows = store.conn.execute(
        "SELECT * FROM events WHERE ts >= ? ORDER BY id", (int(since_ts),)
    ).fetchall()
    by_type: dict[str, list] = {}
    for r in rows:
        by_type.setdefault(r["etype"], []).append(r)
    return by_type


def _known_types() -> set[str]:
    return {etype for etype, _ in SECTIONS}


def _render(store: Store, cfg: Config, since_ts: float, now_ts: float,
            *, as_html: bool, cap: int = MAX_PER_SECTION) -> str:
    by_type = _collect(store, since_ts)
    total = sum(len(v) for v in by_type.values())
    high = sum(1 for v in by_type.values() for r in v if r["priority"] == "high")

    win = (f"{_fmt_ts(since_ts)} → {_fmt_ts(now_ts)} (Warszawa)")
    if as_html:
        lines = [
            "<b>📊 Raport dzienny — Listing Radar</b>",
            f"<i>Okno: {html.escape(win)}</i>",
        ]
    else:
        lines = ["📊 Raport dzienny — Listing Radar", f"Okno: {win}"]

    if total == 0:
        lines.append("")
        lines.append("Brak nowych listowań w tym oknie 🎉")
        return "\n".join(lines)

    known = _known_types()
    for etype, label in SECTIONS:
        rows = by_type.get(etype) or []
        if not rows:
            continue
        shown = rows[:cap]
        head = f"{label} — {len(rows)}"
        lines.append("")
        lines.append(f"<b>{html.escape(head)}</b>" if as_html else head)
        for r in shown:
            hhmm = datetime.fromtimestamp(r["ts"], WARSAW).strftime("%H:%M")
            title = r["title"]
            if as_html:
                t = html.escape(title)
                item = f'• {hhmm} <a href="{html.escape(r["url"], quote=True)}">{t}</a>' \
                    if r["url"] else f"• {hhmm} {t}"
            else:
                item = f"• {hhmm} {title}" + (f"  {r['url']}" if r["url"] else "")
            lines.append(item)
        if len(rows) > cap:
            more = f"... +{len(rows) - cap} więcej"
            lines.append(f"<i>{more}</i>" if as_html else more)

    # ewentualne nieprzewidziane typy zdarzeń
    others = [(etype, rows) for etype, rows in by_type.items()
              if etype not in known and rows]
    for etype, rows in others:
        head = f"📌 Inne ({etype}) — {len(rows)}"
        lines.append("")
        lines.append(f"<b>{html.escape(head)}</b>" if as_html else head)
        for r in rows[:cap]:
            hhmm = datetime.fromtimestamp(r["ts"], WARSAW).strftime("%H:%M")
            t = html.escape(r["title"]) if as_html else r["title"]
            lines.append(f"• {hhmm} {t}")

    foot = f"Razem: {total} zdarzeń (priorytet wysoki: {high})"
    lines.append("")
    lines.append(f"<b>{foot}</b>" if as_html else foot)
    return "\n".join(lines)


def send_report(cfg: Config, store: Store, sink: Sink, *, hours: int = 24,
                mark: bool = True) -> bool:
    """Buduje raport, drukuje do logu i wysyła na Telegram (jeśli skonfigurowany)."""
    now_ts = time.time()
    since_ts = now_ts - hours * 3600

    # zmniejszaj liczbę pozycji, aż zmieści się w limicie Telegrama
    cap = MAX_PER_SECTION
    while True:
        plain = _render(store, cfg, since_ts, now_ts, as_html=False, cap=cap)
        html_text = _render(store, cfg, since_ts, now_ts, as_html=True, cap=cap)
        if not cfg.telegram.enabled or len(html_text) <= TG_LIMIT or cap <= 1:
            break
        cap = max(1, cap // 2)

    sink.info(plain)

    ok = True
    if cfg.telegram.enabled:
        if len(html_text) > TG_LIMIT:
            html_text = html_text[:TG_LIMIT] + "\n…"
        ok = sink.send_telegram(html_text)
        if ok:
            sink.info("📤 Raport dzienny wysłany na Telegram")
        else:
            sink.info("⚠ Raport: NIE wysłano na Telegram (ponowię)")

    if mark and ok:
        today = datetime.now(WARSAW).strftime("%Y-%m-%d")
        store.meta_set("daily_report_last", today)
    return ok


def maybe_send_daily_report(cfg: Config, store: Store, sink: Sink) -> None:
    """Wożone z pętli watch: wysyła raport raz dziennie o ustawionej godzinie."""
    if not cfg.daily_report_enabled:
        return
    now_dt = datetime.now(WARSAW)
    today = now_dt.strftime("%Y-%m-%d")
    if store.meta_get("daily_report_last") == today:
        return
    if (now_dt.hour, now_dt.minute) < (cfg.daily_report_hour, cfg.daily_report_minute):
        return
    retry_after = int(store.meta_get("daily_report_retry_after") or 0)
    if time.time() < retry_after:
        return

    log.info("Raport dzienny: wysyłam (godzina %02d:%02d minęła)",
             cfg.daily_report_hour, cfg.daily_report_minute)
    ok = send_report(cfg, store, sink, hours=24, mark=True)
    if not ok:
        # Telegram zawiodło — ponów za 10 minut, nie co 5 sekund
        store.meta_set("daily_report_retry_after", str(int(time.time()) + 600))
