# 📡 Listing Radar

Program do **wczesnego wykrywania nowych listowań kryptowalut** na:

1. **CoinGecko** — monety dodawane do bazy CoinGecko,
2. **SafeTrade** — nowe rynki na giełdzie SafeTrade (łącznie z rynkami
   *przed notowaniami*, czyli `state=disabled`),
3. **skrzyżowanie obu źródeł (🎯 CROSS)** — sygnał, gdy ten sam coin
   pojawia się jednocześnie na CoinGecko i na SafeTrade (albo gdy CoinGecko
   zaczyna śledzić parę z SafeTrade). To najcenniejszy sygnał — łapany
   jako jeden z pierwszych na rynku.

Alerty: **Telegram** + konsola + plik `alerts.log`. Raz dziennie (domyślnie
**09:00**) dodatkowy **raport podsumowujący ostatnie 24 h** z wszystkimi
nowymi listowaniami.

> ⚠️ Narzędzie informacyjne — nie jest poradą inwestycyjną.

---

## Jak to działa

```
        ┌──────────────────────┐      ┌──────────────────────┐
        │  CoinGecko API       │      │  SafeTrade API       │
        │  /coins/list/new     │      │  /trade/public/      │
        │  /coins/list (free)  │      │  markets + tickers   │
        │  /exchanges/         │      │  (UA przeglądarki,   │
        │   safe_trade/tickers │      │   bo Cloudflare)     │
        └──────────┬───────────┘      └──────────┬───────────┘
                   │  co X s                     │  co X s
                   ▼                             ▼
             ┌────────────────────────────────────────┐
             │  SQLite (snapshoty → różnicowanie)     │
             │  baseline przy 1. uruchomieniu         │
             └────────────────┬───────────────────────┘
                              │  nowe wpisy / zmiany stanu
                              ▼
             ┌────────────────────────────────────────┐
             │  Matcher: symbol ↔ symbol, okno        │
             │  cross_window_days → zdarzenia 🎯CROSS │
             └────────────────┬───────────────────────┘
                              ▼
                     Telegram + alerts.log
```

### Źródła sygnałów

| Sygnał | Źródło | Priorytet | Co oznacza |
|---|---|---|---|
| `st_new_market_pre` | SafeTrade | 🔍/🚨 | Rynek dodany, notowania **jeszcze nie ruszyły** → wcześniejszy sygnał niż sama lista |
| `st_new_market_live` | SafeTrade | 🚨 | Rynek dodany i od razu aktywny |
| `st_state_change` | SafeTrade | 🚨 | `disabled → enabled` — **notowania otwarte** |
| `cg_new_coin` | CoinGecko | 🔍/🚨 | Coin dodany do CoinGecko (z `activated_at` przy Pro API) |
| `cg_safe_pair` | CoinGecko × SafeTrade | 🚨 | CoinGecko zaczął śledzić parę na SafeTrade (po `coin_id` — dopasowanie pewne) |
| `cross_hit` | oba | 🚨 | **CROSS**: ten sam coin na obu źródłach w oknie `cross_window_days` |

---

## Instalacja

```bash
cd listing-radar
pip install -r requirements.txt      # tylko 'requests'
cp config.example.json config.json   # i uzupełnij
```

## Konfiguracja (`config.json`)

```json
{
  "coingecko_api_key": "TWÓJ_KLUCZ_PRO",      // klucz z coingecko.com/en/api
  "telegram": {
    "bot_token": "123456:ABC-DEF...",         // od @BotFather
    "chat_id": "123456789"                    // np. @userinfobot podaje Twoje id
  },
  "intervals": { "coingecko": 300, "safetrade": 120 },   // sekundy
  "cross_window_days": 14,
  "cg_exchange_id": "safe_trade",
  "fetch_cg_exchange_tickers": true,
  "enrich_tickers": true,
  "daily_report": { "enabled": true, "hour": 9, "minute": 0 },  // raport 24h
  "db_path": "listing_radar.db",
  "log_path": "alerts.log",
  "max_events_per_cycle": 30
}
```

* **Bez klucza CoinGecko** program działa w trybie `free` — porównuje pełną
  listę `/coins/list` co `interval_coingecko` sekund (równie skuteczne,
  bez `activated_at`). Klucz Pro włącza `/coins/list/new` (ostatnie 200
  monet ze znacznikiem czasu).
* **Telegram**: stwórz bota przez **@BotFather** → `/newbot` → skopiuj
  `token`; swój `chat_id` zobaczysz np. przez **@userinfobot**. Najpierw
  napisz do swojego bota cokolwiek (inaczej nie może wysłać wiadomości).
  Bez Telegrama alerty trafiają tylko do konsoli i `alerts.log`.
* Zmienne środowiskowe nadpisują plik: `CG_API_KEY`, `TG_BOT_TOKEN`,
  `TG_CHAT_ID`, `RADAR_CONFIG`.

## Uruchamianie

```bash
python3 -m radar watch     # ciągły monitoring (główny tryb)
python3 -m radar check     # jedno sprawdzenie i wyjście
python3 -m radar report    # wyślij raport z ostatnich 24h TERAZ (--hours 48 zmienia zakres)
python3 -m radar status    # statystyki + ostatnie zdarzenia
python3 test_tg.py         # test tokena bota i wysyłki na Telegram
```

> **Windows:** używaj `python` zamiast `python3`, np. `python -m radar watch`.

## Rozwiązywanie problemów (Windows)

| Objawa | Przyczyna | Rozwiązanie |
|---|---|---|
| `No module named 'tzdata'` / `ZoneInfoNotFoundError` | Windows nie ma bazy stref czasowych | `python -m pip install tzdata` |
| `HTTP 401` na `coins/list/new` | Klucz planu **Demo** (nie Pro) — to normalne | Program sam przełącza na darmowe API; z Demo klucza korzysta dla wyższych limitów |
| `HTTP 403` na safetrade.com | Cloudflare blokuje zapytanie | Program sam: pełniejsze nagłówki → `curl` → zapasowa domena `safe.trade` |
| `SyntaxError` przy teście z `python -c` | PowerShell zjada cudzysłowy | Używaj pliku: `python test_tg.py` |
| Telegram „chat not found" | zły `chat_id` albo nie napisałeś pierwszy do bota | Napisz do bota cokolwiek, potem `python test_tg.py` |
| `nie znaleziono Python` | Python nie zainstalowany / brak w PATH | python.org albo `winget install Python.Python.3.13` |

**Pierwsze uruchomienie** zapisuje *bazę odniesienia* (baseline) — bez
alertów. Dopiero różnice w kolejnych cyklach generują alerty.

### Raport dzienny

O skonfigurowanej godzinie (`daily_report.hour`, czas warszawski — domyślnie
09:00) program wysyła na Telegram (i do logu) podsumowanie ostatnich 24 h:

* sekcja 🎯 CROSS, 🚀 listowania SafeTrade, 📥 pre-listingi,
  🔗 pary CoinGecko×SafeTrade, 🆕 nowe monety CoinGecko,
* kolejność wg priorytetu, maks. 12 pozycji na sekcję (reszta „+N więcej"),
* raz dziennie — Restart watch nie powoduje podwójnej wysyłki
  (stan trzymany w DB); przy błędzie Telegram ponów po 10 minutach,
* bez Telegrama raport trafia tylko do konsoli/`alerts.log`.

```bash
python3 -m radar report            # ręcznie, za ostatnie 24h
python3 -m radar report --hours 72 # szerszy zakres
```

### Praca w tle (systemd)

`/etc/systemd/system/listing-radar.service`:

```ini
[Unit]
Description=Listing Radar (CoinGecko + SafeTrade)
After=network-online.target

[Service]
WorkingDirectory=/HOME/USER/listing-radar
ExecStart=/usr/bin/python3 -m radar watch
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now listing-radar
journalctl -u listing-radar -f
```

---

## Wykorzystywane endpointy

**CoinGecko** (darmowe: `api.coingecko.com`, Pro: `pro-api.coingecko.com`):

* `GET /coins/list/new` — ostatnie 200 dodanych monet + `activated_at` (Pro)
* `GET /coins/list` — pełna lista (free, różnicowanie)
* `GET /exchanges/safe_trade/tickers?per_page&page` — pary SafeTrade
  notowane przez CoinGecko
* limit darmowego API: ~30 zapytań/min — program sam obsługuje `429`
  (Retry-After) i robi backoff przy błędach

**SafeTrade** (wymaga przeglądarkowego User-Agent — Cloudflare):

* `GET https://safetrade.com/api/v2/trade/public/markets`
  — m.in. `id, base_unit, quote_unit, state, created_at`
* `GET https://safetrade.com/api/v2/trade/public/tickers`
  — `last, volume` do wzbogacenia alertu
* linki rynków: `https://safetrade.com/exchange/BTC-USDT`

## Ograniczenia / świadome decyzje

* Dopasowanie CoinGecko ↔ SafeTrade **po symbolu** (małe różnice zapisu
  symboli są możliwe, dlatego wiadomość z CROSS linkuje do obu stron do
  weryfikacji). Pary z `/exchanges/safe_trade/tickers` są pewne
  (dopasowanie po `coin_id`).
* Okno `cross_window_days` (domyślnie 14 dni) definiuje, co uznajemy za
  „równoczesne" listowanie.
* `max_events_per_cycle` chroni przed falą alertów (np. gdy API zwróci
  dziwne dane).
* Webhook CoinGecko `cg.coin.listed` jest jeszcze w private beta —
  przyszła optymalizacja (mniej niż 30 s opóźnienia).
