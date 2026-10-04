"""Site adapters for automatic auction discovery.

Each adapter exposes:
  - source:   display name
  - discover(): async -> list of auction dicts (title, image, current_bid, url, [ends_at], [bid_count])
  - refresh_item(url): async -> {"current_bid": int, "ends_at": iso?} or None

Only sites reachable server-side (no anti-bot proxy) are ENABLED. Sites behind
anti-bot / JS challenges (PS Auction, Blinto) or SPA sites without per-item
links (Retrade) are reported in SOURCE_STATUS and stay disabled.
"""
import re
import json

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
        s = str(v).strip().replace("\u00a0", " ")
        s = s.replace(" ", "")
        if s.count(".") == 1 and len(s.split(".")[-1]) == 2:
            return int(round(float(s)))
        return int(round(float(s.replace(".", ""))))
    except (ValueError, TypeError):
        return None


def _price_from_text(t):
    if not t:
        return None
    m = re.search(r"([\d][\d\s\u00a0\.]*\d|\d)\s*kr", t, re.I)
    return _to_int(m.group(1)) if m else None


def _bids_from_text(t):
    if not t:
        return None
    m = re.search(r"(\d+)\s*bud", t, re.I)
    return int(m.group(1)) if m else None


async def _get(url):
    async with httpx.AsyncClient(headers=HEADERS, timeout=25, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.text


def _ld_blocks(soup):
    out = []
    for t in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(t.string or "{}")
        except Exception:
            continue
        out.extend(d if isinstance(d, list) else [d])
    return out


def _ld_type(soup, typ):
    for it in _ld_blocks(soup):
        if isinstance(it, dict) and it.get("@type") == typ:
            return it
    return None


def _offer_price(product):
    offers = product.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    offers = offers or {}
    return _to_int(offers.get("price")), offers.get("priceValidUntil")


class KlaravikAdapter:
    source = "Klaravik"
    listing_url = "https://www.klaravik.se/auktion/"

    async def discover(self):
        soup = BeautifulSoup(await _get(self.listing_url), "lxml")
        il = _ld_type(soup, "ItemList")
        out = []
        for el in (il or {}).get("itemListElement", []):
            item = el.get("item", {}) if isinstance(el, dict) else {}
            name, url = item.get("name"), item.get("url")
            if not name or not url:
                continue
            out.append({
                "source": self.source, "title": name, "image": item.get("image"),
                "current_bid": _to_int((item.get("offers") or {}).get("price")) or 0, "url": url,
            })
        return out

    async def refresh_item(self, url):
        prod = _ld_type(BeautifulSoup(await _get(url), "lxml"), "Product")
        if not prod:
            return None
        price, valid = _offer_price(prod)
        return {"current_bid": price, "ends_at": valid} if price else None


class BudiAdapter:
    source = "Budi"
    listing_url = "https://www.budi.se/"

    async def discover(self):
        soup = BeautifulSoup(await _get(self.listing_url), "lxml")
        out = []
        for a in soup.select("a.budi-auctionobject__card"):
            if a.get("data-budi-auctionobject-isended") == "true":
                continue
            href = a.get("href", "")
            if "/objekt/" not in href:
                continue
            img = a.find("img")
            image = img.get("src") if img else None
            if image and "notfound" in image:
                image = None
            title = (img.get("alt") if img else None) or "Budi-objekt"
            text = a.get_text(" ", strip=True)
            out.append({
                "source": self.source, "title": title, "image": image,
                "current_bid": _price_from_text(text) or 0,
                "bid_count": _bids_from_text(text),
                "ends_at": a.get("data-budi-auctionobject-endingdatetimeiso"),
                "url": "https://www.budi.se" + href,
            })
        return out

    async def refresh_item(self, url):
        prod = _ld_type(BeautifulSoup(await _get(url), "lxml"), "Product")
        if not prod:
            return None
        price, valid = _offer_price(prod)
        return {"current_bid": price, "ends_at": valid} if price else None


class FabeoAdapter:
    source = "Fabeo"
    listing_url = "https://fabeo.se/auktioner/?orderby=auction_end"

    async def discover(self):
        soup = BeautifulSoup(await _get(self.listing_url), "lxml")
        out = []
        for card in soup.select("div.product"):
            a = card.select_one("a.woocommerce-loop-product__link[href]")
            if not a:
                continue
            img = a.find("img")
            title = (img.get("alt") if img else None) or (a.get("title")) or "Fabeo-objekt"
            image = img.get("src") if img else None
            text = card.get_text(" ", strip=True)
            out.append({
                "source": self.source, "title": title, "image": image,
                "current_bid": _price_from_text(text) or 0, "url": a.get("href"),
            })
        return out

    async def refresh_item(self, url):
        soup = BeautifulSoup(await _get(url), "lxml")
        prod = _ld_type(soup, "Product")
        price, valid = (None, None)
        if prod:
            price, valid = _offer_price(prod)
        if not price:
            price = _price_from_text(soup.get_text(" ", strip=True))
        return {"current_bid": price, "ends_at": valid} if price else None


# Active adapters (reliably scrapable server-side, with per-item links)
ENABLED = [KlaravikAdapter(), BudiAdapter(), FabeoAdapter()]

# Reported to the UI so users understand coverage honestly
SOURCE_STATUS = {
    "Klaravik": {"status": "active", "note": "Automatisk bevakning aktiv."},
    "Budi": {"status": "active", "note": "Automatisk bevakning aktiv (inkl. sluttid)."},
    "Fabeo": {"status": "active", "note": "Automatisk bevakning aktiv."},
    "Retrade": {"status": "planned", "note": "Nåbar men SPA saknar direktlänkar per objekt – kräver extra arbete."},
    "Netauktion": {"status": "planned", "note": "Nåbar men listning saknar strukturerade priser per objekt – adapter ej byggd."},
    "Kronofogden": {"status": "planned", "note": "ASP.NET-söksida, mest fastigheter – adapter ej byggd."},
    "Tovek": {"status": "planned", "note": "Kräver inloggning för budvisning – adapter ej byggd."},
    "PS Auction": {"status": "needs_key", "note": "Nåbar endast via renderande skrap-tjänst (Cloudflare). Lägg till SCRAPER_API_KEY för att aktivera."},
    "Blinto": {"status": "blocked", "note": "Anti-bot blockerar även renderande verktyg. Kräver premium-proxy."},
}


def adapter_for_source(source):
    for a in ENABLED:
        if a.source == source:
            return a
    return None
