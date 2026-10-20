# 🌥️ Listing Radar na Oracle Cloud (za darmo,24/7)

Kompletna instrukcja, żeby radar działał **przy wyłączonym komputerze**.
Czas razem: ~30–40 minut. Koszt: **0 zł** (Always Free).

---

## Krok0 — czego potrzebujesz

- karta płanicza/kredytowa (do weryfikacji konta — bez opłat),
- telefon (kod SMS/emailed przy rejestracji),
- te pliki na komputerze: folder **`listing-radar`** (z `config.json` i bazą).

---

## Krok1 — Konto Oracle Cloud (~10 min)

1. Wejdź na **https://www.oracle.com/cloud/free/** → przycisk **„Start for free"**.
2. Formularz:
   - **Country/Region**: Poland
   - **Cloud Account Name**: np. `mojradar` (unikalne, bez spacji)
   - **Home Region**: wybierz **Germany (Europa) — eu-frankfurt-1**
     ⚠️ **regionu nie da się potem zmienić** — Frankfurt jest najbliżej Polski.
   - e-mail, hasło (min.8 znaków, wielka+mała litera+cyfra), zgoda na regulamin.
3. Na maila przychodzi **kod weryfikacyjny** (8 cyfr) → wpisujesz.
4. Dalej: telefon (kod SMS), dane (firma: zostaw puste / „Individual"),
   **dane karty** (nic nie pobierają; mogą zablokować mikro-kwotę ~1 $ i oddać).
5. Logowanie do konsoli: **https://cloud.oracle.com**

> Haczyki: jak rejestracja „się wykrzaczy" — odśwież w trybie incognito / inna
> przeglądarka / za godzinę. Wirtualne karty Revolut czasem odrzucane — wtedy
> zwykła karta.

---

## Krok2 — Tworzenie maszyny (~7 min)

W konsoli Oracle (menu ≡ w lewym górnym rogu):

**Menu → Compute → Instances → Create instance**

Ustawienia (resztę zostaw domyślną):

| Co | Ustawienie |
|---|---|
| **Name** | `listing-radar` |
| **Image** | „Change image" → **Canonical Ubuntu** → **Ubuntu24.04** (lub22.04) → Select |
| **Shape** | „Change shape" → **VM.Standard.E4.Flex**, OCPU **1**, RAM **1 GB** (jest badge „Always Free") — jak nie ma E4, weź **A1.Flex**1 OCPU/1 GB (też free) |
| **Networking** | domyślna nowa sieć (kreator sam zezwala na SSH) |
| **Add SSH keys** | **„Generate a key pair"** → **„Save private key"** → zapisz i **zmień nazwę pliku na `oracle.key`** (w Pobranych). Nie zamykaj strony zanim nie zapiszesz! |
| **Boot volume** | domyślny; **NIE** włączaj „Backup" (płatny) |

Kliknij **Create** → czekaj aż status będzie **RUNNING** (1–2 min) →
**skopiuj Public IP** (np. `129.151.x.x`).

---

## Krok3 — Połączenie z maszyną (PowerShell na Windowsie)

```powershell
ssh -i "$env:USERPROFILE\Downloads\oracle.key" ubuntu@ADRES_IP
```

(podmień `ADRES_IP` na skopiowany; przy pierwszym razie wpisz `yes`
odnośnie odcisku klucza.)

> Jeśli Pisze „UNPROTECTED PRIVATE KEY FILE":
> ```powershell
> icacls "$env:USERPROFILE\Downloads\oracle.key" /inheritance:r /grant:r "$($env:USERNAME):R"
> ```
> i powtórz `ssh`.

Powinien pojawić się wiersz: `ubuntu@instance-...:~$` — **jesteś na serwerze**.

---

## Krok4 — Wgranie programu (z POWROTNEGO okna PowerShell, nie ze ssh)

Otwórz **NOWE** okno PowerShell (Start → PowerShell) i:

```powershell
scp -i "$env:USERPROFILE\Downloads\oracle.key" -r "$env:USERPROFILE\listing-radar" ubuntu@ADRES_IP:
```

Skopiuje cały folder razem z Twoim `config.json` (token bota!) i bazą.

---

## Krok5 — Instalacja jednym skryptem (w oknie ssh)

W oknie, gdzie masz `ubuntu@instance-...`:

```bash
cd ~/listing-radar && chmod +x setup_vm.sh && ./setup_vm.sh
```

Skrypt sam:
1. zainstaluje Pythona,
2. zrobi środowisko wirtualne i zainstaluje zależności,
3. **przetestuje Telegrama** (dostaniesz wiadomość testową na telefon!),
4. zainstaluje usługę **autostartu** (działa po restarcie serwera i przy
   wyłączonym Twoim komputerze),
5. wypisze ostatnie logi.

---

## Krok6 — Weryfikacja

W oknie ssh:

```bash
sudo systemctl status listing-radar --no-pager
```

Powinno być **active (running)**. Logi na żywo:

```bash
sudo journalctl -u listing-radar -f
```

(wyjście: `q` albo Ctrl+C)

Szukaj w logach:
- `▶ Listing Radar startuje — ... TG: TAK`
- `📌 SafeTrade: baza odniesienia...` (albo „źródło zapasowe" — to normalne)

---

## Krok7 — Gotowe

1. **Wyłącz u siebie lokalnego watcha** (Ctrl+C w jego oknie) — żeby nie
   były dwa i nie dublowały alertów.
2. Wyłącz kompa kiedy chcesz — radar jedzie na serwerze.
3. Powiadomienia i raport 9:00 przychodzą na Telegram jak dotąd.

---

## 📖 Codzienna obsługa (ściągawka)

| Chcę... | Komenda (po ssh) |
|---|---|
| Zobaczyć logi na żywo | `sudo journalctl -u listing-radar -f` |
| Sprawdzić status | `sudo systemctl status listing-radar` |
| Zatrzymać | `sudo systemctl stop listing-radar` |
| Uruchomić | `sudo systemctl start listing-radar` |
| Test TG | `cd ~/listing-radar && .venv/bin/python test_tg.py` |
| Jednorazowe sprawdzenie | `cd ~/listing-radar && .venv/bin/python -m radar check` |
| Statystyki | `cd ~/listing-radar && .venv/bin/python -m radar status` |

**Uwaga o IP:** Public IP serwera zmienia się po **zatrzymaniu i włączeniu**
instancji (nie po restarcie systemu). Nowy adres = w Oracle konsoli, przy
instancji. Nowe hasło SSH: nadal `oracle.key`.

## Aktualizacja radaru na serwerze (kiedy zmienię kod)

```powershell
scp -i "$env:USERPROFILE\Downloads\oracle.key" -r "$env:USERPROFILE\listing-radar\radar" "$env:USERPROFILE\listing-radar\test_tg.py" ubuntu@ADRES_IP:listing-radar/
```
potem w ssh:
```bash
sudo systemctl restart listing-radar
```

## Bezpieczeństwo

- SSH jest jedynym otwartym portem (i tylko Twój klucz — hasła wyłączone).
- Program tylko **wychodzi** do internetu (CoinGecko/SafeTrade/Telegram),
  nic nie przyjmuje z zewnątrz.
- `config.json` z tokenem zostaje **u Ciebie na serwerze i u Ciebie na PC** —
  nie wrzucaj nigdzie publicznie.
