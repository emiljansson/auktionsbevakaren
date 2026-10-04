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

## Nästa steg
- Riktig periodisk bevakning + notiser
- Site-adaptrar för budhistorik
