"""Ładowanie konfiguracji: plik JSON + nadpisania zmiennymi środowiskowymi."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TelegramCfg:
    bot_token: str = ""
    chat_id: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass
class Config:
    coingecko_api_key: str = ""
    telegram: TelegramCfg = field(default_factory=TelegramCfg)
    interval_coingecko: int = 300      # sekundy
    interval_safetrade: int = 120      # sekundy
    cross_window_days: int = 14        # okno "równoczesnego" listingu
    cg_exchange_id: str = "safe_trade"
    fetch_cg_exchange_tickers: bool = True
    enrich_tickers: bool = True        # dołącz cenę/wolumen z SafeTrade do alertu
    daily_report_enabled: bool = True  # raport dzienny o nowych listingach
    daily_report_hour: int = 9         # godzina (czas warszawski)
    daily_report_minute: int = 0
    db_path: str = "listing_radar.db"
    log_path: str = "alerts.log"
    max_events_per_cycle: int = 30     # zabezpieczenie przed falą alertów
    # pump.fun — wczesna filtracja „fundamenty vs memecoiny" (Antyscam v2)
    pump_enabled: bool = True          # moduł pump.fun (odłączalny)
    pump_meta_per_cycle: int = 40      # ile metadanych oceniamy w cyklu
    pump_min_score: int = 6            # próg punktów do alertu 🔬 (max 12)
    pump_min_mcap: float = 5000.0      # minimalny market cap w USD (0 = brak)

    @property
    def cg_mode(self) -> str:
        return "pro" if self.coingecko_api_key else "free"


def load_config(path: str | None = None) -> Config:
    cfg = Config()
    cfg_path = Path(path or os.environ.get("RADAR_CONFIG", "config.json"))

    if cfg_path.exists():
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        if "coingecko_api_key" in raw:
            cfg.coingecko_api_key = str(raw["coingecko_api_key"] or "")
        tg = raw.get("telegram") or {}
        cfg.telegram = TelegramCfg(
            bot_token=str(tg.get("bot_token") or ""),
            chat_id=str(tg.get("chat_id") or ""),
        )
        iv = raw.get("intervals") or {}
        cfg.interval_coingecko = int(iv.get("coingecko", cfg.interval_coingecko))
        cfg.interval_safetrade = int(iv.get("safetrade", cfg.interval_safetrade))
        cfg.cross_window_days = int(raw.get("cross_window_days", cfg.cross_window_days))
        cfg.cg_exchange_id = str(raw.get("cg_exchange_id", cfg.cg_exchange_id))
        cfg.fetch_cg_exchange_tickers = bool(
            raw.get("fetch_cg_exchange_tickers", cfg.fetch_cg_exchange_tickers)
        )
        cfg.enrich_tickers = bool(raw.get("enrich_tickers", cfg.enrich_tickers))
        dr = raw.get("daily_report") or {}
        cfg.daily_report_enabled = bool(dr.get("enabled", cfg.daily_report_enabled))
        cfg.daily_report_hour = int(dr.get("hour", cfg.daily_report_hour))
        cfg.daily_report_minute = int(dr.get("minute", cfg.daily_report_minute))
        cfg.db_path = str(raw.get("db_path", cfg.db_path))
        cfg.log_path = str(raw.get("log_path", cfg.log_path))
        cfg.max_events_per_cycle = int(
            raw.get("max_events_per_cycle", cfg.max_events_per_cycle)
        )
        pu = raw.get("pump") or {}
        cfg.pump_enabled = bool(pu.get("enabled", cfg.pump_enabled))
        cfg.pump_meta_per_cycle = int(
            pu.get("meta_per_cycle", cfg.pump_meta_per_cycle))
        cfg.pump_min_score = int(pu.get("min_score", cfg.pump_min_score))
        cfg.pump_min_mcap = float(pu.get("min_mcap", cfg.pump_min_mcap))

    # Nadpisania z env (przydatne w systemd/Dockerze).
    cfg.coingecko_api_key = os.environ.get("CG_API_KEY", cfg.coingecko_api_key)
    cfg.telegram.bot_token = os.environ.get("TG_BOT_TOKEN", cfg.telegram.bot_token)
    cfg.telegram.chat_id = os.environ.get("TG_CHAT_ID", cfg.telegram.chat_id)
    return cfg
