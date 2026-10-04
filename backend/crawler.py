"""Gentle per-site background crawler.

Model (as specified by the product owner):
  - Each enabled site fetches ONE product every CRAWL_INTERVAL_SECONDS (default 10s).
  - When a site has fetched every product once (a full pass), it RESTS for
    CRAWL_REST_SECONDS (default 4 hours) before discovering + fetching again,
    to save traffic.
Observed price changes are appended to each auction's price_history, and the
auction end time (ends_at) is captured so a future push layer can alert on
auctions nearing their close.
"""
import os
import asyncio
import logging
from datetime import datetime, timezone

import adapters

logger = logging.getLogger("crawler")

INTERVAL = int(os.environ.get("CRAWL_INTERVAL_SECONDS", "10"))
REST_SECONDS = int(os.environ.get("CRAWL_REST_SECONDS", str(4 * 3600)))


def _now():
    return datetime.now(timezone.utc)


def _iso():
    return _now().isoformat()


def _secs_since(iso):
    if not iso:
        return 1e12
    try:
        return (_now() - datetime.fromisoformat(iso)).total_seconds()
    except Exception:
        return 1e12


class SiteState:
    def __init__(self, adapter):
        self.adapter = adapter
        self.source = adapter.source
        self.queue = []
        self.resting_until = None   # iso
        self.last_discovery = None
        self.last_action = "väntar"
        self.items = 0
        self.pass_done = 0
        self.error = None

    @property
    def phase(self):
        if self.resting_until and _secs_since(self.resting_until) < 0:
            return "resting"
        if not self.queue:
            return "discovering"
        return "crawling"

    def to_dict(self):
        rest_left = None
        if self.resting_until and _secs_since(self.resting_until) < 0:
            rest_left = int(-_secs_since(self.resting_until))
        return {
            "source": self.source, "phase": self.phase, "queue": len(self.queue),
            "items": self.items, "passes": self.pass_done,
            "rest_seconds_left": rest_left, "last_discovery": self.last_discovery,
            "last_action": self.last_action, "error": self.error,
        }


class Crawler:
    def __init__(self, db):
        self.db = db
        self.states = [SiteState(a) for a in adapters.ENABLED]
        self.task = None
        self.running = False
        self.tick_count = 0
        self.last_tick = None

    def start(self):
        if self.task is None:
            self.running = True
            self.task = asyncio.create_task(self._loop())
            logger.info("Crawler started: %s sites, interval=%ss, rest=%ss",
                        len(self.states), INTERVAL, REST_SECONDS)

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            self.task = None

    async def _loop(self):
        await asyncio.sleep(2)
        while self.running:
            self.tick_count += 1
            self.last_tick = _iso()
            if self.tick_count % 30 == 1:
                await self._cleanup()
            # Each site advances by one unit of work per tick (in parallel).
            await asyncio.gather(*[self._step(st) for st in self.states], return_exceptions=True)
            await asyncio.sleep(INTERVAL)

    async def _cleanup(self):
        """Remove live auctions whose end time has passed, so only ongoing ones show."""
        now = _now()
        docs = await self.db.live_auctions.find(
            {"ends_at": {"$ne": None}}, {"url": 1, "ends_at": 1}
        ).to_list(5000)
        dead = []
        for d in docs:
            try:
                end = datetime.fromisoformat(str(d["ends_at"]).replace("Z", "+00:00"))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                if end < now:
                    dead.append(d["url"])
            except Exception:
                continue
        if dead:
            await self.db.live_auctions.delete_many({"url": {"$in": dead}})
            for st in self.states:
                st.queue = [u for u in st.queue if u not in set(dead)]
            logger.info("Cleanup removed %s ended auctions", len(dead))

    async def _step(self, st):
        try:
            if st.resting_until and _secs_since(st.resting_until) < 0:
                return  # resting
            if not st.queue:
                await self._discover(st)
                return
            url = st.queue.pop(0)
            await self._refresh_one(st, url)
            if not st.queue:
                st.resting_until = _iso_in(REST_SECONDS)
                st.pass_done += 1
                st.last_action = f"pass klar – vilar {REST_SECONDS // 3600}h"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            st.error = str(e)
            logger.exception("step %s failed", st.source)

    async def _discover(self, st):
        st.resting_until = None
        st.last_discovery = _iso()
        try:
            items = await st.adapter.discover()
        except Exception as e:
            st.error = f"discover: {e}"
            st.last_action = "discovery misslyckades"
            st.resting_until = _iso_in(600)  # retry in 10 min
            logger.warning("discover %s failed: %s", st.source, e)
            return
        st.queue = []
        for it in items:
            await self._upsert(it)
            st.queue.append(it["url"])
        st.items = len(items)
        st.last_action = f"discovery: {len(items)} objekt"

    async def _upsert(self, it):
        now = _iso()
        bid = it.get("current_bid") or 0
        vat = 25
        buyer_fee = round(bid * 0.10)
        total_now = round(bid * (1 + vat / 100) + buyer_fee)
        existing = await self.db.live_auctions.find_one({"url": it["url"]})
        if existing:
            keep_bid = (not bid) and existing.get("current_bid")
            update = {
                "title": it["title"], "image": it.get("image"),
                "last_seen": now,
            }
            if not keep_bid:
                update["current_bid"] = bid
                update["buyer_fee"] = buyer_fee
                update["total_now"] = total_now
            if it.get("ends_at"):
                update["ends_at"] = it["ends_at"]
            if it.get("bid_count") is not None:
                update["bid_count"] = it["bid_count"]
            if bid and bid != existing.get("current_bid"):
                hist = existing.get("price_history", []) + [{"t": now, "bid": bid}]
                update["price_history"] = hist[-200:]
            await self.db.live_auctions.update_one({"url": it["url"]}, {"$set": update})
        else:
            await self.db.live_auctions.insert_one({
                "source": it["source"], "title": it["title"], "image": it.get("image"),
                "current_bid": bid, "currency": "SEK", "vat": vat,
                "buyer_fee": buyer_fee, "total_now": total_now, "url": it["url"],
                "ends_at": it.get("ends_at"), "bid_count": it.get("bid_count"),
                "first_seen": now, "last_seen": now, "last_checked": now,
                "price_history": [{"t": now, "bid": bid}] if bid else [],
            })

    async def _refresh_one(self, st, url):
        doc = await self.db.live_auctions.find_one({"url": url})
        if not doc:
            return
        now = _iso()
        try:
            res = await st.adapter.refresh_item(url)
        except Exception as e:
            st.error = f"refresh: {e}"
            await self.db.live_auctions.update_one({"url": url}, {"$set": {"last_checked": now}})
            return
        update = {"last_checked": now, "last_seen": now}
        if res and res.get("ends_at"):
            update["ends_at"] = res["ends_at"]
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
        st.last_action = f"uppdaterade: {doc['title'][:34]}"

    def sync_now(self):
        for st in self.states:
            st.resting_until = None
            st.queue = []

    def status(self):
        actions = " · ".join(f"{s.source}: {s.phase}" for s in self.states)
        return {
            "running": self.running,
            "interval_seconds": INTERVAL,
            "rest_seconds": REST_SECONDS,
            "tick_count": self.tick_count,
            "last_tick": self.last_tick,
            "last_action": actions,
            "sites": [s.to_dict() for s in self.states],
            "sources": adapters.SOURCE_STATUS,
        }


def _iso_in(seconds):
    from datetime import timedelta
    return (_now() + timedelta(seconds=seconds)).isoformat()
