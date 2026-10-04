"""Backend tests focused on Netauktion title fix + regression on live endpoints."""
import os
import re
import time
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
API = f"{BASE_URL}/api"


def _get_netauktion(limit=50, retries=3):
    for i in range(retries):
        r = requests.get(f"{API}/live/auctions", params={"source": "Netauktion", "limit": limit}, timeout=30)
        assert r.status_code == 200
        items = r.json()
        if items:
            return items
        # trigger sync and wait
        requests.post(f"{API}/live/sync", timeout=15)
        time.sleep(45)
    return []


def _slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


# --- Netauktion title correctness ---
def test_netauktion_has_items():
    items = _get_netauktion()
    assert len(items) >= 10, f"Expected >=10 Netauktion items, got {len(items)}"


def test_netauktion_titles_not_default_alt():
    items = _get_netauktion()
    bad = [it for it in items if (it.get("title") or "").strip().lower() == "default alt text"]
    assert not bad, f"{len(bad)} items have 'Default Alt Text' title"


def test_netauktion_titles_non_empty_and_varied():
    items = _get_netauktion()
    titles = [(it.get("title") or "").strip() for it in items]
    assert all(titles), "Some Netauktion titles are empty"
    unique = set(titles)
    # at least 50% unique titles across the batch
    assert len(unique) >= max(5, len(titles) // 2), f"Titles not varied: {len(unique)} unique of {len(titles)}"


def test_netauktion_no_trailing_lot_suffix():
    items = _get_netauktion()
    offenders = [it["title"] for it in items if re.search(r",\s*\d+\s*$", it.get("title") or "")]
    assert not offenders, f"Titles still have trailing lot suffix: {offenders[:5]}"


def test_netauktion_title_matches_url_slug():
    """Title's slugified form should share tokens with the URL product slug."""
    items = _get_netauktion()
    checked = 0
    mismatches = []
    for it in items:
        url = it.get("url") or ""
        m = re.search(r"/auktion/produkt/([^/?#]+)", url)
        if not m:
            continue
        url_slug = m.group(1)
        # strip trailing numeric suffix like -44
        url_slug_core = re.sub(r"-\d+$", "", url_slug)
        title_slug = _slugify(it.get("title") or "")
        # token overlap check
        url_tokens = set(t for t in url_slug_core.split("-") if len(t) > 2)
        title_tokens = set(t for t in title_slug.split("-") if len(t) > 2)
        if url_tokens:
            overlap = url_tokens & title_tokens
            if not overlap:
                mismatches.append((it["title"], url_slug))
        checked += 1
    assert checked >= 10, f"Only checked {checked} url slugs"
    # Allow a few noisy mismatches (e.g., special chars) but majority should match
    assert len(mismatches) <= checked * 0.3, f"Too many title/url mismatches: {mismatches[:5]}"


# --- Regression on other sources ---
@pytest.mark.parametrize("source", ["Klaravik", "Budi", "Retrade"])
def test_other_sources_sensible(source):
    r = requests.get(f"{API}/live/auctions", params={"source": source, "limit": 20}, timeout=30)
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1, f"No items for {source}"
    positive = 0
    for it in items:
        assert it.get("title"), f"Empty title in {source}: {it}"
        assert it.get("url"), f"Empty url in {source}: {it}"
        if (it.get("current_bid") or 0) > 0:
            positive += 1
    # Majority should have a positive current_bid (some real items have no bids yet)
    assert positive >= max(1, len(items) // 2), (
        f"Only {positive}/{len(items)} {source} items have current_bid>0"
    )


# --- /live/status ---
def test_live_status_running_with_netauktion():
    r = requests.get(f"{API}/live/status", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d.get("running") is True
    sites = {s["source"]: s for s in d.get("sites", [])}
    assert "Netauktion" in sites, "Netauktion missing from status sites"
    net = sites["Netauktion"]
    assert net.get("phase") in {"crawling", "discovering", "resting", "idle"}, net.get("phase")
    # Error should not be persistently set
    assert not net.get("error"), f"Netauktion has error: {net.get('error')}"


# --- /live/auctions (no filter) mix ---
def test_live_auctions_mixed_list():
    r = requests.get(f"{API}/live/auctions", params={"limit": 100}, timeout=30)
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 10
    sources = {it.get("source") for it in items}
    assert len(sources) >= 2, f"Expected multiple sources, got {sources}"
    for it in items:
        assert it.get("title"), it
        assert it.get("source"), it
        assert it.get("url"), it
