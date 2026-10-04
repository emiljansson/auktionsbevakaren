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

## Nästa steg
- Push vid stundande avslut via externt system (användaren fixar nyckel senare)
- Adaptrar för Retrade/Netauktion/Kronofogden/Tovek
- Rensa avslutade/inaktuella live-objekt (Budi roterar startsidan → DB växer)
