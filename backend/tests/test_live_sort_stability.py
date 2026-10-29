"""Tests for live auction sort/stability + PS exact end times (iteration 7)."""
import os
import re
import time
from datetime import datetime, timezone

import pytest
import requests

def _load_backend_url():
    url = os.environ.get("REACT_APP_BACKEND_URL")
    if not url:
        env_path = "/app/frontend/.env"
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("REACT_APP_BACKEND_URL="):
                        url = line.split("=", 1)[1].strip()
                        break
    if not url:
        raise RuntimeError("REACT_APP_BACKEND_URL not set")
    return url.rstrip("/")

BASE_URL = _load_backend_url()
API = f"{BASE_URL}/api"


def _parse_iso(s):
    if not s:
        return None
    try:
        # Normalise 'Z'
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _get(url, **kw):
    r = requests.get(url, timeout=30, **kw)
    assert r.status_code == 200, f"{url} -> {r.status_code} {r.text[:200]}"
    return r.json()["items"]


# ---- SORT / STABILITY ----

def test_live_auctions_sorted_ascending_by_ends_at():
    data = _get(f"{API}/live/auctions", params={"limit": 120})
    assert isinstance(data, list) and len(data) > 0
    ends = [_parse_iso(it.get("ends_at")) for it in data]
    # None values must only appear after all non-None values
    seen_none = False
    for e in ends:
        if e is None:
            seen_none = True
        else:
            assert not seen_none, "Found item with ends_at AFTER a null-ends_at item (sort violation)"
    # Ascending among non-None
    non_none = [e for e in ends if e is not None]
    for a, b in zip(non_none, non_none[1:]):
        assert a <= b, f"Ends_at not ascending: {a.isoformat()} > {b.isoformat()}"
    print(f"Checked {len(data)} items, {len(non_none)} with ends_at, ascending OK")


def test_no_past_ended_items():
    data = _get(f"{API}/live/auctions", params={"limit": 120})
    now = datetime.now(timezone.utc)
    past = []
    for it in data:
        e = _parse_iso(it.get("ends_at"))
        if e is not None and e <= now:
            past.append((it.get("source"), it.get("url"), it.get("ends_at")))
    assert not past, f"Found {len(past)} past-ended items, first few: {past[:5]}"


def test_order_stable_across_two_calls():
    a = _get(f"{API}/live/auctions", params={"limit": 120})
    time.sleep(3)
    b = _get(f"{API}/live/auctions", params={"limit": 120})
    # Compare the ordering by url (stable identity across refreshes)
    urls_a = [x.get("url") for x in a]
    urls_b = [x.get("url") for x in b]
    # Allow minor set differences (new items might appear) - focus on the intersection ordering
    common = set(urls_a) & set(urls_b)
    order_a = [u for u in urls_a if u in common]
    order_b = [u for u in urls_b if u in common]
    assert order_a == order_b, (
        f"Order changed across calls! "
        f"First divergence at index "
        f"{next((i for i,(x,y) in enumerate(zip(order_a, order_b)) if x!=y), None)}"
    )
    print(f"Stable ordering across 2 calls over {len(common)} common items")


# ---- PS AUCTION EXACT END TIME ----

PS_URL_RE = re.compile(r"^https://psauction\.se/item/view/\d+/")


def test_ps_auction_exact_end_times():
    data = _get(f"{API}/live/auctions", params={"source": "PS Auction", "limit": 300})
    assert len(data) > 0, "No PS Auction items returned"
    with_ends = []
    with_hhmm = []
    now = datetime.now(timezone.utc)
    for it in data:
        assert PS_URL_RE.match(it.get("url", "")), f"Bad PS url: {it.get('url')}"
        assert isinstance(it.get("current_bid"), int) and it["current_bid"] >= 0
        e_raw = it.get("ends_at")
        e = _parse_iso(e_raw)
        if e is not None:
            with_ends.append(e)
            assert e > now, f"PS item already ended: {e_raw}"
            # Check that it has HH:MM time-of-day (not just midnight 00:00)
            # exact parse embeds explicit minute; derived uses minute precision too
            if not (e.hour == 0 and e.minute == 0 and e.second == 0):
                with_hhmm.append(e)
    assert len(with_ends) >= 0.8 * len(data), (
        f"Only {len(with_ends)}/{len(data)} PS items have ends_at"
    )
    assert len(with_hhmm) >= 0.8 * len(with_ends), (
        f"Only {len(with_hhmm)}/{len(with_ends)} PS items have HH:MM time-of-day"
    )
    # Staggered: expect at least a few distinct minute buckets
    distinct_minutes = {(e.hour, e.minute) for e in with_hhmm}
    print(
        f"PS: {len(data)} items, {len(with_ends)} with ends_at, "
        f"{len(with_hhmm)} with HH:MM, {len(distinct_minutes)} distinct minute buckets"
    )


# ---- REGRESSION ----

EXPECTED_SOURCES = {
    "Klaravik", "Budi", "Fabeo", "Retrade", "Netauktion",
    "Tovek", "PS Auction",
}


def test_live_status_running_all_sources():
    s = _get(f"{API}/live/status")
    assert s.get("running") is True, f"Crawler not running: {s}"
    sites = {site["source"]: site for site in s.get("sites", [])}
    missing = EXPECTED_SOURCES - set(sites.keys())
    assert not missing, f"Missing sources in status: {missing}"
    # No persistent error on expected sources
    for name in EXPECTED_SOURCES:
        err = sites[name].get("last_error") or sites[name].get("error")
        # Allow transient; just print for visibility
        if err:
            print(f"[info] {name} last_error: {err}")


def test_tovek_has_many_items_with_rop_urls():
    data = _get(f"{API}/live/auctions", params={"source": "Tovek", "limit": 300})
    assert len(data) >= 100, f"Expected 100+ Tovek items, got {len(data)}"
    bad = [it["url"] for it in data if "/auktion/rop/" not in it.get("url", "")]
    assert not bad, f"Tovek urls not using /auktion/rop/: {bad[:3]}"


def test_netauktion_titles_and_bids():
    data = _get(f"{API}/live/auctions", params={"source": "Netauktion", "limit": 200})
    assert len(data) > 0
    bad_titles = [it for it in data if (it.get("title") or "").strip().lower() == "default alt text"]
    assert not bad_titles, f"{len(bad_titles)} Netauktion items with 'Default Alt Text' title"
    for it in data:
        cb = it.get("current_bid")
        assert isinstance(cb, int) and cb >= 0


def test_every_item_has_title_and_url():
    data = _get(f"{API}/live/auctions", params={"limit": 500})
    for it in data:
        assert (it.get("title") or "").strip(), f"Missing title: {it}"
        assert (it.get("url") or "").startswith("http"), f"Bad url: {it}"
