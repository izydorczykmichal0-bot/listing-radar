"""Test wzbogacania alertów: kategorie + opis prosto z CoinGecko.

Uruchomienie:  python test_enrich.py bitcoin dogecoin
Sprawdza, że blok „🏷 Kategorie / 📖 Opis" się generuje (albo że cicho
zwraca pusty string przy braku danych — alert i tak by wyszedł).
"""
from __future__ import annotations

import sys

from radar.config import load_config
from radar.enrich import enrich
from radar.httpc import make_session


def main() -> None:
    cfg = load_config()
    session = make_session()
    ids = sys.argv[1:] or ["bitcoin", "dogecoin"]
    ok = 0
    for coin_id in ids:
        block = enrich(session, cfg, coin_id)
        print(f"=== {coin_id} ===")
        print(block if block else "(brak danych — alert i tak by wyszedł)")
        if block:
            ok += 1
    print(f"\nOK: {ok}/{len(ids)} z wzbogaceniem")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
