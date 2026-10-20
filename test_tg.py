#!/usr/bin/env python3
"""Test Telegrama dla Listing Radar: sprawdza token bota i wysyła wiadomość.

Uruchamiaj z folderu z plikiem config.json:

    python test_tg.py

Kody wyjścia: 0 = OK, 1 = problem (komunikat po polsku na ekranie).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests


def main() -> int:
    cfg_path = Path(__file__).with_name("config.json")
    if not cfg_path.exists():
        print("✗ Nie znalazłem config.json obok test_tg.py.")
        print("  Uruchom ten skrypt z folderu, w którym jest config.json.")
        return 1

    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except ValueError as e:
        print(f"✗ config.json to niepoprawny JSON: {e}")
        print("  Otwórz go w Notatniku i sprawdź przecinki/z nawiasy.")
        return 1

    tg = cfg.get("telegram") or {}
    token = str(tg.get("bot_token") or "").strip()
    chat_id = str(tg.get("chat_id") or "").strip()

    if not token or not chat_id:
        print("✗ W config.json brakuje telegram.bot_token albo telegram.chat_id.")
        return 1

    # --- 1. test tokena ---
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20)
        data = r.json()
    except requests.RequestException as e:
        print(f"✗ Błąd sieci przy sprawdzaniu tokena: {e}")
        return 1
    if not data.get("ok"):
        print(f"✗ TOKEN jest zły — Telegram odpowiedział: {data}")
        print("  Skopiuj token jeszcze raz od @BotFather (bez spacji).")
        return 1
    username = data.get("result", {}).get("username", "?")
    print(f"✓ Token OK — bot: @{username}")

    # --- 2. testowa wiadomość ---
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "Listing Radar — test OK 📡\nJesli to widzisz, alerty dzialaja.",
            },
            timeout=20,
        )
        data = r.json()
    except requests.RequestException as e:
        print(f"✗ Błąd sieci przy wysyłce: {e}")
        return 1

    if data.get("ok"):
        print("✓ Wiadomość TEST wysłana — sprawdź Telegram, powinna być na liście.")
        return 0

    desc = str(data.get("description", ""))
    print(f"✗ Wiadomość nie poszła: {desc}")
    if "chat not found" in desc:
        print("  → zły chat_id ALBO nie napisałeś pierwszy do swojego bota.")
        print("    Wejdź w bota w Telegramie, napisz np. 'cześć' i powtórz ten test.")
    elif "bot was blocked" in desc.lower() or "blocked" in desc.lower():
        print("  → zablokowałeś(aś) bota — odblokuj go w Telegramie i powtórz test.")
    elif "chat_id" in desc:
        print("  → chat_id wygląda na zły; sprawdź w @userinfobot.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
