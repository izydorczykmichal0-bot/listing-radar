"""Test modułu pump.fun: leksykon, scoring fundamentów, cykl na żywo.

Uruchomienie:  python test_pump.py
"""
from __future__ import annotations

import os
import sys
import tempfile

from radar.config import load_config
from radar.httpc import make_session
from radar.pump import (brand_bad, cycle_pump, lex_bad, score_token,
                        shortener_bad, twitter_profile_ok)
from radar.store import Store

OK = True


def check(cond: bool, msg: str) -> None:
    global OK
    print(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        OK = False


def unit() -> None:
    print("== leksykon (etap1) ==")
    check(lex_bad("Pepe Cash", "PEPC"), "pepe → memecoin")
    check(lex_bad("DogWifHat2", "WIF2"), "wif/dog → memecoin")
    check(lex_bad("MOON SHOT", "MSHOT"), "moon → memecoin")
    check(not lex_bad("VortexDex", "VRTX"), "VortexDex → NIE jest memem")
    check(not lex_bad("Euler Protocol", "EULR"), "Euler Protocol → NIE jest memem")

    print("== scoring fundamentów (etap2) ==")
    good = ("A cross-chain protocol for payments and staking infrastructure, "
            "connecting DeFi lending markets with an open oracle network for "
            "developers building on-chain tools.")
    pts, ok, reasons = score_token(good, "https://x.io", "https://twitter.com/a",
                                   "", 5)
    check(ok and pts >= 5, f"opis użytkowy + strona + twitter → ALERT ({pts} pkt)")

    _, ok, reasons = score_token(
        "wen moon lfg degen ape coin, to the moon my chad friends!", "", "", "", 5)
    check(not ok, f"opis memowy → odrzucony ({reasons})")

    _, ok, reasons = score_token("", "https://site.io", "", "", 5)
    check(not ok and any("opis" in r for r in reasons),
          f"brak opisu → odrzucony ({reasons})")

    _, ok, reasons = score_token(good, "", "", "", 5)
    check(not ok and any("link" in r for r in reasons),
          f"brak linków → odrzucony ({reasons})")

    print("== antyscam v2 ==")
    check(brand_bad("Google Ai Studio", "GAI", "https://www.google.com/url?i=1"),
          "K1: Google w nazwie/hostcie → odrzucony")
    check(not brand_bad("VortexDex", "VRTX", "https://vortexdex.io"),
          "K1: VortexDex → nie jest impersonacją")
    check(shortener_bad("https://bit.ly/abc"), "K2: bit.ly → odrzucony")
    check(not shortener_bad("https://computefund.site"),
          "K2: zwykła domena → przechodzi")
    check(twitter_profile_ok("https://x.com/vortexdex"), "czysty profil x.com ✅")
    check(not twitter_profile_ok("https://x.com/a/status/123456"),
          "wpis /status/ → nie punktuje")
    pts_a, ok_a, _ = score_token(good, "https://site.io", "", "", 7)
    pts_b, ok_b, _ = score_token(good, "https://site.io", "", "", 7,
                                 domain_days=200)
    check(ok_b and pts_b == pts_a + 2,
          f"wiek domeny ≥90 dni = +2 pkt ({pts_a} → {pts_b})")
    check(not ok_a and pts_a < 7,
          f"bez bonusu poniżej progu ({pts_a} pkt) → cisza")


def e2e_alert() -> None:
    """Pełna ścieżka: kolejka → (podmienione) metadane → scoring → PRAWDZIWY
    emit → zdarzenie w DB → dedupe przy ponownym wystąpieniu."""
    print("== e2e: alert + dedupe (metadane podmienione testowo) ==")
    import radar.pump as P

    cfg = load_config()
    cfg.pump_enabled = True
    cfg.db_path = os.path.join(tempfile.mkdtemp(prefix="pumpa_"), "t.db")
    store = Store(cfg.db_path)
    sink = _SilentSink()
    session = make_session()

    # zamiast sieci: stała lista (baza) + stałe metadane (przechodzą filtr)
    orig_fetch, orig_info = P.fetch_new_coins, P.fetch_token_info
    orig_check, orig_age = P.check_website, P.domain_age_days
    P.fetch_new_coins = lambda s, **k: [{"mint": "base1", "name": "Baza",
                                          "symbol": "BZ"}]
    P.fetch_token_info = lambda s, uri, mint: {
        "desc": ("A decentralized protocol for cross-chain payments and "
                 "staking infrastructure with an open oracle network for "
                 "developers."),
        "website": "https://example.io", "twitter": "", "telegram": "",
    }
    P.check_website = lambda s, u: (True, "ok")     # K5: strona żywa
    P.domain_age_days = lambda s, u: 200            # domena stara → +2

    from radar.app import emit as real_emit

    c = {"n": 0, "suppressed": 0}
    cycle_pump(cfg, store, sink, session, c, real_emit)      # baza
    store.pump_queue_add({
        "mint": "cand1", "name": "VortexDex", "symbol": "VRTX",
        "uri": "https://example.io/m.json", "website": "https://example.io",
        "twitter": "", "created_ms": 1792578000000, "mcap_usd": 42000.0,
        "complete": 0,
    })
    c = {"n": 0, "suppressed": 0}
    cycle_pump(cfg, store, sink, session, c, real_emit)      # przegląd + alert

    check(store.counts()["events"] == 1, "zdarzenie zapisane w DB")
    ev = store.recent_events(1)[0]
    check(ev["etype"] == "pump_promising" and ev["title"].startswith("🔬"),
          f"typ/tytuł: {ev['title']}")
    check("Opis:" in (ev["detail"] or "") and "https://example.io" in (ev["detail"] or ""),
          "detail zawiera opis i link")

    # ponowne wystąpienie tego samego mintage → zero duplikatu
    store.pump_queue_add({
        "mint": "cand1", "name": "VortexDex", "symbol": "VRTX",
        "uri": "https://example.io/m.json", "website": "https://example.io",
        "twitter": "", "created_ms": 1792578000000, "mcap_usd": 42000.0,
        "complete": 0,
    })
    c = {"n": 0, "suppressed": 0}
    cycle_pump(cfg, store, sink, session, c, real_emit)
    check(store.counts()["events"] == 1, "dedupe: drugi raz = bez duplikatu")

    # --- próg MC: token poniżej5k CZEKA, nie dostaje alertu ---
    store.pump_queue_add({
        "mint": "cand2", "name": "FundusVault", "symbol": "FVLT",
        "uri": "https://example.io/f.json", "website": "https://example.io",
        "twitter": "", "created_ms": 1792578000000, "mcap_usd": 4200.0,
        "complete": 0, "creator": "AbcCreator123", "sig": "sigcand2",
    })
    c = {"n": 0, "suppressed": 0}
    s_held = cycle_pump(cfg, store, sink, session, c, real_emit)
    still = store.conn.execute(
        "SELECT last_try FROM pump_queue WHERE mint='cand2'").fetchone()
    check(s_held["held"] == 1 and still is not None and still["last_try"] > 0,
          f"MC $4 200 < 5 000 → wstrzymany (held={s_held['held']})")
    check(store.counts()["events"] == 1, "brak alertu przy niskim MC")

    # --- MC urosło do $6 500 (widać na liście) → pełna ocena + alert ---
    # symulacja: minęło10 min od ostatniego przeglądu (recheck)
    store.conn.execute("UPDATE pump_queue SET last_try=0 WHERE mint='cand2'")
    store.conn.commit()
    P.fetch_new_coins = lambda s, **k: [
        {"mint": "base1", "name": "Baza", "symbol": "BZ"},
        {"mint": "cand2", "name": "FundusVault", "symbol": "FVLT",
         "website": "https://example.io", "metadata_uri": "https://example.io/f.json",
         "created_timestamp": 1792578000000, "market_cap_usd": 6500.0},
    ]
    c = {"n": 0, "suppressed": 0}
    s_pass = cycle_pump(cfg, store, sink, session, c, real_emit)
    check(s_pass["candidates"] == 1 and store.counts()["events"] == 2,
          f"MC $6 500 ≥ 5 000 → ocena i ALERT (kandydaci={s_pass['candidates']})")
    ev2 = store.conn.execute(
        "SELECT * FROM events WHERE dedupe_key='pump:cand2'").fetchone()
    check(ev2 is not None and "$6,500" in ev2["detail"],
          "alert pokazuje świeże MC ($6,500)")

    # przywróć live
    P.fetch_new_coins, P.fetch_token_info = orig_fetch, orig_info
    P.check_website, P.domain_age_days = orig_check, orig_age
    store.close()


def live() -> None:
    print("== cykl na żywo (frontend-api-v3) ==")
    cfg = load_config()
    cfg.pump_enabled = True
    cfg.pump_meta_per_cycle = 3
    cfg.db_path = os.path.join(tempfile.mkdtemp(prefix="pump_"), "t.db")
    alerts: list[dict] = []

    def spy(store, sink, cfg_, **kw):
        alerts.append(kw)
        return True

    store = Store(cfg.db_path)
    sink = _SilentSink()
    session = make_session()

    s1 = cycle_pump(cfg, store, sink, session, {"n": 0, "suppressed": 0}, spy)
    print(f"  cykl1: {s1}")
    check(s1["baseline"] > 0, f"baza odniesienia: {s1['baseline']} tokenów")

    s2 = cycle_pump(cfg, store, sink, session, {"n": 0, "suppressed": 0}, spy)
    print(f"  cykl2: {s2}")
    check(store.pump_seen_count() >= s1["baseline"],
          f"pump_seen rośnie ({store.pump_seen_count()})")
    check(s2["reviewed"] <= cfg.pump_meta_per_cycle,
          "metadane limitowane do meta_per_cycle")
    if alerts:
        print(f"  ALERT! {alerts[0]['title']}")
    else:
        print("  (brak kandydata w tej próbce — normalne, filtr jest ścisły)")

    # eksport/import stanu z nowymi tabelami
    path = cfg.db_path + ".json"
    store.export_state(path)
    with open(path, "rb") as f:
        a = f.read()
    store.export_state(path)
    with open(path, "rb") as f:
        b = f.read()
    check(a == b, "eksport stanu byte-stabilny")
    store2 = Store(cfg.db_path + ".copy.db")
    check(store2.import_state(path), "import stanu OK")
    check(store2.pump_seen_count() == store.pump_seen_count(),
          f"pump_seen przeżył eksport/import ({store2.pump_seen_count()})")
    store.close()
    store2.close()


class _SilentSink:
    def info(self, *a, **k): pass
    def show(self, *a, **k): pass


def main() -> None:
    unit()
    if "--unit" not in sys.argv:
        e2e_alert()
        live()
    print("\n" + ("WSZYSTKO OK ✅" if OK else "SĄ BŁĘDY ❌"))
    sys.exit(0 if OK else 1)


if __name__ == "__main__":
    main()
