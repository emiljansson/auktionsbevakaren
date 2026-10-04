import os
import time
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://auction-watch.preview.emergentagent.com").rstrip("/")


def _get(path, **params):
    return requests.get(f"{BASE_URL}{path}", params=params, timeout=60)


# --- Crawler status (Swedish phases) ---
def test_crawler_status_swedish():
    r = _get("/api/live/status")
    assert r.status_code == 200
    data = r.json()
    # Should contain Swedish phase words
    text = str(data).lower()
    # Any of these must appear
    assert any(w in text for w in ["vilar", "söker nya objekt", "uppdaterar bud"]), f"No Swedish phase: {data}"
    # No English phases
    assert "crawling" not in text
    assert "discovering" not in text


# --- Budi fees known ---
def test_budi_fees_known():
    r = _get("/api/live/auctions", source="Budi", limit=5)
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    if not items:
        pytest.skip("No Budi items currently")
    for it in items[:3]:
        assert it.get("vat") == 25, it
        assert it.get("fee_known") is True, it
        assert it.get("buyer_fee") is not None and it["buyer_fee"] > 0, it
        assert it.get("total_now") is not None, it
        assert "16" in (it.get("fee_note") or ""), it


# --- Klaravik unknown fee ---
def test_klaravik_fee_unknown():
    r = _get("/api/live/auctions", source="Klaravik", limit=5)
    assert r.status_code == 200
    items = r.json()
    if not items:
        pytest.skip("No Klaravik items currently")
    for it in items[:3]:
        assert it.get("fee_known") is False, it
        assert it.get("buyer_fee") in (None, 0), it
        bid = it.get("current_bid") or it.get("starting_price") or 0
        if bid:
            # total_now ~= bid*1.25 (within rounding)
            assert abs(it["total_now"] - round(bid * 1.25)) <= 2, it
        assert "tillkommer" in (it.get("fee_note") or "").lower(), it


# --- Tradera utropspris (0 bud) ---
def test_tradera_utropspris():
    # Tradera is fetched live via search; may take time
    r = _get("/api/live/auctions", q="festool", limit=200)
    assert r.status_code == 200
    items = r.json()
    traderas = [it for it in items if it.get("source") == "Tradera"]
    if not traderas:
        # retry once
        time.sleep(5)
        r = _get("/api/live/auctions", q="festool", limit=200)
        items = r.json()
        traderas = [it for it in items if it.get("source") == "Tradera"]
    assert traderas, "No Tradera items for 'festool'"
    zero_bid = [it for it in traderas if (it.get("current_bid") or 0) == 0]
    if not zero_bid:
        pytest.skip("No Tradera items with 0 bids currently")
    for it in zero_bid[:5]:
        sp = it.get("starting_price") or 0
        assert sp > 0, f"starting_price not >0: {it}"
        # Tradera vat=0 fee=0 => total_now == starting_price
        assert it.get("total_now") == sp, it
        assert (it.get("vat") or 0) == 0, it
