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


def _netauktion_ends(text):
    m = re.search(r"Avslutas:?\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}(?::\d{2})?)", text or "")
    if not m:
        return None
    t = m.group(2)
    if len(t) == 5:
        t += ":00"
    return f"{m.group(1)}T{t}+02:00"


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


class RetradeAdapter:
    source = "Retrade"
    listing_url = "https://www.retrade.eu/sv"

    async def discover(self):
        soup = BeautifulSoup(await _get(self.listing_url), "lxml")
        cards = soup.select('a[href*="/auction/"]')
        il = _ld_type(soup, "ItemList")
        items = (il or {}).get("itemListElement", [])
        out = []
        for i, a in enumerate(cards):
            href = a.get("href", "")
            if not re.search(r"/auction/\d+", href):
                continue
            url = "https://www.retrade.eu" + href if href.startswith("/") else href
            img = a.find("img")
            title = (img.get("alt") if img else None) or "Retrade-objekt"
            image = img.get("src") if img else None
            price, ends = 0, None
            if i < len(items):
                it = items[i].get("item", {}) if isinstance(items[i], dict) else {}
                p, ends = _offer_price(it)
                price = p or 0
                if it.get("name"):
                    title = it["name"]
                if it.get("image"):
                    image = it["image"]
            out.append({"source": self.source, "title": title, "image": image,
                        "current_bid": price, "ends_at": ends, "url": url})
        return out

    async def refresh_item(self, url):
        soup = BeautifulSoup(await _get(url), "lxml")
        prod = _ld_type(soup, "Product")
        if prod:
            price, valid = _offer_price(prod)
            if price:
                return {"current_bid": price, "ends_at": valid}
        price = _price_from_text(soup.get_text(" ", strip=True))
        return {"current_bid": price} if price else None


class NetauktionAdapter:
    source = "Netauktion"
    base = "https://www.netauktion.se"

    async def discover(self):
        soup = BeautifulSoup(await _get(self.base + "/auktion/display/"), "lxml")
        slugs = [a["href"] for a in soup.find_all("a", href=True)
                 if re.match(r"^/auktion/[^/]+$", a["href"])]
        slugs = list(dict.fromkeys(slugs))[:20]
        out = []
        for slug in slugs:
            try:
                page = await _get(self.base + slug)
            except Exception:
                continue
            s = BeautifulSoup(page, "lxml")
            for div in s.select(".open_product_card_div[url]"):
                u = div.get("url")
                if not u:
                    continue
                img = div.find("img")
                card = div.find_parent("article") or div
                tt = card.select_one(".object-card-title-text")
                title = tt.get_text(" ", strip=True) if tt else None
                if not title:
                    alt = (img.get("alt") or img.get("title")) if img else None
                    title = alt if alt and alt.lower() != "default alt text" else "Objekt"
                title = re.sub(r"\s+", " ", title).strip()
                title = re.sub(r",\s*\d+$", "", title).strip() or "Objekt"
                image = img.get("src") if img else None
                if image and image.startswith("/"):
                    image = self.base + image
                text = card.get_text(" ", strip=True)
                # The listing card only shows "Marknadsvärde" (an estimate), not the
                # current bid. Leave current_bid=0 here; refresh_item reads the real
                # "Nuvarande bud" from the product page. Capture end time from the card.
                out.append({"source": self.source, "title": title, "image": image,
                            "current_bid": 0,
                            "ends_at": _netauktion_ends(text),
                            "url": self.base + u if u.startswith("/") else u})
            if len(out) >= 300:
                break
        return out

    async def refresh_item(self, url):
        soup = BeautifulSoup(await _get(url), "lxml")
        bid = None
        el = soup.select_one(".auction-bid-box-body-price")
        if el:
            bid = _to_int(re.sub(r"[^\d]", "", el.get_text()))
        if not bid:
            m = re.search(r"Nuvarande bud\s*([\d\s\u00a0\.]+)\s*(?:SEK|kr)", soup.get_text(" ", strip=True), re.I)
            bid = _to_int(m.group(1)) if m else None
        ends = _netauktion_ends(soup.get_text(" ", strip=True))
        if bid:
            return {"current_bid": bid, "ends_at": ends}
        return {"ends_at": ends} if ends else None


class KronofogdenAdapter:
    """Kronofogden annonserar exekutiva försäljningar (mest fastigheter); själva
    nätbudgivningen sker på extern plattform. Vi listar aktuella försäljningar
    som poster (titel, datum, länk) utan bud."""
    source = "Kronofogden"
    base = "https://auktionstorget.kronofogden.se"

    async def discover(self):
        soup = BeautifulSoup(await _get(self.base + "/Aktuellaforsaljningar.html"), "lxml")
        out, seen = [], set()
        for a in soup.find_all("a", href=True):
            txt = a.get_text(" ", strip=True)
            if re.search(r"/\d+\.html", a["href"]) and re.search(r"\d{4}-\d{2}-\d{2}", txt):
                url = a["href"] if a["href"].startswith("http") else self.base + "/" + a["href"].lstrip("/")
                if url in seen:
                    continue
                seen.add(url)
                out.append({"source": self.source, "title": re.sub(r"\s+", " ", txt), "image": None,
                            "current_bid": 0, "url": url})
        return out

    async def refresh_item(self, url):
        return None


# ---- Tovek (Next.js Server Actions, no login needed) ----
TOVEK_ACT_AUCTIONS = "7f8f949b85599ee0545667288ad781b5e46ed3822d"
TOVEK_ACT_ITEMS = "7f88556099d83557895271d6f6135913755dfc8976"
TOVEK_PAGE = "https://tovek.se/auktioner/pagaende-auktioner"


async def _tovek_action(action_id, args):
    headers = {
        **HEADERS, "Origin": "https://tovek.se", "Referer": TOVEK_PAGE,
        "Next-Action": action_id, "Content-Type": "text/plain;charset=UTF-8",
        "Accept": "text/x-component",
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.post(TOVEK_PAGE, headers=headers, content=json.dumps(args))
    idx = r.text.find("\n1:")
    if idx < 0:
        return {}
    try:
        obj, _ = json.JSONDecoder().raw_decode(r.text[idx + 3:])
        return obj
    except Exception:
        return {}


def _slugify(t):
    t = (t or "objekt").lower()
    for a, b in {"å": "a", "ä": "a", "ö": "o", "é": "e", "ü": "u"}.items():
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t or "objekt"


def _tovek_ends(s):
    if not s or str(s).startswith("0000"):
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)", str(s))
    if not m:
        return None
    t = m.group(2)
    if len(t) == 5:
        t += ":00"
    return f"{m.group(1)}T{t}+02:00"


class TovekAdapter:
    source = "Tovek"

    async def discover(self):
        out, seen_parts = [], set()
        for page in range(1, 4):
            data = await _tovek_action(TOVEK_ACT_AUCTIONS, [{"partStatus": "running"}, page])
            aucs = [a for a in data.get("auctions", []) if a.get("partStatus") == "running"]
            if not aucs:
                break
            for a in aucs:
                pid = a.get("partId")
                if not pid or pid in seen_parts:
                    continue
                seen_parts.add(pid)
                try:
                    idata = await _tovek_action(TOVEK_ACT_ITEMS, [int(pid)])
                except Exception:
                    continue
                for it in idata.get("auctionItems", []):
                    if it.get("itemStatus") != "active":
                        continue
                    iid = it.get("itemId")
                    if not iid:
                        continue
                    imgs = it.get("images") or []
                    out.append({
                        "source": self.source,
                        "title": (it.get("itemTitle") or "Objekt").strip(),
                        "image": imgs[0] if imgs else None,
                        "current_bid": it.get("itemWinningBidValue") or 0,
                        "ends_at": _tovek_ends(it.get("itemEndTime") or it.get("itemLastEndDate")),
                        "url": f"https://tovek.se/auktion/rop/{_slugify(it.get('itemTitle'))}/{iid}",
                    })
            if len(out) >= 400:
                break
        return out

    async def refresh_item(self, url):
        html = (await _get(url)).replace('\\"', '"')
        bm = re.search(r'"itemWinningBidValue":(\d+)', html)
        em = re.search(r'"itemLastEndDate":"([^"]+)"', html) or re.search(r'"itemEndTime":"([^"]+)"', html)
        bid = int(bm.group(1)) if bm else None
        ends = _tovek_ends(em.group(1)) if em else None
        if bid:
            return {"current_bid": bid, "ends_at": ends}
        return {"ends_at": ends} if ends else None


# Active adapters (reliably scrapable server-side, with per-item links)
ENABLED = [KlaravikAdapter(), BudiAdapter(), FabeoAdapter(),
           RetradeAdapter(), NetauktionAdapter(), KronofogdenAdapter(), TovekAdapter()]

# Reported to the UI so users understand coverage honestly
SOURCE_STATUS = {
    "Klaravik": {"status": "active", "note": "Automatisk bevakning aktiv."},
    "Budi": {"status": "active", "note": "Automatisk bevakning aktiv (inkl. sluttid)."},
    "Fabeo": {"status": "active", "note": "Automatisk bevakning aktiv."},
    "Retrade": {"status": "active", "note": "Automatisk bevakning aktiv (per-objekt-länkar)."},
    "Netauktion": {"status": "active", "note": "Automatisk bevakning aktiv (objekt per auktion)."},
    "Kronofogden": {"status": "active", "note": "Listar aktuella försäljningar (budgivning sker externt)."},
    "Tovek": {"status": "active", "note": "Automatisk bevakning aktiv (bud, sluttid, moms & slagavgift)."},
    "PS Auction": {"status": "needs_key", "note": "Nåbar endast via renderande skrap-tjänst (Cloudflare). Lägg till SCRAPER_API_KEY för att aktivera."},
    "Blinto": {"status": "blocked", "note": "Anti-bot blockerar även renderande verktyg. Kräver premium-proxy."},
}


def adapter_for_source(source):
    for a in ENABLED:
        if a.source == source:
            return a
    return None
