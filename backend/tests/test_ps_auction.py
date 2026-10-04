"""Tests for PS Auction collector and overall live status regression."""
import os
import re
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://auction-watch.preview.emergentagent.com").rstrip("/")
PS_URL_RE = re.compile(r"^https://psauction\.se/item/view/\d+/")
EXPECTED_SOURCES = {"Klaravik", "Budi", "Fabeo", "Retrade", "Netauktion", "Kronofogden", "Tovek", "PS Auction"}


@pytest.fixture(scope="module")
def ps_items():
    r = requests.get(f"{BASE_URL}/api/live/auctions", params={"source": "PS Auction", "limit": 500}, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    # Could be list or wrapped
    items = data if isinstance(data, list) else data.get("items") or data.get("auctions") or []
    return items


@pytest.fixture(scope="module")
def status():
    r = requests.get(f"{BASE_URL}/api/live/status", timeout=30)
    assert r.status_code == 200
    return r.json()


# ---- Status regression ----
class TestLiveStatus:
    def test_running(self, status):
        assert status.get("running") is True

    def test_all_8_sources(self, status):
        srcs = {s["source"] for s in status["sites"]}
        missing = EXPECTED_SOURCES - srcs
        assert not missing, f"Missing sources: {missing}"

    def test_no_persistent_errors(self, status):
        errs = {s["source"]: s.get("error") for s in status["sites"] if s.get("error")}
        # Blinto allowed but not expected in list; everything in expected set must be clean
        bad = {k: v for k, v in errs.items() if k in EXPECTED_SOURCES}
        assert not bad, f"Sources with errors: {bad}"

    def test_sources_have_items(self, status):
        # status.items is a per-tick transient counter (can be 0 during 'discovering'
        # or at start of a 'crawling' pass). Verify actual persisted data instead by
        # querying /api/live/auctions for each source.
        for src in EXPECTED_SOURCES:
            r = requests.get(f"{BASE_URL}/api/live/auctions", params={"source": src, "limit": 1}, timeout=30)
            assert r.status_code == 200, f"{src}: status {r.status_code}"
            data = r.json()
            items = data if isinstance(data, list) else data.get("items") or data.get("auctions") or []
            assert items, f"{src}: has no persisted items"


# ---- PS Auction specific ----
class TestPSAuction:
    def test_non_empty_100plus(self, ps_items):
        assert len(ps_items) >= 100, f"Expected 100+ PS items, got {len(ps_items)}"

    def test_source_field(self, ps_items):
        bad = [i for i in ps_items if i.get("source") != "PS Auction"]
        assert not bad, f"{len(bad)} items with wrong source"

    def test_title_non_empty(self, ps_items):
        bad = [i for i in ps_items if not (i.get("title") or "").strip()]
        assert not bad, f"{len(bad)} items with empty title; sample: {bad[:3]}"

    def test_url_format(self, ps_items):
        bad = [i.get("url") for i in ps_items if not PS_URL_RE.match(i.get("url") or "")]
        assert not bad, f"{len(bad)} items with bad url; sample: {bad[:3]}"

    def test_current_bid_integer_nonneg(self, ps_items):
        bad = []
        for i in ps_items:
            cb = i.get("current_bid")
            if not (isinstance(cb, int) and cb >= 0):
                bad.append((i.get("url"), cb))
        assert not bad, f"{len(bad)} items with invalid current_bid; sample: {bad[:5]}"

    def test_bid_variety(self, ps_items):
        bids = [i.get("current_bid", 0) for i in ps_items]
        positive = [b for b in bids if b > 0]
        assert len(positive) >= 5, f"Expected several positive bids, got {len(positive)}"
        # Variety: at least 3 distinct positive values
        assert len(set(positive)) >= 3, f"Bids not varied: {set(positive)}"
        # Some bids in the thousands (vehicles)
        thousands = [b for b in positive if b >= 1000]
        assert thousands, f"No bids >= 1000 SEK; positives sample: {positive[:10]}"


# ---- Regression on other sources ----
class TestOtherSourcesRegression:
    def _fetch(self, source):
        r = requests.get(f"{BASE_URL}/api/live/auctions", params={"source": source, "limit": 500}, timeout=30)
        assert r.status_code == 200
        data = r.json()
        return data if isinstance(data, list) else data.get("items") or data.get("auctions") or []

    def test_netauktion_titles_not_default(self):
        items = self._fetch("Netauktion")
        assert items, "No Netauktion items"
        bad = [i for i in items if (i.get("title") or "").strip().lower() == "default alt text"]
        assert not bad, f"{len(bad)} Netauktion items with 'Default Alt Text' title"

    def test_tovek_url_format(self):
        items = self._fetch("Tovek")
        assert len(items) >= 100, f"Expected 100+ Tovek, got {len(items)}"
        bad = [i.get("url") for i in items if "/auktion/rop/" not in (i.get("url") or "")]
        assert not bad, f"{len(bad)} Tovek items with wrong url; sample: {bad[:3]}"

    def test_all_sources_have_title_and_url(self):
        for src in EXPECTED_SOURCES - {"PS Auction"}:
            items = self._fetch(src)
            assert items, f"{src}: no items"
            bad = [i for i in items if not (i.get("title") or "").strip() or not (i.get("url") or "").strip()]
            assert not bad, f"{src}: {len(bad)} items missing title/url"
