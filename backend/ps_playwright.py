"""PS Auction collector via ScrapingBee (premium proxy + JS rendering).

PS Auction sits behind an AWS WAF JS challenge. ScrapingBee's premium proxy
solves the challenge and returns the fully rendered auction/item HTML, which we
parse with BeautifulSoup. We fetch the auctions index, pick the RUNNING
auctions, then read each auction's item cards (title, image, current bid, bid
count, time left). Resource/credit-bounded: index + a capped number of auctions
per run (the crawler calls this every ~4h).
"""
import os
import re
import logging
from datetime import datetime, timezone, timedelta

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("ps_playwright")

INDEX_URL = "https://psauction.se/auctions"
BASE = "https://psauction.se"
MAX_AUCTIONS = int(os.environ.get("PS_MAX_AUCTIONS", "10"))
MAX_ITEMS = int(os.environ.get("PS_MAX_ITEMS", "500"))

SCRAPINGBEE_API_KEY = os.environ.get("SCRAPINGBEE_API_KEY")
_API = "https://app.scrapingbee.com/api/v1/"

# matches a countdown token like "14h13m", "2d 3h", "45m", "3H30M"
_TIME_RE = re.compile(r"\b\d+\s*[Dd]\b|\d+\s*[Hh]\s*\d+\s*[Mm]|\b\d+\s*[Hh]\b|\b\d+\s*[Mm]\b")


async def _bee(url, wait=12000):
    if not SCRAPINGBEE_API_KEY:
        raise RuntimeError("SCRAPINGBEE_API_KEY saknas")
    params = {
        "api_key": SCRAPINGBEE_API_KEY, "url": url, "render_js": "true",
        "premium_proxy": "true", "country_code": "se", "wait": str(wait),
    }
    async with httpx.AsyncClient(timeout=160, follow_redirects=True) as c:
        r = await c.get(_API, params=params)
        r.raise_for_status()
        return r.text


def _ends_from_timeleft(s):
    if not s or re.search(r"avslut", s, re.I):
        return None
    d = int((re.search(r"(\d+)\s*d", s, re.I) or [0, 0])[1]) if re.search(r"(\d+)\s*d", s, re.I) else 0
    h = int((re.search(r"(\d+)\s*h", s, re.I) or [0, 0])[1]) if re.search(r"(\d+)\s*h", s, re.I) else 0
    m = int((re.search(r"(\d+)\s*m", s, re.I) or [0, 0])[1]) if re.search(r"(\d+)\s*m", s, re.I) else 0
    if d == 0 and h == 0 and m == 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(days=d, hours=h, minutes=m)).isoformat()


def _running_auction_urls(html):
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
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
        out.append(url)
    return out


def _parse_items(html):
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.select('a[href*="/item/view/"]'):
        href = a.get("href", "")
        mid = re.search(r"/item/view/(\d+)", href)
        if not mid or mid.group(1) in seen:
            continue
        full = a.get_text(" ", strip=True)
        if re.search(r"avslutad", full, re.I):
            continue  # ended item
        tl = re.search(r"Tid kvar:\s*([0-9]+\s*[DdHhMm][0-9DdHhMmSs\s]*)", full)
        time_left = tl.group(1).strip() if tl else None
        ends_at = _ends_from_timeleft(time_left)
        if not ends_at:
            continue  # no valid remaining time => not an active lot
        seen.add(mid.group(1))
        h2 = a.find("h2")
        img = a.find("img")
        title = (h2.get_text(" ", strip=True) if h2 else None) or (img.get("alt") if img else None) or "PS Auction-objekt"
        bidm = re.search(r"([\d][\d\s\u00a0]*)\s*SEK", full)
        bid = int(bidm.group(1).replace("\u00a0", "").replace(" ", "")) if bidm else 0
        cntm = re.search(r"SEK\s*(\d+)\b", full)
        bid_count = int(cntm.group(1)) if cntm else None
        out.append({
            "source": "PS Auction", "title": title,
            "image": img.get("src") if img else None,
            "current_bid": bid, "bid_count": bid_count,
            "ends_at": ends_at,
            "url": BASE + href if href.startswith("/") else href,
        })
    return out


async def collect():
    """Return list of PS Auction item records. Empty list on failure."""
    try:
        index_html = await _bee(INDEX_URL)
    except Exception as e:
        logger.warning("PS index fetch failed: %s", e)
        return []
    auctions = _running_auction_urls(index_html)[:MAX_AUCTIONS]
    logger.info("PS Auction: %s running auctions to crawl", len(auctions))
    results = []
    for au in auctions:
        try:
            html = await _bee(au)
        except Exception as e:
            logger.warning("PS auction page failed %s: %s", au, e)
            continue
        results.extend(_parse_items(html))
        if len(results) >= MAX_ITEMS:
            break
    return results[:MAX_ITEMS]
