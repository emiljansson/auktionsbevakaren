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
- **Netauktion-bud fångas nu direkt vid discovery** (2026-06): listningskortet innehåller `.top-bid-<id>` / "Nuvarande bud X SEK", så budet läses direkt istället för att vänta på den långsamma per-objekt-refreshen. ~95/130 objekt visar bud omedelbart (t.ex. "Gamingskärm GTSA Infinity" = 300 kr). refresh_item behålls som färskhets-fallback.
- **PS Auction via ScrapingBee** (2026-06): Playwright-scrapern slutade fungera (AWS WAF `net::ERR_ABORTED`, 0 objekt). `ps_playwright.collect()` skrevs om till ScrapingBee (`premium_proxy=true` + `render_js` löser WAF): hämtar auktionsindex, filtrerar **pågående** auktioner (tid kvar, ej "Avslutad"), och parsar objektkort (h2-titel, cloudfront-bild, bud, antal bud, "Tid kvar" → ends_at). Cap: index + 10 auktioner/pass (~var 4:e h). Nu ~250 objekt. **Sökning funkar nu över alla källor** (t.ex. "såg" ger Blinto + PS Auction + Tovek m.fl.).
- Testat via curl + screenshot (frontend visar Blinto-kort med rätt bilder/bud/nedräkning).

## Sticky flik-meny + direkt-uppdatering av bevakningar – 2026-06 (testat iteration_8, 100%)
- **Sticky meny**: flik-menyn (Live-flöde / Mina bevakningar, `data-testid="view-toggle"`) flyttad in i `<header className="sticky top-0 z-40">` som andra rad → förblir synlig vid scroll (verifierat y=64 desktop + mobil).
- **Ny bevakning syns direkt**: `LiveFeed` får `onWatchAdded={load}` från App; `handleWatch` anropar det efter `api.liveWatch`, så App:s `auctions`+`stats` uppdateras utan omladdning. Dessutom reloadar App när man byter till 'watch'-vyn. Badge 3→4 och nya kort visas direkt (verifierat).
- Kvar (låg prio, kosmetiskt): Tradera-bild-URL:er serveras via http:// → Mixed Content-varning i konsolen.

## PS Auction full trädgenomgång + budget-styrd hybrid – uppdaterat 2026-06
- **Problem**: PS visade bara ett fåtal objekt; objekt som slutar 3-5 dagar fram (t.ex. "Cirkelsåg - Festool HK 55 EBQ-Plus" i auktionen "Överskottsgods elverktyg från Festool") saknades.
- **Rotorsaker & fix**:
  1. **Filter**: `_running_auctions` krävde nedräknings-token men multi-dygns-kort visas som "3D12H" (siffror ihop) → regex `\d+[DdHhMm]`. Nu inkluderas auktioner som slutar flera dagar fram.
  2. **Paginering**: varje auktion visar 48 objekt/sida via sökväg `/auction/{id}/{slug}/sida=N` (inte query-param). Insamlaren paginerar nu alla sidor (läser total ur "Visar X–Y av N").
  3. **Behåll objekt utan parsbar sluttid** istället för att droppa dem.
  4. **Proxy**: `premium_proxy=true` utan `render_js` (löser AWS WAF, 25 credits/sida, snabbare).
- **Budget-styrd hybrid (ScrapingBee 75k/mån)**: tidsgrindad `collect()` — FULL katalog var `PS_FULL_INTERVAL_H` (72h) + budkoll på auktioner som slutar <24h var `PS_SOON_INTERVAL_H` (24h). Grind kollas FÖRE nätverksanrop (0 credits när inget är dags). Tidsstämplar persisteras i `/app/.ps_state.json` (utanför uvicorn --reload-mappen).
- **Frikopplad bakgrundstask**: PS körs i `crawler._ps_loop` (egen asyncio-task, concurrency 10) så dess långsamma crawl aldrig blockerar övriga källor. PS borttagen ur `adapters.ENABLED`.
- **Resultat**: ~3 265 PS-objekt (full katalog), 87 Festool-objekt, Cirkelsåg HK 55 sökbar. Övriga källor opåverkade.
- Full katalog ≈ 4 675 credits; daglig full katalog (~140k/mån) ryms EJ i 75k → kräver plan-uppgradering. Nuvarande kadens håller sig inom 75k.

## Djupare täckning + "Ladda fler" + Kronofogden borttagen – uppdaterat 2026-06
- **Visningsgräns borttagen i praktiken**: `/live/auctions` hämtar upp till 20 000 docs; frontend har en **"Ladda fler"-knapp** som växer gränsen i steg om 120 (verifierat 120→240). Paging återställs vid källbyte/sökning.
- **PS Auction läser ALLA pågående auktioner** (ej längre bara 10): `PS_MAX_AUCTIONS=500`, parallellt med `asyncio.Semaphore(5)` för fart. ~21 pågående auktioner → ~340 objekt per pass.
- **Budi paginerar** via `/objekt?p=N` (alla sidor) → ~1030 objekt (mot ~210).
- **Netauktion/Tovek**: artificiella tak borttagna (Netauktion alla auktionssidor, Tovek upp till 25 sidor).
- **Kronofogden borttagen** helt (ur ENABLED, SOURCE_STATUS, frontend SOURCES; 37 docs raderade). **8 aktiva källor** nu.
- Totalt ~3 500 objekt. Klaravik (~60) och Retrade (~138) är server-renderade listor utan enkel paginering → ligger kvar på vad sidan exponerar.

## Exakta slagavgifter per objekt (där sajten exponerar det) – uppdaterat 2026-06
- Mål (användarens begäran): gräv i varje sajts insamlade objektdata och ta med slagavgiften **endast när sajten presenterar den publikt** (som Tovek).
- Avgiftsmotorn (`crawler.py compute_fees`) tar nu per-objekt-input via `FEE_INPUT_KEYS = (vat, fee_excl, fee_vat, fee_pct, fee_min, fee_max)`. `fee_inputs` persistas i `live_auctions` och återanvänds vid refresh/re-discovery (skrivs aldrig över med tomt).
- **Tovek**: `itemFeeValue` (exkl moms, +25% moms alltid) + `itemVatValue` per rop (vid discovery).
- **Klaravik**: `auctionFee` (exkl moms) + `vat` + bud ur `product-data` JSON (vid refresh).
- **Fabeo**: "slagavgift på X kr (exkl. moms)" ur objekttexten (vid refresh).
- **Netauktion**: "Slagavgift X% exkl. moms (min A – max B)" (vid refresh).
- **Budi**: 16% / min 195 kr (publicerad standard; dynamisk avgift).
- **Blinto**: exakt per-objekt-slagavgift hämtas nu **on-demand för bevakade objekt** via `BlintoAdapter.fetch_fee` (ett billigt ScrapingBee-anrop, render_js=False) som parsar "slagavgift på X SEK (exkl. moms)" ur sidan. Budget-skydd: bakgrundsloopen hämtar ALDRIG Blinto-detaljer (`refresh_item` är no-op); `crawler.learn_fee` körs bara för bevakade objekt med okänd avgift, en gång per objekt, med 6h retry-cooldown (`fee_last_try`) vid miss. Verifierat: bevakat Blinto-objekt visar "Att betala (ca)" med exakt slagavgift.
- **PS Auction / Retrade**: exakt per-objekt-avgift bakom skyddade API:er → behåller "+ avgift tillkommer"-not.
- **Mina bevakningar** (`GET /api/auctions`): berikas alltid med färskaste avgift/bud från live-datan; okända avgifter lärs in on-demand (en gång per objekt). `POST /live/watch` lär in avgiften direkt vid bevakning. Verifierat: Klaravik/Tovek/Fabeo/Netauktion visar nu "Att betala (ca)" med exakt slagavgift.
- Fixat mobil-overflow i sticky header (min-w-0 + truncate på titeln).

## PWA + per-enhet-push via CommHub – uppdaterat 2026-06
- **Push-notiser (CommHub/FCM)**: nycklar i `backend/.env` (`COMMHUB_API_KEY`, `COMMHUB_APP_ID`, `COMMHUB_BASE_URL`). Nytt `backend/commhub.py` (get_web_config, register_token, send_to_token). Auth: `X-API-Key`.
- **Appen är nu en PWA**: `public/manifest.json`, ikoner (`icon-192/512.png`), `firebase-messaging-sw.js` (bakgrundspush), meta i `index.html`.
- **Hård spärr för Bevaka**: Bevaka-knappen är låst tills appen körs i standalone (installerad) OCH pushnotiser är aktiverade. Gating-UI i `src/context/push.jsx` (modal med 2 steg) + `src/lib/push.js`. "Notiser"-knapp i headern.
- **Per-enhet-identitet (inget inlogg)**: `X-Device-Id`-header (localStorage-uuid) på alla anrop via axios-interceptor i `src/lib/api.js`. `devices`-collection lagrar {device_id, token}.
- **Bevakningar är personliga per enhet**: `GET /auctions`, `POST /live/watch`, `DELETE /auctions`, `GET /stats` är scoped på device_id. Watch kräver registrerad enhet (annars 403).
- **Migrering**: första registrerade enheten ärver alla gamla delade bevakningar (engångs).
- **Notifierare**: `_ending_notifier` i server.py pushar till rätt enhets token när en bevakad auktion har < 60 min kvar (en gång per objekt, `notified_ending`).
- VERIFIERAT: backend-flöde (registrera→bevaka→lista→isolering→migrering) via curl; PWA-gating + mobil via skärmdump. EJ verifierbart i preview: faktisk FCM-leverans till en riktig installerad enhet (kräver riktig enhet/token).

## Budhistorik-bugg fixad (2026-06)
- Bevakade objekt visade simulerad 3-punktsgraf + fel budantal. Nu: riktigt budantal från live-datan (t.ex. 37) och riktig spårad `price_history`; när < 2 punkter visas en förklarande not istället för en vilseledande graf. `_bid_history`/`_real_bid_count` i server.py; `AuctionDetail.jsx` graf-fallback.

## Bevakningslista: sortering + dedupe (2026-06)
- `GET /auctions` sorterar nu med **minst tid kvar överst** (ends_at stigande; objekt utan sluttid sist).
- `POST /live/watch` hindrar **dubbletter** per enhet (409 "Du bevakar redan denna auktion."). Unikt index `(device_id, url)` + befintliga dubbletter rensade.

## Blocket som on-demand sökkälla (2026-06)
- Nytt `backend/blocket.py` (speglar `tradera.py`): hämtar blocket.se/annonser-sök på begäran (ingen inloggning), parsar `<article>`-korten (titel, pris, bild, item-länk). Fastpris-annonser → `fixed_price=True`, `vat=0`, inga bud/avgift/sluttid.
- Integrerat i `GET /live/auctions`: augmenteras vid sökning (throttlas 120s via `_blocket_seen`), source "Blocket" exkluderas ur icke-sök-flödet (som Tradera).
- On-demand-objekt taggas med `search_q` (persistas i `_upsert`) så de visas även när titeln inte innehåller sökordet (t.ex. "Yamaha" för "moped").
- Sökresultat varvar nu källorna (round-robin) så Blocket/Tradera-fastpris syns nära toppen istället för sist.
- `sources.js`: Blocket tillagd med lime-badge. Verifierat via curl + skärmdump.

## Blocket sök-precision fixad (2026-06)
- Problem: `hela_sverige?q=moped` omdirigerade till `/mobility/search/mc` (hela MC-kategorin) och ignorerade nyckelordet → visade motorcyklar utan ordet "moped".
- Fix: `blocket.py` använder nu `recommerce/forsale/search?q=` direkt (nyckelordsmatchar som Blocket-appen) + efterfiltrering som bara behåller annonser där alla sökord finns i kort-texten. Gamla felaktiga Blocket-docs rensade.

## Flerstegs- & nytt-bud-notiser (2026-06)
- `_watch_notifier` (server.py) ersätter enkel-notisen: per enhet och bevakat objekt skickas nu
  (1) **push vid nytt bud** (när live-budet överstiger senast notifierade; spåras i `notified_bid`), och
  (2) **flerstegs-avslutsnotis** vid < 60 min, < 10 min och sista minuten. Endast det mest akuta osedda steget skickas (spåras i `notified_steps`), så användaren inte spammas.
- Verifierat end-to-end mot CommHub (ny-bud + 10-min-steg triggade korrekt).

## Säkerhetshärdning efter audit (2026-06)
Löste audit-fynden UTAN att begränsa publik funktion, plus spärr mot sök-flod:
- **SSRF (SEC-001)**: `scraper.py` blockerar nu interna/privata/loopback/link-local-IP:n (och icke-http) + validerar varje redirect-hopp. Publika auktions-URL:er fungerar som vanligt.
- **BOLA (SEC-002)**: `GET/POST(refresh,analyze)/DELETE /api/auctions/{id}` scope:ade på `device_id` (annan enhet → 404). `create_auction` kräver + lagrar device_id.
- **ReDoS/obegränsad laddning (SEC-003)**: `q` escapas (`re.escape`, max 100 tecken), `to_list` kapad 20000→5000, `limit` kapad ≤500.
- **Rate limiting (SEC-004, användarens önskemål)**: in-memory per-IP (X-Forwarded-For). Sök (q) 15/20s, scrape/refresh/create 20/60s, push_register 20/60s, live/sync 5/60s. Normal flödes-polling (utan q) opåverkad. push-token valideras (20–500 tecken).
- **Hardening**: CORS `allow_credentials=False` (identitet via header, ej cookies), generiska felmeddelanden (inga råa undantag).
- Verifierat: testagenten 23/23 gröna, inga regressioner. Regressionssvit: `/app/backend/tests/test_security_hardening.py`.
- OBS skalning: rate-limitern är per-process (OK för en instans); horisontell skalning kräver Redis-backad limiter.
- Produktion: fixarna gäller koden – publicera om (Re-publish) för att få dem live, och lägg till COMMHUB-nycklarna i Secrets.

## Djuphämtning Klaravik/Retrade + städning (2026-06)
Användarfråga: "9297 objekt och stiger – riktigt eller loopar indexeringen?" → Svar: **riktigt, ingen loop** (alla URL:er unika, 0 dubbletter). Ökningen kom från (a) bakgrundscrawlern + (b) on-demand sökträffar (Tradera/Blocket, search_q) som aldrig städades. Åtgärdat enligt användarens val (alla tre rekommenderade):
- **Klaravik djuphämtning** (`adapters.py`): `discover()` paginerar nu hela katalogen via `?curpage=N` (60 objekt/sida, upp till 120 sidor), sekventiellt med 0,3s paus för att inte trigga anti-bot (403). Resultat: **64 → ~1959 objekt**. Parsar JSON-LD ItemList per sida, dedupe på URL.
- **Retrade djuphämtning + svenskt filter** (`adapters.py`): default-listan `/sv` är nordisk (SEK/DKK/NOK). Ny `discover()` läser inline `__NUXT_DATA__`-payloaden, paginerar `?page=N` och behåller **endast currency == SEK** (= exakt 159 = Retrades sv-facet). Varje objekt får titel, bild, aktuellt bud (highestBid), sluttid (auctionEnd) och URL `/sv/auction/<id>`. `refresh_item` läser samma payload på detaljsidan.
- **Auto-städning av sökträffar** (`crawler.py _cleanup`): tar nu även bort `search_q`-dokument vars `last_seen` är > 24h (stale/engångssökningar + testskräp försvinner av sig själva). Avslutade auktioner (ends_at passerat) rensas som tidigare – bekräftat fungerande.
- Verifierat: adapter-körning (Klaravik 1959, Retrade 159 alla med ends_at), live-API curl, crawler-loggar (curpage 1..19 → 200 OK, inga 403 med sekventiell hämtning).

## Nästa steg (kvar)
- Push vid stundande avslut via externt system.
- Prisfilter/sortering i live-flödet.

## Responsiv header (2026-06)
Headern var trång på mobil (logo + notis + 3 flikar på en rad, kollision). Omgjord i `App.js`: container `flex-wrap`; rad 1 = logo/titel/"Övervakar X källor" + notis-knapp, rad 2 (mobil) = flik-kontrollen som helbredds-segmenterad kontroll (`order-last w-full`, varje flik `flex-1`). På desktop (`sm:`) återgår flikarna till en inline-rad till höger. Verifierat: ingen header-overflow på 390px eller 1920px; desktop-header 74px (en rad). Dessutom: Auto-crawler-statuskortet i live-flödet döljs på mobil (`hidden sm:block`) för att spara plats.

## Ta bort felaktiga referensobjekt i AI-värdering (2026-06)
AI:n matchade ibland fel jämförelseobjekt. Nu kan man slänga dem:
- Backend: `_find_comps` och `valuation.valuate`s `sold_comparables` inkluderar nu `url`. Nytt endpoint `DELETE /api/sold-items` (body `{url}`, device-scope:at) raderar referensobjektet permanent ur `sold_items` så det inte används i framtida värderingar.
- Frontend (`AuctionDetail`): varje rad under "Verkliga slutpriser" har nu en papperskorg (`remove-comp-*`). Klick → `api.deleteSoldItem(url)`, raden tas bort lokalt och median/spann (`sold-median-inline`) räknas om direkt från kvarvarande objekt. `removed`-set nollställs när man öppnar ett nytt objekt.
- Verifierat: delete-endpoint (deleted:1), `url` följer med i comps, och UI end-to-end (2 referensobjekt → papperskorg → 1 kvar + bekräftelse-toast + omräknad median).

## Klaravik-klocka från start + AI-knapp dold på mobil (2026-06)
- **Klaravik nedräkning från start**: listningssidan bäddar in per-objekt sluttid (`"id":..,"auctionEnd":<unix>`). `KlaravikAdapter._page_items` parsar nu detta och matchar objekt-id ur URL:en (`/produkt/<id>`) → varje Klaravik-objekt får `ends_at` direkt vid discovery (tidigare bara via refresh = 6% täckning → nu 100%). Gamla stale Klaravik-objekt utan sluttid (ej längre i listningen) rensades (engångs).
- **AI/Sparkles-knapp dold på mobil**: "Detaljer & AI"-knappen på live-korten (`detail-live-*`) får `hidden sm:inline-flex` – syns på desktop, döljs på mobil (fick inte plats). Verifierat: mobil 0 synliga + Klaravik-klocka på alla kort; desktop alla kort har knappen.

## Ny källa: Riksauktioner.se (2026-06)
Riksauktioner är en headless WordPress + Next.js-sajt med ett separat JSON auktions-API på `se01.riksauktioner.se`. Ny `RiksauktionerAdapter` (i `adapters.py`):
- `discover()`: paginerar `/objects?limit=50&page=N` (~12 sidor, 500+ objekt), behåller `status=="available"`. Fält: titel, `leading_bid`→current_bid, `num_bids_placed`→bid_count, `ending`→ends_at, per-objekt moms (`no_tax=="YES"`→0% annars 25%), objekt-URL `riksauktioner.se/objekt/{id}`.
- Bilder: bulk-upplösning via `/media?ids=...&limit=N` (≈12 anrop för hela katalogen istället för ett per objekt) → S3 500×500-URL.
- `refresh_item(url)`: `/objects/{id}` → uppdaterar bud, sluttid, moms.
- Registrerad i `ENABLED`, `SOURCE_STATUS` (active), `crawler.FEE_CONFIG`. Frontend: tillagd i `SOURCES` + teal-badge. Headern visar nu "11 källor".
- **Exakt inropsavgift (klubbavgift)**: hämtad från sajtens FAQ: 12% av klubbat pris, min 120 kr, max 25 000 kr, exkl. moms (dvs +25% moms även på avgiften). Konfigurerad i `FEE_CONFIG` som `fee_pct:12, fee_min:120, fee_max:25000` (fee_vat=25 default). Per-objekt moms via `no_tax` (0/25). `fee_known=True` → "🪙 Inkl. avgift"-badge + exakt totalpris visas. 530 befintliga objekt omräknade; nya beräknas automatiskt.
- Verifierat: 513+ objekt i flödet, alla med bild + guldmynt-badge (120/120 i filtret), korrekt total (bud 50 → 212 kr), källfilter fungerar (screenshot).

## Tradera/Blocket som källor + Sökbevakning (2026-06)
- **Tradera & Blocket i Auto-crawler-menyn**: tillagda i `adapters.SOURCE_STATUS` med status `"search"` + notis ("Sök på begäran"). Frontend `STATUS_CHIP.search` (lila chip). De syns nu i Auto-crawler-kortet och källfiltret (headern visar "10 källor"). Förblir sök-på-begäran (inget auto-crawl utan sökord).
- **Sökbevakning (keyword watch, ny flik)**: bevaka ett sökord i ALLA 10 system; var 4:e timme körs sökningen om och pushnotis skickas OM nya objekt dykt upp sedan start.
  - Collection `keyword_watches`: `{id, device_id, keyword, keyword_lower, created_at, last_check, seen_urls[], matches[{url,found_at}], unseen_count, active}`. Index: device_id + unik (device_id, keyword_lower).
  - Baslinje vid skapande: alla nuvarande träffar sparas som `seen_urls` (räknas ej som nya).
  - `server._keyword_matches(keyword)`: live-sök Tradera+Blocket (upsert) + DB-match (titel-regex ELLER search_q) → alla matchande URL:er.
  - `_keyword_watch_loop()` (startup-task): var 15:e min, kör `_run_keyword_check` för watches vars `last_check` > 4h. Nya URL:er (ej i seen_urls) → lägg i matches, öka unseen_count, pusha "🔎 Nya träffar för 'X'" via CommHub.
  - Endpoints (alla device-scope:ade, POST kräver push-token): `POST/GET/GET {id}/DELETE /api/keyword-watches`. GET {id} nollställer unseen_count och returnerar upplösta objekt (nyast först).
  - Frontend: ny flik "Sökbevakning" (`view-search`), komponent `KeywordWatches.jsx` – lägg till sökord (gated bakom PWA+push), lista med "X nya"-badge, klicka → träfflista (återanvänder `LiveCard` så man kan öppna AI-värdering + "Bevaka pris"). **Notis-badge på fliken**: App.js summerar osedda träffar över alla sökbevakningar (`kwUnseen`, pollar var 60:e sek via `api.kwList`), röd prick med antal på "Sök"-fliken (`data-testid="kw-unseen-badge"`); nollställs när man öppnar en bevakning (`onUnseenChange`).
  - Verifierat backend end-to-end (baslinje 441 moped, ny-detektering via injicerat objekt → matches/unseen ökar, CommHub push HTTP 200, GET detail upplöser objekt) och frontend (flik renderar, gating, inga kompileringsfel). Push-gated create testad med seedad enhets-token.

## AI-värdering mot egna slutpriser (2026-06)
Användarens insikt: auktionssajternas faktiska slutpriser är den mest exakta källan till begagnatvärde – bättre än externa sajter. Ny arkitektur:
- **`sold_items`-kollektion (historik)**: `crawler._cleanup` arkiverar nu varje avslutad auktion med slutpris INNAN den raderas ur flödet. `_archive_sold` gör en **exakt sista-hämtning** (adapter `refresh_item` på den nu stängda sidan) för att fånga vinnande slutbud; faller tillbaka på senast kända bud för sajter utan refresh (Blinto/PS/Tradera/Blocket). Endast objekt med bud (>0) sparas. Fält: `url, source, title, image, final_bid, total_final (inkl. moms+avgift), bid_count, ends_at, sold_at`. Index: unik `url` + text-index på `title`.
- **Similarity-sökning (smal/precis)**: `server._find_comps` + `_key_tokens` klassar titelns tokens: **modellkod** (bokstäver+siffror ≥3 tecken, t.ex. R730, FH16, EC220 – märkesspecifik, den primära diskriminatorn), **märke** (VERSALER, t.ex. SCANIA), och **svaga** (årtal, axelkonfig 8x4, V8). Generiska kategoriord ("Lastbil med schaktflak") ignoreras. En kandidat måste dela modellkoden (eller märket om ingen modellkod finns), så "Lastbil med schaktflak 8x4 SCANIA R730" matchar bara andra SCANIA R730 – inte alla lastbilar. Verifierat: R730-sök matchar även "Scania R730" (gemener) men exkluderar MERCEDES 8x4/V8; VOLVO FH16 → bara FH16.
- **`valuation.valuate(..., comps)`**: grundar `used_price_estimate` PRIMÄRT på verkliga slutpriser (confidence 70-95), annars allmän kunskap (30-60). Returnerar `sold_comparables`, `based_on_real_sales` och `sold_summary` (median_final, median_total, min/max, count). Behåller externa jämförelser (Prisjakt/Google/Blocket).
- **Nytt endpoint `POST /api/live/analyze`**: AI-värdering på VALFRITT live-objekt (ingen bevakning krävs), device-scope:at + rate-limitat, cachat på live-dokumentet i 24h för att spara credits. Befintliga bevaknings-analysen (`/auctions/{id}/analyze`) använder nu också comps.
- **Frontend**: live-korten har en Sparkles-knapp "Detaljer & AI" (`data-testid="detail-live-*"`) som öppnar detaljmodalen; `App.handleAnalyze` grenar på bevakat (id) vs live (url→`liveAnalyze`). `AuctionDetail` visar en grön sektion "Verkliga slutpriser" (`data-testid="sold-comparables"`) + badge "Baserad på verkliga auktionsslutpriser" (`data-testid="real-sales-badge"`), vid sidan av de externa jämförelserna.
- Verifierat: arkivering (8 sålda fångade med slutpris+avgifter), text-sök-matchning (Lastbil→GMC, Traktorvagn→Bredöls m.fl.), `live/analyze` end-to-end med riktigt LLM-svar (externa comps kvar, based_on_real_sales korrekt), frontend-smoke (modal öppnas från live-kort). Databasen byggs vidare automatiskt varje gång auktioner avslutas.

## Tidigare steg (kvar)
- AI-prisvärdering byggdes ovan.

## PS Auction-budget i MongoDB (2026-06)
Flyttade ScrapingBee-budgetgaten från lokal fil (`/app/.ps_state.json`, försvinner vid pod-omstart/redeploy) till MongoDB så cadencen (72h full / 24h soon) överlever omstarter:
- `ps_playwright.py`: `_load_state`/`_save_state` är nu async och läser/skriver `crawler_state`-kollektionen (`_id="ps_auction"`, fält `last_full`/`last_soon`). Ny `set_db(db)`. Import-tidens filladdning borttagen.
- `crawler.py _ps_loop`: anropar `ps_playwright.set_db(self.db)` + `await _load_state()` innan loopen.
- Befintliga filvärden migrerades in i DB (gaten förblev armerad, ålder ~12h) och `.ps_state.json` togs bort.
- Verifierat: state laddas från DB, gaten returnerar korrekt `[]` (ingen oväntad dyr full-crawl), backend frisk, inga fel.

## Fix: radera felaktiga AI-referensobjekt (soptunnan) (2026-06)
Buggrapport: "kan inte ta bort detta objekt"-toast vid klick på soptunnan (ex. Veteranbil VOLVO 145 De Luxe).
- Grundorsak: cachade AI-värderingar skapade med äldre kod saknade `url` på `sold_comparables`; frontendens delete-guard (`if (!c.url)`) blockerade då raderingen direkt. Nuvarande kod sparar `url` korrekt – endast gamla 24h-cacher var trasiga.
- Åtgärd 1 (engångsrensning): nollställde `valuation`/`valuation_at` på live/auctions-dokument vars `sold_comparables` saknade `url`, så nästa analys räknas om med `url`. Verifierat: Volvo 145-analys ger nu 6 comps alla med `url`.
- Åtgärd 2 (robust radering): `DeleteSoldReq.url` nu optional + `title`/`source`; `DELETE /api/sold-items` raderar på `url` om den finns, annars `title`(+`source`), annars 400. Frontend `deleteSoldItem(comp)` + `handleRemoveComp` faller tillbaka på titel+källa och nyckar `removed`/loading på `url||title`.
- Verifierat (curl): delete via url → deleted:1, via title+source → deleted:1, utan identifierare → 400.

## Bättre AI-matchning (kategori-spärr) + Auctionet (2026-06)
**1. Kategori-spärr i `_find_comps` (server.py):** Grundorsak till felmatchning – rena sifferkoder ("145") räknas inte som modellkod, så matchningen föll tillbaka på enbart märket (VOLVO) och drog in alla VOLVO-lastbilar/traktorer. Lade till `_CATEGORY_GROUPS` + `_category(title)` som klassar titelns typ-substantiv (bil/lastbil/traktor/maskin/släp/båt/buss/husbil). Kandidater i en annan igenkänd grupp exkluderas. Verifierat: "Veteranbil VOLVO 145" (bil) → 0 lastbils-comps (tidigare 6); "Lastbil GMC Sierra" (lastbil) matchar fortfarande lastbil. Lenient: okänd kategori spärras ej.

**2. Auctionet (sök på begäran, som Tradera/Blocket):** Ny `/app/backend/auctionet.py` använder publikt JSON-API `auctionet.com/sv/search.json?q=&is_buy_now=&page=` (titel, bud, antal bud, sluttid, värdering=estimate, bild, kategori; priser i SEK). Wiring i server.py: import, `_auctionet_seen` throttle, augment-gren i `/live/auctions`, `$nin`-lista + `_keyword_matches`-loop. `adapters.SOURCE_STATUS["Auctionet"]="search"`. Frontend: tillagd i `SOURCES` + fuchsia-badge.
- **Avgift (schablon, uppskattad):** `FEE_CONFIG["Auctionet"]={vat:0, fee_pct:25, fee_add:80, approx:True}`. Ny `approx`-gren i `compute_fees` → `buyer_fee = 25%*bud + 80 kr`, `fee_known=False` (INGEN guldmynt-badge), nytt fält `fee_approx=True`, total inkluderar avgiften. `_upsert`/`_apply_res` persisterar `fee_approx`. Frontend (LiveFeed + AuctionCard) visar "att betala (ca, uppskattad avgift)" när `fee_approx`.
- Verifierat (curl): Auctionet q=rolex → 6 objekt, bud 6432 → total 8120 (=6432+1608+80), fee_known=False, fee_approx=True. Header visar "12 källor", Auctionet-chip "Sök på begäran" i statusraden.

## Fix: dubbla pushnotiser + sökbevakningars träffar försvann efter 24h (2026-06)
**Bug 1 – dubbla pushar:** Grundorsak (bekräftat via integration_expert + Firebase-dok): CommHub `/api/push/send` skickar en FCM *notification*-payload som webbläsaren visar AUTOMATISKT i bakgrunden, OCH `firebase-messaging-sw.js` `onBackgroundMessage` kallade dessutom `showNotification()` → två identiska notiser. Fix: tog bort det manuella `showNotification()`-anropet i service workern (behålls endast för loggning). FCM visar nu exakt en. OBS: slår igenom först när enhetens installerade PWA uppdaterar sin service worker (vid nästa öppning).

**Bug 2 – träffar försvann efter 24h:** `get_keyword_watch` byggde `items` från `live_auctions` via URL, men `crawler._cleanup` rensar sök-träffar (`search_q`, `last_seen < 24h`) → de matchade objekten försvann ur sökbevakningen om man inte lade prisbevakning (som kopierar till `auctions`). Fix: `_run_keyword_check` sparar nu en **snapshot** (titel, bild, källa, bud, total, sluttid, fee-flaggor) per ny träff i `matches[].snapshot`; `get_keyword_watch` använder live-dokumentet om det finns (färskast bud), annars snapshotten. Verifierat (curl): watch med raderat live-dokument returnerar ändå träffen från snapshot. LiveCard renderar snapshots oförändrat.

## Auctionet slutpriser i AI-värderingen (2026-06)
Mål: grunda AI-värderingar i Auctionets stora arkiv av VERKLIGA slutpriser (konst/design/antikt/klockor/samlarobjekt), on-demand.
- `auctionet.py`: ny `fetch_ended(q)` använder `search.json?is=ended&q=` – realiserat klubbslag = JSON-fältet `bid`. Returnerar ENDAST sålda (>=1 bud, bid>0) som sold-comp-dicts (`final_bid`, `total_final`=bud+25%+80, `bid_count`, `ends_at`). Cache 5 min.
- `server._ingest_auctionet_sold(title)`: bygger en kort query från `_key_tokens` (märke+modell), hämtar Auctionets sålda och upsertar i `sold_items` (samma schema som `_archive_sold`). Körs on-demand i BÅDA analys-endpoints (`/auctions/{id}/analyze` och `/live/analyze`) precis före `_find_comps`, så AI:n väger in dem. Resultatet cachas 24h på objektet.
- Kategori-spärr utökad med "leksak"-grupp (modellbil/modellfordon/leksaksbil/plåtbil/modell) så en riktig "veteranbil" inte matchas mot Auctionets leksaks-/modellbilar.
- Verifierat (curl): analys av Auctionet-objekt "Rolex viklås" → 8 sold_comparables alla från Auctionet, `based_on_real_sales=true`, med verkliga slutpriser (951–18 285 kr). Backend frisk.

## Enhetsåterställning: "Ladda mina bevakningar" (2026-06)
Kontext: preview och produktion använder SEPARATA databaser. En användare byggde bevakningar i preview, gick live (tom prod-DB) och behövde få över dem. localStorage `ab_device_id` är per-origin, så preview- och live-enhets-id skiljer sig.
- Backend: `POST /api/account/import {from_device_id}` (server.py) kopierar `auctions` + `keyword_watches` från ett annat device_id till det nuvarande (header `X-Device-Id`), dedupe på url / keyword_lower. Kräver ej push-token. Idempotent.
- Frontend: Panel i "Mina bevakningar"-vyn (App.js) som visar nuvarande `getDeviceId()` ("Denna enhets kod") + knapp "Ladda mina bevakningar" (device-koden är hårdkodad på knappen per användarens önskemål). Ny `api.importWatches(fromId)`.
- Verifierat i PROD (curl): import 17 bevakningar + 6 sökbevakningar till användarens iPhone-device `9ea92875-...`; push registrerad. Preview-test: 17/6 kopierat, andra körning 0/0 (idempotent).
- Not: Kan tas bort på begäran. Den hårdkodade koden `f8d3d881-...` i knappen är användarspecifik och bör generaliseras (fält) om funktionen ska vara kvar permanent.


## Lösenordslöst kontonamn för återställning (2026-06)
Ersatte den tillfälliga hårdkodade "Ladda mina bevakningar"-knappen (och `POST /api/account/import`) med en namn-baserad återställning.
- Backend (server.py): `GET /api/account/name`, `POST /api/account/name {name}` (claimar namn på enhetens `devices`-dok: `account_name` + `account_name_lower`), `POST /api/account/restore {name}` (kopierar auctions+keyword_watches från ALLA enheter med samma namn till nuvarande device, dedupe på url/keyword_lower, skiftlägesokänsligt, länkar även nuvarande enhet till namnet). Ingen push-token krävs. Idempotent.
- Frontend (App.js): I "Mina bevakningar"-vyn visas "Ditt kontonamn"-panel (input + Spara) när man HAR bevakningar. I tomt läge visas "eller skriv ditt namn om du redan har bevakningar" (input + Hämta) som kör restore. Nya api-metoder: getAccountName/setAccountName/restoreByName. Tog bort importWatches + getDeviceId-användning.
- Verifierat (curl, preview): sätt namn på enhet med 17+6 -> ny tom enhet restore via namn (skiftlägesokänsligt) -> 17 bevakningar + 6 sökbevakningar. Frontend kompilerar utan fel.
- Not: namnet är en enkel identifierare utan lösenord (appen är medvetet lösenordslös). Kräver ompublicering för att synas på live.


## 4-siffrig PIN-kod på kontonamn (2026-06)
Lade till valfritt PIN-skydd på det lösenordslösa kontonamnet (via integration_expert-playbook: `bcrypt`-paketet direkt, inte passlib).
- Backend (server.py): `bcrypt` hash/verify via `asyncio.to_thread` (`_hash_pin_async`/`_verify_pin_async`, rounds=12). Ny `accounts`-kollektion `{name, name_lower, pin_hash}`. `POST /account/name {name, pin?}` sätter/äger namn (fel/saknad PIN på ett PIN-skyddat namn -> 403). `POST /account/restore {name, pin?}` kräver rätt PIN om namnet är skyddat (403 annars), importerar sedan. Båda rate-limitade (`account_name` 15/60, `account_restore` 10/60). `GET /account/name` returnerar `{name, has_pin}`.
- Frontend (App.js): PIN-fält (4 siffror, numeriskt) i "Ditt kontonamn"-panelen (+ "🔒 Skyddat med PIN-kod"-indikator) och i tomt-läge-återställningen. api.setAccountName/restoreByName tar nu (name, pin). Visar serverns felmeddelande vid fel PIN.
- Verifierat (curl, preview): sätt namn+PIN 1234 -> restore utan PIN=403, fel PIN=403, rätt PIN=17 bevakningar+6 sökbevakningar. Frontend kompilerar utan fel.
- Not: 4-siffrig PIN har låg entropi (accepterat av produktägaren); skyddar vid vila (bcrypt) + rate limiting. Kräver ompublicering för live. bcrypt==4.1.3 redan i requirements.

## Refaktorering + fixar (2026-06)
1. **Avgiftsbugg i refresh_auction** (server.py): använde hårdkodad 10% slagavgift + förenklad moms och hade en MOCKAD `random`-budökning för objekt utan URL. Nu: `compute_fees(source, new_bid, doc)` (korrekt per-sajt-avgift), och HTTP 400 om URL saknas (mock borttagen). Följdbugg (fångad av testagent): `crawler` är en Crawler-INSTANS; `compute_fees` är modulnivå -> ändrade importen till `from crawler import Crawler, compute_fees`. Verifierat: Klaravik-bud 497 500 -> total 621 875 (=×1.25), fee_known=false.
2. **Delad värderingshjälpare** `_run_valuation(doc)` (server.py) används av både `/auctions/{id}/analyze` och `/live/analyze` (tog bort ~20 dubblerade rader; felhanteringen kan inte längre glida isär).
3. **App.js uppdelad**: bröt ut AccountNamePanel + RestoreByNamePanel till `/app/frontend/src/components/AccountTools.jsx` (komponenterna äger sitt eget state). Tog bort 9 useState-hooks + 2 handlers + 1 useEffect ur App.js.
- Testagent (iteration_12): 7/8 backend-pytest pass + frontend pass (rename 'Auktiomera.se', 3 flikar, tomt-läge-restore-panel). Enda kritiska (crawler.compute_fees 500) nu fixad och verifierad.

## Pushspam var 60:e sek + fel sorteringsordning (2026-06)
1. **Push varje minut** (server.py): vid `/account/restore` behölls objektets `id` oförändrat när det kopierades till en ny enhet -> två bevakningar med identiskt `id` på olika enheter. `_watch_notifier` (körs var 60:e sek) sparade notifierad-status med `update_one({"id": a["id"]})` som då träffade fel dok -> statusen sparades aldrig för den nya enheten -> samma "nytt bud"-push skickades om varje minut. Fix: (a) rad ~450 genererar nytt unikt `a["id"]=uuid4()` vid import, (b) notifieraren uppdaterar nu via `{"_id": a["_id"]}` (robust även vid ev. dubbletter). DB-migrering kördes: 0 dubbletter fanns (bara en enhet i preview). Verifierat via simulering att importerad kopia får färskt unikt id.
2. **Fel tidsordning i Mina bevakningar** (server.py `list_auctions`): sorterade `ends_at` som ren text (`or "9999"`); blandade ISO-format (`...Z` vs `...+02:00`) gav icke-kronologisk ordning. Fix: `docs.sort(key=lambda d: _parse_iso(d.get("ends_at")) or far)`. Verifierat (curl): 17 bevakningar nu i stigande sluttid, "Thule Flaksläp Obromsat" (07:41 UTC) korrekt placerad mellan 07:31 och 08:09.
- Not: pushspam-fixen kan ej e2e-reproduceras i preview (commhub + flera enheter krävs); grundorsak & fix verifierade via kodanalys + simulering. Kräver ompublicering för live.

## Push för BORTTAGNA objekt / kvarvarande spam (2026-06)
Djupare orsak upptäckt efter användarrapport ("får push även på saker jag tagit bort"): vid ominstallation av PWA får enheten ett NYTT `device_id` men FCM-token är ofta samma. Den gamla `devices`-posten ligger kvar med samma token OCH sina bevakningar. Radering på nuvarande enhet tar bara bort den egna kopian → gamla enhetens kopia finns kvar och notifieraren skickar push till samma token (telefonen) – även för objekt användaren tagit bort.
- Fix 1 (`push_register`, server.py): en push-token hör till EN telefon. Vid registrering nollställs (`$unset`) token på alla andra `devices` med samma token → bara nuvarande enhet pushar. Spökenheter slutar pusha direkt.
- Fix 2 (`delete_auction`): raderar även kopior av samma url på device_ids som delar telefonens token, så radering verkligen stoppar notiser.
- Fix 3 (`_watch_notifier`): dedupar sända pushar per (token, url) per varv som extra skydd.
- Verifierat (curl + DB-simulering mot live backend): ny enhet registrerar delad token -> gammal enhets token nollställd; radering tar bort alla kopior (2 -> 0); backend startar rent.
- VIKTIGT: fixarna ligger i preview och tar effekt på LIVE först efter OMPUBLICERING. Efter ompublicering: öppna appen en gång på telefonen så kör push/register och nollställer automatiskt token på gamla spökenheter -> spammet upphör. Ingen manuell prod-DB-städning behövs.
- Fix 4 (startup-sweep `_dedupe_device_tokens`): körs automatiskt vid backend-start (idempotent) och nollställer token på alla utom nyaste enheten per delad token. Därmed städas BEFINTLIGA spökenheter i produktion direkt vid ompublicering, utan att användaren ens behöver öppna appen. Verifierat i preview: 3 enheter med delad token -> bara nyaste behåller token ("cleared token on 2 stale devices").
- Not: prod-Atlas kunde ej nås från preview-containern (IP-allowlist/timeout), därför löstes prod-städningen via startup-sweepen istället för direkt DB-åtkomst.
- Produktbeslut (användare): EXAKT en notis per nytt bud önskas; ingen 15-min-dämpning/throttle. "Återupprepande" syftade på att SAMMA bud-notis upprepades (buggen), inte en notis per riktigt bud. Kärnfixarna (token-konsolidering + persistens via _id) ger redan en notis per höjt bud utan upprepning. Verifierat via simulering (pass1 ingen, pass2 SEND 700, pass3 ingen upprepning, pass4 SEND 900). Kräver ompublicering för live.

## Slagavgift visades inkl. moms – ska vara exkl. moms (2026-06)
Användare: "Toveks slagavgift är 400kr och inte 500kr som det står i gränssnittet" (med fakturabevis: Slagavgift 400,00 exkl. moms). Appen multiplicerade Toveks `itemFeeValue` (400 exkl) med 1,25 och visade 500 (inkl. moms) under etiketten "Slagavgift" – Toveks faktura kallar 400 (exkl. moms) för slagavgift. Rent presentationsfel: totalen var korrekt men avgiftssiffran matchade inte fakturan.
- Backend (`crawler.compute_fees`): returnerar nu `buyer_fee_excl` (= slagavgift exkl. moms) vid sidan av `buyer_fee` (inkl. moms, används i total). Noten ändrad från "slagavgift 500 kr (inkl. moms)" till "slagavgift 400 kr + 25% moms". Propagerat i alla persistens-punkter (crawler update/insert/_apply_res, server refresh_auction, watch_live, list_auctions-enrichment) + nytt fält `buyer_fee_excl` i Auction-modellen.
- Frontend (`AuctionDetail.jsx`): metriken heter nu "Slagavgift (exkl. moms)" och visar `buyer_fee_excl` (fallback: `buyer_fee/1,25`).
- DB-backfill (preview): satte `buyer_fee_excl` + ny not på 1962 live_auctions + 8 auctions som saknade fältet (avslutade objekt crawlas ej om). Thule-objektet: buyer_fee=500, buyer_fee_excl=400, not korrekt.
- Verifierat (API + skärmdump): Thule-detaljvyn visar "Slagavgift (exkl. moms) 400 kr", totalpris inkl. moms 3 625 kr. Gäller alla sajter (slagavgift visas nu exkl. moms, som auktionshusen själva anger).
- Kvarstår (ej rapporterat, ej åtgärdat): (a) appens sista kända bud var 2 500 kr men vinnande klubbslag blev 2 600 kr (appen missade sista budet på avslutat objekt); (b) Toveks expeditionsavgift (~30 kr + moms) ingår inte i totalen. Kräver ompublicering för live.

## Avslutade bevakningar: ingen "Avslutad"-stämpel, låg kvar för alltid, fel ordning (2026-06)
Användare (livesiten): avslutade objekt låg kvar under Bevakning, utan klocka och utan "Avslutad"-stämpel; en Blocket-annons utan klocka låg oförklarligt sist. Rotorsak: `watch_live` sparade aldrig `ends_at` på bevakningen; när objektet slutade raderades dess `live_auctions`-post → bevakningen tappade sin sluttid → `timeLeft()` i frontend fick null → ingen klocka/stämpel, och ingen städning kunde veta att det tagit slut. Blocket-annonser saknar sluttid helt (radannonser) → naturligt ingen klocka.
Val (användare): auto-ta-bort 24h efter avslut; avslutade sorteras lägre. Fix:
- `watch_live` (server.py): sparar nu `ends_at` vid bevakningstillfället.
- `crawler._cleanup`: när ett objekt slutar stämplas final `ends_at`+bud/total på alla bevakningskopior (via url) INNAN live-posten raderas; dessutom tas bevakningar bort som slutade för >24h sedan (endast de MED sluttid → Blocket/no-end rörs aldrig).
- `list_auctions` (server.py): om live-post saknas hämtas `ends_at`/bud/avgift från `sold_items`-arkivet (läs-tid) så "Avslutad"-stämpeln + sortering funkar direkt även för redan raderade objekt. Ny sortering: aktiva (snart först) → avslutade (senast avslutad först) → utan sluttid (Blocket) sist.
- Startup-task `_backfill_watch_ends_at` (server.py): fyller `ends_at` på gamla bevakningar från `sold_items` → självläker de 2 föräldralösa objekten på livesidan vid ompublicering.
- Verifierat: API-simulering (aktiv/avslutad-2h/avslutad-48h/Blocket) ger rätt ordning + ends_at-återställning; backfill återställer B,C (ej Blocket); 24h-städning tar bort endast 48h-objektet; UI-skärmdump: 6 "Avslutad"-stämplar syns, aktiva först, Blocket sist. Kräver ompublicering för live.
