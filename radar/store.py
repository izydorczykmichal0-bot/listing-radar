"""Magazyn danych: SQLite (snapshoty źródeł + historia zdarzeń).

Dzięki snapshotom przy pierwszym uruchomieniu budowana jest baza odniesienia
(baseline) bez zalewu alertów — dopiero różnice w kolejnych cyklach
generują zdarzenia.

Stan (bez pola last_seen, które zmienia się co cykl) można wyeksportować
do JSONA i zaimportować — używane w wariant GitHub Actions, gdzie każda
praca dostaje czystą maszynę, a stan trzymamy w repozytorium.
"""
from __future__ import annotations

import json
import sqlite3
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cg_coins (
    id           TEXT PRIMARY KEY,
    symbol       TEXT NOT NULL,
    name         TEXT NOT NULL,
    activated_at INTEGER,
    first_seen   INTEGER NOT NULL,
    source       TEXT NOT NULL            -- pro | free
);
CREATE INDEX IF NOT EXISTS idx_cg_symbol ON cg_coins(symbol COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS cg_safe_pairs (
    pair_id    TEXT PRIMARY KEY,          -- coin_id:base:target
    coin_id    TEXT NOT NULL,
    base       TEXT NOT NULL,
    target     TEXT NOT NULL,
    volume     REAL,
    first_seen INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS st_markets (
    id         TEXT PRIMARY KEY,          -- np. 'pearlusdt'
    base       TEXT NOT NULL,
    quote      TEXT NOT NULL,
    state      TEXT NOT NULL,             -- enabled | disabled
    created_at TEXT,
    first_seen INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_st_base ON st_markets(base COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    source     TEXT NOT NULL,             -- coingecko | safetrade | cross
    etype      TEXT NOT NULL,
    priority   TEXT NOT NULL,             -- high | normal
    title      TEXT NOT NULL,
    detail     TEXT,
    url        TEXT,
    alerted    INTEGER NOT NULL DEFAULT 0 -- 0=czeka, 1=wysłany, 2=pominięty, 3=wygasły
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # ---------------- meta ----------------
    def meta_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ---------------- CoinGecko ----------------
    def insert_cg_coin(self, coin: dict, source: str, now: int,
                       baseline: bool = False) -> bool:
        """baseline=True → first_seen=0 (nie znamy prawdziwej daty listingu,
        nie wolno traktować takiego wpisu jako 'świeżo dodanego')."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO cg_coins(id,symbol,name,activated_at,first_seen,source) "
            "VALUES(?,?,?,?,?,?)",
            (
                coin["id"],
                coin.get("symbol", ""),
                coin.get("name", coin["id"]),
                coin.get("activated_at"),
                0 if baseline else now,
                source,
            ),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def has_cg_coin(self, coin_id: str) -> bool:
        return (
            self.conn.execute("SELECT 1 FROM cg_coins WHERE id=?", (coin_id,)).fetchone()
            is not None
        )

    def find_cg_by_symbol(self, symbol: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM cg_coins WHERE symbol = ? COLLATE NOCASE LIMIT 10",
            (symbol,),
        ).fetchall()

    def insert_cg_pair(self, pair_id: str, coin_id: str, base: str,
                       target: str, volume, now: int) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO cg_safe_pairs(pair_id,coin_id,base,target,volume,first_seen) "
            "VALUES(?,?,?,?,?,?)",
            (pair_id, coin_id, base, target, volume, now),
        )
        self.conn.commit()
        return cur.rowcount == 1

    # ---------------- SafeTrade ----------------
    def get_st_market(self, market_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM st_markets WHERE id=?", (market_id,)
        ).fetchone()

    def insert_st_market(self, m: dict, now: int, baseline: bool = False) -> None:
        """baseline=True → first_seen=0; data utworzenia rynku (created_at)
        pochodzi z API i pozostaje wiarygodna."""
        self.conn.execute(
            "INSERT OR IGNORE INTO st_markets(id,base,quote,state,created_at,first_seen,last_seen) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                m["id"],
                m.get("base_unit", ""),
                m.get("quote_unit", ""),
                m.get("state", ""),
                m.get("created_at"),
                0 if baseline else now,
                now,
            ),
        )
        self.conn.commit()

    def update_st_market(self, market_id: str, state: str, now: int) -> None:
        self.conn.execute(
            "UPDATE st_markets SET state=?, last_seen=? WHERE id=?",
            (state, now, market_id),
        )
        self.conn.commit()

    def find_st_by_base(self, base: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM st_markets WHERE base = ? COLLATE NOCASE", (base,)
        ).fetchall()

    # ---------------- zdarzenia ----------------
    def add_event(self, dedupe_key: str, source: str, etype: str, priority: str,
                  title: str, detail: str, url: str | None, now: int,
                  tg_enabled: bool) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO events"
            "(ts,dedupe_key,source,etype,priority,title,detail,url,alerted) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (now, dedupe_key, source, etype, priority, title, detail, url,
             0 if tg_enabled else 2),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def pending_events(self, max_age: int = 86400) -> list[sqlite3.Row]:
        cutoff = int(time.time()) - max_age
        rows = self.conn.execute(
            "SELECT * FROM events WHERE alerted=0 ORDER BY id"
        ).fetchall()
        fresh, expired = [], []
        for r in rows:
            (fresh if r["ts"] >= cutoff else expired).append(r)
        if expired:
            self.conn.execute(
                "UPDATE events SET alerted=3 WHERE alerted=0 AND ts<?", (cutoff,)
            )
            self.conn.commit()
        return fresh

    def mark_alerted(self, event_id: int, value: int) -> None:
        self.conn.execute("UPDATE events SET alerted=? WHERE id=?", (value, event_id))
        self.conn.commit()

    # ---------------- statystyki ----------------
    def counts(self) -> dict:
        c = {}
        c["cg_coins"] = self.conn.execute("SELECT COUNT(*) n FROM cg_coins").fetchone()["n"]
        c["cg_safe_pairs"] = self.conn.execute(
            "SELECT COUNT(*) n FROM cg_safe_pairs").fetchone()["n"]
        c["st_markets"] = self.conn.execute("SELECT COUNT(*) n FROM st_markets").fetchone()["n"]
        c["st_enabled"] = self.conn.execute(
            "SELECT COUNT(*) n FROM st_markets WHERE state='enabled'").fetchone()["n"]
        c["events"] = self.conn.execute("SELECT COUNT(*) n FROM events").fetchone()["n"]
        c["events_high"] = self.conn.execute(
            "SELECT COUNT(*) n FROM events WHERE priority='high'").fetchone()["n"]
        return c

    def recent_events(self, n: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()

    def oldest_newest_first_seen(self) -> tuple[int | None, int | None]:
        row = self.conn.execute(
            "SELECT MIN(ts) a, MAX(ts) b FROM events").fetchone()
        return row["a"], row["b"]

    # ---------------- eksport / import stanu (wariant GitHub) ----------------
    def export_state(self, path: str) -> int:
        """Zapisuje pełny stan do JSON (bez last_seen, które zmienia się co cykl).

        Dzięki temu plik jest **byte-stabilny** — przy braku nowych zdarzeń
        dwa eksporty są identyczne, więc GitHub Actions nie robi zbędnych
        commitów.
        """
        def rows(q: str) -> list[list]:
            return [list(r) for r in self.conn.execute(q).fetchall()]

        data = {
            "version": 1,
            "meta": {r["key"]: r["value"]
                     for r in self.conn.execute(
                         "SELECT key, value FROM meta ORDER BY key")},
            "cg_coins": rows("SELECT id, symbol, name, activated_at, first_seen, source "
                             "FROM cg_coins ORDER BY id"),
            "st_markets": rows("SELECT id, base, quote, state, created_at, first_seen "
                               "FROM st_markets ORDER BY id"),
            "cg_safe_pairs": rows("SELECT pair_id, coin_id, base, target, volume, first_seen "
                                  "FROM cg_safe_pairs ORDER BY pair_id"),
            "events": rows("SELECT ts, dedupe_key, source, etype, priority, title, "
                           "detail, url, alerted FROM events ORDER BY id"),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        return len(data["events"])

    def import_state(self, path: str) -> bool:
        """Wczytuje stan z JSON (nadaje się na czystej bazie po checkoutcie)."""
        import os
        if not os.path.exists(path):
            return False
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        c = self.conn
        for tbl in ("events", "cg_safe_pairs", "st_markets", "cg_coins", "meta"):
            c.execute(f"DELETE FROM {tbl}")

        c.executemany(
            "INSERT INTO meta(key, value) VALUES(?, ?)",
            list(data.get("meta", {}).items()))
        c.executemany(
            "INSERT INTO cg_coins(id, symbol, name, activated_at, first_seen, source) "
            "VALUES(?,?,?,?,?,?)", data.get("cg_coins", []))
        c.executemany(
            "INSERT INTO st_markets(id, base, quote, state, created_at, first_seen, last_seen) "
            "VALUES(?,?,?,?,?,?,?)",
            [(r[0], r[1], r[2], r[3], r[4], r[5], int(time.time()))
             for r in data.get("st_markets", [])])
        c.executemany(
            "INSERT INTO cg_safe_pairs(pair_id, coin_id, base, target, volume, first_seen) "
            "VALUES(?,?,?,?,?,?)", data.get("cg_safe_pairs", []))
        c.executemany(
            "INSERT INTO events(ts, dedupe_key, source, etype, priority, title, "
            "detail, url, alerted) VALUES(?,?,?,?,?,?,?,?,?)",
            data.get("events", []))
        c.commit()
        return True
