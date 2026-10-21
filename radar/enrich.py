"""Wzbogacanie alertów o kategorię coinu i krótki opis zastosowań.

Przy każdym nowym listowaniu do szczegółów zdarzenia dopinamy blok:

    🏷 Kategorie: L1, DeFi
    📖 Opis: Moneta X to ...

Dane pobieramy z CoinGecko (/coins/{id}) **wyłącznie w momencie tworzenia
zdarzenia** (kilka–kilkanaście wywołań dziennie), więc mieścimy się nawet
w darmowym limicie bez klucza. Każde niepowodzenie jest ciche — alert i tak
wychodzi, tylko bez bloku (wzbogacanie nigdy nie może zatrzymać sygnału).
"""
from __future__ import annotations

import logging
import re

from . import coingecko as cg
from .config import Config

log = logging.getLogger("radar")

# reguły: fragment nazwy kategorii → czytelna etykieta w alercie
# (CoinGecko zwraca nazwy typu "Layer 1 (L1)", "Meme Token" — mapujemy na krótkie L1/L2/...)
_CAT_RULES: list[tuple[str, str]] = [
    ("layer 1", "L1"), ("layer-1", "L1"),
    ("layer 2", "L2"), ("layer-2", "L2"),
    ("meme", "Memecoin"), ("dog-themed", "Memecoin"),
    ("defi", "DeFi"),
    ("decentralized exchange", "DEX"), ("dex", "DEX"),
    ("stablecoin", "Stablecoin"),
    ("oracle", "Oracle"),
    ("privacy", "Privacy"),
    ("gaming", "Gaming"), ("play-to-earn", "P2E"),
    ("artificial intelligence", "AI"), (" ai ", "AI"),
    ("nft", "NFT"),
    ("real world asset", "RWA"), ("rwa", "RWA"),
    ("interoperab", "Interop"),
    ("zero knowledge", "zk"),
    ("dao", "DAO"),
    ("payment", "Płatności"),
    ("staking", "Staking"),
    ("scaling", "Scaling"),
]

# szum, którego nie chcemy w alercie: ekosystemy, indeksy, holdingi
_SKIP_SUBSTR = ("ecosystem", "index", "holdings")

_MAX_CATS = 4


def _pretty(cid: str) -> str:
    if " " in cid or any(ch.isupper() for ch in cid):
        return cid.strip()              # to już czytelna nazwa — nie przekształcamy
    return re.sub(r"[-_]+", " ", cid).strip().title()


def _map_cat(name: str) -> str | None:
    low = f" {name.lower()} "
    for probe, label in _CAT_RULES:
        if probe in low:
            return label
    return None


def _categories(raw) -> list[str]:
    out: list[str] = []
    for cid in raw or []:
        if not isinstance(cid, str) or not cid.strip():
            continue
        low = cid.lower()
        if any(s in low for s in _SKIP_SUBSTR):   # "solana-ecosystem", "Gmci Index"...
            continue
        name = _map_cat(cid) or _pretty(cid)
        if name not in out:
            out.append(name)
        if len(out) >= _MAX_CATS:
            break
    return out


def _clean_description(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text or "")   # markdown → tekst
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 420:
        cut = text[:420]
        sp = cut.rfind(" ")
        text = (cut[:sp] if sp > 250 else cut).rstrip(" ,.;:–-") + "…"
    return text


def fetch_info(session, cfg: Config, coin_id: str) -> dict | None:
    """Kategorie + opis coinu albo None (błąd/brak danych — bez wyjątków)."""
    if not coin_id:
        return None
    try:
        data, status = cg.fetch_coin(session, coin_id, cfg.coingecko_api_key)
    except Exception as e:  # noqa: BLE001 — wzbogacanie nie może przerwać alertu
        log.warning("enrich: wyjątek przy %s: %s", coin_id, e)
        return None
    if not data:
        log.info("enrich: brak szczegółów %s (status %s)", coin_id, status)
        return None
    desc_raw = ""
    d = data.get("description") or {}
    for lang in ("pl", "en"):            # wolimy polski opis, jak CoinGecko ma
        v = d.get(lang)
        if isinstance(v, str) and v.strip():
            desc_raw = v
            break
    return {
        "categories": _categories(data.get("categories")),
        "description": _clean_description(desc_raw),
    }


def enrich(session, cfg: Config, coin_id: str) -> str:
    """Blok do dopięcia do szczegółów zdarzenia — „\\n🏷 ..." albo ""."""
    info = fetch_info(session, cfg, coin_id)
    if not info:
        return ""
    parts: list[str] = []
    if info["categories"]:
        parts.append("🏷 Kategorie: " + ", ".join(info["categories"]))
    if info["description"]:
        parts.append("📖 Opis: " + info["description"])
    return ("\n" + "\n".join(parts)) if parts else ""
