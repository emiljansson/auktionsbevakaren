"""Verify crawler._upsert bid-preservation fix (iteration_3 minor regression).

NetauktionAdapter.discover() returns current_bid=0 by design. Previously
_upsert overwrote the stored positive current_bid with 0 at each re-discovery,
temporarily wiping refreshed bids after POST /api/live/sync.

Fix: when incoming bid==0 and existing.current_bid>0, keep existing
current_bid/buyer_fee/total_now. Only title/image/last_seen/ends_at refresh.

Also re-validates the regression surface:
 - /api/live/status: running=true + 6 active sites present with no persistent error.
 - /api/live/auctions items have title/source/url.
 - Netauktion titles not 'Default Alt Text'; current_bid (when >0) == product
   page 'Nuvarande bud' (not 'Marknadsvärde').
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

ACTIVE_SITES = {"Klaravik", "Budi", "Fabeo", "Retrade", "Netauktion", "Kronofogden"}


def _to_int(s):
    s = re.sub(r"[^\d]", "", s or "")
    return int(s) if s else None


def _get_netauktion(limit=200):
    r = requests.get(f"{API}/live/auctions",
                     params={"source": "Netauktion", "limit": limit}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


def _wait_for_bids(min_count=3, max_wait=120):
    deadline = time.time() + max_wait
    while True:
        items = _get_netauktion()
        with_bid = [it for it in items if (it.get("current_bid") or 0) > 0]
        if len(with_bid) >= min_count or time.time() >= deadline:
            return items, with_bid
        time.sleep(10)


def _scrape_product(url):
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


# ---------- /api/live/status regression ----------
def test_live_status_running_with_all_six_sites():
    r = requests.get(f"{API}/live/status", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d.get("running") is True, d
    sites = {s["source"]: s for s in d.get("sites", [])}
    missing = ACTIVE_SITES - set(sites)
    assert not missing, f"Missing active sites in status: {missing}; got {list(sites)}"
    errored = {src: sites[src].get("error") for src in ACTIVE_SITES if sites[src].get("error")}
    assert not errored, f"Active sites with persistent error: {errored}"


# ---------- /api/live/auctions base shape ----------
def test_live_auctions_items_have_title_source_url():
    r = requests.get(f"{API}/live/auctions", params={"limit": 50}, timeout=30)
    assert r.status_code == 200
    items = r.json()
    assert len(items) > 0
    for it in items:
        assert it.get("title"), it
        assert it.get("source"), it
        assert it.get("url"), it


def test_netauktion_titles_not_default_alt_text():
    items = _get_netauktion()
    bad = [it for it in items if (it.get("title") or "").strip().lower() == "default alt text"]
    assert not bad, f"{len(bad)} Netauktion items have placeholder 'Default Alt Text' titles"


# ---------- Netauktion current_bid vs product page (regression) ----------
def test_netauktion_current_bid_matches_product_page_not_estimate():
    _, with_bid = _wait_for_bids(min_count=3, max_wait=120)
    sample = with_bid[:5]
    if len(sample) < 2:
        pytest.skip(f"Only {len(sample)} Netauktion items with bid>0 available")
    mismatches, equal_to_estimate, verified = [], [], 0
    for it in sample:
        try:
            page_bid, mv = _scrape_product(it["url"])
        except Exception as e:
            print(f"skip {it['url']}: {e}")
            continue
        if page_bid is None:
            continue
        verified += 1
        our = int(it["current_bid"])
        print(f"{it['title']}: our={our} page={page_bid} mv={mv}")
        if our != page_bid:
            mismatches.append((it["title"], our, page_bid, it["url"]))
        if mv is not None and our == mv:
            equal_to_estimate.append((it["title"], our, mv, it["url"]))
    assert verified >= 2, f"Only verified {verified} live pages"
    assert not mismatches, f"current_bid != product page Nuvarande bud: {mismatches}"
    assert not equal_to_estimate, f"current_bid == Marknadsvärde (regression!): {equal_to_estimate}"


# ---------- CORE FIX: bid preservation across POST /api/live/sync ----------
def test_bid_preserved_after_sync_rediscovery():
    """Primary assertion for the iteration_3 regression fix.

    Snapshot Netauktion items that currently have current_bid>0, trigger a
    POST /api/live/sync (which re-runs NetauktionAdapter.discover() -> bid=0),
    wait long enough for Netauktion's discovery step to execute inside the
    crawler loop (one site advances per 10s tick), then verify each snapshot
    item still has current_bid > 0 (ideally unchanged) when fetched by url.
    """
    _, with_bid = _wait_for_bids(min_count=3, max_wait=120)
    if len(with_bid) < 2:
        pytest.skip(f"Need >=2 Netauktion items with bid>0, got {len(with_bid)}")

    # Snapshot sample
    sample = with_bid[:6]
    before = {it["url"]: int(it["current_bid"]) for it in sample}
    print(f"Snapshot (pre-sync): {before}")

    # Trigger re-discovery for all sites
    rs = requests.post(f"{API}/live/sync", timeout=30)
    assert rs.status_code in (200, 201, 202, 204), rs.text
    sync_t = time.time()

    # Wait: Netauktion is one of ~6 sites all stepping in parallel per 10s
    # tick. Discovery happens in the next tick after sync (within ~15s). Give
    # a comfortable window to be sure _upsert ran for every item.
    time.sleep(30)

    # Re-fetch and compare
    items_after = _get_netauktion(limit=400)
    by_url = {it["url"]: it for it in items_after}

    reset_to_zero, missing = [], []
    preserved_exact, preserved_increased = 0, 0
    for url, prev_bid in before.items():
        if url not in by_url:
            missing.append(url)
            continue
        now_bid = int(by_url[url].get("current_bid") or 0)
        if now_bid == 0:
            reset_to_zero.append((url, prev_bid))
        elif now_bid == prev_bid:
            preserved_exact += 1
        else:
            # Could be a real refresh-loop update (bid went up); still >0 so OK
            preserved_increased += 1
        print(f"url={url} prev={prev_bid} now={now_bid}")

    print(f"preserved_exact={preserved_exact} preserved_increased={preserved_increased} "
          f"reset_to_zero={len(reset_to_zero)} missing={len(missing)} "
          f"elapsed={time.time() - sync_t:.1f}s")

    # Allow 'missing' (very rare: cleanup removed an ended auction)
    assert not reset_to_zero, (
        f"REGRESSION: {len(reset_to_zero)} Netauktion items had current_bid reset "
        f"to 0 after POST /api/live/sync: {reset_to_zero[:3]}"
    )
    # At least one of the snapshot items must still be present and >0
    assert (preserved_exact + preserved_increased) >= max(1, len(before) - len(missing)), (
        "Expected all present snapshot items to preserve positive current_bid"
    )


def test_bid_preserved_second_sync_double_check():
    """Second sync in a row: still preserved (idempotency of the fix)."""
    items = _get_netauktion()
    with_bid = [it for it in items if (it.get("current_bid") or 0) > 0]
    if len(with_bid) < 2:
        pytest.skip(f"Need >=2 Netauktion items with bid>0, got {len(with_bid)}")
    before = {it["url"]: int(it["current_bid"]) for it in with_bid[:5]}

    requests.post(f"{API}/live/sync", timeout=30)
    time.sleep(25)

    after = {it["url"]: it for it in _get_netauktion(limit=400)}
    reset = [(u, b) for u, b in before.items()
             if u in after and int(after[u].get("current_bid") or 0) == 0]
    assert not reset, f"Second-sync regression: {reset[:3]}"
