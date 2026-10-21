"""Wczesne wykrywanie tokenów z sygnałami fundamentów na pump.fun — Antyscam v2.

pump.fun to z definicji fabryka memecoinów (~40 tys. tokenów dziennie;
wg Solidus Labs ~98,6% wykazuje cechy pump-and-dump). Ten moduł nie obiecuje
„pewnych diamondów" — robi mierzalny, agresywny filtr sygnałów:

ETAP 1 (ingest, bez zapytań sieciowych) — TWARDE ODRZUCENIA:
  K0  leksykon memów po nazwie/symbolu (pepe, doge, moon, wif...)
  K1  podszycie pod markę — nazwa/symbol lub host strony zawiera znaną
      markę/polityka (google, binance, tesla, trump...) → impersonacja
  K2  skrócone/przekierowujące linki (bit.ly, google.com/url, t.co...)
  K3  kopia kopii — ten sam metadata_uri u ≥2 mintów (fabryka spamu)
  K4  dev-fabryka — ten sam twórca ≥3 tokenów w oknie 3h
  (oraz nsfw / is_banned)

ETAP 2 (przegląd, metadane + sieć):
  K5  strona WYMAGANA i ŻYWA (HTTP 200-399, tytuł, realna treść)
  Twitter liczy się tylko jako czysty profil (x.com/nazwa), nie /status/
  Wiek domeny z RDAP: rejestracja ≥90 dni = +2 pkt
  Próg MC: token czeka w kolejce, aż market cap ≥ `pump.min_mcap`
      (domyślnie5000 USD; przegląd co ~10 min, MC czytane z listy = darmowe)
  Scoring: słowa użytkowe 0-4 + strona 2 + twitter 2 + telegram 1
           + długi opis 1 + stara domena 2 = MAX 12 pkt
  Próg alertu: `pump.min_score` (domyślnie 6).

Każdy token oceniany jest DOKŁADNIE RAZ (pump_seen/pump_queue; stare oceny
przycinane po 3h). Błędy modułu łapane wyżej (run_cycle/watch) — nigdy nie
blokują CoinGecko/SafeTrade. Moduł odłączalny: `pump.enabled=false`.
To filtr sygnałów informacyjnych, NIE doradztwo inwestycyjne.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

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

# K1: znane marki/politycy — impersonacja w nazwie lub hostcie strony
_BRAND_RE = re.compile(
    r"\b(" + "|".join((
        "google", "openai", "chatgpt", "binance", "coinbase", "kraken",
        "metamask", "bitcoin", "ethereum", "tesla", "spacex", "amazon",
        "apple", "microsoft", "netflix", "nvidia", "paypal",
        "elon", "musk", "trump", "biden", "obama", "putin", "zelensky",
        "kanye", "zuckerberg", "bezos", "gates", "facebook", "instagram",
        "whatsapp", "tiktok", "youtube", "starlink", "visa", "mastercard",
        "ripple", "litecoin", "cardano", "polkadot", "avalanche", "tether",
        "binance", "opensea", "uniswap", "pancakeswap",
    )) + r")\b", re.IGNORECASE)

# K2: skrótniki i przekierowania
_SHORTENERS = (
    "bit.ly/", "tinyurl", "cutt.ly", "t.co/", "rb.gy", "is.gd", "v.gd",
    "goo.gl", "ow.ly", "bl.ink", "lnkd.in", "rebrand.ly", "google.com/url",
    "/redirect?", "shorturl", "u.to", "soo.gd", "clk.sh",
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
_RETENTION = 3 * 3600     # oceny sprzed 3h nie wracają na listę najnowszych
_CREATOR_WINDOW = 3 * 3600    # okno wykrywania dev-fabryki (K4)
_CREATOR_MAX = 3               # ≥ tokenów tego samego twórcy = fabryka
_AGE_BONUS_DAYS = 90           # domena ≥90 dni = +2 pkt
_RECHECK = 600                 # wstrzymany kandydat: ponowny przegląd MC co ~10 min


def lex_bad(name: str, symbol: str) -> bool:
    """K0: True = nazwa/symbol wygląda na memecoina."""
    low = f"{name or ''} {symbol or ''}".lower()
    return any(p in low for p in _LEX)


def brand_bad(name: str, symbol: str, website: str) -> bool:
    """K1: podszycie pod znaną markę — w nazwie/symbolu albo w hostcie."""
    if _BRAND_RE.search(f"{name or ''} {symbol or ''}"):
        return True
    try:
        host = urlparse(website or "").netloc.lower()
    except ValueError:
        return False
    return bool(host) and bool(_BRAND_RE.search(host))


def shortener_bad(website: str) -> bool:
    """K2: skrócone/przekierowujące URL-e."""
    w = (website or "").lower()
    return any(s in w for s in _SHORTENERS)


def twitter_profile_ok(url: str) -> bool:
    """Twitter liczy się tylko jako czysty profil (nie wpis /status/)."""
    w = (url or "").lower()
    if "x.com/" not in w and "twitter.com/" not in w:
        return False
    return "/status/" not in w and "/i/" not in w and "?" not in w


def _sig(uri: str, mint: str) -> str:
    """K3: sygnatura kopii — hash metadata_uri (albo mint, gdy brak URI)."""
    return hashlib.sha1((uri or mint or "?").strip().lower()
                        .encode("utf-8")).hexdigest()


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


def check_website(session, url: str) -> tuple[bool, str]:
    """K5: strona żywa? (HTTP 200-399 + tytuł + realna treść)."""
    if not url or not url.startswith(("http://", "https://")):
        return False, "brak http(s)"
    try:
        r = session.get(url, timeout=8, allow_redirects=True)
    except Exception:  # noqa: BLE001 — sieć potrafi wszystko
        return False, "brak odpowiedzi"
    if not (200 <= r.status_code < 400):
        return False, f"HTTP {r.status_code}"
    try:
        html = r.text or ""
    except Exception:  # noqa: BLE001
        return False, "brak treści"
    if len(html) < 800:
        return False, "strona pusta"
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = (m.group(1).strip() if m else "")
    if len(title) < 3:
        return False, "brak tytułu"
    return True, "ok"


def domain_age_days(session, url: str) -> int | None:
    """Wiek domeny z RDAP (darmowe, bez klucza). None = brak danych."""
    try:
        host = urlparse(url or "").netloc.lower()
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    host = host.split(":", 1)[0]
    if "." not in host:
        return None
    data, _status = get_json(session, f"https://rdap.org/domain/{host}",
                             timeout=8, max_retries=1, label="pump/rdap")
    if not isinstance(data, dict):
        return None
    for ev in data.get("events") or []:
        if isinstance(ev, dict) and ev.get("eventAction") == "registration":
            try:
                dt = datetime.fromisoformat(
                    str(ev.get("eventDate")).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                days = (datetime.now(timezone.utc) - dt).days
                return days if days >= 0 else None
            except ValueError:
                return None
    return None


def score_token(desc: str, website: str, twitter: str, telegram: str,
                min_score: int, *, domain_days: int | None = None,
                ) -> tuple[int, bool, list[str]]:
    """Scoring fundamentów v2. Zwraca (punkty, alert?, powody odrzucenia).

    Max = 12: użytkowość 0-4 + strona 2 + twitter 2 + telegram 1
              + długi opis 1 + domena ≥90 dni 2.
    """
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
    if not website:
        reasons.append("brak jakichkolwiek linków")
    if reasons:
        return 0, False, reasons
    pts = min(util, 4)
    pts += 2 if website else 0
    pts += 2 if twitter else 0            # wywołujący kasuje nie-profilowe URL
    pts += 1 if telegram else 0
    pts += 1 if len(desc) >= 200 else 0
    if domain_days is not None and domain_days >= _AGE_BONUS_DAYS:
        pts += 2
    if pts < min_score:
        return pts, False, [f"za niski wynik ({pts}<{min_score})"]
    return pts, True, []


def cycle_pump(cfg: Config, store, sink, session, counter: dict, emit_fn) -> dict:
    """Jeden cykl: nowe monety → antyscam etap1 → metadane → K5/scoring → 🔬."""
    stats = {"fresh": 0, "queued": 0, "reviewed": 0, "candidates": 0,
             "baseline": 0, "held": 0}
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
            [{"mint": x["mint"], "name": x.get("name"),
              "symbol": x.get("symbol"), "creator": x.get("creator"),
              "sig": _sig(str(x.get("metadata_uri") or ""), x["mint"])}
             for x in items], now, "baseline")
        stats["baseline"] = len(items)
        sink.info(f"📌 Pump.fun: baza odniesienia — {len(items)} ostatnich "
                  f"tokenów pominiętych; kolejne cykle = tylko nowe.")
        return stats

    # świeże MC z bieżącej listy (darmowe — dane i tak mamy z ingestu)
    mc_cache = {str(x.get("mint")): float(x.get("market_cap_usd") or 0.0)
                for x in items}

    # ---------------- etap1: tanie zabójcy (bez zapytań) ----------------
    now = int(time.time())
    for x in items:
        mint = x["mint"]
        if store.pump_known(mint):
            continue
        stats["fresh"] += 1
        name = str(x.get("name") or "")
        symbol = str(x.get("symbol") or "")
        website = str(x.get("website") or "")
        uri = str(x.get("metadata_uri") or "")
        creator = str(x.get("creator") or "")
        sig = _sig(uri, mint)

        if x.get("nsfw") or x.get("is_banned"):
            store.pump_verdict(mint, name, symbol, "nsfw", 0, now,
                               creator=creator, sig=sig)
            continue
        if lex_bad(name, symbol):
            store.pump_verdict(mint, name, symbol, "lex", 0, now,
                               creator=creator, sig=sig)
            continue
        if brand_bad(name, symbol, website):
            store.pump_verdict(mint, name, symbol, "brand", 0, now,
                               creator=creator, sig=sig)
            continue
        if shortener_bad(website):
            store.pump_verdict(mint, name, symbol, "short", 0, now,
                               creator=creator, sig=sig)
            continue
        if store.pump_sig_exists(sig):
            store.pump_verdict(mint, name, symbol, "dup", 0, now,
                               creator=creator, sig=sig)
            continue
        if creator and store.pump_creator_count(
                creator, now - _CREATOR_WINDOW) >= _CREATOR_MAX:
            store.pump_verdict(mint, name, symbol, "factory", 0, now,
                               creator=creator, sig=sig)
            continue
        store.pump_queue_add({
            "mint": mint, "name": name, "symbol": symbol, "uri": uri,
            "website": website, "twitter": str(x.get("twitter") or ""),
            "created_ms": int(x.get("created_timestamp") or 0),
            "mcap_usd": float(x.get("market_cap_usd") or 0.0),
            "complete": 1 if x.get("complete") else 0,
            "creator": creator, "sig": sig,
        })
        stats["queued"] += 1

    # ---------------- etap2: próg MC → metadane → K5 → scoring ----------------
    for row in store.pump_queue_pop(now - _RECHECK, cfg.pump_meta_per_cycle):
        mint = row["mint"]

        # próg minimalnego market cap (przegląd co ~10 min, MC z listy = darmowe)
        mc_val = float(row["mcap_usd"] or 0.0)
        if cfg.pump_min_mcap > 0:
            fresh = mc_cache.get(mint)
            if fresh is not None and fresh > 0:
                mc_val = fresh
            if mc_val < cfg.pump_min_mcap:
                store.pump_touch(mint, now, mc_val)   # zostań w kolejce, wróć
                stats["held"] += 1
                continue

        info = fetch_token_info(session, row["uri"], mint)
        if info is None:
            store.pump_verdict(mint, row["name"], row["symbol"], "meta_fail",
                               0, now, creator=row["creator"], sig=row["sig"])
            continue
        desc = _clean_description(info["desc"])
        website = row["website"] or info["website"]
        twitter = row["twitter"] or info["twitter"]
        telegram = info["telegram"]

        # K5: strona wymagana i żywa
        if not website:
            store.pump_verdict(mint, row["name"], row["symbol"],
                               "no:brak strony", 0, now,
                               creator=row["creator"], sig=row["sig"])
            continue
        alive, why = check_website(session, website)
        if not alive:
            store.pump_verdict(mint, row["name"], row["symbol"],
                               f"no:site {why}"[:60], 0, now,
                               creator=row["creator"], sig=row["sig"])
            continue

        t_ok = twitter_profile_ok(twitter)
        age = domain_age_days(session, website)
        pts, ok, reasons = score_token(
            desc, website, twitter if t_ok else "", telegram,
            cfg.pump_min_score, domain_days=age)
        stats["reviewed"] += 1
        if not ok:
            store.pump_verdict(mint, row["name"], row["symbol"],
                               f"no:{reasons[0][:40]}", pts, now,
                               creator=row["creator"], sig=row["sig"])
            continue

        # ---------------- kandydat: ALERT ----------------
        store.pump_verdict(mint, row["name"], row["symbol"], "alerted", pts, now,
                           creator=row["creator"], sig=row["sig"])
        stats["candidates"] += 1
        created = row["created_ms"] // 1000 if row["created_ms"] > 10**11 \
            else row["created_ms"]
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(created)) if created else "?"
        sym = (row["symbol"] or "?").upper()
        parts = ["opis ✅", "strona ✅"]
        if t_ok and twitter:
            parts.append("twitter ✅")
        if telegram:
            parts.append("telegram ✅")
        if age is not None and age >= _AGE_BONUS_DAYS:
            parts.append(f"domena ≥{_AGE_BONUS_DAYS} dni (+2)")
        lines = [
            f"Wynik fundamentów: {pts} pkt (" + ", ".join(parts) + ")",
            f"Opis: {desc[:400]}",
            f"Strona: {website}",
        ]
        if twitter:
            lines.append(f"Twitter: {twitter}")
        if telegram:
            lines.append(f"Telegram: {telegram}")
        lines.append(f"Utworzony: {when} | MC: ${mc_val:,.0f} | "
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
                  f"wstrzymanych (MC) {stats['held']}, "
                  f"oceniono {stats['reviewed']}, kandydatów {stats['candidates']}"
                  + (f", baza {stats['baseline']}" if stats["baseline"] else ""))
    return stats
