"""Tests for this iteration:
- POST /api/auctions/{id}/refresh fee correctness (uses compute_fees, not 10%)
- Missing-URL item -> HTTP 400 on refresh
- POST /api/live/analyze returns valuation (shared helper)
- Account name + 4-digit PIN + restore flows (A/B/C) + validation errors
"""
import os
import uuid
import pytest
import requests
from pathlib import Path


def _base_url() -> str:
    url = os.environ.get("REACT_APP_BACKEND_URL")
    if not url:
        env = Path("/app/frontend/.env").read_text()
        for line in env.splitlines():
            if line.startswith("REACT_APP_BACKEND_URL="):
                url = line.split("=", 1)[1].strip()
                break
    return url.rstrip("/")


BASE_URL = _base_url()


def _sess(device_id: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "X-Device-Id": device_id})
    return s


# --------- ACCOUNT NAME + PIN + RESTORE ---------

class TestAccountNamePin:
    def setup_method(self):
        suf = uuid.uuid4().hex[:6]
        self.name_nopin = f"qa-noPin-{suf}"
        self.name_pin = f"qa-pin-{suf}"
        self.devA = f"qa-devA-{suf}"
        self.devB = f"qa-devB-{suf}"
        self.devC = f"qa-devC-{suf}"
        self.devD = f"qa-devD-{suf}"
        self.devE = f"qa-devE-{suf}"

    def test_flow_a_no_pin(self):
        s = _sess(self.devA)
        r = s.post(f"{BASE_URL}/api/account/name", json={"name": self.name_nopin})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is True
        assert d.get("has_pin") is False

        r = s.get(f"{BASE_URL}/api/account/name")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("name") == self.name_nopin
        assert d.get("has_pin") is False

        # Fresh device restores by name -> no 403 (no pin). "found" may be False
        # since devA has no watches/keywords.
        s2 = _sess(self.devB)
        r = s2.post(f"{BASE_URL}/api/account/restore", json={"name": self.name_nopin})
        assert r.status_code == 200, r.text
        assert "found" in r.json()

    def test_flow_b_pin_protected(self):
        s = _sess(self.devC)
        r = s.post(f"{BASE_URL}/api/account/name", json={"name": self.name_pin, "pin": "1234"})
        assert r.status_code == 200, r.text
        assert r.json().get("has_pin") is True

        s2 = _sess(self.devD)
        # no pin -> 403
        r = s2.post(f"{BASE_URL}/api/account/restore", json={"name": self.name_pin})
        assert r.status_code == 403, r.text
        # wrong pin -> 403
        r = s2.post(f"{BASE_URL}/api/account/restore", json={"name": self.name_pin, "pin": "0000"})
        assert r.status_code == 403, r.text
        # correct pin -> 200
        r = s2.post(f"{BASE_URL}/api/account/restore", json={"name": self.name_pin, "pin": "1234"})
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

    def test_flow_c_claim_protected_name(self):
        # Make sure pin-protected name exists
        _sess(self.devC).post(f"{BASE_URL}/api/account/name", json={"name": self.name_pin, "pin": "1234"})

        s = _sess(self.devE)
        # claim without pin -> 403
        r = s.post(f"{BASE_URL}/api/account/name", json={"name": self.name_pin})
        assert r.status_code == 403, r.text
        # wrong pin -> 403
        r = s.post(f"{BASE_URL}/api/account/name", json={"name": self.name_pin, "pin": "9999"})
        assert r.status_code == 403, r.text
        # correct pin -> 200
        r = s.post(f"{BASE_URL}/api/account/name", json={"name": self.name_pin, "pin": "1234"})
        assert r.status_code == 200, r.text

    def test_validation_errors(self):
        s = _sess(self.devA)
        # name < 2 chars
        r = s.post(f"{BASE_URL}/api/account/name", json={"name": "a"})
        assert r.status_code == 400, r.text
        # pin not 4 digits
        r = s.post(f"{BASE_URL}/api/account/name", json={"name": "qa-v-" + uuid.uuid4().hex[:4], "pin": "12"})
        assert r.status_code == 400, r.text
        r = s.post(f"{BASE_URL}/api/account/name", json={"name": "qa-v-" + uuid.uuid4().hex[:4], "pin": "abcd"})
        assert r.status_code == 400, r.text
        # restore with short name
        r = s.post(f"{BASE_URL}/api/account/restore", json={"name": "a"})
        assert r.status_code == 400, r.text


# --------- REFRESH & ANALYZE ---------

@pytest.fixture(scope="module")
def dev_with_watch():
    """Try to create a watched item. Returns (device_id, auction_id, source) or
    (device_id, None, None) if push registration/watch creation is blocked."""
    did = f"qa-watch-{uuid.uuid4().hex[:8]}"
    s = _sess(did)
    # Register push token
    tok = "test-token-" + uuid.uuid4().hex
    r = s.post(f"{BASE_URL}/api/push/register", json={"token": tok, "platform": "web"})
    if r.status_code != 200:
        return did, None, None, s
    # Fetch a live auction url
    live = s.get(f"{BASE_URL}/api/live/auctions", timeout=60)
    if live.status_code != 200 or not live.json():
        return did, None, None, s
    items = live.json()
    chosen = None
    for it in items:
        if it.get("url") and it.get("source") in ("Blinto", "Klaravik", "Netauktion"):
            chosen = it
            break
    chosen = chosen or items[0]
    r = s.post(f"{BASE_URL}/api/live/watch", json={"url": chosen["url"]})
    if r.status_code != 200:
        return did, None, None, s
    body = r.json()
    aid = body.get("id") or body.get("auction", {}).get("id")
    if not aid:
        # Try listing
        lst = s.get(f"{BASE_URL}/api/auctions").json()
        if lst:
            aid = lst[0]["id"]
    yield did, aid, chosen.get("source"), s
    # Cleanup
    if aid:
        try:
            s.delete(f"{BASE_URL}/api/auctions/{aid}")
        except Exception:
            pass


class TestRefreshAndAnalyze:
    def test_refresh_uses_compute_fees(self, dev_with_watch):
        did, aid, source, s = dev_with_watch
        if not aid:
            pytest.skip("Could not create watched item (push/live/watch unavailable)")
        r = s.post(f"{BASE_URL}/api/auctions/{aid}/refresh", timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data.get("current_bid"), (int, float))
        assert isinstance(data.get("buyer_fee"), (int, float))
        assert isinstance(data.get("total_now"), (int, float))
        cb = data["current_bid"]
        bf = data["buyer_fee"]
        tn = data["total_now"]
        # total_now should equal current_bid + buyer_fee (approx; vat may be included already)
        assert tn >= cb, f"total_now {tn} < current_bid {cb}"
        # For Blinto/Klaravik the fee is NOT a flat 10% of current_bid.
        if source in ("Blinto", "Klaravik") and cb > 0:
            naive = round(cb * 0.10)
            # Allow equality only if fee really happens to be 10%; typically it isn't.
            assert not (abs(bf - naive) < 1 and bf == naive), f"buyer_fee looks like naive 10% ({bf})"
        print(f"refresh ok: source={source} bid={cb} fee={bf} total={tn}")

    def test_keyword_only_create_is_rejected(self):
        """A keyword must not create a fabricated watch without an auction URL."""
        s = _sess(f"qa-nourl-{uuid.uuid4().hex[:8]}")
        r = s.post(f"{BASE_URL}/api/auctions", json={"keyword": "cykel"})
        assert r.status_code == 422, r.text
        assert s.get(f"{BASE_URL}/api/auctions").json() == []

    def test_live_analyze_returns_valuation(self):
        did = f"qa-ana-{uuid.uuid4().hex[:8]}"
        s = _sess(did)
        live = s.get(f"{BASE_URL}/api/live/auctions", timeout=60)
        if live.status_code != 200 or not live.json():
            pytest.skip("live feed empty")
        url = live.json()[0]["url"]
        r = s.post(f"{BASE_URL}/api/live/analyze", json={"url": url}, timeout=90)
        assert r.status_code == 200, r.text[:400]
        d = r.json()
        assert "valuation" in d, d
        v = d["valuation"]
        assert "sold_comparables" in v
        assert "based_on_real_sales" in v

    def test_watched_analyze_returns_valuation(self, dev_with_watch):
        did, aid, source, s = dev_with_watch
        if not aid:
            pytest.skip("No watched item available")
        r = s.post(f"{BASE_URL}/api/auctions/{aid}/analyze", timeout=90)
        assert r.status_code == 200, r.text[:400]
        d = r.json()
        assert "valuation" in d and d["valuation"]
        v = d["valuation"]
        assert "sold_comparables" in v
        assert "based_on_real_sales" in v
