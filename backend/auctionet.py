"""Auctionet live search (no login, public JSON endpoint).

Auctionet gathers European auction houses (art, design, antiques, collectibles).
It isn't background-crawled. Like Tradera/Blocket we query its public search JSON
on demand when a user searches in the live feed:
  https://auctionet.com/sv/search.json?q=<query>&is_buy_now=&page=<n>
Prices are in SEK. The buyer fee varies per auction house, so we show an
APPROXIMATE total (ca 25% provision inkl. moms + 80 kr slagavgift) flagged as
uppskattad (no exact "inkl. avgift"/guldmynt badge).
"""
import time
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("auctionet")

SEARCH = "https://auctionet.com/sv/search.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "sv-SE,sv;q=0.9",
}

_cache = {}  # q(lower) -> (timestamp, [items])
CACHE_TTL = 300


def _parse_end(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _item(raw):
    url = raw.get("url")
    title = (raw.get("title") or "").strip()
    if not url or not title:
        return None
    bid = raw.get("bid") or 0
    est = raw.get("estimate") or 0
    imgs = raw.get("image_urls") or {}
    image = imgs.get("full") or imgs.get("medium") or imgs.get("thumb")
    return {
        "source": "Auctionet",
        "title": title,
        "image": image,
        "current_bid": bid,
        "starting_price": est if (not bid and est) else None,
        "bid_count": raw.get("bids_count") or 0,
        "fixed_price": False,
        "ends_at": _parse_end(raw.get("ends_at")),
        "url": url,
    }


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
                r = await c.get(SEARCH, params={"q": q, "is_buy_now": "", "page": page})
                if r.status_code != 200:
                    break
                data = r.json()
                rows = data.get("items", []) if isinstance(data, dict) else []
                new = 0
                for raw in rows:
                    it = _item(raw)
                    if not it or it["url"] in seen:
                        continue
                    seen.add(it["url"])
                    items.append(it)
                    new += 1
                if new == 0 or len(items) >= max_items:
                    break
    except Exception as e:
        logger.warning("Auctionet search failed for %r: %s", q, e)
        return hit[1] if hit else []
    items = items[:max_items]
    _cache[key] = (time.time(), items)
    return items
