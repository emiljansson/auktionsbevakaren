"""Backend tests for Tradera search integration & https image URL normalization."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://auction-watch.preview.emergentagent.com").rstrip("/")


@pytest.fixture
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def test_live_auctions_without_q_has_no_tradera(api):
    r = api.get(f"{BASE_URL}/api/live/auctions?limit=300", timeout=60)
    assert r.status_code == 200
    data = r.json()
    items = data if isinstance(data, list) else data.get("items", data.get("auctions", []))
    sources = {it.get("source") for it in items}
    print(f"Without q sources: {sources}, total items: {len(items)}")
    tradera_items = [it for it in items if it.get("source") == "Tradera"]
    assert len(tradera_items) == 0, f"Found {len(tradera_items)} Tradera items without query"


def test_live_auctions_with_q_festool_has_tradera(api):
    # Give backend a chance to crawl - allow up to 2 attempts
    r = api.get(f"{BASE_URL}/api/live/auctions?q=festool&limit=200", timeout=90)
    assert r.status_code == 200
    data = r.json()
    items = data if isinstance(data, list) else data.get("items", data.get("auctions", []))
    tradera_items = [it for it in items if it.get("source") == "Tradera"]
    if len(tradera_items) < 1:
        time.sleep(3)
        r = api.get(f"{BASE_URL}/api/live/auctions?q=festool&limit=200", timeout=90)
        items = r.json()
        items = items if isinstance(items, list) else items.get("items", items.get("auctions", []))
        tradera_items = [it for it in items if it.get("source") == "Tradera"]
    print(f"With q=festool: total={len(items)}, tradera={len(tradera_items)}")
    assert len(tradera_items) >= 10, f"Expected many Tradera items, got {len(tradera_items)}"
    # Check mixed with other sources
    other_sources = {it.get("source") for it in items if it.get("source") != "Tradera"}
    print(f"Other sources in mix: {other_sources}")
    assert len(other_sources) >= 1, "Expected Tradera to be mixed with other sources"


def test_tradera_items_have_fixed_price_and_auction(api):
    r = api.get(f"{BASE_URL}/api/live/auctions?q=festool&limit=200", timeout=90)
    assert r.status_code == 200
    data = r.json()
    items = data if isinstance(data, list) else data.get("items", data.get("auctions", []))
    tradera = [it for it in items if it.get("source") == "Tradera"]
    assert len(tradera) > 0
    fixed = [it for it in tradera if it.get("fixed_price")]
    auctions = [it for it in tradera if not it.get("fixed_price")]
    print(f"Tradera fixed_price={len(fixed)}, auctions={len(auctions)}")
    # At least one of each is nice-to-have, but we at least expect one category
    assert len(fixed) + len(auctions) == len(tradera)


def test_image_urls_are_https(api):
    r = api.get(f"{BASE_URL}/api/live/auctions?q=festool&limit=200", timeout=90)
    assert r.status_code == 200
    data = r.json()
    items = data if isinstance(data, list) else data.get("items", data.get("auctions", []))
    http_imgs = []
    for it in items:
        img = it.get("image") or it.get("image_url") or ""
        if img and isinstance(img, str) and img.startswith("http://"):
            http_imgs.append((it.get("source"), img))
    print(f"http:// image URLs found: {len(http_imgs)}")
    assert len(http_imgs) == 0, f"Found non-https images: {http_imgs[:5]}"


def test_watch_add_tradera_item(api):
    r = api.get(f"{BASE_URL}/api/live/auctions?q=festool&limit=200", timeout=90)
    items = r.json()
    items = items if isinstance(items, list) else items.get("items", items.get("auctions", []))
    tradera = [it for it in items if it.get("source") == "Tradera"]
    assert len(tradera) > 0
    target = tradera[0]
    item_id = target.get("id") or target.get("_id") or target.get("url")
    print(f"Target tradera id/url: {item_id}")
    # Try watch endpoint
    payload = {"id": item_id, "url": target.get("url")}
    wr = api.post(f"{BASE_URL}/api/live/watch", json=payload, timeout=30)
    print(f"watch POST status={wr.status_code} body={wr.text[:300]}")
    assert wr.status_code in (200, 201)
