"""Site adapters for automatic auction discovery.

Each adapter exposes:
  - source:   display name
  - discover(): async -> list of auction dicts (title, image, current_bid, url)
  - refresh_item(url): async -> {"current_bid": int} or None

Only sites that are reliably reachable server-side are ENABLED. Sites behind
anti-bot / JS challenges (PS Auction, Blinto) are reported in SOURCE_STATUS with
instructions and stay disabled until a rendering scraper key is configured.
"""
import os
import json
import re

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _to_int(v):
    if v is None:
        return None
    try:
        return int(round(float(str(v).replace(" ", "").replace(",", "."))))
    except (ValueError, TypeError):
        return None


def _itemlist(soup):
    for t in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(t.string or "{}")
        except Exception:
            continue
        arr = d if isinstance(d, list) else [d]
        for it in arr:
            if isinstance(it, dict) and it.get("@type") == "ItemList":
                return it.get("itemListElement", [])
    return []


def _product(soup):
    for t in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(t.string or "{}")
        except Exception:
            continue
        arr = d if isinstance(d, list) else [d]
        for it in arr:
            if isinstance(it, dict) and it.get("@type") == "Product":
                return it
    return None


class KlaravikAdapter:
    source = "Klaravik"
    listing_url = "https://www.klaravik.se/auktion/"

    async def _get(self, client, url):
        r = await client.get(url)
        r.raise_for_status()
        return r.text

    async def discover(self):
        out = []
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            html = await self._get(c, self.listing_url)
        soup = BeautifulSoup(html, "lxml")
        for el in _itemlist(soup):
            item = el.get("item", {}) if isinstance(el, dict) else {}
            name = item.get("name")
            url = item.get("url")
            if not name or not url:
                continue
            price = _to_int((item.get("offers") or {}).get("price"))
            out.append({
                "source": self.source,
                "title": name,
                "image": item.get("image"),
                "current_bid": price or 0,
                "url": url,
            })
        return out

    async def refresh_item(self, url):
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            html = await self._get(c, url)
        prod = _product(BeautifulSoup(html, "lxml"))
        if not prod:
            return None
        price = _to_int((prod.get("offers") or {}).get("price"))
        return {"current_bid": price} if price else None


# Active adapters (reliably scrapable server-side)
ENABLED = [KlaravikAdapter()]

# Reported to the UI so users understand coverage honestly
SOURCE_STATUS = {
    "Klaravik": {"status": "active", "note": "Automatisk bevakning aktiv."},
    "PS Auction": {"status": "needs_key", "note": "Nåbar endast via renderande skrap-tjänst (Cloudflare). Lägg till SCRAPER_API_KEY för att aktivera."},
    "Blinto": {"status": "blocked", "note": "Anti-bot blockerar även renderande verktyg. Kräver premium-proxy."},
    "Tovek": {"status": "planned", "note": "Adapter ej byggd ännu."},
    "Netauktion": {"status": "planned", "note": "Adapter ej byggd ännu."},
    "Retrade": {"status": "planned", "note": "Adapter ej byggd ännu."},
    "Budi": {"status": "planned", "note": "Adapter ej byggd ännu."},
    "Fabeo": {"status": "planned", "note": "Adapter ej byggd ännu."},
    "Kronofogden": {"status": "planned", "note": "Adapter ej byggd ännu."},
}


def adapter_for_source(source):
    for a in ENABLED:
        if a.source == source:
            return a
    return None
