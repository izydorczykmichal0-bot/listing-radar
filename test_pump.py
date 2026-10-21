"""Test modułu pump.fun: leksykon, scoring fundamentów, cykl na żywo.

Uruchomienie:  python test_pump.py
"""
from __future__ import annotations

import os
import sys
import tempfile

from radar.config import load_config
from radar.httpc import make_session
from radar.pump import cycle_pump, lex_bad, score_token
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
    P.fetch_new_coins = lambda s, **k: [{"mint": "base1", "name": "Baza",
                                          "symbol": "BZ"}]
    P.fetch_token_info = lambda s, uri, mint: {
        "desc": ("A decentralized protocol for cross-chain payments and "
                 "staking infrastructure with an open oracle network for "
                 "developers."),
        "website": "https://example.io", "twitter": "", "telegram": "",
    }

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
    P.fetch_new_coins, P.fetch_token_info = orig_fetch, orig_info  # przywróć live
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
