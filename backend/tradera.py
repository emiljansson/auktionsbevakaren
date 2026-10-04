"""Tradera live search (no login, plain HTTP).

Tradera isn't background-crawled (millions of listings). Instead we query its
search page on demand when a user searches in the live feed:
  https://www.tradera.com/search?q=<query>&page=<n>
and parse the result cards (title, price, bids, end time, image, buy-now flag).
Results are cached briefly so the feed's 10s polling doesn't refetch every tick.
"""
import re
import time
import logging
from datetime import datetime, timezone, timedelta

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("tradera")

BASE = "https://www.tradera.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "sv-SE,sv;q=0.9",
}
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12}

_cache = {}  # q(lower) -> (timestamp, [items])
CACHE_TTL = 300


def _parse_end(s):
    m = re.match(r"(\d{1,2})\s+([a-zåäö]+)\s+(\d{1,2}):(\d{2})", s.strip(), re.I)
    if not m:
        return None
    day, mon, hh, mm = int(m.group(1)), _MONTHS.get(m.group(2)[:3].lower()), int(m.group(3)), int(m.group(4))
    if not mon:
        return None
    now = datetime.now(timezone(timedelta(hours=2)))
    year = now.year
    try:
        dt = datetime(year, mon, day, hh, mm, tzinfo=timezone(timedelta(hours=2)))
    except ValueError:
        return None
    if dt < now - timedelta(days=1):
        dt = dt.replace(year=year + 1)
    return dt.astimezone(timezone.utc).isoformat()


def _parse_page(html):
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.select('a[href*="/item/"]'):
        href = a.get("href", "")
        m = re.search(r"/item/\d+/(\d+)/", href)
        if not m:
            continue
        title = a.get_text(" ", strip=True)
        if not title or m.group(1) in seen:
            continue  # skip image-only anchors / dupes
        iid = m.group(1)
        card = a
        for _ in range(4):
            if card.parent:
                card = card.parent
        text = card.get_text(" ", strip=True)
        img = card.find("img")
        image = (img.get("src") or img.get("data-src")) if img else None
        if image and image.startswith("http://"):
            image = "https://" + image[7:]
        bud = re.search(r"(\d+)\s*bud", text)
        prism = re.search(r"([\d][\d\s\u00a0]*)\s*kr", text)
        price = int(prism.group(1).replace("\u00a0", "").replace(" ", "")) if prism else 0
        endm = re.search(r"Sluttid\s*([0-9]{1,2}\s+[a-zåäö]+\s+[0-9]{1,2}:[0-9]{2})", text, re.I)
        ends_at = _parse_end(endm.group(1)) if endm else None
        bid_count = int(bud.group(1)) if bud else 0
        fixed = ("köp nu" in text.lower()) and bid_count == 0
        seen.add(iid)
        out.append({
            "source": "Tradera",
            "title": title,
            "image": image,
            "current_bid": price if bid_count > 0 else 0,
            "starting_price": price if (bid_count == 0 and price) else None,
            "bid_count": bid_count,
            "fixed_price": fixed,
            "vat": 0,
            "buyer_fee": 0,
            "ends_at": ends_at,
            "url": BASE + href if href.startswith("/") else href,
        })
    return out


async def search(q, max_items=150):
    q = (q or "").strip()
    if len(q) < 2:
        return []
    key = q.lower()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    items, seen = [], set()
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            for page in range(1, 4):
                url = f"{BASE}/search?q={httpx.QueryParams({'x': q})['x']}&page={page}"
                r = await c.get(url)
                if r.status_code != 200:
                    break
                page_items = _parse_page(r.text)
                new = 0
                for it in page_items:
                    if it["url"] in seen:
                        continue
                    seen.add(it["url"])
                    items.append(it)
                    new += 1
                if new == 0 or len(items) >= max_items:
                    break
    except Exception as e:
        logger.warning("Tradera search failed for %r: %s", q, e)
        return hit[1] if hit else []
    items = items[:max_items]
    _cache[key] = (time.time(), items)
    return items
