"""Site adapters for automatic auction discovery.

Each adapter exposes:
  - source:   display name
  - discover(): async -> list of auction dicts (title, image, current_bid, url, [ends_at], [bid_count])
  - refresh_item(url): async -> {"current_bid": int, "ends_at": iso?} or None

Only sites reachable server-side (no anti-bot proxy) are ENABLED. Sites behind
anti-bot / JS challenges (PS Auction, Blinto) or SPA sites without per-item
links (Retrade) are reported in SOURCE_STATUS and stay disabled.
"""
import os
import re
import json
import asyncio
import logging
import html as _html
from datetime import datetime, timezone, timedelta

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


SCRAPINGBEE_API_KEY = os.environ.get("SCRAPINGBEE_API_KEY")


async def _scrapingbee_get(url, render_js=True, wait=5000):
    """Fetch a page through ScrapingBee (bypasses Cloudflare/anti-bot)."""
    if not SCRAPINGBEE_API_KEY:
        raise RuntimeError("SCRAPINGBEE_API_KEY saknas")
    params = {"api_key": SCRAPINGBEE_API_KEY, "url": url, "country_code": "se"}
    if render_js:
        params["render_js"] = "true"
        params["wait"] = str(wait)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get("https://app.scrapingbee.com/api/v1/", params=params)
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

    @staticmethod
    def _product_data(html):
        """Klaravik embeds a product-data JSON on each item page with the exact
        inropsavgift (auctionFee, EXCL VAT), current bid and VAT%."""
        i = html.find("product-data='")
        if i < 0:
            return None
        start = i + len("product-data='")
        end = html.find("'", start)
        if end < 0:
            return None
        try:
            return json.loads(html[start:end])
        except Exception:
            return None

    @staticmethod
    def _page_items(html):
        """Parse one listing page's JSON-LD ItemList into item dicts. The listing
        also embeds a per-object end-time blob ("id":..,"auctionEnd":<unix>), so
        every item gets its countdown (ends_at) straight from discovery."""
        ends = {}
        for m in re.finditer(r'"id":(\d+),"timeLeft":".*?","auctionEnd":(\d+)', html):
            ends[m.group(1)] = int(m.group(2))
        il = _ld_type(BeautifulSoup(html, "lxml"), "ItemList")
        out = []
        for el in (il or {}).get("itemListElement", []):
            item = el.get("item", {}) if isinstance(el, dict) else {}
            name, url = item.get("name"), item.get("url")
            if not name or not url:
                continue
            ends_at = None
            mid = re.search(r"/produkt/(\d+)", url)
            if mid and ends.get(mid.group(1)):
                try:
                    ends_at = datetime.fromtimestamp(ends[mid.group(1)], tz=timezone.utc).isoformat()
                except Exception:
                    ends_at = None
            out.append({
                "source": "Klaravik", "title": name, "image": item.get("image"),
                "current_bid": _to_int((item.get("offers") or {}).get("price")) or 0,
                "ends_at": ends_at, "url": url,
            })
        return out

    async def discover(self):
        """Klaravik paginates its full catalogue via ?curpage=N (60 objects per
        page). Page 1 exposes the highest curpage link. We fetch pages one at a
        time with a short pause so the burst never trips Klaravik's anti-bot."""
        first = await _get(self.listing_url + "?curpage=1")
        pages = [int(m) for m in re.findall(r"curpage=(\d+)", first)]
        last = min(max(pages) if pages else 1, 120)
        out, seen = [], set()

        def collect(html):
            for it in self._page_items(html):
                if it["url"] not in seen:
                    seen.add(it["url"])
                    out.append(it)
        collect(first)
        for p in range(2, last + 1):
            await asyncio.sleep(0.3)
            try:
                collect(await _get(f"{self.listing_url}?curpage={p}"))
            except Exception:
                continue
        return out

    async def refresh_item(self, url):
        html = await _get(url)
        pd = self._product_data(html)
        if pd:
            res = {}
            bid = _to_int((pd.get("bidBox") or {}).get("bid"))
            if bid:
                res["current_bid"] = bid
            if pd.get("auctionFee") is not None:
                res["fee_excl"] = _to_int(pd["auctionFee"])
                res["fee_vat"] = 25
            if pd.get("vat") is not None:
                res["vat"] = pd["vat"]
            unix = (pd.get("closeBox") or {}).get("auctionEndTimeUnix")
            if unix:
                try:
                    res["ends_at"] = datetime.fromtimestamp(int(unix), tz=timezone.utc).isoformat()
                except Exception:
                    pass
            if res:
                return res
        prod = _ld_type(BeautifulSoup(html, "lxml"), "Product")
        if not prod:
            return None
        price, valid = _offer_price(prod)
        return {"current_bid": price, "ends_at": valid} if price else None


class BudiAdapter:
    source = "Budi"
    listing_url = "https://www.budi.se/"

    async def discover(self):
        out, seen = [], set()
        for page in range(1, 40):
            url = f"https://www.budi.se/objekt?p={page}&s=rel" if page > 1 else "https://www.budi.se/objekt?s=rel"
            try:
                soup = BeautifulSoup(await _get(url), "lxml")
            except Exception:
                break
            cards = soup.select("a.budi-auctionobject__card")
            new = 0
            for a in cards:
                if a.get("data-budi-auctionobject-isended") == "true":
                    continue
                href = a.get("href", "")
                if "/objekt/" not in href or href in seen:
                    continue
                seen.add(href)
                new += 1
                img = a.find("img")
                image = img.get("src") if img else None
                if image and "notfound" in image:
                    image = None
                title = (img.get("alt") if img else None) or "Budi-objekt"
                text = a.get_text(" ", strip=True)
                price_el = a.select_one("span.budi-auctionobject__bid-current-amount") or a.select_one("div.budi-auctionobject__summary")
                price = _price_from_text(price_el.get_text(" ", strip=True)) if price_el else None
                if price is None:
                    price = _price_from_text(text)
                out.append({
                    "source": self.source, "title": title, "image": image,
                    "current_bid": price or 0,
                    "bid_count": _bids_from_text(text),
                    "ends_at": a.get("data-budi-auctionobject-endingdatetimeiso"),
                    "url": "https://www.budi.se" + href,
                })
            if new == 0:
                break
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

    @staticmethod
    def _ends(node):
        el = node.select_one(".auction-time-countdown[data-time]")
        if not el:
            return None
        try:
            return datetime.fromtimestamp(int(el["data-time"]), tz=timezone.utc).isoformat()
        except Exception:
            return None

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
                "ends_at": self._ends(card),
            })
        return out

    async def refresh_item(self, url):
        soup = BeautifulSoup(await _get(url), "lxml")
        text = soup.get_text(" ", strip=True)
        prod = _ld_type(soup, "Product")
        price, valid = (None, None)
        if prod:
            price, valid = _offer_price(prod)
        if not price:
            price = _price_from_text(text)
        res = {}
        if price:
            res["current_bid"] = price
        ends = self._ends(soup)
        if ends:
            res["ends_at"] = ends
        # Fabeo states the exact per-object slagavgift publicly, e.g.
        # "En slagavgift på 25 000 kr (exkl. moms) tillkommer."
        m = re.search(r"slagavgift\s*p[åa]\s*([\d\s\u00a0\.]+)\s*kr\s*\(exkl", text, re.I)
        if m:
            res["fee_excl"] = _to_int(m.group(1))
            res["fee_vat"] = 25
        return res or None


class RetradeAdapter:
    source = "Retrade"
    listing_url = "https://www.retrade.eu/sv"

    @staticmethod
    def _nuxt(html):
        """Retrade is a Nuxt SSR app: every listing/detail page embeds a
        __NUXT_DATA__ payload (an index-referenced flat array). We resolve the
        auction objects (id, heading, highestBid, currency, image, place,
        auctionEnd) and the pagination block out of it."""
        tag = BeautifulSoup(html, "lxml").find("script", id="__NUXT_DATA__")
        if not tag or not tag.string:
            return [], None
        try:
            arr = json.loads(tag.string)
        except Exception:
            return [], None

        def d(i):
            return arr[i] if isinstance(i, int) and 0 <= i < len(arr) else i
        items, total_pages = [], None
        for e in arr:
            if isinstance(e, dict) and {"id", "heading", "currency"} <= set(e.keys()):
                items.append({k: d(v) for k, v in e.items()})
            elif isinstance(e, dict) and "totalPages" in e and "currentPage" in e:
                total_pages = d(e["totalPages"])
        return items, total_pages

    def _to_item(self, it):
        iid = it.get("id")
        if not iid:
            return None
        return {
            "source": self.source,
            "title": (it.get("heading") or "Retrade-objekt").strip(),
            "image": it.get("image"),
            "current_bid": _to_int(it.get("highestBid")) or 0,
            "ends_at": it.get("auctionEnd"),
            "url": f"https://www.retrade.eu/sv/auction/{iid}",
        }

    async def discover(self):
        """The default /sv listing is Nordic (SEK/DKK/NOK); we keep only the
        Swedish objects (currency == SEK) and paginate ?page=N across the whole
        Swedish catalogue."""
        first = await _get(self.listing_url + "?page=1")
        raw, total = self._nuxt(first)
        total = min(total or 1, 30)
        if total > 1:
            sem = asyncio.Semaphore(5)

            async def fetch(p):
                async with sem:
                    try:
                        return await _get(f"{self.listing_url}?page={p}")
                    except Exception:
                        return None
            for h in await asyncio.gather(*[fetch(p) for p in range(2, total + 1)]):
                if h:
                    raw += self._nuxt(h)[0]
        out, seen = [], set()
        for it in raw:
            if it.get("currency") != "SEK":
                continue
            if it.get("id") in seen:
                continue
            seen.add(it.get("id"))
            d = self._to_item(it)
            if d:
                out.append(d)
        return out

    async def refresh_item(self, url):
        raw, _ = self._nuxt(await _get(url))
        m = re.search(r"/auction/(\d+)", url)
        iid = m.group(1) if m else None
        cand = next((it for it in raw if str(it.get("id")) == iid), None) or (raw[0] if raw else None)
        if not cand:
            return None
        res = {}
        bid = _to_int(cand.get("highestBid"))
        if bid:
            res["current_bid"] = bid
        if cand.get("auctionEnd"):
            res["ends_at"] = cand["auctionEnd"]
        return res or None


class NetauktionAdapter:
    source = "Netauktion"
    base = "https://www.netauktion.se"

    async def discover(self):
        soup = BeautifulSoup(await _get(self.base + "/auktion/display/"), "lxml")
        slugs = [a["href"] for a in soup.find_all("a", href=True)
                 if re.match(r"^/auktion/[^/]+$", a["href"])]
        slugs = list(dict.fromkeys(slugs))
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
                # The listing card carries the real current bid ("Nuvarande bud X SEK",
                # also in .top-bid-<id>). Parse it directly so bids show immediately at
                # discovery instead of waiting for the slow per-item refresh.
                bid = None
                tb = card.select_one('[class*="top-bid-"]:not([class*="avatar"])')
                if tb:
                    bid = _to_int(re.sub(r"[^\d]", "", tb.get_text()))
                if not bid:
                    m = re.search(r"Nuvarande bud\s*([\d\s\u00a0\.]+)\s*(?:SEK|kr)", text, re.I)
                    bid = _to_int(m.group(1)) if m else None
                out.append({"source": self.source, "title": title, "image": image,
                            "current_bid": bid or 0,
                            "ends_at": _netauktion_ends(text),
                            "url": self.base + u if u.startswith("/") else u})
        return out

    async def refresh_item(self, url):
        html = await _get(url)
        soup = BeautifulSoup(html, "lxml")
        full = soup.get_text(" ", strip=True)
        bid = None
        el = soup.select_one(".auction-bid-box-body-price")
        if el:
            bid = _to_int(re.sub(r"[^\d]", "", el.get_text()))
        if not bid:
            m = re.search(r"Nuvarande bud\s*([\d\s\u00a0\.]+)\s*(?:SEK|kr)", full, re.I)
            bid = _to_int(m.group(1)) if m else None
        ends = _netauktion_ends(full)
        res = {}
        if bid:
            res["current_bid"] = bid
        if ends:
            res["ends_at"] = ends
        # Netauktion publishes the exact slagavgift rule per object, e.g.
        # "Slagavgift 12% exkl. moms tillkommer budet (min: 100 kr - max: 50 000 kr exkl. moms)."
        m = re.search(
            r"Slagavgift\s*(\d+)\s*%\s*exkl.*?min:\s*([\d\s\u00a0\.]+)\s*kr\s*-\s*max:\s*([\d\s\u00a0\.]+)\s*kr",
            html, re.I | re.S)
        if m:
            res["fee_pct"] = int(m.group(1))
            res["fee_min"] = _to_int(m.group(2))
            res["fee_max"] = _to_int(m.group(3))
            res["fee_vat"] = 25
        return res or None


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
TOVEK_ACT_BIDS = "7f793e0ef55011c257e2db8306e27783b561adaa5f"
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

    async def _bids_map(self, item_ids):
        """itemId -> {bid, ends} via getRecentItemBidsByItemIds (batched)."""
        out = {}
        for i in range(0, len(item_ids), 100):
            chunk = item_ids[i:i + 100]
            try:
                data = await _tovek_action(TOVEK_ACT_BIDS, [chunk])
            except Exception:
                continue
            for entry in data.get("allItemBids", []):
                iid = entry.get("itemId")
                bids = entry.get("bids") or []
                top = max((b.get("historyBidValue") or 0 for b in bids), default=0)
                out[iid] = {"bid": top, "ends": _tovek_ends(entry.get("itemEndTime"))}
        return out

    async def discover(self):
        raw, seen_parts = [], set()
        for page in range(1, 25):
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
                    if it.get("itemStatus") != "active" or not it.get("itemId"):
                        continue
                    imgs = it.get("images") or []
                    raw.append({
                        "iid": it["itemId"],
                        "title": (it.get("itemTitle") or "Objekt").strip(),
                        "image": imgs[0] if imgs else None,
                        "min": it.get("itemMinBid") or 0,
                        # Tovek exposes the exact per-object slagavgift (EXCL VAT,
                        # +25% moms) and the bid VAT% publicly on every rop.
                        "fee_excl": it.get("itemFeeValue"),
                        "vat": it.get("itemVatValue"),
                        "ends_item": _tovek_ends(it.get("itemEndTime") or it.get("itemLastEndDate")),
                    })
        bids = await self._bids_map([r["iid"] for r in raw])
        out = []
        for r in raw:
            b = bids.get(r["iid"], {})
            bid = b.get("bid") or 0
            out.append({
                "source": self.source, "title": r["title"], "image": r["image"],
                "current_bid": bid,
                "starting_price": (r["min"] or None) if not bid else None,
                "fee_excl": r["fee_excl"], "vat": r["vat"], "fee_vat": 25,
                "ends_at": b.get("ends") or r["ends_item"],
                "url": f"https://tovek.se/auktion/rop/{_slugify(r['title'])}/{r['iid']}",
            })
        return out

    async def refresh_item(self, url):
        m = re.search(r"/rop/[^/]+/(\d+)", url)
        if not m:
            return None
        iid = int(m.group(1))
        bmap = await self._bids_map([iid])
        info = bmap.get(iid)
        if not info:
            return None
        res = {"ends_at": info.get("ends")}
        if info.get("bid"):
            res["current_bid"] = info["bid"]
        return res


class PSAuctionAdapter:
    """PS Auction via headless Chromium (AWS WAF challenge solved by the browser)."""
    source = "PS Auction"

    async def discover(self):
        try:
            import ps_playwright
            return await ps_playwright.collect()
        except Exception as e:
            logging.getLogger("adapters").warning("PS Auction collect failed: %s", e)
            return []

    async def refresh_item(self, url):
        return None  # bids refreshed on each ~4h discovery pass (browser is heavy)


class BlintoAdapter:
    """Blinto via ScrapingBee (Cloudflare/anti-bot bypass with JS-rendering).

    The listing page already carries every live object with title, price,
    bid count and seconds-remaining, so one rendered fetch per discovery pass
    refreshes the whole catalogue. refresh_item is a no-op to save credits.
    """
    source = "Blinto"
    listing_url = "https://www.blinto.se/"

    async def discover(self):
        html = await _scrapingbee_get(self.listing_url, render_js=True, wait=6000)
        soup = BeautifulSoup(html, "lxml")
        ends = {i.get("id"): i.get("value")
                for i in soup.select("input.hidden-endtimes-input")}
        now = datetime.now(timezone.utc)
        out = []
        for c in soup.select("div.auction-product"):
            a = c.find("a", href=True)
            if not a or "/auction/" not in a["href"]:
                continue
            url = "https://www.blinto.se" + a["href"] if a["href"].startswith("/") else a["href"]
            img = c.find("img")
            title = None
            if img:
                title = (img.get("title") or img.get("alt") or "").strip() or None
            if not title:
                h3 = c.select_one("h3.brand-type")
                title = h3.get_text(" ", strip=True) if h3 else "Blinto-objekt"
            image = img.get("src") if img else None
            if img:
                raw_img = img.get("actualimg") or img.get("data-src") or img.get("src")
                if raw_img:
                    raw_img = raw_img.replace("_hidden_", "")
                    image = None if ("bild_laddas" in raw_img or "notfound" in raw_img) else raw_img
            price_el = c.select_one(".objectprice")
            price = None
            if price_el:
                digits = re.sub(r"[^\d]", "", price_el.get_text(" ", strip=True))
                price = int(digits) if digits else None
            bids_el = c.select_one(".bids-amount")
            bid_count = _bids_from_text(bids_el.get_text(" ", strip=True)) if bids_el else None
            ends_at = None
            oid = (c.get("id") or "").replace("card", "")
            secs = ends.get("endtime_" + oid)
            if secs and secs.isdigit() and int(secs) > 0:
                ends_at = (now + timedelta(seconds=int(secs))).isoformat()
            has_bids = bool(bid_count and bid_count > 0)
            out.append({
                "source": self.source, "title": title, "image": image,
                "current_bid": price if has_bids else 0,
                "starting_price": price if (not has_bids and price) else None,
                "bid_count": bid_count, "ends_at": ends_at, "url": url,
            })
        return out

    async def refresh_item(self, url):
        return None  # full catalogue refreshes on each discovery pass

    async def fetch_fee(self, url):
        """On-demand ONLY (watched items) – one ScrapingBee fetch (no JS, cheap).
        Blinto renders the exact per-object slagavgift into the page HTML, e.g.
        "... samt slagavgift på 17 400 SEK (exkl. moms)."."""
        html = _html.unescape(await _scrapingbee_get(url, render_js=False))
        res = {}
        m = re.search(r"slagavgift\s*p[åa]\s*([\d\s\u00a0\.]+)\s*(?:SEK|kr)\s*\(exkl", html, re.I)
        if m:
            res["fee_excl"] = _to_int(m.group(1))
            res["fee_vat"] = 25
        return res or None


class RiksauktionerAdapter:
    """Riksauktioner.se – headless WP + a JSON auction API (se01.riksauktioner.se).
    Objects are paginated at /objects; images are resolved in bulk via
    /media?ids=... so we need only ~2 dozen requests for the whole catalogue."""
    source = "Riksauktioner"
    api = "https://se01.riksauktioner.se"

    async def _json(self, path, params=None):
        async with httpx.AsyncClient(headers={**HEADERS, "Accept": "application/json"},
                                     timeout=25, follow_redirects=True) as c:
            r = await c.get(self.api + path, params=params)
            r.raise_for_status()
            return r.json()

    async def _resolve_images(self, ids):
        out = {}
        ids = [str(i) for i in ids if i]
        for i in range(0, len(ids), 80):
            batch = ids[i:i + 80]
            try:
                d = await self._json("/media", {"ids": ",".join(batch), "limit": len(batch)})
            except Exception:
                continue
            for m in d.get("data", []):
                sizes = m.get("sizes") or {}
                out[str(m.get("id"))] = sizes.get("500x500") or sizes.get("1024x1024") or m.get("url")
        return out

    async def discover(self):
        first = await self._json("/objects", {"limit": 50, "page": 1})
        pages = min(int(first.get("pages") or 1), 20)
        rows = list(first.get("data", []))
        for p in range(2, pages + 1):
            await asyncio.sleep(0.2)
            try:
                rows += (await self._json("/objects", {"limit": 50, "page": p})).get("data", [])
            except Exception:
                continue
        rows = [o for o in rows if o.get("status") == "available" and not o.get("deleted")]
        imgs = await self._resolve_images({o.get("thumbnail") for o in rows})
        out = []
        for o in rows:
            oid = o.get("id")
            if not oid:
                continue
            out.append({
                "source": self.source,
                "title": (o.get("title") or "Objekt").strip(),
                "image": imgs.get(str(o.get("thumbnail"))),
                "current_bid": _to_int(o.get("leading_bid")) or 0,
                "bid_count": o.get("num_bids_placed"),
                "ends_at": o.get("ending"),
                "vat": 0 if o.get("no_tax") == "YES" else 25,
                "url": f"https://riksauktioner.se/objekt/{oid}",
            })
        return out

    async def refresh_item(self, url):
        m = re.search(r"/objekt/(\d+)", url)
        if not m:
            return None
        try:
            d = await self._json(f"/objects/{m.group(1)}")
        except Exception:
            return None
        o = d.get("data", d) if isinstance(d, dict) else {}
        res = {}
        bid = _to_int(o.get("leading_bid"))
        if bid is not None:
            res["current_bid"] = bid
        if o.get("ending"):
            res["ends_at"] = o["ending"]
        if o.get("no_tax") is not None:
            res["vat"] = 0 if o.get("no_tax") == "YES" else 25
        return res or None


# Active adapters (reliably scrapable server-side, with per-item links)
ENABLED = [KlaravikAdapter(), BudiAdapter(), FabeoAdapter(),
           RetradeAdapter(), NetauktionAdapter(),
           TovekAdapter(), BlintoAdapter(), RiksauktionerAdapter()]
# PS Auction is crawled on a dedicated background task (see crawler._ps_loop)
# because its ScrapingBee crawl is slow and would block the per-tick loop.

# Reported to the UI so users understand coverage honestly
SOURCE_STATUS = {
    "Klaravik": {"status": "active", "note": "Automatisk bevakning aktiv."},
    "Budi": {"status": "active", "note": "Automatisk bevakning aktiv (inkl. sluttid)."},
    "Fabeo": {"status": "active", "note": "Automatisk bevakning aktiv."},
    "Retrade": {"status": "active", "note": "Automatisk bevakning aktiv (per-objekt-länkar)."},
    "Netauktion": {"status": "active", "note": "Automatisk bevakning aktiv (objekt per auktion)."},
    "Tovek": {"status": "active", "note": "Automatisk bevakning aktiv (bud, sluttid, moms & slagavgift)."},
    "PS Auction": {"status": "active", "note": "Automatisk bevakning via renderande proxy (löser AWS WAF). Uppdateras var ~4:e timme."},
    "Blinto": {"status": "active", "note": "Automatisk bevakning aktiv (via renderande proxy som löser anti-bot)."},
    "Riksauktioner": {"status": "active", "note": "Automatisk bevakning aktiv (bud, sluttid & moms via API)."},
    "Tradera": {"status": "search", "note": "Sök på begäran – skriv ett sökord så hämtas Tradera-träffar live (inget automatiskt 'hämta allt'-flöde)."},
    "Blocket": {"status": "search", "note": "Sök på begäran – skriv ett sökord så hämtas Blocket-träffar live (inget automatiskt 'hämta allt'-flöde)."},
    "Auctionet": {"status": "search", "note": "Sök på begäran – skriv ett sökord så hämtas Auctionet-träffar live (konst, design & antikt). Avgift visas som uppskattning."},
}


def adapter_for_source(source):
    for a in ENABLED:
        if a.source == source:
            return a
    return None
