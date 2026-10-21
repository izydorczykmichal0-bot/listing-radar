"""Logika aplikacji: cykle odpytywania, skrzyżowanie źródeł, pętla watch."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import coingecko as cg
from . import safetrade as st
from .alerts import Sink, _fmt_ts
from .config import Config
from .enrich import enrich
from .httpc import make_session
from .pump import cycle_pump
from .report import maybe_send_daily_report, send_report
from .store import Store

log = logging.getLogger("radar")
WARSAW = ZoneInfo("Europe/Warsaw")


def _warsaw_date(ts: int | float | None) -> str:
    if not ts:
        return "?"
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(WARSAW).strftime(
        "%Y-%m-%d %H:%M:%S")


def _parse_iso(value) -> int | None:
    """'2026-10-18T16:53:31Z' -> epoch sek. (albo None)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(dt.timestamp())
    except ValueError:
        return None


def _cg_listed_ts(row) -> int | None:
    """Kiedy coin trafił na CoinGecko (activated_at > first_seen; 0 = nieznane)."""
    if row["activated_at"]:
        return int(row["activated_at"])
    fs = row["first_seen"] or 0
    return fs if fs > 0 else None


def _st_created_ts(row) -> int | None:
    """Kiedy rynek powstał na SafeTrade (created_at > first_seen; 0 = nieznane)."""
    ts = _parse_iso(row["created_at"])
    if ts:
        return ts
    fs = row["first_seen"] or 0
    return fs if fs > 0 else None


def _is_fresh(ts: int | None, now: int, window_days: int) -> bool:
    """Czy zdarzenie listingu mieści się w oknie 'równoczesności'."""
    return ts is not None and (now - ts) <= window_days * 86400


# ----------------------------------------------------------------- emit
def emit(store: Store, sink: Sink, cfg: Config, *, dedupe_key: str, source: str,
         etype: str, priority: str, title: str, detail: str = "",
         url: str | None = None, counter: dict | None = None) -> bool:
    now = int(time.time())
    if counter is not None and counter.get("n", 0) >= cfg.max_events_per_cycle:
        counter["suppressed"] = counter.get("suppressed", 0) + 1
        return False
    inserted = store.add_event(dedupe_key, source, etype, priority, title,
                               detail, url, now, cfg.telegram.enabled)
    if inserted and counter is not None:
        counter["n"] = counter.get("n", 0) + 1
        sink.show({"ts": now, "priority": priority, "title": title,
                   "detail": detail, "url": url})
    return inserted


# ----------------------------------------------------------------- SafeTrade
def cycle_safetrade(cfg: Config, store: Store, sink: Sink, session,
                    counter: dict) -> bool:
    markets, status, source = st.fetch_markets(session)
    if markets is None:
        sink.info(f"⚠ SafeTrade: nie udało się pobrać rynków (status {status}) — pomijam cykl")
        return False

    now = int(time.time())
    baseline = store.meta_get("st_baseline_done") != "1"
    prev_source = store.meta_get("st_baseline_source")

    # Zmiana źródła (np. powrót z zapasowego safe.trade po odblokowaniu
    # safetrade.com) nie może generować fali fałszywych „nowych" rynków —
    # wtedy cicho odświeżamy bazę odniesienia.
    if baseline is False and prev_source and prev_source != source:
        sink.info(f"ℹ SafeTrade: źródło zmienione {prev_source} → {source}, "
                  "ponawiam bazę odniesienia")
        baseline = True
    elif baseline is False and not prev_source:
        store.meta_set("st_baseline_source", source)  # starsze bazy bez źródła

    for mid, m in sorted(markets.items()):
        row = store.get_st_market(mid)
        if row is None:
            store.insert_st_market(m, now, baseline=baseline)
            if baseline:
                continue

            base = (m.get("base_unit") or "").upper()
            quote = (m.get("quote_unit") or "").upper()
            state = m.get("state", "")
            created = m.get("created_at", "?")
            st_ts = _parse_iso(created) or now

            # skrzyżowanie: coin równocześnie nowy na CoinGecko i na SafeTrade?
            cands = store.find_cg_by_symbol(m.get("base_unit") or "")
            cg_note = ""
            info_block = enrich(session, cfg, cands[0]["id"]) if cands else ""
            if cands:
                c0 = cands[0]
                cg_note = f"\nCoinGecko: {c0['name']} ({c0['id']}) — {cg.coin_url(c0['id'])}"
                fresh = [c for c in cands
                         if _is_fresh(_cg_listed_ts(c), now, cfg.cross_window_days)]
                if fresh and _is_fresh(st_ts, now, cfg.cross_window_days):
                    c = fresh[0]
                    emit(store, sink, cfg,
                         dedupe_key=f"cross:{c['id']}:{mid}",
                         source="cross", etype="cross_hit", priority="high",
                         title=f"🎯 CROSS: {c['name']} ({(c['symbol'] or '').upper()}) "
                               f"— równocześnie na CoinGecko i SafeTrade!",
                         detail=f"SafeTrade dodaje rynek {base}/{quote} "
                                f"(stan: {state}, utworzono: {created}), a ten coin "
                                f"trafił na CoinGecko "
                                f"{_warsaw_date(_cg_listed_ts(c))}." + info_block,
                         url=cg.coin_url(c["id"]), counter=counter)

            if state == "enabled":
                title = f"🚀 SafeTrade: LISTING — {base}/{quote} notowania wystartowały"
                priority = "high"
            else:
                title = (f"📥 SafeTrade: PRE-LISTING — dodano rynek {base}/{quote} "
                         f"(notowania jeszcze nie ruszyły)")
                priority = "normal"
            detail = (f"Stan: {state}\nUtworzono rynku: {created}"
                      + cg_note + info_block)
            emit(store, sink, cfg,
                 dedupe_key=f"st_new:{mid}",
                 source="safetrade",
                 etype="st_new_market_live" if state == "enabled" else "st_new_market_pre",
                 priority=priority, title=title, detail=detail,
                 url=st.trade_url(base, quote), counter=counter)

        elif row["state"] != m.get("state"):
            old_state, new_state = row["state"], m.get("state", "")
            store.update_st_market(mid, new_state, now)
            if baseline:
                continue
            base = (m.get("base_unit") or "").upper()
            quote = (m.get("quote_unit") or "").upper()
            went_live = old_state != "enabled" and new_state == "enabled"
            cands2 = store.find_cg_by_symbol(m.get("base_unit") or "")
            info2 = enrich(session, cfg, cands2[0]["id"]) if cands2 else ""
            emit(store, sink, cfg,
                 dedupe_key=f"st_state:{mid}:{new_state}",
                 source="safetrade", etype="st_state_change",
                 priority="high" if went_live else "normal",
                 title=(f"⚡ SafeTrade: {base}/{quote} — notowania OTWARTE!"
                        if went_live else
                        f"SafeTrade: {base}/{quote} zmiana stanu "
                        f"{old_state} → {new_state}"),
                 detail=f"Rynek utworzony: {m.get('created_at', '?')}" + info2,
                 url=st.trade_url(base, quote), counter=counter)
        else:
            store.update_st_market(mid, m.get("state", ""), now)

    if baseline:
        store.meta_set("st_baseline_done", "1")
        store.meta_set("st_baseline_source", source)
        enabled = sum(1 for m in markets.values() if m.get("state") == "enabled")
        sink.info(f"📌 SafeTrade: baza odniesienia zapisana — {len(markets)} rynków "
                  f"({enabled} aktywnych, źródło: {source}). "
                  "Kolejne zmiany będą raportowane.")
    return True


# ----------------------------------------------------------------- CoinGecko
def _handle_new_cg_coin(cfg, store, sink, session, coin, source, now, counter):
    coin_id = coin.get("id") or ""
    sym = coin.get("symbol") or ""
    name = coin.get("name") or coin_id
    activated = coin.get("activated_at")
    activated_s = _warsaw_date(activated) if activated else _warsaw_date(now)

    # wzbogacenie alertu: kategoria (L1/memecoin/...) + opis zastosowań
    info_block = enrich(session, cfg, coin_id)

    # skrzyżowanie: czy ten symbol jest już/notowany na SafeTrade?
    st_rows = store.find_st_by_base(sym)
    st_note, st_url, priority = "", None, "normal"
    if st_rows:
        r0 = st_rows[0]
        st_url = st.trade_url(r0["base"], r0["quote"])
        states = ", ".join(f"{r['base'].upper()}/{r['quote'].upper()} ({r['state']})"
                           for r in st_rows[:4])
        st_note = f"\nSafeTrade: {states}"
        priority = "high"
        fresh_st = [r for r in st_rows if now - (r["first_seen"] or 0)
                    <= cfg.cross_window_days * 86400]
        if fresh_st:
            r = fresh_st[0]
            emit(store, sink, cfg,
                 dedupe_key=f"cross:{coin_id}:{r['id']}",
                 source="cross", etype="cross_hit", priority="high",
                 title=f"🎯 CROSS: {name} ({sym.upper()}) — jednocześnie na "
                       f"CoinGecko i SafeTrade!",
                 detail=(f"CoinGecko dodał coin {coin_id} ({activated_s}), "
                         f"a rynek {r['base'].upper()}/{r['quote'].upper()} na "
                         f"SafeTrade powstał {r['created_at'] or '?'} "
                         f"(stan: {r['state']}).") + info_block,
                 url=cg.coin_url(coin_id), counter=counter)

    emit(store, sink, cfg,
         dedupe_key=f"cg_new:{coin_id}",
         source="coingecko", etype="cg_new_coin", priority=priority,
         title=f"🆕 CoinGecko: nowy coin {name} ({sym.upper()})",
         detail=f"Dodany: {activated_s}\nId: {coin_id}" + st_note + info_block,
         url=cg.coin_url(coin_id), counter=counter)


def cycle_coingecko(cfg: Config, store: Store, sink: Sink, session,
                    counter: dict) -> bool:
    now = int(time.time())
    ok = True

    # --- 1. nowe monety (pro / free) ---
    baseline_key = "cg_baseline_done"
    baseline_done = store.meta_get(baseline_key) == "1"
    baseline_src = store.meta_get("cg_baseline_source")

    items, source, status = None, cfg.cg_mode, 0
    if cfg.coingecko_api_key:
        pro_items, status = cg.fetch_new_coins_pro(session, cfg.coingecko_api_key)
        if pro_items is not None:
            items, source = pro_items, "pro"
        else:
            sink.info(f"ℹ CoinGecko: /coins/list/new niedostępny dla tego klucza "
                      f"(status {status}; klucz planu Demo?) — użyję różnicowania "
                      "pełnej listy")

    if items is None:
        free_items, status = cg.fetch_coins_list(session, cfg.coingecko_api_key)
        if free_items is None:
            sink.info(f"⚠ CoinGecko: nie udało się pobrać listy monet (status {status})")
            ok = False
            items = None
        else:
            items, source = free_items, "free"

    if items is not None:
        # Zmiana trybu wymaga ponownego zbudowania bazy (inaczej zalanie alertów).
        if baseline_done and baseline_src and baseline_src != source:
            sink.info(f"ℹ CoinGecko: tryb zmieniony {baseline_src} → {source}, "
                      "ponawiam bazę odniesienia")
            baseline_done = False
            store.meta_set(baseline_key, "0")

        new_coins = []
        for c in items:
            if not isinstance(c, dict) or not c.get("id"):
                continue
            if store.insert_cg_coin(c, source, now, baseline=not baseline_done):
                if baseline_done:
                    new_coins.append(c)
        if not baseline_done:
            store.meta_set(baseline_key, "1")
            store.meta_set("cg_baseline_source", source)
            sink.info(f"📌 CoinGecko: baza odniesienia zapisana — {len(items)} monet "
                      f"(tryb: {source}). Kolejne listowania będą raportowane.")
        else:
            for c in new_coins:
                _handle_new_cg_coin(cfg, store, sink, session, c, source, now, counter)

    # --- 2. pary SafeTrade śledzone przez CoinGecko (sygnał skrzyżowania) ---
    if cfg.fetch_cg_exchange_tickers:
        pairs_baseline = store.meta_get("cg_pairs_baseline") == "1"
        tickers, status = cg.fetch_exchange_tickers(
            session, cfg.cg_exchange_id, cfg.coingecko_api_key)
        if tickers is None:
            sink.info(f"⚠ CoinGecko/tickers({cfg.cg_exchange_id}): "
                      f"brak danych (status {status}) — pomijam w tym cyklu")
            ok = False
        else:
            new_pairs = []
            for t in tickers:
                base = (t.get("base") or "").upper()
                target = (t.get("target") or "").upper()
                coin_id = t.get("coin_id") or ""
                if not base or not coin_id:
                    continue
                pair_id = f"{coin_id}:{base}:{target}"
                try:
                    vol = float(t.get("volume") or 0)
                except (TypeError, ValueError):
                    vol = None
                if store.insert_cg_pair(pair_id, coin_id, base, target, vol, now):
                    if pairs_baseline:
                        new_pairs.append((coin_id, base, target, vol, t))
            if not pairs_baseline:
                store.meta_set("cg_pairs_baseline", "1")
                sink.info(f"📌 CoinGecko: baza par SafeTrade zapisana — "
                          f"{len(tickers)} par (giełda: {cfg.cg_exchange_id}).")
            else:
                for coin_id, base, target, vol, t in new_pairs:
                    coin_name = t.get("coin_id")
                    known = store.find_cg_by_symbol(base)
                    nm = known[0]["name"] if known else coin_id
                    st_rows = store.find_st_by_base(base)
                    st_note = ""
                    if st_rows:
                        r0 = st_rows[0]
                        st_note = (f"\nSafeTrade: {r0['base'].upper()}/{r0['quote'].upper()} "
                                   f"(stan: {r0['state']}, od {r0['created_at'] or '?'})")
                    info2 = enrich(session, cfg, coin_id)
                    emit(store, sink, cfg,
                         dedupe_key=f"cg_pair:{pair_id}",
                         source="cross", etype="cg_safe_pair", priority="high",
                         title=f"🔗 CoinGecko → SafeTrade: {nm} ({base}/{target}) "
                               f"notowany na SafeTrade",
                         detail=(f"CoinGecko śledzi tę parę giełdy "
                                 f"{cfg.cg_exchange_id}.\nCoin: {coin_id}"
                                 + (f"\n24h wolumen: {vol:,.0f}" if vol else "")
                                 + st_note + info2),
                         url=cg.coin_url(coin_id), counter=counter)
    return ok


# ----------------------------------------------------------------- flush TG
def flush_telegram(cfg: Config, store: Store, sink: Sink) -> None:
    if not cfg.telegram.enabled:
        return
    pending = store.pending_events()
    sent = 0
    for ev in pending:
        html_text = sink.event_to_html(dict(ev))
        if sink.send_telegram(html_text):
            store.mark_alerted(ev["id"], 1)
            sent += 1
        else:
            log.warning("Nie wysłano alertu id=%s (ponowię)", ev["id"])
    if sent:
        sink.info(f"📤 Telegram: wysłano {sent} alert(ów)")


# ----------------------------------------------------------------- pętle
def run_cycle(cfg: Config, store: Store, sink: Sink, session) -> None:
    counter = {"n": 0, "suppressed": 0}
    maybe_send_daily_report(cfg, store, sink)
    cycle_safetrade(cfg, store, sink, session, counter)
    cycle_coingecko(cfg, store, sink, session, counter)
    if cfg.pump_enabled:
        try:
            cycle_pump(cfg, store, sink, session, counter, emit)
        except Exception:  # noqa: BLE001 — pump.fun to dodatek, nie trzon
            log.exception("Pump.fun: nieoczekiwany błąd (radar działa dalej)")
            sink.info("⚠ Pump.fun: błąd modułu — pomijam cykl pump.fun")
    flush_telegram(cfg, store, sink)
    if counter.get("suppressed"):
        sink.info(f"ℹ W tym cyklu stłumiono {counter['suppressed']} zdarzeń "
                  f"(limit {cfg.max_events_per_cycle}/cykl)")
    store.conn.commit()


def cmd_check(cfg: Config) -> None:
    store = Store(cfg.db_path)
    sink = Sink(cfg)
    session = make_session()
    try:
        sink.info(f"▶ Pojedyncze sprawdzenie (źródła: CoinGecko[{cfg.cg_mode}], "
                  f"SafeTrade{', Pump.fun' if cfg.pump_enabled else ''}; "
                  f"TG: {'TAK' if cfg.telegram.enabled else 'nie'})")
        run_cycle(cfg, store, sink, session)
        c = store.counts()
        sink.info(f"✔ Koniec. Monety CG: {c['cg_coins']}, rynki ST: {c['st_markets']}, "
                  f"pary CG×ST: {c['cg_safe_pairs']}, zdarzenia: {c['events']}")
    finally:
        store.close()


def cmd_watch(cfg: Config) -> None:
    store = Store(cfg.db_path)
    sink = Sink(cfg)
    session = make_session()
    next_st = 0.0
    next_cg = 0.0
    next_pump = 0.0
    fails = {"st": 0, "cg": 0}
    sink.info(f"▶ Listing Radar startuje — CG[{cfg.cg_mode}] co {cfg.interval_coingecko}s, "
              f"SafeTrade co {cfg.interval_safetrade}s, TG: "
              f"{'TAK' if cfg.telegram.enabled else 'NIE (tylko log)'}; "
              f"DB={cfg.db_path}"
              + (f"; raport dzienny {cfg.daily_report_hour:02d}:"
                 f"{cfg.daily_report_minute:02d} (Warszawa)"
                 if cfg.daily_report_enabled else "; raport dzienny WYŁ"))
    try:
        while True:
            maybe_send_daily_report(cfg, store, sink)

            now = time.time()
            if now >= next_st:
                counter = {"n": 0, "suppressed": 0}
                try:
                    ok = cycle_safetrade(cfg, store, sink, session, counter)
                except Exception as e:  # noqa: BLE001 — pętla ma przeżyć wszystko
                    log.exception("SafeTrade: nieoczekiwany błąd")
                    sink.info(f"⚠ SafeTrade: błąd {e}")
                    ok = False
                fails["st"] = 0 if ok else fails["st"] + 1
                backoff = min(cfg.interval_safetrade * (2 ** min(fails["st"], 4)),
                              cfg.interval_safetrade * 10)
                next_st = time.time() + max(cfg.interval_safetrade, backoff)
                flush_telegram(cfg, store, sink)

            now = time.time()
            if now >= next_cg:
                counter = {"n": 0, "suppressed": 0}
                try:
                    ok = cycle_coingecko(cfg, store, sink, session, counter)
                except Exception as e:  # noqa: BLE001
                    log.exception("CoinGecko: nieoczekiwany błąd")
                    sink.info(f"⚠ CoinGecko: błąd {e}")
                    ok = False
                fails["cg"] = 0 if ok else fails["cg"] + 1
                backoff = min(cfg.interval_coingecko * (2 ** min(fails["cg"], 4)),
                              cfg.interval_coingecko * 10)
                next_cg = time.time() + max(cfg.interval_coingecko, backoff)
                if counter.get("suppressed"):
                    sink.info(f"ℹ Stłumiono {counter['suppressed']} zdarzeń "
                              f"(limit {cfg.max_events_per_cycle}/cykl)")
                flush_telegram(cfg, store, sink)

            now = time.time()
            if now >= next_pump and cfg.pump_enabled:
                counter = {"n": 0, "suppressed": 0}
                try:
                    cycle_pump(cfg, store, sink, session, counter, emit)
                except Exception as e:  # noqa: BLE001
                    log.exception("Pump.fun: nieoczekiwany błąd")
                    sink.info(f"⚠ Pump.fun: błąd {e}")
                next_pump = time.time() + cfg.interval_coingecko
                flush_telegram(cfg, store, sink)

            time.sleep(min(5.0, max(0.5, min(next_st, next_cg, next_pump)
                                    - time.time())))
    except KeyboardInterrupt:
        sink.info("■ Listing Radar zatrzymany przez użytkownika.")
    finally:
        store.close()


def cmd_export_state(cfg: Config, path: str = "state.json") -> None:
    """Zapis stanu do JSON — dla wariantu GitHub Actions."""
    store = Store(cfg.db_path)
    try:
        n = store.export_state(path)
        print(f"✓ Stan zapisany do {path} "
              f"(monety: {store.counts()['cg_coins']}, rynki: {store.counts()['st_markets']}, "
              f"zdarzenia: {n})")
    finally:
        store.close()


def cmd_import_state(cfg: Config, path: str = "state.json") -> None:
    """Wczytanie stanu z JSON — pierwszy krok po checkoutcie na runnerze."""
    store = Store(cfg.db_path)
    try:
        if store.import_state(path):
            c = store.counts()
            print(f"✓ Stan wczytany z {path}: monety {c['cg_coins']}, "
                  f"rynki {c['st_markets']}, pary {c['cg_safe_pairs']}, "
                  f"zdarzenia {c['events']}")
        else:
            print(f"ℹ Brak {path} — to pierwszy przebieg, zapiszę bazę odniesienia")
    finally:
        store.close()


def cmd_report(cfg: Config, hours: int = 24) -> None:
    """Ręczny raport: python3 -m radar report [--hours N]."""
    store = Store(cfg.db_path)
    sink = Sink(cfg)
    try:
        sink.info(f"▶ Raport za ostatnie {hours}h "
                  f"(TG: {'TAK' if cfg.telegram.enabled else 'nie'})")
        ok = send_report(cfg, store, sink, hours=hours, mark=True)
        if not ok:
            raise SystemExit(1)
    finally:
        store.close()


def cmd_status(cfg: Config) -> None:
    store = Store(cfg.db_path)
    try:
        c = store.counts()
        print("=== Listing Radar — status ===")
        print(f"DB: {cfg.db_path}")
        print(f"CoinGecko: tryb {cfg.cg_mode}, monet w bazie: {c['cg_coins']}")
        print(f"SafeTrade: rynków {c['st_markets']} (aktywne: {c['st_enabled']})")
        print(f"Pary CG×SafeTrade: {c['cg_safe_pairs']}")
        print(f"Zdarzenia: {c['events']} (w tym wysokie: {c['events_high']})")
        print(f"Telegram: {'skonfigurowany' if cfg.telegram.enabled else 'NIE skonfigurowany'}")
        rows = store.recent_events(10)
        if rows:
            print("\n--- Ostatnie 10 zdarzeń ---")
            for r in rows:
                print(f"{_fmt_ts(r['ts'])} [{r['priority']}] {r['title']}")
        else:
            print("\nBrak zdarzeń — uruchom 'check' najpierw, żeby zbudować bazę.")
    finally:
        store.close()
