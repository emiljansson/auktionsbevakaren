"""Verify Tovek 'Inga bud' bug fix: bids / starting_price / ends_at + regressions."""
import os, re, time
from datetime import datetime, timezone
import pytest
import requests


def _base():
    v = os.environ.get("REACT_APP_BACKEND_URL")
    if not v:
        for line in open("/app/frontend/.env"):
            if line.startswith("REACT_APP_BACKEND_URL="):
                v = line.split("=", 1)[1].strip()
                break
    return v.rstrip("/")


BASE = _base()


def _get_items(params):
    r = requests.get(f"{BASE}/api/live/auctions", params=params, timeout=60)
    assert r.status_code == 200, r.text
    j = r.json()
    return j if isinstance(j, list) else j.get("items", [])


@pytest.fixture(scope="module")
def tovek():
    items = _get_items({"source": "Tovek", "limit": 500})
    if len(items) < 20 or sum(1 for i in items if (i.get("current_bid") or 0) > 0) < 5:
        requests.post(f"{BASE}/api/live/sync", timeout=30)
        time.sleep(70)
        items = _get_items({"source": "Tovek", "limit": 500})
    return items


def test_status_all_8_sources():
    r = requests.get(f"{BASE}/api/live/status", timeout=30)
    assert r.status_code == 200
    data = r.json()
    assert data["running"] is True
    sources = {s["source"]: s for s in data["sites"]}
    expected = ["Klaravik", "Budi", "Fabeo", "Retrade", "Netauktion",
                "Kronofogden", "Tovek", "PS Auction"]
    for src in expected:
        assert src in sources, f"Missing source {src}; got {list(sources)}"
        err = sources[src].get("error")
        assert err in (None, "") or "rate" in str(err).lower() or "blinto" in str(err).lower(), f"{src}: {err}"


def test_tovek_count(tovek):
    assert len(tovek) >= 50, f"Only {len(tovek)} Tovek items"


def test_tovek_has_real_bids(tovek):
    """Core bug: current_bid should be > 0 for a substantial subset."""
    bids = [it.get("current_bid") or 0 for it in tovek]
    with_bid = [b for b in bids if b > 0]
    print(f"Tovek n={len(tovek)} with_bid={len(with_bid)} unique_vals={len(set(with_bid))} max={max(bids) if bids else 0}")
    assert len(with_bid) >= 20, f"Only {len(with_bid)} items have current_bid > 0 (bug not fixed)"
    # Not all identical
    assert len(set(with_bid)) >= 10, f"Only {len(set(with_bid))} unique bid values - looks suspicious"
    # Has at least one high-value item (e.g. a tractor near ~47k)
    assert max(with_bid) >= 10000, f"Max bid only {max(with_bid)} - expected real-world varied bids"


def test_tovek_starting_price_when_no_bid(tovek):
    """Items with current_bid == 0 should have starting_price > 0 (utropspris) for most."""
    zero = [it for it in tovek if (it.get("current_bid") or 0) == 0]
    if not zero:
        pytest.skip("No zero-bid items")
    with_sp = [it for it in zero if (it.get("starting_price") or 0) > 0]
    print(f"Tovek zero-bid={len(zero)} with_starting_price>0={len(with_sp)}")
    # At least ~50% of zero-bid items should carry utropspris
    assert len(with_sp) >= max(5, int(0.4 * len(zero))), (
        f"Only {len(with_sp)}/{len(zero)} zero-bid Tovek items have starting_price>0"
    )


def test_tovek_ends_at_parseable_future(tovek):
    now = datetime.now(timezone.utc)
    bad = []
    hhmm_variation = set()
    for it in tovek:
        ea = it.get("ends_at")
        if not ea:
            continue
        try:
            dt = datetime.fromisoformat(ea.replace("Z", "+00:00"))
        except Exception:
            bad.append(("unparseable", ea))
            continue
        if dt <= now:
            bad.append(("past", ea))
        hhmm_variation.add(dt.strftime("%H:%M"))
    print(f"Tovek distinct HH:MM count={len(hhmm_variation)} bad={len(bad)}")
    assert not bad, f"Bad ends_at: {bad[:5]}"
    # Exact HH:MM, not all set to same default like 00:00
    assert len(hhmm_variation) >= 3, f"HH:MM variety={hhmm_variation}, looks like default times"


def test_sort_stability_and_no_past():
    """Default list is sorted by ends_at asc (nulls last), no past items, stable across polls."""
    def fetch():
        return _get_items({"limit": 120})

    a = fetch()
    time.sleep(3)
    b = fetch()

    def key(it):
        return (it.get("url") or "", it.get("source") or "")

    now = datetime.now(timezone.utc)
    # No past ends_at
    for it in a:
        ea = it.get("ends_at")
        if ea:
            try:
                dt = datetime.fromisoformat(ea.replace("Z", "+00:00"))
                assert dt > now, f"Past ends_at found: {ea} for {it.get('url')}"
            except ValueError:
                pass

    # Sort invariant: non-null ends_at asc, then nulls at end
    last_dt = None
    hit_null = False
    for it in a:
        ea = it.get("ends_at")
        if not ea:
            hit_null = True
            continue
        assert not hit_null, "null ends_at appeared before non-null"
        try:
            dt = datetime.fromisoformat(ea.replace("Z", "+00:00"))
        except ValueError:
            continue
        if last_dt is not None:
            assert dt >= last_dt, f"Out of order: {last_dt} then {dt}"
        last_dt = dt

    # Stability across polls (identical ordering)
    ids_a = [key(x) for x in a]
    ids_b = [key(x) for x in b]
    assert ids_a == ids_b, f"Ordering changed between polls (first diff at {next((i for i,(x,y) in enumerate(zip(ids_a,ids_b)) if x!=y), '?')})"


def test_regression_ps_auction_format():
    items = _get_items({"source": "PS Auction", "limit": 500})
    assert items, "No PS Auction items"
    url_re = re.compile(r"^https://psauction\.se/item/view/\d+(?:/[A-Za-z0-9\-]*)?/?$")
    bad = [i for i in items if not url_re.match(i.get("url") or "")]
    assert not bad, f"{len(bad)} PS Auction urls off-format, sample: {[i.get('url') for i in bad[:3]]}"
    # exact HH:MM
    hhmm = set()
    for it in items:
        ea = it.get("ends_at")
        if ea:
            try:
                dt = datetime.fromisoformat(ea.replace("Z", "+00:00"))
                hhmm.add(dt.strftime("%H:%M"))
            except Exception:
                pass
    assert len(hhmm) >= 3, f"PS Auction HH:MM variety={hhmm}"


def test_regression_netauktion_titles_not_default():
    items = _get_items({"source": "Netauktion", "limit": 500})
    assert items
    bad = [i for i in items if (i.get("title") or "").strip().lower() == "default alt text"]
    assert not bad, f"{len(bad)} Netauktion items have 'Default Alt Text'"
