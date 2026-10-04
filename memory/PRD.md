# PRD – Auktionsbevakning.se

## Problemformulering (original)
Ett system som kollar våra vanligaste auktionssiter i Sverige och bevakar budhistoriken och tar fram nypris eller uppskattar begagnatpris med hjälp av AI-agent. Använt t.ex. prisjakt.se och Google för att hitta liknande produkt.

## Användarval
- Källor: PS Auction, Klaravik, Blinto, Tovek, Netauktion, Retrade, Budi, Fabeo, Kronofogden
- AI: Emergent Universal Key (gpt-5.4) för prisuppskattning
- Riktig web-scraping (best-effort med realistisk fallback)
- Ingen inloggning (öppen app)
- Svenska, modernt proffsigt gränssnitt (mörkt tema)

## Arkitektur
- Backend: FastAPI (/app/backend/server.py), MongoDB (motor). Moduler: scraper.py (httpx+BeautifulSoup, källdetektering, budhistorik), valuation.py (emergentintegrations LlmChat).
- Frontend: React + Tailwind + shadcn/ui + framer-motion + recharts. App.js dashboard, komponenter: AuctionCard, AddAuctionModal, AuctionDetail.

## Användarpersonas
- Begagnatköpare/handlare som bevakar svenska auktioner och vill veta om budet är ett fynd.

## Implementerat (2026-06)
- Lägg till bevakning via URL (riktig scraping) eller sökord
- Dashboard med statistik, källfilter, auktionskort med nedräkning/totalpris
- Detaljvy med budhistorik-graf (Recharts) och moms-toggle
- AI-prisanalys: nypris, begagnatpris, konfidens, marknadsposition, jämförbara produkter (Prisjakt/Google/Blocket), svensk insikt
- Uppdatera bud (refresh), ta bort bevakning
- Endpoints: /api/sources, /api/auctions (GET/POST), /api/auctions/{id} (GET/DELETE), /analyze, /refresh, /stats, /scrape/preview
- Testat 100% backend + frontend (iteration_1)

## Backlog
- P1: Automatisk periodisk re-scrape/bevakning (cron) + notiser när bud höjs
- P1: Site-specifika scraper-adaptrar för exaktare budhistorik per källa
- P2: Kategori-/prisfilter och sortering i dashboard
- P2: Mer relevanta objektbilder (hämta per produkttyp)
- P2: Rate limiting på /analyze (öppen app)

## Automatisk bevakning (crawler) – tillagt 2026-06
- Bakgrunds-crawler (`crawler.py`) hämtar 1 objekt var 10:e sekund (skonsam takt) och roterar genom källorna; kör periodisk discovery (var 10:e min) + observerar prisändringar → `price_history`.
- Site-adaptrar (`adapters.py`): **Klaravik aktiv** (riktig JSON-LD-skrapning, ~60 live-objekt). PS Auction = kräver renderande skrap-tjänst (SCRAPER_API_KEY), Blinto = blockerad (premium anti-bot), övriga 6 = planerade adaptrar.
- Endpoints: GET /api/live/status, GET /api/live/auctions, POST /api/live/sync, POST /api/live/watch.
- Frontend: vy-växlare "Live-flöde" / "Mina bevakningar"; live-grid auto-uppdateras var 10:e s, källstatus-chips, "Synka nu", "Bevaka" sparar till mina bevakningar.

## Flera källor + per-sajt-crawler – uppdaterat 2026-06
- Crawlern kör nu **per sajt**: varje aktiv sajt hämtar ett objekt var 10:e sekund och vilar sedan 4h efter en full pass (`CRAWL_REST_SECONDS`). Fångar `ends_at` (sluttid) per objekt.
- **Aktiva adaptrar (ingen proxy): Klaravik, Budi, Fabeo** (riktiga titlar, priser, bilder, per-objekt-länkar; Budi även sluttid + antal bud).
- Planerade (nåbara men kräver mer arbete): Retrade (SPA utan direktlänkar), Netauktion, Kronofogden, Tovek.
- Kräver proxy/nyckel: PS Auction (renderande skrap-tjänst), Blinto (premium anti-bot).
- Frontend live-kort visar nedräkning + antal bud; källstatus-chips speglar ovan.

## Sex aktiva källor + rensning – uppdaterat 2026-06
- **Aktiva adaptrar (ingen proxy): Klaravik, Budi, Fabeo, Retrade, Netauktion, Kronofogden.** ~470 objekt hämtas automatiskt.
  - Retrade: per-objekt-länkar (`/sv/auction/<id>`) ur SSR-HTML + ItemList (pris/sluttid).
  - Netauktion: itererar auktionssidor, parsar objektkort (`open_product_card_div`), per-objekt-länkar + pris/bud.
  - Kronofogden: listar aktuella exekutiva försäljningar (titel/datum/länk); budgivning sker externt (auction2000.se) så inget bud visas ("Exekutiv försäljning").
- **Rensning**: crawler tar bort live-objekt vars `ends_at` passerat (var ~5 min), så bara pågående visas.
- Kvar: Tovek (kräver inloggning), PS Auction (renderande nyckel), Blinto (premium anti-bot).

## Nästa steg
- Exakt totalpris för Tovek (moms + slagavgift finns i datan)
- Push vid stundande avslut via externt system (användaren fixar nyckel)
- PS Auction via skrap-nyckel; prisfilter/sortering i live-flödet

## Tovek aktiverad utan inloggning – uppdaterat 2026-06
- Reverse-engineerade Toveks Next.js **Server Actions** (anropas server-side, ingen inloggning):
  - `getAuctions([{partStatus:"running"}, sida])` → pågående auktioner (partId).
  - `getAuctionItems([partId])` → objekt med `itemWinningBidValue` (bud), `itemEndTime`, moms, slagavgift, bild.
  - URL: `tovek.se/auktion/rop/{slug}/{itemId}`. ~160 objekt.
- **7 aktiva källor nu:** Klaravik, Budi, Fabeo, Retrade, Netauktion, Kronofogden, Tovek. Testat 100% (iteration_5).

## Blinto aktiverad via ScrapingBee – uppdaterat 2026-06 (ALLA 9 KÄLLOR AKTIVA)
- **Blinto** scrapas nu via **ScrapingBee** (`SCRAPINGBEE_API_KEY` i backend/.env) med `render_js=true` som löser Cloudflare/anti-bot.
  - `BlintoAdapter.discover()` (adapters.py): ett renderat anrop mot `blinto.se` ger hela katalogen (~1587 live-objekt) med titel, bild, aktuellt bud, antal bud och sluttid.
  - Sluttid beräknas från `input.hidden-endtimes-input` (sekunder kvar → `ends_at`). Pris läses ur `.objectprice` (SEK). Bild läses ur `actualimg` (lazy-load; `_hidden_`-prefix strippas) med fallback till `src`.
  - `refresh_item` = no-op (hela katalogen uppdateras per discovery-pass) för att spara ScrapingBee-krediter (1 anrop/pass istället för 1587).
- **9 aktiva källor:** Klaravik, Budi, Fabeo, Retrade, Netauktion, Kronofogden, Tovek, PS Auction, Blinto.
- **Netauktion/Tovek-bud verifierat**: bud synkas korrekt (Tovek-traktor visar 47 000 kr; ~86/130 Netauktion och ~85/164 Tovek har bud; 0-bud-objekt visar utropspris eller "Inga bud ännu"). Netauktion fyller bud gradvis via refresh (1 objekt/10s).
- Testat via curl + screenshot (frontend visar Blinto-kort med rätt bilder/bud/nedräkning).

## Nästa steg (kvar)
- AI-prisvärdering mot riktig Toolab-data (valuation.py är idag LLM-genererad/uppskattad).
- Push vid stundande avslut via externt system.
- Prisfilter/sortering i live-flödet.
