# 🐙 Listing Radar na GitHub Actions (za darmo, bez karty,24/7)

Radar co **5 minut** sam odpala się na serwerach GitHuba, sprawdza
listowania i wysyła alerty na Telegram. **Twój komputer może być wyłączony.**
Koszt: **0 zł** (GitHub Actions dla repo PUBLICZNEGO = minute bez limitu).

---

## Krok0 — czego potrzebujesz

- konto na **github.com** (za darmo, **bez karty** — sam mail i hasło),
- plik **`listing-radar-github.zip`** z tego czatu,
- swój `config.json`… nie! — **sekrety (token, chat_id) wpiszesz na
  GitHubie w „Secrets"**, plik na repo nie trafia (bezpieczeństwo).

---

## Krok1 — Rozpakuj paczkę GitHub

1. Pobierz **`listing-radar-github.zip`** z czatu.
2. PP na pliku → **„Wypakuj wszystko"** → Pulpit.
3. Otwórz rozpakowany folder — na wierzchu muszą być: `radar`,
   **`.github`** (folder z kropką!), `github-setup.md`, `test_tg.py` itd.

> Folder `.github` bywa w Explorerze ukryty — to normalne, ma być.

---

## Krok2 — Konto i repozytorium

1. **github.com** → Sign up (mail + hasło + kod z maila).
2. Zalogowany → przycisk **„+"** (prawy górny róg) → **New repository**:
   - Repository name: `listing-radar`
   - **Public** ← WAŻNE (prywatne = limity minut; patrz „Uwagi")
   - checkbox „Add a README file" **ODHACZONY** (puste repo!)
   - **Create repository**

---

## Krok3 — Wrzuć pliki

1. Na stronie pustego repo kliknij link **„uploading an existing file"**
   (albo: **Add file → Upload files**).
2. Otwórz rozpakowany folder u siebie → **Ctrl+A** (zaznacz wszystko) →
   **przeciągnij** w pole na GitHubie.
3. Zielony przycisk **„Commit changes"**.
4. **WERYFIKACJA (nie pomijaj!)** — na głównej stronie repo musisz widzieć:
   - `.github` → wejdź → `workflows` → **`radar.yml`** ← jeśli TEGO NIE MA,
     plik nie wjechał (kropkowane foldery czasem gubią się przy drag&drop).
     Naprawa: **Add file → Create new file** → w polu nazwy wpisz
     `.github/workflows/radar.yml` → wklej zawartość tego samego pliku
     ze swojego folderu → Commit.

---

## Krok4 — Sekrety (token bota itp.)

W repo: **Settings → Secrets and variables → zakładka „Actions" →
New repository secret** — dodaj TRZY (nazwy dokładnie tak):

| Name | Value |
|---|---|
| `TG_BOT_TOKEN` | token od @BotFather (bez spacji) |
| `TG_CHAT_ID` | Twój chat_id |
| `CG_API_KEY` | klucz CoinGecko — **jeśli masz**; jak nie, TEN PUNKT POMIŃ |

Nigdy nie wklejaj tokena do plików w repo — tylko tutaj, do Secrets.

---

## Krok5 — Odpalenie

1. Zakładka **Actions** (na górze strony repo) →
   kliknij **„I understand my workflows, go ahead and enable them"**.
2. Po lewej: **Listing Radar** → po prawej **Run workflow → Run workflow**
   (ręczny pierwszy przebieg).
3. Poczekaj na **zielony ✓** (1–3 min). Wejdź w run → zobaczysz logi:
   - `📌 ... baza odniesienia zapisana` (pierwszy raz = baseline)
   - potem w zakładce **Code** pojawi się **`state.json`** (commit stanu)
4. Możesz dostać na Telegram raport/test — potwierdzenie, że sekrety grają.

---

## Krok6 — Sprzątanie na Twoim komputerze

- **Zatrzymaj lokalnego watcha** (Ctrl+C w jego oknie) — inaczej oba
  (lokalny i GitHub) będą wysyłać te same alerty drugi raz.
- Od teraz Twój komputer **nie jest potrzebny** do działania radara.

---

## Uwagi i uczciwe gwiazdki

- **Opóźnienia cron GitHuba**: harmonogramy bywają spóźnione o kilka
  minut (realnie sprawdzenie co5–10 min). To wciąż bardzo wcześnie.
- **Repo musi zostać Publiczne** dla `*/5` (nieskończone darmowe minuty).
  Chcesz prywatne → w `.github/workflows/radar.yml` zmień cron `*/5`
  na `*/10` (miesięczny limit2000 min wystarczy).
- **60 dni bez aktywności** → GitHub wyłącza harmonogramy; workflow ma
  wbudowany **heartbeat** (raz na6 dni rusza `last_run.txt`), więc samo
  się odświeża.
- **Raport dzienny o9:00 (Warszawa)** działa tak samo jak wcześniej —
  dopilnuje go sam program przy najbliższym przebiegu po9:00.
- Logi każdego przebiegu: zakładka **Actions** → kliknij przebieg →
  „Sprawdź listowania". Alerty i tak lądują na Telegramie.

## Codzienna ściągawka

| Chcę... | Gdzie |
|---|---|
| Zobaczyć logi | GitHub → Actions → ostatni przebieg |
| Sprawdzić stan/statystyki | u siebie: `python -m radar status` (po pobraniu `state.json`? — stan żyje w repo) |
| Zmienić sekret | Settings → Secrets and variables → Actions → Update |
| Zatrzymać radar | GitHub → Actions → Listing Radar → „..." → Disable workflow |
| Wrócić do trybu lokalnego | u siebie: `python -m radar watch` |

---

## Wariant produkcyjny: zewnętrzny cron (cron-job.org) — obowiązkowy fallback

**Dlaczego:** od października 2026 natywny harmonogram GitHuba (`on: schedule`)
potrafi w ogóle nie tworzyć przebiegów (bug po stronie GitHuba: `workflow_dispatch`
działa, `schedule` martwe). Docelowym zabezpieczeniem jest darmowy cron-job.org,
który wywołuje ten sam workflow przez API co 5 minut.

### Potrzebne

1. **Token GitHub (fine-grained PAT):**
   - https://github.com/settings/personal-access-tokens/new
   - nazwa: `listing-radar-cron`, Expiration: **No expiration**
   - Repository access: **Only select repositories** → `izydorczykmichal0-bot/listing-radar`
   - Permissions → Repository permissions → **Actions: Read and write** (Metadata doda się samo)
   - Generate → skopiować `github_pat_...` (pokazuje się raz!; przy błędzie
     „failed to generate the API credential" → Regenerate token)
2. **Konto cron-job.org** (darmowe, bez karty): https://console.cron-job.org/signup

### Zadanie w cron-job.org

| Pole | Wartość |
|---|---|
| Tytuł | `Listing Radar 5min` |
| URL | `https://api.github.com/repos/izydorczykmichal0-bot/listing-radar/actions/workflows/radar.yml/dispatches` |
| Metoda (Zaawansowane) | **POST** |
| Treść żądania | `{"ref":"main"}` |
| Harmonogram | Co 5 minuta (`*/5 * * * *`), Europe/Warsaw |
| Przełącznik „Zapisz odpowiedzi..." | włączony |

Nagłówki (Zaawansowane → Nagłówki → Dodaj):

```
Authorization: Bearer <github_pat_...>
Content-Type: application/json
Accept: application/vnd.github+json
```

Kod sukcesu w logu: **204**. Test: „Uruchomienie testowe" → w GitHub Actions
pojawia się nowy przebieg `workflow_dispatch`.

### Uwagi eksploatacyjne

- cron-job.org **wyłącza zadanie po 25 kolejnych błędach** → włącz e-mailowe
  powiadomienia o błędach (zakładka POWIADOMIENIA).
- Natywny `on: schedule` w radar.yml **zostaje** — gdy GitHub naprawi swojego
  crona, oba wyzwalacze mogą działać równolegle (grupa `concurrency` +
  dedupe zdarzeń chronią przed dublowaniem alertów).
- Token trzymać w tajemnicy; wycofanie = usunięcie tokena w ustawieniach GitHuba.
- Komendy lokalne (git) są jednorazowe — do codziennej pracy Terminal NIE jest
  potrzebny; radar żyje w chmurze.
