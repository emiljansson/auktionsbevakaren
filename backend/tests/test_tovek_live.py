"""Tovek adapter live tests + regression for /api/live endpoints."""
import os, re, json, time
import pytest
import requests

def _load_backend_url():
    v = os.environ.get("REACT_APP_BACKEND_URL")
    if v:
        return v.rstrip("/")
    env_path = "/app/frontend/.env"
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith("REACT_APP_BACKEND_URL="):
                return line.split("=", 1)[1].strip().rstrip("/")
    raise RuntimeError("REACT_APP_BACKEND_URL missing")

BASE_URL = _load_backend_url()

TOVEK_URL_RE = re.compile(r"^https://tovek\.se/auktion/rop/[^/]+/\d+$")


@pytest.fixture(scope="module")
def tovek_items():
    # Try a few times in case tovek is still populating
    items = []
    for attempt in range(3):
        r = requests.get(f"{BASE_URL}/api/live/auctions", params={"source": "Tovek", "limit": 500}, timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", [])
        if len(items) >= 20:
            return items
        # trigger sync and wait
        requests.post(f"{BASE_URL}/api/live/sync", timeout=30)
        time.sleep(45)
    return items


def test_status_running_and_all_sites():
    r = requests.get(f"{BASE_URL}/api/live/status", timeout=30)
    assert r.status_code == 200
    data = r.json()
    assert data["running"] is True
    sources = {s["source"]: s for s in data["sites"]}
    for expected in ["Klaravik", "Budi", "Fabeo", "Retrade", "Netauktion", "Tovek"]:
        assert expected in sources, f"Missing source {expected}"
        err = sources[expected].get("error")
        # tolerate transient but flag persistent
        assert err in (None, "",) or "rate" in str(err).lower(), f"{expected} error: {err}"


def test_tovek_non_empty_and_count(tovek_items):
    assert len(tovek_items) >= 50, f"Only {len(tovek_items)} Tovek items (expected 100+ ideally, 50 min)"


def test_tovek_item_fields(tovek_items):
    assert len(tovek_items) > 0
    bad_title = 0
    bad_url = 0
    missing_ends = 0
    neg_bid = 0
    non_tovek_source = 0
    for it in tovek_items:
        title = (it.get("title") or "").strip()
        if not title or title.lower() == "objekt":
            bad_title += 1
        if not TOVEK_URL_RE.match(it.get("url") or ""):
            bad_url += 1
        if not it.get("ends_at"):
            missing_ends += 1
        cb = it.get("current_bid")
        if cb is None or not isinstance(cb, (int, float)) or cb < 0:
            neg_bid += 1
        if it.get("source") != "Tovek":
            non_tovek_source += 1
    total = len(tovek_items)
    print(f"Tovek stats n={total} bad_title={bad_title} bad_url={bad_url} missing_ends={missing_ends} neg_bid={neg_bid} non_tovek={non_tovek_source}")
    assert bad_title == 0, f"{bad_title} items have missing/'Objekt' title"
    assert bad_url == 0, f"{bad_url} items have invalid Tovek URL"
    assert non_tovek_source == 0
    assert neg_bid == 0, f"{neg_bid} items have invalid current_bid"
    # majority must have ends_at
    assert missing_ends <= total * 0.2, f"Too many missing ends_at: {missing_ends}/{total}"


def test_tovek_urls_resolve(tovek_items):
    assert len(tovek_items) >= 5
    sample = tovek_items[:5]
    ok = 0
    for it in sample:
        try:
            r = requests.get(it["url"], timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                ok += 1
            else:
                print(f"URL {it['url']} -> {r.status_code}")
        except Exception as e:
            print(f"URL {it['url']} err {e}")
    assert ok >= 3, f"Only {ok}/5 Tovek urls returned 200"


def test_tovek_bid_correctness(tovek_items):
    """If any Tovek item has current_bid>0, verify the url contains that value."""
    with_bids = [it for it in tovek_items if (it.get("current_bid") or 0) > 0]
    if not with_bids:
        pytest.skip("No Tovek items with current_bid > 0 yet (acceptable, many early auctions)")
    checked = 0
    matched = 0
    for it in with_bids[:5]:
        try:
            r = requests.get(it["url"], timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            checked += 1
            body = r.text
            cb = int(it["current_bid"])
            # Tovek embeds the number as itemWinningBidValue in page json
            # Accept either direct number or presence as integer
            if re.search(rf'itemWinningBidValue[^0-9]{{0,20}}{cb}\b', body):
                matched += 1
            else:
                print(f"Bid {cb} not found verbatim in {it['url']}")
        except Exception as e:
            print(f"err {e}")
    print(f"Bid correctness checked={checked} matched={matched}")
    assert checked == 0 or matched >= 1, "No Tovek bid matched expected itemWinningBidValue"


# Regression: other sources
@pytest.fixture(scope="module")
def all_auctions():
    r = requests.get(f"{BASE_URL}/api/live/auctions", params={"limit": 1000}, timeout=60)
    assert r.status_code == 200
    data = r.json()
    return data if isinstance(data, list) else data.get("items", [])


def test_regression_other_sources_have_title_url(all_auctions):
    by_src = {}
    for it in all_auctions:
        by_src.setdefault(it.get("source"), []).append(it)
    for src in ["Klaravik", "Budi", "Fabeo", "Retrade", "Netauktion"]:
        items = by_src.get(src, [])
        assert len(items) > 0, f"No items for {src}"
        bad = [i for i in items if not (i.get("title") or "").strip() or not (i.get("url") or "").startswith("http")]
        assert not bad, f"{src} has {len(bad)} items missing title/url"


def test_regression_netauktion_titles(all_auctions):
    net = [i for i in all_auctions if i.get("source") == "Netauktion"]
    assert net
    bad = [i for i in net if (i.get("title") or "").strip().lower() == "default alt text"]
    assert not bad, f"{len(bad)} Netauktion items have 'Default Alt Text'"


def test_regression_bid_preservation():
    """POST sync and ensure previously-positive bids are not zeroed."""
    r1 = requests.get(f"{BASE_URL}/api/live/auctions", params={"limit": 1000}, timeout=60)
    before = r1.json() if r1.ok else []
    before = before if isinstance(before, list) else before.get("items", [])
    pos_before = {i["url"]: i["current_bid"] for i in before if (i.get("current_bid") or 0) > 0 and i.get("url")}
    if len(pos_before) < 5:
        pytest.skip("Not enough positive-bid items to verify preservation")
    requests.post(f"{BASE_URL}/api/live/sync", timeout=30)
    time.sleep(20)
    r2 = requests.get(f"{BASE_URL}/api/live/auctions", params={"limit": 1000}, timeout=60)
    after = r2.json()
    after = after if isinstance(after, list) else after.get("items", [])
    after_map = {i["url"]: i.get("current_bid") or 0 for i in after if i.get("url")}
    zeroed = [u for u, v in pos_before.items() if u in after_map and after_map[u] == 0]
    assert len(zeroed) <= max(1, int(len(pos_before) * 0.05)), f"{len(zeroed)}/{len(pos_before)} positive bids zeroed after sync"
