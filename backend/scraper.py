"""Best-effort real web scraping for Swedish auction sites.

Attempts to fetch and parse an auction page using Open Graph tags, JSON-LD
and Swedish price heuristics. When a site blocks the request or required data cannot be
found, an error is returned instead of inventing auction data.
"""
import re
import json
import socket
import ipaddress
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin

import httpx
from bs4 import BeautifulSoup

SOURCES = {
    "psauction": "PS Auction",
    "klaravik": "Klaravik",
    "blinto": "Blinto",
    "tovek": "Tovek",
    "netauktion": "Netauktion",
    "retrade": "Retrade",
    "budi": "Budi",
    "fabeo": "Fabeo",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}



def detect_source(url: str) -> str:
    host = (urlparse(url).netloc or url).lower()
    for key, name in SOURCES.items():
        if key in host or key in url.lower():
            return name
    return "Okänd källa"


def _parse_price(text: str):
    if not text:
        return None
    # Normalise Swedish number formats: "12 500 kr", "12.500", "1 299,00 SEK"
    m = re.findall(r"(\d[\d\s\.\u00a0]*\d|\d)", text.replace("\u00a0", " "))
    if not m:
        return None
    raw = m[0].replace(" ", "").replace(".", "")
    try:
        return int(raw)
    except ValueError:
        return None


def _find_jsonld(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict):
                return it
    return {}


def _extract(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    def meta(prop):
        t = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        return t.get("content") if t and t.get("content") else None

    title = meta("og:title")
    if not title and soup.title:
        title = soup.title.string
    title = (title or "").strip()

    image = meta("og:image")

    jsonld = _find_jsonld(soup)
    price = None
    offers = jsonld.get("offers") if isinstance(jsonld, dict) else None
    if isinstance(offers, dict):
        price = offers.get("price") if offers.get("price") is not None else offers.get("highPrice")
    if price is not None:
        price = _parse_price(str(price))

    if price is None:
        price = _parse_price(meta("product:price:amount") or "")

    if price is None:
        body_text = soup.get_text(" ", strip=True)
        m = re.search(r"(?:bud|pris|utrop)[^0-9]{0,20}(\d[\d\s\.\u00a0]{2,})\s*(?:kr|sek)", body_text, re.I)
        if m:
            price = _parse_price(m.group(1))

    bid_count = None
    body_text = soup.get_text(" ", strip=True)
    bm = re.search(r"(\d+)\s*bud", body_text, re.I)
    if bm:
        bid_count = int(bm.group(1))

    return {
        "title": title or None,
        "image": image,
        "current_bid": price,
        "bid_count": bid_count,
        "ends_at": jsonld.get("endDate") or (offers.get("availabilityEnds") if isinstance(offers, dict) else None),
    }


def _build_record(url: str, data: dict, scraped: bool) -> dict:
    from crawler import compute_fees
    source = detect_source(url)
    bid = data.get("current_bid")
    if not data.get("title") or bid is None:
        raise ValueError("Kunde inte läsa objektets titel och pris. Prova länken i live-flödet.")
    fees = compute_fees(source, bid, data)
    return {
        **data, **fees, "source": source, "current_bid": bid,
        "bid_count": data.get("bid_count") or 0, "currency": "SEK",
        "ends_at": data.get("ends_at"), "url": url, "scraped": scraped,
    }


def _is_blocked_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved
            or a.is_multicast or a.is_unspecified)


def _assert_public_url(url: str):
    """Block SSRF to internal/private hosts while still allowing any public URL."""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("blocked scheme")
    host = p.hostname
    if not host:
        raise ValueError("no host")
    for info in socket.getaddrinfo(host, None):
        if _is_blocked_ip(info[4][0]):
            raise ValueError("blocked host")


def scrape_auction(url: str = None, keyword: str = None) -> dict:
    """Synchronous scrape. Returns a normalised auction record."""
    if not url:
        raise ValueError("Ange en auktionslänk. Använd live-flödet för att söka efter annonser.")
    try:
        with httpx.Client(headers=HEADERS, timeout=12, follow_redirects=False) as c:
            cur, resp = url, None
            for _ in range(4):
                _assert_public_url(cur)
                r = c.get(cur)
                loc = r.headers.get("location")
                if r.is_redirect and loc:
                    cur = urljoin(cur, loc)
                    continue
                resp = r
                break
            if resp is None:
                raise ValueError("För många omdirigeringar.")
            resp.raise_for_status()
            return _build_record(url, _extract(cur, resp.text), True)
    except (httpx.HTTPError, OSError) as exc:
        raise ValueError("Kunde inte hämta auktionssidan. Försök igen senare.") from exc


def generate_bid_history(current_bid: int, bid_count: int, created_at: str = None) -> list:
    """Only the current observation is known; never fabricate historical bids."""
    if not current_bid:
        return []
    return [{"timestamp": created_at or datetime.now(timezone.utc).isoformat(),
             "bid": current_bid}]
