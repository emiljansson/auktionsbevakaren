# Auktiomera.se

> Bevaka Sveriges auktionssajter på ett ställe – realtidsflöde, budbevakning med
> push-notiser, sökbevakning och AI-värdering baserad på faktiska slutpriser.

Auktiomera.se (tidigare *Auktionsbevakning*) är en öppen PWA som aggregerar och
bevakar annonser från Sveriges vanligaste auktionssajter. Ingen inloggning krävs
för att titta och söka. För att **bevaka** ett objekt eller skapa en
**sökbevakning** måste användaren installera appen (PWA) och godkänna
push-notiser – bevakningar är enhetsbaserade.

---

## Innehåll
- [Funktioner](#funktioner)
- [Auktionssajter (källor)](#auktionssajter-källor)
- [Teknisk stack](#teknisk-stack)
- [Arkitektur](#arkitektur)
- [Projektstruktur](#projektstruktur)
- [Datamodeller](#datamodeller)
- [API-endpoints](#api-endpoints)
- [Bakgrundsprocesser](#bakgrundsprocesser)
- [Avgiftsberäkning (Att betala)](#avgiftsberäkning-att-betala)
- [Push-notiser & PWA](#push-notiser--pwa)
- [Enhets- & kontosystem (Namn + PIN)](#enhets--kontosystem-namn--pin)
- [Integrationer](#integrationer)
- [Miljövariabler](#miljövariabler)
- [Lokal utveckling](#lokal-utveckling)

---

## Funktioner

- **Live-flöde** – paginerat flöde (100 objekt/sida) med "frozen snapshot": ordningen
  och objektmängden fryses när du öppnar flödet så inget hoppar mellan sidorna
  medan du bläddrar. Avslutade objekt ligger kvar och markeras "Avslutad";
  flödet uppdateras först vid omladdning, källbyte, sökning eller synk.
- **Riktig webbskrapning** utan att användaren behöver logga in.
- **Budbevakning** – push-notis vid nya (högre) bud och flerstegs sluttidsvarningar
  (<60 min, <10 min, sista minuten). Idempotent sändning hindrar dubbletter.
- **Sökbevakning (Keyword Watch)** – bevaka en sökterm; push-notis när nya träffar
  dyker upp i auktionssystemen (kontroll var 4:e timme).
- **Nedräkningsklocka & "Slutpris"** – avslutade bevakade objekt visar faktiskt
  slutpris (hämtas från `sold_items`-arkivet).
- **AI-värdering** – uppskattat slutpris baserat på faktiska avslutade försäljningar
  (inkl. Auctionets arkiv), via Emergent Universal Key.
- **Exakt avgiftsberäkning** – slagavgift per auktionshus + expeditionsavgift, med
  moms uppdelat i "Att betala"-sammanställningen.
- **Enhetsmigrering** – kontonamn + 4-siffrig PIN för att återställa bevakningar på
  en ny enhet.
- **Svenskt, modernt och professionellt UI** (mörkt tema).

---

## Auktionssajter (källor)

Totalt **12 källor**. Alla stöder bevakning av objekt.

### Bakgrundsbevakade (crawlas automatiskt, syns i live-flödet)
| # | Källa | Not |
|---|-------|-----|
| 1 | Klaravik | |
| 2 | Budi | |
| 3 | Fabeo | |
| 4 | Retrade | Expeditionsavgift 250 kr |
| 5 | Netauktion | |
| 6 | Tovek | Expeditionsavgift 30 kr + moms |
| 7 | Blinto | Helkatalog-render; per-objekt-refresh sker via discover (sparar krediter) |
| 8 | Riksauktioner | |
| 9 | PS Auction | Tung hämtning via Playwright/ScrapingBee; full crawl var 72:a h, bevakade objekt uppdateras separat |

### Sök-på-begäran (hämtas live vid sökning, ej i löpande flödet)
| # | Källa | Not |
|---|-------|-----|
| 10 | Tradera | Budbevakning via `leadingBid`, sluttid via `endDate` |
| 11 | Blocket | Fastpris/"köp nu" – ingen budgivning eller sluttid |
| 12 | Auctionet | Inkl. arkiv för AI-värdering; priser tvingas till SEK |

> **Kronofogden** har tagits bort ur systemet.

---

## Teknisk stack

- **Frontend:** React, Tailwind CSS, shadcn/ui, lucide-react, sonner, PWA +
  Firebase Cloud Messaging (service worker).
- **Backend:** FastAPI (Python), asyncio bakgrundsloopar.
- **Databas:** MongoDB (Motor async-driver).
- **Skrapning:** httpx + BeautifulSoup, Playwright (PS Auction), ScrapingBee
  (proxy/WAF-bypass).
- **Push:** CommHub (FCM-baserad web push).
- **AI:** Emergent Universal Key (LLM) för värderingslogik.
- **Nyckelversioner:** fastapi 0.110.1 · motor 3.3.1 · httpx 0.28.1 ·
  playwright 1.63.0 · beautifulsoup4 4.15.0 · bcrypt 4.1.3 · pymongo 4.6.3 ·
  emergentintegrations 0.2.2.

---

## Arkitektur

```
  React PWA (frontend:3000)
        │  REACT_APP_BACKEND_URL
        ▼
  FastAPI (backend:8001, alla rutter under /api)
   ├─ API-rutter: live-flöde, bevakningar, sökbevakning, konto/PIN, push
   ├─ Bakgrundsloopar (asyncio):
   │    • crawler       – en produkt var 10:e s per sajt, vilar efter full pass
   │    • auto-refresh  – bevakade objekt var 30:e min
   │    • notifier      – bud/sluttidsnotiser var 60:e s (idempotent)
   │    • keyword watch – var 4:e h
   │    • startup-städning (dedupe tokens, backfill ends_at/exp-avgift)
   └─ MongoDB  (live_auctions, auctions, sold_items, devices, keyword_watches,
                push_events)

  Externt: ScrapingBee (proxy) · CommHub (push/FCM) · Emergent LLM (värdering)
```

Routing: allt som börjar med `/api` går till backend (port 8001), övrigt till
frontend (port 3000). Frontend använder alltid `REACT_APP_BACKEND_URL`; backend
använder alltid `MONGO_URL`/`DB_NAME`.

---

## Projektstruktur

```
/app
├── backend/
│   ├── server.py           # FastAPI, API-rutter, enhets/PIN-scoping, bakgrundsloopar
│   ├── crawler.py          # Schemaläggare, avgiftsberäkning (compute_fees), sold_items-arkiv
│   ├── adapters.py         # Adaptrar: Klaravik, Budi, Fabeo, Retrade, Netauktion,
│   │                       #           Tovek, Blinto, Riksauktioner (+ ENABLED/SOURCE_STATUS)
│   ├── ps_playwright.py     # PS Auction (Playwright/ScrapingBee) + refresh_item
│   ├── tradera.py           # Tradera sök + refresh_item
│   ├── blocket.py           # Blocket sök (fastpris)
│   ├── auctionet.py         # Auctionet sök/arkiv + refresh_item (SEK)
│   ├── scraper.py           # Hjälpfunktioner, källmappning
│   ├── commhub.py           # Push-integration (CommHub)
│   ├── valuation.py         # AI-värdering (Emergent LLM)
│   └── .env
├── frontend/
│   ├── public/
│   │   └── firebase-messaging-sw.js   # Service worker för push
│   └── src/
│       ├── App.js
│       ├── context/push.jsx           # PWA/push-state (enhet, token)
│       ├── components/
│       │   ├── LiveFeed.jsx           # Paginerat, fruset live-flöde
│       │   ├── AuctionCard.jsx        # Kort (visar "Slutpris" på avslutade)
│       │   ├── AuctionDetail.jsx      # Detalj, avgiftsnedbrytning, budhistorik
│       │   ├── KeywordWatches.jsx     # Sökbevakning
│       │   ├── AccountTools.jsx       # Kontonamn + PIN
│       │   └── AddAuctionModal.jsx
│       └── lib/
│           ├── api.js                 # API-klient (axios)
│           └── sources.js             # Källfärger, kategorier, timeLeft()
└── memory/                            # PRD.md, CHANGELOG, test_credentials m.m.
```

---

## Datamodeller (MongoDB)

- **`live_auctions`** – aktuella objekt från crawl/sök:
  `{ url, source, title, image, current_bid, starting_price, bid_count, ends_at,
     fee_known, exp_fee_excl, total_now, first_seen, last_seen, notified_bid,
     notified_steps, search_q, vat, fee_inputs }`
- **`auctions`** – bevakade objekt (som ovan + `device_id`, unikt `id`,
  `updated_at`). Berikas dynamiskt med `final_bid`/`ends_at` från `sold_items`.
- **`sold_items`** – arkiv av avslutade försäljningar (underlag för AI-värdering):
  `{ url, source, title, image, final_bid, starting_price, ends_at, fee_known,
     category, total_now, title_text }`
- **`devices`** – `{ device_id (unikt), account_name, pin_hash (bcrypt), token }`
- **`keyword_watches`** – `{ id, device_id, keyword, last_check, seen_urls }`
- **`push_events`** – idempotensnycklar `{ event_id (unikt), created_at (TTL 14 d) }`

---

## API-endpoints

Alla prefixas med `/api`. Enhetsidentitet skickas via `X-Device-Id`-header.

### Push & PWA
- `GET  /push/config` – Firebase web-config (publik)
- `POST /push/register` – registrera FCM-token för enheten
- `GET  /push/status`

### Konto (enhetsmigrering)
- `GET  /account/name`
- `POST /account/name` – sätt kontonamn + PIN
- `POST /account/restore` – återställ bevakningar via namn + PIN

### Live-flöde
- `GET  /live/status` – crawler-status + antal objekt (bakgrundskällor)
- `GET  /live/auctions?source=&q=&page=&page_size=&snapshot=` – paginerat/fruset flöde
- `POST /live/sync` – trigga synk
- `POST /live/watch` – bevaka ett live-objekt (skapar UUID-id)
- `POST /live/analyze` – AI-värdering för ett live-objekt

### Bevakningar
- `GET    /auctions` – enhetens bevakningar (berikade med slutpris/sluttid)
- `GET    /auctions/{id}`
- `POST   /auctions` – lägg till bevakning
- `POST   /auctions/{id}/analyze` – AI-värdering
- `POST   /auctions/{id}/refresh` – uppdatera objekt
- `DELETE /auctions/{id}`

### Sökbevakning
- `POST   /keyword-watches`
- `GET    /keyword-watches` / `GET /keyword-watches/{id}`
- `DELETE /keyword-watches/{id}`

### Övrigt
- `GET    /sources` · `GET /stats` · `POST /scrape/preview` · `DELETE /sold-items`

---

## Bakgrundsprocesser (asyncio)

| Loop | Intervall | Syfte |
|------|-----------|-------|
| `crawler` | 1 objekt var 10:e s per sajt, vilar efter full pass | Håller `live_auctions` färsk utan att blockeras |
| `_watch_auto_refresh` | 30 min | Uppdaterar bevakade objekt (bud, antal bud, sluttid) – kringgår långsamma crawl-intervall (t.ex. PS Auctions 72 h) |
| `_watch_notifier` | 60 s | Bud- och sluttidsnotiser; idempotent via `push_events` |
| `_keyword_watch_loop` | 4 h | Sökbevakningar → push vid nya träffar |
| `_dedupe_device_tokens` | vid start | Säkerställer en token per enhet (mot dubbel-push) |
| `_backfill_watch_ends_at` / `_backfill_sold_exp` | vid start | Fyller i sluttider & expeditionsavgifter retroaktivt |

---

## Avgiftsberäkning (Att betala)

`compute_fees(source, bid, ctx)` i `crawler.py` beräknar "Att betala":

- **Slagavgift** per auktionshus (procent/trappa, med min-belopp).
- **Moms** (vat) där det gäller.
- **Expeditionsavgift** (fast fakturaavgift): **Tovek 30 kr + moms**, **Retrade 250 kr**.
- `total_now` = bud (inkl. moms där relevant) + slagavgift + expeditionsavgift.
- Fält för känd/okänd avgift (`fee_known`) och förklarande `fee_note`.

Budi visar priser som auktionshuset anger (exkl./inkl. moms oförändrat).

---

## Push-notiser & PWA

- **Strikt PWA-gate:** användaren måste installera appen och godkänna push för att
  kunna bevaka objekt eller skapa sökbevakning.
- **Service worker** (`firebase-messaging-sw.js`) förlitar sig på att webbläsaren
  auto-visar FCM:s `notification`-payload (ingen dubbelvisning).
- **Idempotent sändning:** varje notis "claimas" atomärt i `push_events` (unikt
  `event_id`, t.ex. `bid|{token}|{url}|{bud}`) innan den skickas. Endast en vinnare
  får skicka – skyddar mot dubbletter vid races, dubbelstartade loopar eller flera
  backend-instanser som delar databasen. (CommHub saknar native tag/collapse.)

---

## Enhets- & kontosystem (Namn + PIN)

- Ingen traditionell inloggning. Enheten får ett `device_id` (UUID i localStorage),
  som skickas via `X-Device-Id`. Bevakningar är enhetsscopade.
- För migrering till ny enhet: sätt **kontonamn + 4-siffrig PIN** (bcrypt-hashad).
  På ny enhet återställs bevakningar via `POST /account/restore` (namn + PIN).
- Vid import/återställning genereras unika `id` för objekt så att de inte krockar
  mellan enheter.

---

## Integrationer

| Tjänst | Syfte | Nyckel |
|--------|-------|--------|
| **CommHub** | Push-notiser (FCM web push) | `COMMHUB_API_KEY`, `COMMHUB_APP_ID`, `COMMHUB_BASE_URL` |
| **ScrapingBee** | Proxy/WAF-bypass vid skrapning | `SCRAPINGBEE_API_KEY` |
| **Firebase/FCM** | Service worker / push (publik web-config) | hämtas via `/api/push/config` |
| **Emergent Universal Key** | AI-värdering (LLM) | `EMERGENT_LLM_KEY` |

---

## Miljövariabler

**`backend/.env`**
```
MONGO_URL=...          # lokal MongoDB (ändra ej)
DB_NAME=...            # databasnamn (ändra ej)
CORS_ORIGINS=...
EMERGENT_LLM_KEY=...
SCRAPINGBEE_API_KEY=...
COMMHUB_API_KEY=...
COMMHUB_APP_ID=...
COMMHUB_BASE_URL=...
CRAWL_INTERVAL_SECONDS=10   # valfri, default 10
```

**`frontend/.env`**
```
REACT_APP_BACKEND_URL=...   # extern backend-URL (ändra ej)
```

Vid lokal Docker-körning läser backend variabler från `backend/.env`.
Kopiera `backend/env.example` till `backend/.env` och fyll i din
`EMERGENT_LLM_KEY` för AI-värdering. OpenAI-modellen används via Emergent;
en separat `OPENAI_API_KEY` används inte av den nuvarande integrationen.
Efter en nyckeländring behöver containern återskapas:
`docker compose up -d --no-deps --force-recreate backend`.

> Inga default-/fallback-värden i koden – saknad konfiguration ska fela tidigt.
> Lägg aldrig hemligheter i frontend-bundeln (endast Firebas publika web-config).

---

## Lokal utveckling

Tjänsterna körs via **supervisor** (ändra inte portar/kommandon):

```bash
# status / omstart (behövs bara vid .env- eller beroendeändringar; annars hot reload)
sudo supervisorctl status
sudo supervisorctl restart backend
sudo supervisorctl restart frontend

# backend-loggar
tail -n 100 /var/log/supervisor/backend.*.log
```

**Beroenden**
```bash
# backend
pip install -r backend/requirements.txt
# frontend
cd frontend && yarn install
```

**Snabbtest av API**
```bash
API=$(grep REACT_APP_BACKEND_URL frontend/.env | cut -d= -f2)
curl -s "$API/api/live/status"
curl -s "$API/api/live/auctions?page=1&page_size=100"
```

---

*Språk: svenska. Öppen app – ingen inloggning krävs för att titta och söka;
push + PWA-installation krävs endast för bevakning.*


## ChatGPT-koppling vid lokal utveckling

Öppna **ChatGPT** i sidhuvudet på `http://localhost:3000` och välj
**Continue with ChatGPT**. Logga in hos OpenAI och godkänn användning av din
ChatGPT-plan. Ett berättigat Plus- eller Pro-konto krävs. Inloggningen återvänder
till `http://127.0.0.1:8000/api/chatgpt/callback`; backend måste därför köras
på samma dator som webbläsaren. Ingen API-nyckel behövs för denna koppling.

Välj konto och modell i ChatGPT-panelen. I den lokala Compose-installationen krävs
en ChatGPT-inloggning för AI-värdering, som använder det valda kontots plan. Användning och frånkoppling hanteras
även i ChatGPT-inställningarna. OpenAI:s officiella flöde beskrivs här:
https://developers.openai.com/siwc/token-sharing-open-source

OAuth-uppgifterna lagras endast i backend, i Docker-volymen `chatgpt_data`,
med filrättigheter 0600. Behåll volymen vid ombyggnad och omstart för att
behålla samma installation och dess kopplingar. Kör endast en backendprocess
mot denna volym så att roterande refresh-token inte används samtidigt.
Kopplingen aktiveras med `CHATGPT_LOCAL_ENABLED=true` i lokal Compose.
Fjärrpublicering kräver en separat godkänd integration.
