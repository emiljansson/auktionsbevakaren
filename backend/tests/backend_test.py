"""Backend API tests for Auktionsbevakning."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback: read from frontend/.env for test environment
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
API = f"{BASE_URL}/api"

EXPECTED_SOURCES = {
    "PS Auction", "Klaravik", "Blinto", "Tovek", "Netauktion",
    "Retrade", "Budi", "Fabeo",
}


@pytest.fixture(scope="module")
def created_ids():
    ids = []
    yield ids
    for _id in ids:
        try:
            requests.delete(f"{API}/auctions/{_id}", timeout=15)
        except Exception:
            pass


# ---------- Sources ----------
def test_sources_returns_nine_swedish():
    r = requests.get(f"{API}/sources", timeout=15)
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert EXPECTED_SOURCES.issubset(names), f"Missing: {EXPECTED_SOURCES - names}"


# ---------- Create via keyword ----------
def test_create_auction_by_keyword(created_ids):
    r = requests.post(f"{API}/auctions", json={"keyword": "TEST_Milwaukee verktyg", "category": "Verktyg"}, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    for f in ["id", "source", "title", "current_bid", "bid_count", "currency",
              "vat", "buyer_fee", "total_now", "ends_at", "url", "bid_history"]:
        assert f in data, f"Missing field {f}"
    expected_total = round(data["current_bid"] * (1 + data["vat"] / 100) + data["buyer_fee"])
    assert data["total_now"] == expected_total
    assert isinstance(data["bid_history"], list) and len(data["bid_history"]) > 0
    bids = [p["bid"] for p in data["bid_history"]]
    assert bids == sorted(bids), "bid_history must be ascending"
    assert bids[-1] == data["current_bid"]
    created_ids.append(data["id"])


# ---------- Create via URL (real scrape attempt) ----------
def test_create_auction_by_url_klaravik(created_ids):
    r = requests.post(f"{API}/auctions", json={"url": "https://www.klaravik.se/", "category": "Övrigt"}, timeout=45)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["source"] == "Klaravik"
    assert "scraped" in data
    created_ids.append(data["id"])


# ---------- List + filter ----------
def test_list_auctions_and_filter(created_ids):
    r = requests.get(f"{API}/auctions", timeout=15)
    assert r.status_code == 200
    all_items = r.json()
    assert isinstance(all_items, list) and len(all_items) >= 1

    r2 = requests.get(f"{API}/auctions", params={"source": "Klaravik"}, timeout=15)
    assert r2.status_code == 200
    items = r2.json()
    for it in items:
        assert it["source"] == "Klaravik"


# ---------- Get by id + 404 ----------
def test_get_auction_by_id(created_ids):
    assert created_ids
    _id = created_ids[0]
    r = requests.get(f"{API}/auctions/{_id}", timeout=15)
    assert r.status_code == 200
    assert r.json()["id"] == _id

    r404 = requests.get(f"{API}/auctions/does-not-exist-xyz", timeout=15)
    assert r404.status_code == 404


# ---------- Refresh ----------
def test_refresh_auction_never_decreases(created_ids):
    assert created_ids
    _id = created_ids[0]
    before = requests.get(f"{API}/auctions/{_id}", timeout=15).json()
    r = requests.post(f"{API}/auctions/{_id}/refresh", timeout=30)
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["current_bid"] >= before["current_bid"]
    expected_total = round(after["current_bid"] * (1 + after["vat"] / 100) + after["buyer_fee"])
    assert after["total_now"] == expected_total


# ---------- AI valuation ----------
def test_analyze_auction_ai(created_ids):
    assert created_ids
    _id = created_ids[0]
    r = requests.post(f"{API}/auctions/{_id}/analyze", timeout=90)
    assert r.status_code == 200, r.text
    data = r.json()
    val = data.get("valuation")
    assert val is not None, "valuation should be populated"
    for key in ["new_price_estimate", "used_price_estimate", "confidence",
                "market_position", "comparables", "insight"]:
        assert key in val, f"Missing valuation.{key}"
    assert isinstance(val["comparables"], list)
    assert isinstance(val["insight"], str) and len(val["insight"]) > 0


# ---------- Stats ----------
def test_stats_shape():
    r = requests.get(f"{API}/stats", timeout=15)
    assert r.status_code == 200
    d = r.json()
    for k in ["total", "total_value", "sources_tracked", "good_deals"]:
        assert k in d
    assert isinstance(d["total"], int)


# ---------- Delete + 404 ----------
def test_delete_auction(created_ids):
    assert created_ids
    _id = created_ids.pop(0)
    r = requests.delete(f"{API}/auctions/{_id}", timeout=15)
    assert r.status_code == 200
    r2 = requests.get(f"{API}/auctions/{_id}", timeout=15)
    assert r2.status_code == 404
    r3 = requests.delete(f"{API}/auctions/{_id}", timeout=15)
    assert r3.status_code == 404
