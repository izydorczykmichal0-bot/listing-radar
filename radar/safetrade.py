"""Klient giełdy SafeTrade (API typu Peatio, wersja v4).

Łańcuch pobierania rynków (odporny na blokady):
  1. https://safetrade.com/api/v2/trade/public/markets   (przez requests,
     pełne nagłówki przeglądarki)
  2. ten sam adres, ale przez curl (inny „odcisk" TLS — omija część
     blokad Cloudflare na Windows)
  3. https://safe.trade/api/v2/peatio/public/markets?limit=500
     (stara domena — brak pola created_at, ale komplet rynków)

Pola rynku: id, base_unit, quote_unit, state (enabled/disabled),
created_at → po created_at poznajemy moment dodania rynku.
"""
from __future__ import annotations

import logging

from .httpc import BROWSER_HEADERS, get_json, get_json_via_curl

log = logging.getLogger("radar")

BASE = "https://safetrade.com/api/v2/trade/public"
LEGACY_BASE = "https://safe.trade/api/v2/peatio/public"

# (url, sposób pobrania) w kolejności priorytetu; źródło trafia do alertów
_MARKET_SOURCES = (
    ("requests", f"{BASE}/markets"),
    ("curl", f"{BASE}/markets"),
    ("safe.trade", f"{LEGACY_BASE}/markets?limit=500"),
)
_TICKER_SOURCES = (
    ("requests", f"{BASE}/tickers"),
    ("curl", f"{BASE}/tickers"),
    ("safe.trade", f"{LEGACY_BASE}/markets/tickers"),
)


def _fetch_chain(session, chain, label: str):
    """Zwraca (data, status, source) pierwszego działającego źródła."""
    last_status = 0
    for source, url in chain:
        if source == "curl":
            data, status = get_json_via_curl(url, headers=BROWSER_HEADERS,
                                             label=f"{label}/curl")
        else:
            data, status = get_json(session, url, headers=BROWSER_HEADERS,
                                    label=f"{label}/{source}")
        last_status = status
        if isinstance(data, (list, dict)) and data:
            if source != "requests":
                log.info("%s: używam źródła zapasowego %s", label, source)
            return data, 200, source
    return None, last_status, ""


def fetch_markets(session) -> tuple[dict[str, dict] | None, int, str]:
    """(rynki po id, status, źródło 'requests'|'curl'|'safe.trade')."""
    data, status, source = _fetch_chain(session, _MARKET_SOURCES, "st/markets")
    if not isinstance(data, list):
        return None, status, ""
    return {m["id"]: m for m in data if isinstance(m, dict) and m.get("id")}, 200, source


def fetch_tickers(session) -> tuple[dict[str, dict] | None, int]:
    """(tickery po id rynku, status) — tylko do wzbogacenia alertu ceną."""
    data, status, _ = _fetch_chain(session, _TICKER_SOURCES, "st/tickers")
    if not isinstance(data, dict):
        return None, status
    return data, 200


def trade_url(base: str, quote: str) -> str:
    """Link do rynku w UI SafeTrade, np. .../exchange/BTC-USDT."""
    return f"https://safetrade.com/exchange/{base.upper()}-{quote.upper()}"


def announcements_url() -> str:
    return "https://safetrade.com/announcements"
