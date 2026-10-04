"""Verify Netauktion current_bid bug fix.

Previously, the listing card's 'Marknadsvärde' (estimate) was parsed as the
price, inflating current_bid dramatically. The fix sets current_bid=0 on
discovery and reads real 'Nuvarande bud' from each product page on refresh.

Ground truth: live product page '.auction-bid-box-body-price' /
'Nuvarande bud <N> SEK'. We also confirm our stored value is NOT the
'Marknadsvärde' estimate on the same page.
"""
import os
import re
import time

import httpx
import pytest
import requests
from bs4 import BeautifulSoup

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
API = f"{BASE_URL}/api"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
}

NOT_FAIL_SOURCES = {"Tovek", "PS Auction", "Blinto"}


def _to_int(s):
    s = re.sub(r"[^\d]", "", s or "")
    return int(s) if s else None


def _fetch_items(source="Netauktion", limit=200, want_with_bids=3, max_wait=180):
    """Fetch items. Poll (without re-syncing — sync re-runs discovery which
    temporarily resets Netauktion current_bid=0 for all items by design).
    """
    deadline = time.time() + max_wait
    items, with_bid = [], []
    while True:
        r = requests.get(f"{API}/live/auctions",
                         params={"source": source, "limit": limit}, timeout=30)
        assert r.status_code == 200, r.text
        items = r.json()
        with_bid = [it for it in items if (it.get("current_bid") or 0) > 0]
        if len(with_bid) >= want_with_bids or time.time() >= deadline:
            return items, with_bid
        time.sleep(15)


def _scrape_product(url):
    """Return (current_bid_int, marknadsvarde_int) from the live product page."""
    with httpx.Client(headers=HEADERS, timeout=25, follow_redirects=True) as c:
        html = c.get(url).text
    soup = BeautifulSoup(html, "lxml")
    bid = None
    el = soup.select_one(".auction-bid-box-body-price")
    if el:
        bid = _to_int(el.get_text())
    text = soup.get_text(" ", strip=True)
    if not bid:
        m = re.search(r"Nuvarande bud\s*([\d\s\u00a0\.]+)\s*(?:SEK|kr)", text, re.I)
        bid = _to_int(m.group(1)) if m else None
    mv = None
    m2 = re.search(r"Marknadsv[äa]rde[^\d]*([\d\s\u00a0\.]+)\s*kr", text, re.I)
    if m2:
        mv = _to_int(m2.group(1))
    return bid, mv


# ---------- Netauktion price fix ----------
def test_netauktion_has_items_and_some_bids():
    items, with_bid = _fetch_items(want_with_bids=3)
    assert len(items) >= 10, f"Expected >=10 Netauktion items, got {len(items)}"
    assert len(with_bid) >= 2, (
        f"Only {len(with_bid)}/{len(items)} Netauktion items have current_bid>0; "
        f"background crawler may need more time."
    )


def test_netauktion_current_bid_matches_product_page():
    """For Netauktion items with current_bid>0, our value must equal the
    product page 'Nuvarande bud' and must NOT equal 'Marknadsvärde'."""
    _, with_bid = _fetch_items(want_with_bids=3)
    sample = with_bid[:5]
    assert len(sample) >= 2, f"Need at least 2 items with bid>0, got {len(sample)}"

    mismatches = []
    equal_to_estimate = []
    verified = 0
    for it in sample:
        url = it["url"]
        our_bid = int(it["current_bid"])
        try:
            page_bid, mv = _scrape_product(url)
        except Exception as e:
            print(f"skip {url}: {e}")
            continue
        print(f"{it['title']}: our={our_bid} page_nuvarande={page_bid} marknadsvarde={mv} url={url}")
        if page_bid is None:
            # Likely auction ended between fetches; skip
            continue
        verified += 1
        if our_bid != page_bid:
            mismatches.append((it["title"], our_bid, page_bid, url))
        if mv is not None and our_bid == mv:
            equal_to_estimate.append((it["title"], our_bid, mv, url))

    assert verified >= 2, f"Only verified {verified} items against live pages"
    assert not mismatches, f"Stored current_bid != product page 'Nuvarande bud': {mismatches}"
    assert not equal_to_estimate, f"current_bid equals Marknadsvärde (regression!): {equal_to_estimate}"


def test_netauktion_current_bid_not_in_estimate_range():
    """Sanity: no Netauktion item should have an absurdly large current_bid
    typical of a Marknadsvärde estimate (e.g. > 100 000 kr for the small lots)."""
    items, _ = _fetch_items(want_with_bids=3)
    huge = [it for it in items if (it.get("current_bid") or 0) >= 100000]
    # If any item >= 100k, cross-check against product page bid
    bad = []
    for it in huge[:5]:
        try:
            page_bid, _ = _scrape_product(it["url"])
        except Exception:
            continue
        if page_bid is not None and it["current_bid"] != page_bid:
            bad.append((it["title"], it["current_bid"], page_bid))
    assert not bad, f"Suspiciously high current_bid vs page: {bad}"


def test_netauktion_ends_at_present_and_parseable():
    items, _ = _fetch_items(want_with_bids=3)
    with_ends = [it for it in items if it.get("ends_at")]
    assert len(with_ends) >= len(items) * 0.6, (
        f"Only {len(with_ends)}/{len(items)} Netauktion items have ends_at"
    )
    iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?([+-]\d{2}:?\d{2}|Z)?$")
    bad = [it["ends_at"] for it in with_ends if not iso_re.match(it["ends_at"])]
    assert not bad, f"Unparseable ends_at values: {bad[:5]}"


# ---------- Regression ----------
def test_live_status_running_and_netauktion_no_error():
    r = requests.get(f"{API}/live/status", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d.get("running") is True, d
    sites = {s["source"]: s for s in d.get("sites", [])}
    assert "Netauktion" in sites
    net = sites["Netauktion"]
    assert not net.get("error"), f"Netauktion error: {net.get('error')}"


@pytest.mark.parametrize("source", ["Klaravik", "Budi", "Retrade"])
def test_other_sources_regression(source):
    r = requests.get(f"{API}/live/auctions",
                     params={"source": source, "limit": 20}, timeout=30)
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1, f"No items for {source}"
    for it in items:
        assert it.get("title"), it
        assert it.get("url"), it
    positive = sum(1 for it in items if (it.get("current_bid") or 0) > 0)
    assert positive >= max(1, len(items) // 2), (
        f"Only {positive}/{len(items)} {source} items have current_bid>0"
    )
