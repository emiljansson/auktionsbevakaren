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

# Buyer premium (slagavgift/inropsavgift) + VAT per source. Most Swedish B2B
# auction sites quote bids EXCL. VAT and add 25% VAT on both bid and fee.
# Where a site publicly exposes the exact per-object fee we dig it out in the
# adapter and pass it here via fee inputs (see FEE_INPUT_KEYS); otherwise we only
# apply a concrete % where it is publicly documented and flag the rest as
# "tillkommer". Totals are estimates ("ca").
#   fee_excl         : concrete buyer fee for this object, EXCL VAT
#   fee_vat          : VAT % added on the fee (default 25)
#   fee_pct/min/max  : percentage fee (EXCL VAT) with optional floor/ceiling
#   vat              : VAT % on the bid itself
FEE_INPUT_KEYS = ("vat", "fee_excl", "fee_vat", "fee_pct", "fee_min", "fee_max")

FEE_CONFIG = {
    "Budi":        {"vat": 25, "fee_pct": 16, "fee_min": 195, "note": "Moms 25% + serviceavgift ca 16% (min 195 kr) + moms"},
    "PS Auction":  {"vat": 25, "note": "Moms 25% + budavgift tillkommer (se auktionssidan)"},
    "Klaravik":    {"vat": 25, "note": "Moms 25% + inropsavgift tillkommer (visas vid bud)"},
    "Blinto":      {"vat": 25, "note": "Moms 25% + slagavgift tillkommer (se auktionssidan)"},
    "Fabeo":       {"vat": 25, "note": "Moms 25% + slagavgift tillkommer (se auktionssidan)"},
    "Netauktion":  {"vat": 25, "note": "Moms 25% + slagavgift tillkommer (se auktionssidan)"},
    "Retrade":     {"vat": 25, "note": "Moms 25% + auktionsavgift + 250 kr/faktura tillkommer"},
    "Tovek":       {"vat": 25, "note": "Moms 25% + fast slagavgift tillkommer (se auktionssidan)"},
    "Tradera":     {"vat": 0,  "fee_pct": 0, "fee_min": 0, "note": "Privatköp – pris som visat (ingen slagavgift)"},
}


def _fmt_kr(n):
    return f"{int(round(n)):,}".replace(",", " ")


def compute_fees(source, bid, it):
    """Return the VAT/fee/total breakdown for an item. buyer_fee is incl. VAT.
    fee_known=False means a buyer fee applies but the exact amount isn't known.
    `it` may carry per-object fee inputs (FEE_INPUT_KEYS) dug out by the adapter."""
    cfg = FEE_CONFIG.get(source, {"vat": 25, "note": "Moms 25% + ev. avgift tillkommer"})
    vat = it.get("vat") if it.get("vat") is not None else cfg.get("vat", 25)
    bid = bid or it.get("starting_price") or 0
    vat_amount = round(bid * vat / 100)
    bid_incl = bid + vat_amount
    fee_vat = it.get("fee_vat") if it.get("fee_vat") is not None else cfg.get("fee_vat", 25)

    fee_excl = None
    if it.get("fee_excl") is not None:
        fee_excl = it["fee_excl"]
    else:
        pct = it.get("fee_pct") if it.get("fee_pct") is not None else cfg.get("fee_pct")
        if pct is not None:
            fmin = it.get("fee_min") if it.get("fee_min") is not None else cfg.get("fee_min", 0)
            fmax = it.get("fee_max") if it.get("fee_max") is not None else cfg.get("fee_max")
            fee_excl = max(bid * pct / 100, fmin or 0)
            if fmax:
                fee_excl = min(fee_excl, fmax)

    if fee_excl is not None:
        buyer_fee, fee_known = round(fee_excl * (1 + fee_vat / 100)), True
        if buyer_fee > 0:
            note = (f"Moms {vat}% på bud + slagavgift {_fmt_kr(buyer_fee)} kr (inkl. moms)"
                    if vat else f"Slagavgift {_fmt_kr(buyer_fee)} kr (inkl. moms)")
        else:
            note = cfg.get("note")
    else:
        buyer_fee, fee_known, note = None, False, cfg.get("note")
    total_now = bid_incl + (buyer_fee or 0)
    return {"vat": vat, "vat_amount": vat_amount, "bid_incl": bid_incl,
            "buyer_fee": buyer_fee, "fee_known": fee_known,
            "total_now": total_now, "fee_note": note}




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
            return "vilar"
        if not self.queue:
            return "söker nya objekt"
        return "uppdaterar bud"

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
        self.ps_task = None
        self.ps_status = "väntar"
        self.running = False
        self.tick_count = 0
        self.last_tick = None

    def start(self):
        if self.task is None:
            self.running = True
            self.task = asyncio.create_task(self._loop())
            self.ps_task = asyncio.create_task(self._ps_loop())
            logger.info("Crawler started: %s sites, interval=%ss, rest=%ss",
                        len(self.states), INTERVAL, REST_SECONDS)

    async def stop(self):
        self.running = False
        for t in (self.task, self.ps_task):
            if t:
                t.cancel()
        self.task = None
        self.ps_task = None

    async def _ps_loop(self):
        """PS Auction runs on its own task so its heavy, slow ScrapingBee crawl
        never blocks the other sites. collect() is time-gated internally and
        returns [] cheaply when no full/soon pass is due."""
        import ps_playwright
        await asyncio.sleep(5)
        while self.running:
            try:
                items = await asyncio.wait_for(ps_playwright.collect(), timeout=900)
                if items:
                    for it in items:
                        await self._upsert(it)
                    self.ps_status = f"{len(items)} objekt ({_iso()[11:16]})"
                    logger.info("PS Auction task upserted %s items", len(items))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.ps_status = f"fel: {e}"
                logger.warning("PS task failed: %s", e)
            await asyncio.sleep(1800)  # re-check gates every 30 min

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
        ).to_list(20000)
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
        image = it.get("image")
        if isinstance(image, str) and image.startswith("http://"):
            image = "https://" + image[len("http://"):]
        bid = it.get("current_bid") or 0
        fixed_price = bool(it.get("fixed_price"))
        existing = await self.db.live_auctions.find_one({"url": it["url"]})
        incoming_fi = {k: it[k] for k in FEE_INPUT_KEYS if it.get(k) is not None}
        prev_fi = existing.get("fee_inputs", {}) if existing else {}
        fee_inputs = {**prev_fi, **incoming_fi}
        fee_ctx = {**fee_inputs}
        if it.get("starting_price") is not None:
            fee_ctx["starting_price"] = it["starting_price"]
        f = compute_fees(it["source"], bid, fee_ctx)
        if existing:
            keep_bid = (not bid) and existing.get("current_bid")
            update = {
                "title": it["title"], "image": image,
                "fixed_price": fixed_price,
                "fee_inputs": fee_inputs,
                "last_seen": now,
            }
            if not keep_bid:
                update["current_bid"] = bid
                update["vat"] = f["vat"]
                update["vat_amount"] = f["vat_amount"]
                update["bid_incl"] = f["bid_incl"]
                update["buyer_fee"] = f["buyer_fee"]
                update["fee_known"] = f["fee_known"]
                update["fee_note"] = f["fee_note"]
                update["total_now"] = f["total_now"]
            if it.get("ends_at"):
                update["ends_at"] = it["ends_at"]
            if it.get("bid_count") is not None:
                update["bid_count"] = it["bid_count"]
            if it.get("starting_price") is not None:
                update["starting_price"] = it["starting_price"]
            if bid and bid != existing.get("current_bid"):
                hist = existing.get("price_history", []) + [{"t": now, "bid": bid}]
                update["price_history"] = hist[-200:]
            await self.db.live_auctions.update_one({"url": it["url"]}, {"$set": update})
        else:
            await self.db.live_auctions.insert_one({
                "source": it["source"], "title": it["title"], "image": image,
                "current_bid": bid, "currency": "SEK", "vat": f["vat"],
                "vat_amount": f["vat_amount"], "bid_incl": f["bid_incl"],
                "buyer_fee": f["buyer_fee"], "fee_known": f["fee_known"],
                "fee_note": f["fee_note"], "total_now": f["total_now"], "url": it["url"],
                "fee_inputs": fee_inputs,
                "ends_at": it.get("ends_at"), "bid_count": it.get("bid_count"),
                "starting_price": it.get("starting_price"), "fixed_price": fixed_price,
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
        if res:
            new_fi = {k: res[k] for k in FEE_INPUT_KEYS if res.get(k) is not None}
            new_bid = res.get("current_bid")
            if new_bid or new_fi:
                bid = new_bid or doc.get("current_bid") or 0
                fee_inputs = {**doc.get("fee_inputs", {}), **new_fi}
                fee_ctx = {**fee_inputs, "starting_price": doc.get("starting_price")}
                f = compute_fees(doc["source"], bid, fee_ctx)
                update["fee_inputs"] = fee_inputs
                update["vat"] = f["vat"]
                update["vat_amount"] = f["vat_amount"]
                update["bid_incl"] = f["bid_incl"]
                update["buyer_fee"] = f["buyer_fee"]
                update["fee_known"] = f["fee_known"]
                update["fee_note"] = f["fee_note"]
                update["total_now"] = f["total_now"]
                if new_bid:
                    update["current_bid"] = new_bid
                    if new_bid != doc.get("current_bid"):
                        hist = doc.get("price_history", []) + [{"t": now, "bid": new_bid}]
                        update["price_history"] = hist[-200:]
        await self.db.live_auctions.update_one({"url": url}, {"$set": update})
        st.last_action = f"uppdaterade: {doc['title'][:34]}"

    async def refresh_url(self, url):
        """Refresh a single known item on demand (e.g. right when a user watches
        it) so its bid + fee are learned immediately instead of on the next pass."""
        doc = await self.db.live_auctions.find_one({"url": url})
        if not doc:
            return
        st = next((s for s in self.states if s.source == doc.get("source")), None)
        if st:
            await self._refresh_one(st, url)

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
            "ps_auction": self.ps_status,
            "sources": adapters.SOURCE_STATUS,
        }


def _iso_in(seconds):
    from datetime import timedelta
    return (_now() + timedelta(seconds=seconds)).isoformat()
