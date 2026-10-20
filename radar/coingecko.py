"""Klient CoinGecko z automatycznym doborem planu API.

Warianty autoryzacji (próbowane po kolei, trafienie cache'owane):
  1. plan PRO  — https://pro-api.coingecko.com  + nagłówek x-cg-pro-api-key
  2. plan DEMO — https://api.coingecko.com      + nagłówek x-cg-demo-api-key
  3. bez klucza — https://api.coingecko.com      (ostateczność)

Sam odróżnia klucz DEMO od PRO po HTTP 401/403, więc w config.json możesz
wpisać każdy z nich.

Wykrywanie nowych monet:
  * z kluczem Pro  — GET /coins/list/new (ostatnie 200 monet + activated_at)
  * fallback       — GET /coins/list, różnicowanie pełnej listy

Dodatkowo: GET /exchanges/{id}/tickers — pary notowane na SafeTrade
śledzone przez CoinGecko (pewne skrzyżowanie obu źródeł, po coin_id).
"""
from __future__ import annotations

import logging

from .httpc import get_json

log = logging.getLogger("radar")

FREE_BASE = "https://api.coingecko.com/api/v3"
PRO_BASE = "https://pro-api.coingecko.com/api/v3"

# zapamiętany trafiony wariant: {"pair": (base, header_name)}
_KEY_CACHE: dict = {}

_PRO_VARIANT = (PRO_BASE, "x-cg-pro-api-key")
_DEMO_VARIANT = (FREE_BASE, "x-cg-demo-api-key")


def reset_key_cache() -> None:
    _KEY_CACHE.clear()


def _attempts(api_key: str, keyless: bool) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    if api_key:
        pair = _KEY_CACHE.get("pair")
        if pair:
            out.append(pair)
        for v in (_PRO_VARIANT, _DEMO_VARIANT):
            if v not in out:
                out.append(v)
    if keyless:
        out.append((FREE_BASE, None))
    return out


def _cg_get(session, path: str, *, params: dict | None = None,
            api_key: str = "", keyless: bool = True,
            label: str = "") -> tuple[object | None, int]:
    """GET z kolejką wariantów host+nagłówek. Zwraca (json|None, status)."""
    last_status = 0
    for base, header in _attempts(api_key, keyless):
        headers = {header: api_key} if header else None
        data, status = get_json(session, base + path, params=params,
                                headers=headers, label=label or path)
        last_status = status
        if data is not None:
            if header:
                _KEY_CACHE["pair"] = (base, header)  # zapamiętaj trafiony wariant
            return data, 200
        if status in (401, 403, 404):
            continue  # zły host/nagłówek albo brak endpointu w planie — następny wariant
        if status == 429:
            break  # get_json już odczekał limit; nie ma co próbować dalej
    return None, last_status


def fetch_new_coins_pro(session, api_key: str) -> tuple[list | None, int]:
    """Ostatnie 200 monet dodanych na CoinGecko (endpoint planów Pro).

    Dla klucza Demo/invalid zwróci (None, status) → wywołujący używa
    darmowego różnicowania /coins/list.
    """
    data, status = _cg_get(session, "/coins/list/new", api_key=api_key,
                           keyless=False, label="cg/list/new")
    if not isinstance(data, list):
        return None, status
    return data, 200


def fetch_coins_list(session, api_key: str = "") -> tuple[list | None, int]:
    """Pełna lista aktywnych monet CoinGecko (z kluczem Demo — wyższy limit)."""
    data, status = _cg_get(session, "/coins/list", api_key=api_key,
                           keyless=True, label="cg/list")
    if not isinstance(data, list):
        return None, status
    return data, 200


def fetch_exchange_tickers(session, exchange_id: str, api_key: str = "",
                           per_page: int = 100, max_pages: int = 10
                           ) -> tuple[list | None, int]:
    """Tickery giełdy na CoinGecko — pola m.in.: base, target, coin_id, volume."""
    out: list = []
    page = 1
    while page <= max_pages:
        data, status = _cg_get(
            session, f"/exchanges/{exchange_id}/tickers",
            params={"per_page": per_page, "page": page},
            api_key=api_key, keyless=True, label="cg/exchanges/tickers")
        if not isinstance(data, dict):
            return None, status
        tickers = data.get("tickers") or []
        out.extend(tickers)
        if len(tickers) < per_page:
            break
        page += 1
    return out, 200


def coin_url(coin_id: str) -> str:
    return f"https://www.coingecko.com/en/coins/{coin_id}"
