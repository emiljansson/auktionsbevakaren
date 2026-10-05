"""Blocket live search (no login, plain HTTP).

Blocket isn't background-crawled. Like Tradera we query its search page on
demand when a user searches in the live feed and parse the result cards.
Blocket listings are fixed-price classifieds (no bids / no end time / no fees),
so they surface as fixed-price items for price comparison.
"""
import re
import time
import logging

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("blocket")

BASE = "https://www.blocket.se"
# recommerce/forsale keyword-searches the marketplace. Using it directly avoids
# Blocket's vehicle redirect (e.g. "moped" -> /mobility/search/mc) which returns
# a whole category and ignores the keyword.
SEARCH = "https://www.blocket.se/recommerce/forsale/search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "sv-SE,sv;q=0.9",
}

_cache = {}  # q(lower) -> (timestamp, [items])
CACHE_TTL = 300


def _parse_page(html, tokens=()):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for art in soup.select("article"):
        link = art.select_one('a[href*="/item/"], a[href*="/annons/"]')
        if not link:
            continue
        url = link.get("href", "")
        if url.startswith("/"):
            url = BASE + url
        heading = art.select_one("h1, h2, h3, h4")
        title = re.sub(r"\s+", " ", heading.get_text(" ", strip=True)) if heading else ""
        if not title:
            continue
        text = re.sub(r"\s+", " ", art.get_text(" ", strip=True))
        # Keep only listings that actually match the query term(s) – Blocket
        # sometimes surfaces loosely-related items in a category.
        low = text.lower()
        if tokens and not all(t in low for t in tokens):
            continue
        pm = re.search(r"([\d][\d\s\u00a0]*)\s*kr", text)
        price = int(pm.group(1).replace("\u00a0", "").replace(" ", "")) if pm else 0
        img = art.find("img")
        image = (img.get("src") or img.get("data-src")) if img else None
        if image and image.startswith("http://"):
            image = "https://" + image[7:]
        out.append({
            "source": "Blocket",
            "title": title,
            "image": image,
            "current_bid": 0,
            "starting_price": price or None,
            "bid_count": 0,
            "fixed_price": True,
            "vat": 0,
            "buyer_fee": 0,
            "ends_at": None,
            "url": url,
        })
    return out


async def search(q, max_items=150):
    q = (q or "").strip()
    if len(q) < 2:
        return []
    key = q.lower()
    tokens = [t for t in re.split(r"\s+", key) if len(t) >= 2]
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    items, seen = [], set()
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            for page in range(1, 4):
                params = {"q": q, "page": page}
                r = await c.get(SEARCH, params=params)
                if r.status_code != 200:
                    break
                new = 0
                for it in _parse_page(r.text, tokens):
                    if not it["url"] or it["url"] in seen:
                        continue
                    seen.add(it["url"])
                    items.append(it)
                    new += 1
                if new == 0 or len(items) >= max_items:
                    break
    except Exception as e:
        logger.warning("Blocket search failed for %r: %s", q, e)
        return hit[1] if hit else []
    items = items[:max_items]
    _cache[key] = (time.time(), items)
    return items
