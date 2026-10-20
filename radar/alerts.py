"""Alerty: konsola + plik log (zawsze) oraz Telegram (opcjonalnie)."""
from __future__ import annotations

import html
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .config import Config

log = logging.getLogger("radar")
WARSAW = ZoneInfo("Europe/Warsaw")

PRIORITY_EMOJI = {"high": "🚨", "normal": "🔍"}


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, WARSAW).strftime("%Y-%m-%d %H:%M:%S")


class Sink:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log_path = cfg.log_path

    # ---------- zwykłe komunikaty ----------
    def info(self, msg: str) -> None:
        line = f"{_fmt_ts(int(time.time()))} {msg}"
        print(line, flush=True)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            log.error("Nie mogę zapisać do %s: %s", self.log_path, e)

    # ---------- zdarzenia ----------
    def show(self, ev: dict) -> None:
        emoji = PRIORITY_EMOJI.get(ev.get("priority", "normal"), "ℹ️")
        head = f"{emoji} [{ev.get('priority', 'normal').upper()}] {ev['title']}"
        body = ev.get("detail") or ""
        text = head + (("\n   " + body.replace("\n", "\n   ")) if body else "")
        if ev.get("url"):
            text += f"\n   {ev['url']}"
        self.info(text)

    # ---------- Telegram ----------
    def send_telegram(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.cfg.telegram.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.cfg.telegram.bot_token}/sendMessage"
        payload = {
            "chat_id": self.cfg.telegram.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        for attempt in range(3):
            try:
                r = requests.post(url, json=payload, timeout=20)
                if r.status_code == 200:
                    return True
                log.warning("Telegram HTTP %s: %s", r.status_code, r.text[:200])
                if r.status_code in (400, 403):  # zły token/chat — nie ma co powtarzać
                    return False
            except requests.RequestException as e:
                log.warning("Telegram błąd sieci: %s", e)
            time.sleep(2 ** attempt)
        return False

    def event_to_html(self, ev: dict) -> str:
        emoji = PRIORITY_EMOJI.get(ev.get("priority", "normal"), "ℹ️")
        title = html.escape(ev["title"])
        lines = [f"{emoji} <b>{title}</b>"]
        if ev.get("detail"):
            for ln in ev["detail"].split("\n"):
                if ln.strip():
                    lines.append(html.escape(ln))
        if ev.get("url"):
            lines.append(f'<a href="{html.escape(ev["url"], quote=True)}">{html.escape(ev["url"])}</a>')
        lines.append(f"<i>{_fmt_ts(ev['ts'])} (Warszawa)</i>")
        return "\n".join(lines)
