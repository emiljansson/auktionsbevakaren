"""Regression tests run against a disposable Mongo database, with external services mocked."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

import server
import scraper


@pytest.fixture
def run_case(monkeypatch):
    def run(case):
        async def main():
            client = AsyncIOMotorClient(os.environ["MONGO_URL"])
            database = client[f"repair_test_{uuid.uuid4().hex}"]
            monkeypatch.setattr(server, "db", database)
            monkeypatch.setattr(server, "CHATGPT_LOCAL_ENABLED", False)
            monkeypatch.setenv("ENABLE_BACKGROUND_JOBS", "false")
            server._rate_buckets.clear()
            try:
                await server.startup()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as api:
                    await case(database, api)
            finally:
                await client.drop_database(database.name)
                client.close()
        asyncio.run(main())
    return run


def watch(**fields):
    return {**server.Auction(source="Klaravik", title="Volvo EC220", current_bid=1000,
                            bid_count=2, total_now=1250, url="https://example.com/item").model_dump(),
            "device_id": "owner", **fields}


def test_zero_bid_and_unknown_end_are_preserved():
    result = scraper._build_record("https://klaravik.se/item", {"title": "Objekt", "current_bid": 0, "bid_count": 0}, True)
    assert result["current_bid"] == result["bid_count"] == 0
    assert result["ends_at"] is None
    assert result["fee_known"] is False
    history = scraper.generate_bid_history(1000, 20)
    assert len(history) == 1
    assert history[0]["bid"] == 1000


def test_missing_data_never_generates_an_auction():
    with pytest.raises(ValueError):
        scraper._build_record("https://example.com", {"title": "No price"}, True)
    with pytest.raises(ValueError):
        scraper.scrape_auction(keyword="Volvo")


def test_internal_url_does_not_send_http_request(monkeypatch):
    request = AsyncMock()
    monkeypatch.setattr(httpx.Client, "get", request)
    with pytest.raises(ValueError):
        scraper.scrape_auction("http://127.0.0.1/private")
    request.assert_not_called()


def test_refresh_updates_real_bid_count_end_and_fees(run_case, monkeypatch):
    async def case(db, api):
        doc = watch()
        await db.auctions.insert_one(doc)
        await db.live_auctions.insert_one({"url": doc["url"], "fee_inputs": {"fee_excl": 80}})
        end = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        monkeypatch.setattr(server, "_fetch_item_refresh", AsyncMock(return_value={"current_bid": 2000, "bid_count": 14, "ends_at": end}))
        response = await api.post(f"/api/auctions/{doc['id']}/refresh", headers={"X-Device-Id": "owner"})
        assert response.status_code == 200, response.text
        result = response.json()
        assert (result["current_bid"], result["bid_count"], result["ends_at"]) == (2000, 14, end)
        assert result["buyer_fee"] == 100
        assert result["total_now"] == 2600
        assert (await db.live_auctions.find_one({"url": doc["url"]}))["ends_at"] == end
    run_case(case)


def test_refresh_failure_leaves_watch_unchanged(run_case, monkeypatch):
    async def case(db, api):
        doc = watch()
        await db.auctions.insert_one(doc)
        monkeypatch.setattr(server, "_fetch_item_refresh", AsyncMock(side_effect=RuntimeError("network unavailable")))
        response = await api.post(f"/api/auctions/{doc['id']}/refresh", headers={"X-Device-Id": "owner"})
        assert response.status_code == 502
        assert (await db.auctions.find_one({"id": doc["id"]}))["current_bid"] == 1000
    run_case(case)


def test_exclusion_preserves_shared_history_and_other_devices(run_case):
    async def case(db, api):
        sold = {"url": "https://example.com/sold", "title": "Volvo EC220", "source": "Klaravik", "final_bid": 2000}
        await db.sold_items.insert_one(sold)
        await db.device_valuations.insert_many([
            {"device_id": "owner", "url": "item", "valuation": {}},
            {"device_id": "other", "url": "item", "valuation": {}},
        ])
        assert len(await server._find_comps("Volvo EC220", device_id="owner")) == 1
        response = await api.request("DELETE", "/api/sold-items", json={"url": sold["url"]}, headers={"X-Device-Id": "owner"})
        assert response.status_code == 200
        assert await db.sold_items.count_documents({}) == 1
        assert await db.device_valuations.count_documents({"device_id": "other"}) == 1
        assert await db.device_valuations.count_documents({"device_id": "owner"}) == 0
        assert await server._find_comps("Volvo EC220", device_id="owner") == []
        assert len(await server._find_comps("Volvo EC220", device_id="other")) == 1
    run_case(case)


def test_snapshot_survives_cleanup_and_changed_end_times(run_case):
    async def case(db, api):
        now = datetime.now(timezone.utc)
        docs = [{"url": f"https://example.com/{i}", "title": f"Objekt {i}", "source": "Klaravik",
                 "current_bid": 100, "ends_at": (now + timedelta(hours=i + 1)).isoformat(),
                 "first_seen": (now - timedelta(hours=1)).isoformat()} for i in range(3)]
        await db.live_auctions.insert_many(docs)
        params = {"snapshot": now.isoformat(), "page_size": 1, "page": 1}
        first = (await api.get("/api/live/auctions", params=params)).json()
        assert first["items"][0]["url"] == docs[0]["url"]
        await db.live_auctions.delete_one({"url": docs[1]["url"]})
        await db.live_auctions.update_one({"url": docs[0]["url"]}, {"$set": {"ends_at": (now + timedelta(days=2)).isoformat(), "current_bid": 200}})
        await db.live_auctions.insert_one({**docs[2], "_id": "new", "url": "https://example.com/new"})
        again = (await api.get("/api/live/auctions", params=params)).json()
        second = (await api.get("/api/live/auctions", params={**params, "page": 2})).json()
        assert again["items"][0]["url"] == docs[0]["url"]
        assert again["items"][0]["current_bid"] == 200
        assert second["items"][0]["url"] == docs[1]["url"]
        assert again["total"] == second["total"] == 3
    run_case(case)


def test_push_retry_and_successful_deduplication(run_case, monkeypatch):
    async def case(db, api):
        send = AsyncMock(side_effect=[RuntimeError("temporary error"), {}])
        monkeypatch.setattr(server.commhub, "send_to_token", send)
        assert not await server._send_push("event", "token", title="Test")
        assert await server._send_push("event", "token", title="Test")
        assert await server._send_push("event", "token", title="Test")
        assert send.await_count == 2
        assert (await db.push_events.find_one({"event_id": "event"}))["status"] == "sent"
    run_case(case)


def test_concurrent_pushes_send_only_once_and_expired_lease_retries(run_case, monkeypatch):
    async def case(db, api):
        send = AsyncMock(return_value={})
        monkeypatch.setattr(server.commhub, "send_to_token", send)
        await asyncio.gather(*(server._send_push("event", "token", title="Test") for _ in range(5)))
        assert send.await_count == 1
        await db.push_events.insert_one({"event_id": "interrupted", "status": "sending", "lease_until": datetime.now(timezone.utc) - timedelta(seconds=1)})
        assert await server._send_push("interrupted", "token", title="Test")
        assert send.await_count == 2
    run_case(case)


def test_failed_notification_does_not_advance_watch(run_case, monkeypatch):
    async def case(db, api):
        doc = watch(notified_bid=1000, ends_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
        await db.auctions.insert_one(doc)
        await db.devices.insert_one({"device_id": "owner", "token": "token"})
        await db.live_auctions.insert_one({"url": doc["url"], "current_bid": 2000})
        monkeypatch.setattr(server.commhub, "enabled", lambda: True)
        monkeypatch.setattr(server.commhub, "send_to_token", AsyncMock(side_effect=RuntimeError("temporary")))
        async def sleep(seconds):
            if seconds == 60:
                raise asyncio.CancelledError()
        monkeypatch.setattr(server, "asyncio", SimpleNamespace(sleep=sleep))
        with pytest.raises(asyncio.CancelledError):
            await server._watch_notifier()
        stored = await db.auctions.find_one({"id": doc["id"]})
        assert stored["notified_bid"] == 1000
        assert not stored.get("notified_steps")
    run_case(case)


def test_failed_manual_creation_does_not_store_fake_data(run_case, monkeypatch):
    async def case(db, api):
        monkeypatch.setattr(scraper, "scrape_auction", lambda *args: (_ for _ in ()).throw(ValueError("Kunde inte läsa pris")))
        response = await api.post("/api/auctions", json={"url": "https://example.com/item"}, headers={"X-Device-Id": "owner"})
        assert response.status_code == 422
        assert await db.auctions.count_documents({}) == 0
    run_case(case)


def test_jsonld_zero_price_is_not_replaced():
    data = scraper._extract("https://example.com/item", '<html><title>Objekt</title><script type="application/ld+json">{"offers":{"price":0},"endDate":"2026-12-01T12:00:00Z"}</script><body>0 bud</body></html>')
    assert data["current_bid"] == 0
    assert data["bid_count"] == 0
    assert data["ends_at"] == "2026-12-01T12:00:00Z"


def test_tradera_search_encodes_query_and_does_not_cache_http_failures(monkeypatch):
    import tradera
    original_client = httpx.AsyncClient
    requests = []
    html = '<html><body><div><div><div><a href="/item/1/123/cykel">Cykel & delar</a><span>3 bud 100 kr</span></div></div></div></body></html>'
    def respond(request):
        requests.append(request)
        return httpx.Response(503 if len(requests) == 1 else 200, text=html if request.url.params["page"] == "1" else "")
    monkeypatch.setattr(tradera.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(tradera, "_cache", {})
    async def case():
        assert await tradera.search("cykel & delar") == []
        assert "cykel & delar" not in tradera._cache
        items = await tradera.search("cykel & delar")
        assert len(items) == 1
        assert items[0]["source"] == "Tradera"
        assert all(r.url.params["q"] == "cykel & delar" for r in requests)
    asyncio.run(case())


def test_tradera_winter_times_use_swedish_timezone():
    import tradera
    assert tradera._parse_end("15 dec 12:00").endswith("11:00:00+00:00")


def test_missing_ai_key_returns_actionable_error_without_external_fetch(run_case, monkeypatch):
    import valuation
    async def case(db, api):
        doc = watch()
        await db.auctions.insert_one(doc)
        monkeypatch.setattr(valuation, "EMERGENT_LLM_KEY", None)
        ingest = AsyncMock()
        monkeypatch.setattr(server, "_ingest_auctionet_sold", ingest)
        response = await api.post(f"/api/auctions/{doc['id']}/analyze", headers={"X-Device-Id": "owner"})
        assert response.status_code == 503
        assert "EMERGENT_LLM_KEY" in response.json()["detail"]
        ingest.assert_not_awaited()
    run_case(case)


def test_missing_ai_library_is_reported(monkeypatch):
    import valuation
    monkeypatch.setattr(valuation, "EMERGENT_LLM_KEY", "test-placeholder")
    monkeypatch.setattr(valuation, "LlmChat", None)
    with pytest.raises(valuation.ValuationUnavailable, match="bibliotek"):
        valuation.require_configured()
