"""Sesja HTTP: przeglądarkowy User-Agent, obsługa 429/5xx, ponowienia.

SafeTrade stoi za Cloudflare i odrzuca zapytania z domyślnego UA
(np. python-requests), dlatego wszędzie używamy UA przeglądarki.
"""
from __future__ import annotations

import json as _json
import logging
import shutil
import subprocess
import time

import requests

log = logging.getLogger("radar")

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Pełny zestaw nagłówków „prawdziwej przeglądarki" — pomija część
# blokad Cloudflare, które patrzą nie tylko na User-Agent.
BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    # Accept-Encoding pominięty świadomie: requests sam dekompresuje
    # gzip/deflate, a wymuszanie "br" bez pakietu brotli psułoby JSON.
    "Referer": "https://safetrade.com/",
    "Origin": "https://safetrade.com",
    "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": BROWSER_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return s


def get_json(
    session: requests.Session,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 25,
    max_retries: int = 3,
    label: str = "",
) -> tuple[object | None, int]:
    """GET -> (json | None, status).

    status: 200 = OK, inna liczba = ostatni znany kod błędu,
    0 = nie udało się po wszystkich ponowieniach (błąd sieci).
    """
    last_status = 0
    for attempt in range(max_retries):
        try:
            r = session.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            last_status = 0
            wait = min(2 ** (attempt + 1), 20)
            log.warning("%s błąd sieci (%s), ponawiam za %ss", label or url, e, wait)
            time.sleep(wait)
            continue

        if r.status_code == 429:
            try:
                wait = int(r.headers.get("Retry-After") or 0)
            except ValueError:
                wait = 0
            wait = wait or min(60, 15 * (attempt + 1))
            last_status = 429
            log.warning("%s rate-limit 429, czekam %ss", label or url, wait)
            time.sleep(wait)
            continue

        if r.status_code >= 500:
            last_status = r.status_code
            log.warning("%s HTTP %s, ponawiam...", label or url, r.status_code)
            time.sleep(min(3 * (attempt + 1), 30))
            continue

        if r.status_code >= 400:
            log.warning("%s HTTP %s dla %s", label or url, r.status_code, url)
            return None, r.status_code

        try:
            return r.json(), 200
        except ValueError:
            log.error("%s odpowiedź nie jest JSON: %s", label or url, url[:120])
            return None, -1

    return None, last_status


def get_json_via_curl(url: str, *, headers: dict | None = None,
                      timeout: int = 25, label: str = "") -> tuple[object | None, int]:
    """Rezerwowe pobranie JSON przez curl (Windows 10+ ma wbudowany curl.exe).

    Inny „odcisk" TLS niż Python bywa akceptowany przez Cloudflare tam,
    gdzie requests dostaje 403.
    """
    exe = shutil.which("curl")
    if not exe:
        return None, -1
    cmd = [exe, "-sS", "--compressed", "--max-time", str(timeout)]
    for k, v in (headers or {}).items():
        if k.lower() == "user-agent":
            cmd += ["-A", v]
        else:
            cmd += ["-H", f"{k}: {v}"]
    cmd += ["-w", "\n__RADAR_STATUS__:%{http_code}", url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=timeout + 15)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("%s curl nie wystartował: %s", label or url, e)
        return None, -1
    text = out.stdout or ""
    if "__RADAR_STATUS__:" not in text:
        err = (out.stderr or "").strip()[:200]
        log.warning("%s curl bez statusu (%s): %s", label or url, out.returncode, err)
        return None, 0
    body, _, status_s = text.rpartition("__RADAR_STATUS__:")
    try:
        status = int(status_s.strip())
    except ValueError:
        return None, 0
    if status != 200:
        log.warning("%s curl HTTP %s", label or url, status)
        return None, status
    try:
        return _json.loads(body), 200
    except ValueError:
        log.error("%s curl: odpowiedź nie jest JSON (%s)", label or url, url[:120])
        return None, -1
