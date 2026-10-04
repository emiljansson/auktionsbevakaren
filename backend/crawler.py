"""Gentle background crawler.

Fetches ONE auction item every CRAWL_INTERVAL_SECONDS (default 10s) and rotates
through the enabled source adapters, so we never hammer the auction sites.
Periodically re-runs discovery to pick up new listings. Observed price changes
are appended to each auction's price_history, giving a live bid curve over time.
"""
import os
import asyncio
import logging
from datetime import datetime, timezone

import adapters

logger = logging.getLogger("crawler")

INTERVAL = int(os.environ.get("CRAWL_INTERVAL_SECONDS", "10"))
DISCOVERY_EVERY = int(os.environ.get("CRAWL_DISCOVERY_SECONDS", "600"))


def _now():
    return datetime.now(timezone.utc)


def _iso():
    return _now().isoformat()


class Crawler:
    def __init__(self, db):
        self.db = db
        self.queue = []            # auction URLs to refresh, round-robin
        self.task = None
        self.running = False
        self.force_discovery = False
        self.last_tick = None
        self.last_discovery = None
        self.last_action = "startar..."
        self.last_error = None
        self.tick_count = 0

    def start(self):
        if self.task is None:
            self.running = True
            self.task = asyncio.create_task(self._loop())
            logger.info("Crawler started, interval=%ss", INTERVAL)

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            self.task = None

    async def _loop(self):
        await asyncio.sleep(2)
        try:
            await self._discover()
        except Exception as e:
            self.last_error = str(e)
            logger.exception("initial discovery failed")
        while self.running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.last_error = str(e)
                logger.exception("tick failed")
            await asyncio.sleep(INTERVAL)

    def _secs_since(self, iso):
        if not iso:
            return 1e9
        return (_now() - datetime.fromisoformat(iso)).total_seconds()

    async def _tick(self):
        self.tick_count += 1
        self.last_tick = _iso()
        if self.force_discovery or self._secs_since(self.last_discovery) > DISCOVERY_EVERY or not self.queue:
            await self._discover()
            return
        url = self.queue.pop(0)
        self.queue.append(url)  # rotate
        await self._refresh_one(url)

    async def _discover(self):
        self.force_discovery = False
        self.last_discovery = _iso()
        total = 0
        for ad in adapters.ENABLED:
            try:
                items = await ad.discover()
            except Exception as e:
                self.last_error = f"{ad.source}: {e}"
                logger.warning("discover %s failed: %s", ad.source, e)
                continue
            for it in items:
                await self._upsert(it)
                if it["url"] not in self.queue:
                    self.queue.append(it["url"])
                total += 1
        self.last_action = f"discovery: {total} objekt"

    async def _upsert(self, it):
        now = _iso()
        existing = await self.db.live_auctions.find_one({"url": it["url"]})
        bid = it.get("current_bid") or 0
        vat = 25
        buyer_fee = round(bid * 0.10)
        total_now = round(bid * (1 + vat / 100) + buyer_fee)
        if existing:
            update = {
                "title": it["title"], "image": it.get("image"),
                "current_bid": bid, "buyer_fee": buyer_fee, "total_now": total_now,
                "last_seen": now,
            }
            if bid and bid != existing.get("current_bid"):
                hist = existing.get("price_history", []) + [{"t": now, "bid": bid}]
                update["price_history"] = hist[-200:]
            await self.db.live_auctions.update_one({"url": it["url"]}, {"$set": update})
        else:
            await self.db.live_auctions.insert_one({
                "source": it["source"], "title": it["title"], "image": it.get("image"),
                "current_bid": bid, "currency": "SEK", "vat": vat,
                "buyer_fee": buyer_fee, "total_now": total_now, "url": it["url"],
                "first_seen": now, "last_seen": now, "last_checked": now,
                "price_history": [{"t": now, "bid": bid}] if bid else [],
            })

    async def _refresh_one(self, url):
        doc = await self.db.live_auctions.find_one({"url": url})
        if not doc:
            if url in self.queue:
                self.queue.remove(url)
            return
        ad = adapters.adapter_for_source(doc["source"])
        if not ad:
            return
        now = _iso()
        try:
            res = await ad.refresh_item(url)
        except Exception as e:
            self.last_error = f"refresh {doc['source']}: {e}"
            await self.db.live_auctions.update_one({"url": url}, {"$set": {"last_checked": now}})
            self.last_action = f"uppdaterade (fel): {doc['title'][:40]}"
            return
        update = {"last_checked": now, "last_seen": now}
        if res and res.get("current_bid"):
            bid = res["current_bid"]
            vat = doc.get("vat", 25)
            update["current_bid"] = bid
            update["buyer_fee"] = round(bid * 0.10)
            update["total_now"] = round(bid * (1 + vat / 100) + round(bid * 0.10))
            if bid != doc.get("current_bid"):
                hist = doc.get("price_history", []) + [{"t": now, "bid": bid}]
                update["price_history"] = hist[-200:]
        await self.db.live_auctions.update_one({"url": url}, {"$set": update})
        self.last_action = f"uppdaterade: {doc['title'][:40]}"

    def status(self):
        return {
            "running": self.running,
            "interval_seconds": INTERVAL,
            "discovery_interval_seconds": DISCOVERY_EVERY,
            "tick_count": self.tick_count,
            "queue_size": len(self.queue),
            "last_tick": self.last_tick,
            "last_discovery": self.last_discovery,
            "last_action": self.last_action,
            "last_error": self.last_error,
            "sources": adapters.SOURCE_STATUS,
        }
