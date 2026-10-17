"""PS Auction collector via ScrapingBee (premium/residential proxy).

PS Auction sits behind an AWS WAF challenge; ScrapingBee's premium proxy returns
the real HTML (no JS rendering needed, 25 credits/page). The site tree is:
  /auctions                     -> lists all auctions (1 page)
  /auction/{id}/{slug}/sida=N   -> 48 items per page (paginated)
  /item/view/{id}               -> single item

Because every page costs 25 ScrapingBee credits and there are ~3300 live items,
we crawl on a BUDGET-AWARE, TIME-GATED cadence (checked BEFORE any network call
so the crawler can poll cheaply):
  * FULL pass (every PS_FULL_INTERVAL_H, default 24h): every running auction,
    fully paginated -> captures ALL items incl. ones ending days out.
  * SOON pass (every PS_SOON_INTERVAL_H, default 12h): only auctions ending
    within PS_SOON_WINDOW_H (default 24h) -> keeps bids fresh where it matters.
  * Otherwise returns [] without touching the network (0 credits).
"""
import os
import re
import time
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("ps_playwright")

INDEX_URL = "https://psauction.se/auctions"
BASE = "https://psauction.se"
MAX_AUCTIONS = int(os.environ.get("PS_MAX_AUCTIONS", "500"))
MAX_ITEMS = int(os.environ.get("PS_MAX_ITEMS", "8000"))
CONCURRENCY = int(os.environ.get("PS_CONCURRENCY", "10"))
PAGE_SIZE = 48  # PS Auction shows 48 items per auction page (/sida=N)
MAX_PAGES = int(os.environ.get("PS_MAX_PAGES", "20"))

FULL_INTERVAL = int(os.environ.get("PS_FULL_INTERVAL_H", "72")) * 3600
SOON_INTERVAL = int(os.environ.get("PS_SOON_INTERVAL_H", "24")) * 3600
SOON_WINDOW = int(os.environ.get("PS_SOON_WINDOW_H", "24")) * 3600

SCRAPINGBEE_API_KEY = os.environ.get("SCRAPINGBEE_API_KEY")
_API = "https://app.scrapingbee.com/api/v1/"

# Compact countdown tokens: "3D12H", "14H13M", "45M", "2D", "5H".
_TIME_RE = re.compile(r"\d+[DdHhMm]")

_last_full = 0.0
_last_soon = 0.0

# Budget gate timestamps live in MongoDB (collection `crawler_state`, _id
# "ps_auction") so the 72h/24h crawl cadence survives pod restarts/redeploys
# where a local file would be lost. set_db() is called once by the crawler.
_db = None
_STATE_ID = "ps_auction"


def set_db(db):
    global _db
    _db = db


async def _load_state():
    global _last_full, _last_soon
    if _db is None:
        return
    try:
        doc = await _db.crawler_state.find_one({"_id": _STATE_ID})
        if doc:
            _last_full = float(doc.get("last_full", 0))
            _last_soon = float(doc.get("last_soon", 0))
    except Exception:
        logger.exception("PS state load failed")


async def _save_state():
    if _db is None:
        return
    try:
        await _db.crawler_state.update_one(
            {"_id": _STATE_ID},
            {"$set": {"last_full": _last_full, "last_soon": _last_soon}},
            upsert=True,
        )
    except Exception:
        logger.exception("PS state save failed")


async def _bee(url):
    if not SCRAPINGBEE_API_KEY:
        raise RuntimeError("SCRAPINGBEE_API_KEY saknas")
    params = {"api_key": SCRAPINGBEE_API_KEY, "url": url, "premium_proxy": "true", "country_code": "se"}
    async with httpx.AsyncClient(timeout=160, follow_redirects=True) as c:
        r = await c.get(_API, params=params)
        r.raise_for_status()
        return r.text



async def _local_fetch(url):
    """Fetch rendered HTML using local Chromium."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(locale="sv-SE")
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            selector = (
                'a[href*="/auction/"]'
                if url.rstrip("/") == INDEX_URL
                else 'a[href*="/item/view/"]'
            )

            await page.locator(selector).first.wait_for(
                state="attached",
                timeout=30000,
            )

            return await page.content()
        finally:
            await browser.close()


async def _fetch(url):
    """Prefer local Chromium; optionally fall back to ScrapingBee."""
    try:
        return await _local_fetch(url)
    except Exception as exc:
        logger.warning("PS local fetch failed %s: %s", url, exc)
        if SCRAPINGBEE_API_KEY:
            return await _bee(url)
        raise



@asynccontextmanager
async def _browser_session():
    """Reuse one Chromium browser for a collection pass."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(locale="sv-SE")
        try:
            yield context
        finally:
            await context.close()
            await browser.close()


async def _session_fetch(context, url):
    """Fetch rendered HTML using the shared Chromium browser."""
    page = await context.new_page()
    try:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        if response and response.status >= 400:
            raise RuntimeError(
                f"PS Auction returned HTTP {response.status}: {url}"
            )

        path = url.split("?", 1)[0].rstrip("/")

        if path == INDEX_URL.rstrip("/"):
            selector = 'a[href*="/auction/"]'
        elif re.search(r"/auction/\d+/[^/]+(?:/sida=\d+)?$", path):
            selector = 'a[href*="/item/view/"]'
        elif re.search(r"/item/view/\d+", path):
            selector = None
        else:
            selector = None

        if selector:
            await page.locator(selector).first.wait_for(
                state="attached",
                timeout=30000,
            )
        else:
            await page.locator("body").wait_for(
                state="attached",
                timeout=10000,
            )

        return await page.content()
    finally:
        await page.close()


async def _session_fetch_with_fallback(context, url):
    try:
        return await _session_fetch(context, url)
    except Exception as exc:
        logger.warning("PS Chromium failed %s: %s", url, exc)
        if SCRAPINGBEE_API_KEY:
            return await _bee(url)
        raise


def _parse_exact(s):
    m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", (s or "").strip())
    return f"{m.group(1)}T{m.group(2)}:00+02:00" if m else None


def _ends_from_timeleft(s):
    if not s or re.search(r"avslut", s, re.I):
        return None
    d = int((re.search(r"(\d+)\s*d", s, re.I) or [0, 0])[1]) if re.search(r"(\d+)\s*d", s, re.I) else 0
    h = int((re.search(r"(\d+)\s*h", s, re.I) or [0, 0])[1]) if re.search(r"(\d+)\s*h", s, re.I) else 0
    m = int((re.search(r"(\d+)\s*m", s, re.I) or [0, 0])[1]) if re.search(r"(\d+)\s*m", s, re.I) else 0
    if d == 0 and h == 0 and m == 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(days=d, hours=h, minutes=m)).isoformat()


def _card_seconds(txt):
    d = int((re.search(r"(\d+)[Dd]", txt) or [0, 0])[1]) if re.search(r"(\d+)[Dd]", txt) else 0
    h = int((re.search(r"(\d+)[Hh]", txt) or [0, 0])[1]) if re.search(r"(\d+)[Hh]", txt) else 0
    m = int((re.search(r"(\d+)[Mm]", txt) or [0, 0])[1]) if re.search(r"(\d+)[Mm]", txt) else 0
    return (d * 86400 + h * 3600 + m * 60) or 10 ** 12


def _running_auctions(html):
    """Return [(seconds_left, url)] for running auctions, soonest-ending first."""
    soup = BeautifulSoup(html, "lxml")
    seen, cand = set(), []
    for a in soup.select('a[href*="/auction/"]'):
        m = re.search(r"/auction/\d+/[^?#\"]+", a.get("href", ""))
        if not m:
            continue
        url = BASE + m.group(0)
        if url in seen:
            continue
        txt = a.get_text(" ", strip=True)
        if re.search(r"avslutad", txt, re.I) or not _TIME_RE.search(txt):
            continue
        seen.add(url)
        cand.append((_card_seconds(txt), url))
    cand.sort(key=lambda x: x[0])
    return cand


def _total_items(html):
    m = re.search(r"Visar\s*[\d\u2013\-]+\s*av\s*(\d+)", html)
    return int(m.group(1)) if m else None


def _parse_items(html):
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.select('a[href*="/item/view/"]'):
        href = a.get("href", "")
        mid = re.search(r"/item/view/(\d+)", href)
        if not mid or mid.group(1) in seen:
            continue
        full = a.get_text(" ", strip=True)
        if re.search(r"\bavslutad\b", full, re.I):
            continue  # ended item
        tl = re.search(r"Tid kvar:\s*([0-9]+\s*[DdHhMm][0-9DdHhMmSs\s]*)", full)
        ends_at = _ends_from_timeleft(tl.group(1).strip() if tl else None)
        if not ends_at:
            dm = re.search(r"(\d{4}-\d{2}-\d{2})[\sT]+(\d{2}:\d{2})", full)
            ends_at = _parse_exact(f"{dm.group(1)} {dm.group(2)}") if dm else None
        seen.add(mid.group(1))
        h2 = a.find("h2")
        img = a.find("img")
        title = (h2.get_text(" ", strip=True) if h2 else None) or (img.get("alt") if img else None) or "PS Auction-objekt"
        bidm = re.search(r"([\d][\d\s\u00a0]*)\s*SEK", full)
        bid = int(bidm.group(1).replace("\u00a0", "").replace(" ", "")) if bidm else 0
        cntm = re.search(r"SEK\s*(\d+)\b", full)
        out.append({
            "source": "PS Auction", "title": title,
            "image": img.get("src") if img else None,
            "current_bid": bid, "bid_count": int(cntm.group(1)) if cntm else None,
            "ends_at": ends_at,
            "url": BASE + href if href.startswith("/") else href,
        })
    return out


async def _crawl_auction(au, sem, context):
    async def page(url):
        async with sem:
            try:
                return await _session_fetch_with_fallback(context, url)
            except Exception as e:
                logger.warning("PS page failed %s: %s", url, e)
                return None
    html = await page(f"{au}/sida=1")
    if not html:
        raise RuntimeError(f"Första sidan kunde inte hämtas: {au}")

    items = _parse_items(html)
    total = _total_items(html)

    if total is not None and total > MAX_PAGES * PAGE_SIZE:
        raise RuntimeError(
            f"Auktionen har {total} objekt, över sidgränsen "
            f"{MAX_PAGES * PAGE_SIZE}: {au}"
        )

    if total and total > PAGE_SIZE:
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        rest = await asyncio.gather(
            *[page(f"{au}/sida={p}") for p in range(2, pages + 1)]
        )

        if any(h is None for h in rest):
            raise RuntimeError(f"En eller flera undersidor misslyckades: {au}")

        for h in rest:
            items.extend(_parse_items(h))

    if total is not None and len(items) < total:
        raise RuntimeError(
            f"Ofullständig auktion {au}: {len(items)} av {total} objekt"
        )

    return items


async def collect():
    """Budget-aware, time-gated PS Auction crawl. Returns item records (maybe [])."""
    global _last_full, _last_soon
    now = time.time()
    if now - _last_full >= FULL_INTERVAL:
        mode = "full"
    elif now - _last_soon >= SOON_INTERVAL:
        mode = "soon"
    else:
        return []  # gated — no network, no credits
    try:
        async with _browser_session() as context:
            index_html = await _session_fetch_with_fallback(
                context, INDEX_URL
            )
            running = _running_auctions(index_html)

            if not running:
                logger.warning("PS index returned no running auctions")
                return []

            if mode == "soon":
                targets = [
                    u for s, u in running if s <= SOON_WINDOW
                ][:MAX_AUCTIONS]
            else:
                targets = [u for _, u in running][:MAX_AUCTIONS]

            logger.info(
                "PS Auction %s pass: %s auctions",
                mode, len(targets)
            )

            sem = asyncio.Semaphore(min(CONCURRENCY, 2))
            outcomes = await asyncio.gather(
                *[
                    _crawl_auction(au, sem, context)
                    for au in targets
                ],
                return_exceptions=True,
            )

            failures = [
                result for result in outcomes
                if isinstance(result, BaseException)
            ]
            if failures:
                for error in failures:
                    logger.warning(
                        "PS auction crawl failed: %s", error
                    )
                raise RuntimeError(
                    f"{len(failures)} av {len(targets)} auktioner "
                    "kunde inte hämtas komplett"
                )

            chunks = outcomes
    except Exception as e:
        logger.warning("PS collection failed: %s", e)
        return []
    results, seen = [], set()
    for ch in chunks:
        for it in ch:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            results.append(it)

    if len(results) > MAX_ITEMS:
        logger.warning(
            "PS collection exceeds MAX_ITEMS: %s > %s; "
            "refusing to mark the crawl complete",
            len(results), MAX_ITEMS,
        )
        return []

    # Only arm the time-gate after a successful crawl, so an interrupted run
    # retries instead of locking out for the full interval.
    if mode == "soon":
        _last_soon = now
    else:
        _last_full = _last_soon = now
    await _save_state()
    return results[:MAX_ITEMS]



async def refresh_item(url):
    """Refresh a single PS Auction item using local Chromium."""
    html = None

    for attempt in range(2):
        try:
            async with _browser_session() as context:
                html = await _session_fetch_with_fallback(
                    context, url
                )
            break
        except Exception as e:
            logger.warning(
                "PS refresh_item failed %s (try %s): %s",
                url, attempt + 1, e
            )
            if attempt == 0:
                await asyncio.sleep(3)

    if not html:
        return None

    txt = BeautifulSoup(html, "lxml").get_text(" ", strip=True)

    bm = (
        re.search(
            r"Ledande bud\s*([\d\s\u00a0]+?)\s*SEK",
            txt
        )
        or re.search(
            r"Högsta bud\s*([\d\s\u00a0]+?)\s*SEK",
            txt
        )
    )

    if not bm:
        return None

    bid = int(
        bm.group(1).replace("\u00a0", "").replace(" ", "")
    )

    res = {"current_bid": bid}

    cm = re.search(r"(\d+)\s*bud\b", txt)
    if cm:
        res["bid_count"] = int(cm.group(1))

    return res
