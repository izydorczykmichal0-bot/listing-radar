"""Wczesne wykrywanie tokenów z sygnałami fundamentów na pump.fun.

pump.fun to z definicji fabryka memecoinów (~40 tys. tokenów dziennie, ~2,7%
dyplomacji). Ten moduł nie obiecuje „pewnych diamondów" — robi coś skromnego
i mierzalnego:

1. pobiera listę NAJNOWSzych tokenów (frontend-api-v3.pump.fun — API
   nieoficjalne, odłączalne flagą `pump.enabled`),
2. odsiewa memy po nazwie/symbolu (leksykon: pepe, doge, inu, moon...),
3. dla ocalałych pobiera metadane (opis + linki z IPFS/stron twórców),
4. wypuszcza alert 🔬 TYLKO gdy token ma:
   - opis ≥80 znaków ze słowami użytkowymi (protocol, defi, payments, ai...),
   - JAKIKOLWIEK link (strona / twitter / telegram),
   - brak memowego opisu,
   - scoring ≥ progu (`pump.min_score`).

Każdy token oceniany jest DOKŁADNIE RAZ (tabele pump_seen/pump_queue).
Wszystkie błędy modułu są łapane wyżej (run_cycle/watch) — nigdy nie blokują
CoinGecko/SafeTrade. To filtr sygnałów informacyjnych, NIE doradztwo
inwestycyjne.
"""
from __future__ import annotations

import logging
import re
import time

from .config import Config
from .enrich import _clean_description
from .httpc import get_json

log = logging.getLogger(__name__)

API = "https://frontend-api-v3.pump.fun"

# --------------------------------------------------------------- etap1: nazwa
# leksykon memecoinów — substring w nazwie/symbolu (lowercase).
_LEX = (
    "pepe", "doge", "dogwif", "wif", "inu", "shib", "cat", "frog", "bonk",
    "popcat", "mog", "memecoin", "meme", "chad", "wojak", "npc", "pup",
    "kitten", "hamster", "moon", "rocket", "fart", "retard", "rtrd", "kek",
    "blub", "cheems", "quack", "dog", "snek", "giga", "turbo",
)

# --------------------------------------------------------------- etap2: opis
# słowa użytkowe (sygnał „projekt ma zastosowanie") — regex z granicami słów
_UTIL = (
    r"\bprotocol", r"\bnetwork", r"\bdefi\b", r"\bdex\b", r"\bswap",
    r"\blend", r"\bborrow", r"\bstak", r"\byield", r"\boracle", r"\bbridge",
    r"\bpayment", r"infrastructure", r"\binfra\b", r"artific", r"\bai\b",
    r"\bagents?\b", r"\bcompute", r"\bstorage\b", r"\bprivacy\b", r"\bzk\b",
    r"\bscaling\b", r"\brwa\b", r"tokeniz", r"\bgovern", r"\bdao\b",
    r"\bwallet", r"developers?\b", r"\bapi\b", r"\btools?\b", r"analytic",
    r"\bidentity\b", r"\bfintech\b", r"payroll", r"\boracles?\b", r"secur",
    r"scalab", r"ledger", r"\bnode", r"staking", r"\bfarms?\b",
)
# słowa memowe w OPISIE (≥2 = odrzucamy)
_MEME = (
    r"to the moon", r"\bmoon\b", r"\brocket\b", r"\bwen\b", r"\bngmi\b",
    r"\bwagmi\b", r"\bdegens?\b", r"\bpepe\b", r"\bdoge\b", r"\bchad",
    r"\binu\b", r"\bmeme", r"wen\b", r"\blfg\b", r"\bgm\b",
)

_MIN_DESC = 80
_RETENTION = 3 * 3600   # oceny sprzed 3h nie wracają na listę najnowszych


def lex_bad(name: str, symbol: str) -> bool:
    """True = nazwa/symbol wygląda na memecoina."""
    low = f"{name or ''} {symbol or ''}".lower()
    return any(p in low for p in _LEX)


def fetch_new_coins(session, *, page_size: int = 100, max_pages: int = 10) -> list[dict]:
    """Najnowsze monety z frontend-api-v3 (desc → starsze). [] przy błędzie."""
    out: list[dict] = []
    for page in range(max_pages):
        data, status = get_json(
            session, f"{API}/coins",
            params={"limit": page_size, "offset": page * page_size, "order": "desc"},
            timeout=15, max_retries=2, label="pump/list",
        )
        if not isinstance(data, list) or not data:
            break
        out.extend(x for x in data if isinstance(x, dict) and x.get("mint"))
        if len(data) < page_size:
            break
    return out


def fetch_metadata(session, uri: str) -> tuple[dict | None, int]:
    """JSON metadanych tokenu (IPFS/arweave/domena twórcy). (None, status)."""
    if not uri or not uri.startswith(("http://", "https://")):
        return None, 0
    data, status = get_json(session, uri, timeout=10, max_retries=1, label="pump/meta")
    if isinstance(data, dict):
        return data, 200
    return None, status


def fetch_token_info(session, uri: str, mint: str) -> dict | None:
    """Opis + linki tokenu. Łańcuch źródeł (jedno API/ipfs.io potrafi403):

    1. oryginalny metadata_uri (ipfs.io / pinata / domena twórcy),
    2. dla IPFS: bramka gateway.pinata.cloud (odporniejsza na403),
    3. własne API pump: /coins-v2/{mint}.
    None = wszystkie źródła zawiodły.
    """
    tries: list[str] = []
    if uri:
        tries.append(uri)
        if "/ipfs/" in uri:
            path = uri.split("/ipfs/", 1)[1]
            if "?" in path:
                path = path.split("?", 1)[0]
            tries.append(f"https://gateway.pinata.cloud/ipfs/{path}")
    for u in tries:
        meta, _ = fetch_metadata(session, u)
        if meta is not None:
            return {
                "desc": str(meta.get("description") or ""),
                "website": str(meta.get("website") or ""),
                "twitter": str(meta.get("twitter") or ""),
                "telegram": str(meta.get("telegram") or ""),
            }
    data, _status = get_json(session, f"{API}/coins-v2/{mint}",
                             timeout=15, max_retries=1, label="pump/coins-v2")
    if isinstance(data, dict):
        return {
            "desc": str(data.get("description") or ""),
            "website": str(data.get("website") or ""),
            "twitter": str(data.get("twitter") or ""),
            "telegram": str(data.get("telegram") or ""),
        }
    return None


def score_token(desc: str, website: str, twitter: str, telegram: str,
                min_score: int) -> tuple[int, bool, list[str]]:
    """Scoring fundamentów. Zwraca (punkty, alert?, powody odrzucenia)."""
    d = f" {desc.lower()} "
    util = sum(1 for p in _UTIL if re.search(p, d))
    meme = sum(1 for p in _MEME if re.search(p, d))
    reasons: list[str] = []
    if len(desc) < _MIN_DESC:
        reasons.append(f"opis <{_MIN_DESC} znaków")
    if util == 0:
        reasons.append("brak słów użytkowych")
    if meme >= 2:
        reasons.append(f"opis memowy ({meme} traf.)")
    if not (website or twitter or telegram):
        reasons.append("brak jakichkolwiek linków")
    if reasons:
        return 0, False, reasons
    pts = min(util, 4)
    pts += 2 if website else 0
    pts += 2 if twitter else 0
    pts += 1 if telegram else 0
    pts += 1 if len(desc) >= 200 else 0
    if pts < min_score:
        return pts, False, [f"za niski wynik ({pts}<{min_score})"]
    return pts, True, []


def cycle_pump(cfg: Config, store, sink, session, counter: dict, emit_fn) -> dict:
    """Jeden cykl: nowe monety → odsiew → metadane → scoring → alerty 🔬."""
    stats = {"fresh": 0, "queued": 0, "reviewed": 0, "candidates": 0,
             "baseline": 0}
    if not cfg.pump_enabled:
        return stats

    store.pump_prune(int(time.time()) - _RETENTION)
    items = fetch_new_coins(session)
    if not items:
        return stats

    # ---------------- baza odniesienia (pierwszy cykl) ----------------
    if store.pump_seen_count() == 0:
        now = int(time.time())
        store.pump_mark_seen(
            [{"mint": x["mint"], "name": x.get("name"), "symbol": x.get("symbol")}
             for x in items], now, "baseline")
        stats["baseline"] = len(items)
        sink.info(f"📌 Pump.fun: baza odniesienia — {len(items)} ostatnich "
                  f"tokenów pominiętych; kolejne cykle = tylko nowe.")
        return stats

    # ---------------- etap1: znane / nsfw / leksykon ----------------
    now = int(time.time())
    for x in items:
        mint = x["mint"]
        if store.pump_known(mint):
            continue
        stats["fresh"] += 1
        name = str(x.get("name") or "")
        symbol = str(x.get("symbol") or "")
        if x.get("nsfw") or x.get("is_banned"):
            store.pump_verdict(mint, name, symbol, "nsfw", 0, now)
            continue
        if lex_bad(name, symbol):
            store.pump_verdict(mint, name, symbol, "lex", 0, now)
            continue
        store.pump_queue_add({
            "mint": mint, "name": name, "symbol": symbol,
            "uri": str(x.get("metadata_uri") or ""),
            "website": str(x.get("website") or ""),
            "twitter": str(x.get("twitter") or ""),
            "created_ms": int(x.get("created_timestamp") or 0),
            "mcap_usd": float(x.get("market_cap_usd") or 0.0),
            "complete": 1 if x.get("complete") else 0,
        })
        stats["queued"] += 1

    # ---------------- etap2: metadane + scoring ----------------
    for row in store.pump_queue_pop(cfg.pump_meta_per_cycle):
        mint = row["mint"]
        info = fetch_token_info(session, row["uri"], mint)
        if info is None:
            store.pump_verdict(mint, row["name"], row["symbol"], "meta_fail", 0, now)
            continue
        desc = _clean_description(info["desc"])
        website = row["website"] or info["website"]
        twitter = row["twitter"] or info["twitter"]
        telegram = info["telegram"]
        pts, ok, reasons = score_token(desc, website, twitter, telegram,
                                       cfg.pump_min_score)
        stats["reviewed"] += 1
        if not ok:
            store.pump_verdict(mint, row["name"], row["symbol"],
                               f"no:{reasons[0][:40]}", pts, now)
            continue
        # ---------------- kandydat: ALERT ----------------
        store.pump_verdict(mint, row["name"], row["symbol"], "alerted", pts, now)
        stats["candidates"] += 1
        created = row["created_ms"] // 1000 if row["created_ms"] > 10**11 else row["created_ms"]
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(created)) if created else "?"
        sym = (row["symbol"] or "?").upper()
        lines = [
            f"Wynik fundamentów: {pts} pkt (opis ✅, "
            + ", ".join(p for p, v in (("strona", website), ("twitter", twitter),
                                       ("telegram", telegram)) if v) + ")",
            f"Opis: {desc[:400]}",
        ]
        if website:
            lines.append(f"Strona: {website}")
        if twitter:
            lines.append(f"Twitter: {twitter}")
        if telegram:
            lines.append(f"Telegram: {telegram}")
        lines.append(f"Utworzony: {when} | MC: ${row['mcap_usd']:,.0f} | "
                     f"Dyplomował: {'TAK' if row['complete'] else 'nie'}")
        emit_fn(
            store, sink, cfg,
            dedupe_key=f"pump:{mint}",
            source="pump", etype="pump_promising", priority="high",
            title=f"🔬 Pump.fun: {row['name']} ({sym}) — sygnały fundamentów",
            detail="\n".join(lines),
            url=f"https://pump.fun/coin/{mint}",
            counter=counter,
        )

    if any(stats.values()):
        sink.info(f"Pump.fun: nowych {stats['fresh']}, w kolejce {stats['queued']}, "
                  f"oceniono {stats['reviewed']}, kandydatów {stats['candidates']}"
                  + (f", baza {stats['baseline']}" if stats["baseline"] else ""))
    return stats
