from fastapi import FastAPI, APIRouter, HTTPException, Depends
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import asyncio
import logging
import time
import uuid
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone, timedelta

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import scraper
import valuation
import tradera
import blocket
import commhub
from fastapi import Header
from crawler import Crawler

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]
crawler = Crawler(db)

app = FastAPI(title="Auktionsbevakning API")
api_router = APIRouter(prefix="/api")

logger = logging.getLogger("auktion")
logging.basicConfig(level=logging.INFO)

SOURCE_LIST = list(scraper.SOURCES.values())
_tradera_seen = {}  # q(lower) -> last upsert timestamp (throttles Tradera fetch)
_blocket_seen = {}  # q(lower) -> last upsert timestamp (throttles Blocket fetch)


# ---------- Models ----------
class WatchCreate(BaseModel):
    url: Optional[str] = None
    keyword: Optional[str] = None
    category: Optional[str] = "Övrigt"


class Auction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str
    title: str
    image: Optional[str] = None
    category: str = "Övrigt"
    current_bid: int
    starting_price: Optional[int] = None
    bid_count: int
    currency: str = "SEK"
    vat: int = 25
    buyer_fee: Optional[int] = 0
    fee_known: bool = True
    fee_note: Optional[str] = None
    total_now: int
    ends_at: Optional[str] = None
    url: str = ""
    scraped: bool = False
    bid_history: List[dict] = []
    valuation: Optional[dict] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _clean(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc


def _bid_history(doc: dict) -> list:
    """Real observed price points, mapped to the chart's {timestamp, bid} shape."""
    return [{"timestamp": h.get("t"), "bid": h.get("bid")}
            for h in (doc.get("price_history") or []) if h.get("bid") is not None]


def _real_bid_count(doc: dict) -> int:
    """The site's actual bid count when known, else the number of observed points."""
    bc = doc.get("bid_count")
    if bc:
        return bc
    return len(doc.get("price_history") or []) or (1 if doc.get("current_bid") else 0)


class PushRegister(BaseModel):
    token: str
    platform: Optional[str] = "web"


async def require_device(x_device_id: Optional[str] = Header(None)) -> str:
    if not x_device_id:
        raise HTTPException(status_code=400, detail="Enhets-ID saknas.")
    return x_device_id


# ---------- Push notifications (per-device, no login) ----------
@api_router.get("/push/config")
async def push_config():
    if not commhub.enabled():
        raise HTTPException(status_code=503, detail="Push är inte konfigurerat.")
    try:
        cfg = await commhub.get_web_config()
    except Exception:
        logger.exception("push config")
        raise HTTPException(status_code=502, detail="Kunde inte hämta push-konfiguration.")
    return {"config": cfg.get("config"), "vapidKey": cfg.get("vapidKey"), "app_id": commhub.APP_ID}


@api_router.post("/push/register")
async def push_register(payload: PushRegister, device_id: str = Depends(require_device)):
    if not commhub.enabled():
        raise HTTPException(status_code=503, detail="Push är inte konfigurerat.")
    first_device = await db.devices.count_documents({}) == 0
    try:
        await commhub.register_token(payload.token, device_id, payload.platform or "web")
    except Exception:
        logger.exception("push register")
        raise HTTPException(status_code=502, detail="Kunde inte registrera för notiser.")
    now = datetime.now(timezone.utc).isoformat()
    await db.devices.update_one(
        {"device_id": device_id},
        {"$set": {"token": payload.token, "platform": payload.platform or "web", "updated_at": now},
         "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    # Migrate legacy shared watches to the very first registered device.
    if first_device:
        await db.auctions.update_many(
            {"$or": [{"device_id": {"$exists": False}}, {"device_id": None}]},
            {"$set": {"device_id": device_id}},
        )
    return {"ok": True}


@api_router.get("/push/status")
async def push_status(x_device_id: Optional[str] = Header(None)):
    dev = await db.devices.find_one({"device_id": x_device_id}) if x_device_id else None
    return {"registered": bool(dev and dev.get("token"))}


# ---------- Routes ----------
@api_router.get("/")
async def root():
    return {"message": "Auktionsbevakning API", "sources": SOURCE_LIST}


@api_router.get("/sources")
async def get_sources():
    return [{"name": n} for n in SOURCE_LIST]


@api_router.post("/scrape/preview")
async def scrape_preview(payload: WatchCreate):
    if not payload.url and not payload.keyword:
        raise HTTPException(status_code=400, detail="Ange en URL eller ett sökord.")
    data = await asyncio.to_thread(scraper.scrape_auction, payload.url, payload.keyword)
    return data


@api_router.get("/auctions", response_model=List[Auction])
async def list_auctions(source: Optional[str] = None, x_device_id: Optional[str] = Header(None)):
    if not x_device_id:
        return []
    query = {"device_id": x_device_id}
    if source and source != "Alla":
        query["source"] = source
    docs = await db.auctions.find(query).sort("created_at", -1).to_list(500)
    # Enrich each watched item with the latest fee/bid from the live feed so
    # Mina bevakningar always shows the current slagavgift, not a stale snapshot.
    urls = [d.get("url") for d in docs if d.get("url")]
    live_map = {}
    if urls:
        async for lv in db.live_auctions.find({"url": {"$in": urls}}, {"_id": 0}):
            live_map[lv["url"]] = lv
    # One-time per item: if the live doc hasn't learned the exact fee yet, learn
    # it now (self-limiting – stops once fee_known is True; failures are retried
    # at most every 6h via fee_last_try so Blinto fetches stay frugal).
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    unknown = [u for u, lv in live_map.items()
               if not lv.get("fee_known")
               and (not lv.get("fee_last_try") or lv["fee_last_try"] < cutoff)][:10]
    if unknown:
        await asyncio.gather(*[crawler.learn_fee(u) for u in unknown],
                             return_exceptions=True)
        async for lv in db.live_auctions.find({"url": {"$in": unknown}}, {"_id": 0}):
            live_map[lv["url"]] = lv
    for d in docs:
        lv = live_map.get(d.get("url"))
        if not lv:
            continue
        for k in ("current_bid", "starting_price", "vat", "buyer_fee",
                  "fee_known", "fee_note", "total_now", "ends_at", "bid_count"):
            if lv.get(k) is not None:
                d[k] = lv[k]
        d["bid_count"] = _real_bid_count(lv)
        d["bid_history"] = _bid_history(lv)
    # Soonest-ending first; items without an end time go last.
    docs.sort(key=lambda d: d.get("ends_at") or "9999")
    return [Auction(**_clean(d)) for d in docs]


@api_router.get("/auctions/{auction_id}", response_model=Auction)
async def get_auction(auction_id: str):
    doc = await db.auctions.find_one({"id": auction_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Bevakning hittades inte.")
    return Auction(**_clean(doc))


@api_router.post("/auctions", response_model=Auction)
async def create_auction(payload: WatchCreate):
    if not payload.url and not payload.keyword:
        raise HTTPException(status_code=400, detail="Ange en URL eller ett sökord.")
    data = await asyncio.to_thread(scraper.scrape_auction, payload.url, payload.keyword)
    history = scraper.generate_bid_history(data["current_bid"], data["bid_count"])
    auction = Auction(
        source=data["source"],
        title=data["title"],
        image=data.get("image"),
        category=payload.category or "Övrigt",
        current_bid=data["current_bid"],
        bid_count=data["bid_count"],
        vat=data["vat"],
        buyer_fee=data["buyer_fee"],
        total_now=data["total_now"],
        ends_at=data.get("ends_at"),
        url=data.get("url", ""),
        scraped=data.get("scraped", False),
        bid_history=history,
    )
    await db.auctions.insert_one(auction.model_dump())
    return auction


@api_router.post("/auctions/{auction_id}/analyze", response_model=Auction)
async def analyze_auction(auction_id: str):
    doc = await db.auctions.find_one({"id": auction_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Bevakning hittades inte.")
    doc = _clean(doc)
    try:
        val = await valuation.valuate(
            doc["title"], doc["current_bid"], doc["source"], doc["total_now"]
        )
    except Exception as e:
        logger.exception("Valuation failed")
        raise HTTPException(status_code=502, detail=f"AI-analys misslyckades: {e}")
    now = datetime.now(timezone.utc).isoformat()
    await db.auctions.update_one(
        {"id": auction_id}, {"$set": {"valuation": val, "updated_at": now}}
    )
    doc["valuation"] = val
    doc["updated_at"] = now
    return Auction(**doc)


@api_router.post("/auctions/{auction_id}/refresh", response_model=Auction)
async def refresh_auction(auction_id: str):
    doc = await db.auctions.find_one({"id": auction_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Bevakning hittades inte.")
    doc = _clean(doc)
    if doc.get("url"):
        data = await asyncio.to_thread(scraper.scrape_auction, doc["url"], None)
        new_bid = max(doc["current_bid"], data["current_bid"])
    else:
        import random
        new_bid = int(doc["current_bid"] * random.uniform(1.0, 1.08))
    vat = doc.get("vat", 25)
    buyer_fee = round(new_bid * 0.10)
    total_now = round(new_bid * (1 + vat / 100) + buyer_fee)
    now = datetime.now(timezone.utc).isoformat()
    history = doc.get("bid_history", [])
    if new_bid > doc["current_bid"]:
        history = history + [{
            "timestamp": now, "bid": new_bid, "bidder": "Ny budgivare",
            "increment": new_bid - doc["current_bid"],
        }]
    bid_count = doc.get("bid_count", 0) + (1 if new_bid > doc["current_bid"] else 0)
    await db.auctions.update_one({"id": auction_id}, {"$set": {
        "current_bid": new_bid, "buyer_fee": buyer_fee, "total_now": total_now,
        "bid_count": bid_count, "bid_history": history, "updated_at": now,
    }})
    doc.update({
        "current_bid": new_bid, "buyer_fee": buyer_fee, "total_now": total_now,
        "bid_count": bid_count, "bid_history": history, "updated_at": now,
    })
    return Auction(**doc)


@api_router.delete("/auctions/{auction_id}")
async def delete_auction(auction_id: str, device_id: str = Depends(require_device)):
    res = await db.auctions.delete_one({"id": auction_id, "device_id": device_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Bevakning hittades inte.")
    return {"ok": True}


@api_router.get("/stats")
async def stats(x_device_id: Optional[str] = Header(None)):
    if not x_device_id:
        return {"total": 0, "total_value": 0, "sources_tracked": 0, "good_deals": 0}
    docs = await db.auctions.find({"device_id": x_device_id}, {"_id": 0}).to_list(500)
    total = len(docs)
    total_value = sum(d.get("total_now", 0) for d in docs)
    by_source = {}
    deals = 0
    for d in docs:
        by_source[d["source"]] = by_source.get(d["source"], 0) + 1
        val = d.get("valuation") or {}
        if val.get("market_position") == "under":
            deals += 1
    return {
        "total": total,
        "total_value": total_value,
        "sources_tracked": len(by_source),
        "good_deals": deals,
    }


# ---------- Live auto-crawl ----------
@api_router.get("/live/status")
async def live_status():
    count = await db.live_auctions.count_documents({})
    s = crawler.status()
    s["live_count"] = count
    return s


@api_router.get("/live/auctions")
async def live_auctions(source: Optional[str] = None, q: Optional[str] = None, limit: int = 120):
    # On search, augment the feed with live Tradera + Blocket results (not background-crawled).
    if q and len((q or "").strip()) >= 2:
        key = q.strip().lower()
        if time.time() - _tradera_seen.get(key, 0) > 120:
            try:
                for it in await tradera.search(q):
                    it["search_q"] = key
                    await crawler._upsert(it)
                _tradera_seen[key] = time.time()
            except Exception:
                logger.exception("Tradera augment failed")
        if time.time() - _blocket_seen.get(key, 0) > 120:
            try:
                for it in await blocket.search(q):
                    it["search_q"] = key
                    await crawler._upsert(it)
                _blocket_seen[key] = time.time()
            except Exception:
                logger.exception("Blocket augment failed")
    query = {}
    if source and source != "Alla":
        query["source"] = source
    elif not q:
        query["source"] = {"$nin": ["Tradera", "Blocket"]}  # on-demand sources only appear on search
    if q:
        key = q.strip().lower()
        # Match on title OR items specifically fetched for this search (Tradera/Blocket
        # titles don't always contain the query term, e.g. "Yamaha" for "moped").
        query["$or"] = [
            {"title": {"$regex": q, "$options": "i"}},
            {"search_q": key},
        ]
    docs = await db.live_auctions.find(query, {"_id": 0}).to_list(20000)
    now = datetime.now(timezone.utc)
    far = datetime.max.replace(tzinfo=timezone.utc)

    def end_dt(d):
        e = d.get("ends_at")
        if not e:
            return None
        try:
            dt = datetime.fromisoformat(str(e).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    # Keep only ongoing (no end time, or end time in the future).
    live = [d for d in docs if (end_dt(d) is None or end_dt(d) > now)]
    live.sort(key=lambda d: (end_dt(d) is None, end_dt(d) or far))
    if q:
        # On search, round-robin across sources so on-demand fixed-price results
        # (Blocket, Tradera "köp nu") surface near the top instead of being
        # buried below every auction.
        from collections import OrderedDict
        groups = OrderedDict()
        for d in live:
            groups.setdefault(d["source"], []).append(d)
        merged, i = [], 0
        while len(merged) < len(live):
            for g in groups.values():
                if i < len(g):
                    merged.append(g[i])
            i += 1
        live = merged
    return live[:limit]


@api_router.post("/live/sync")
async def live_sync():
    crawler.sync_now()
    return {"ok": True, "message": "Synkning startad"}


@api_router.post("/live/watch", response_model=Auction)
async def watch_live(payload: WatchCreate, device_id: str = Depends(require_device)):
    dev = await db.devices.find_one({"device_id": device_id})
    if not (dev and dev.get("token")):
        raise HTTPException(status_code=403, detail="Aktivera pushnotiser för att kunna bevaka.")
    doc = await db.live_auctions.find_one({"url": payload.url}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Live-auktion hittades inte.")
    if await db.auctions.find_one({"url": payload.url, "device_id": device_id}):
        raise HTTPException(status_code=409, detail="Du bevakar redan denna auktion.")
    # Learn the exact slagavgift right away if it isn't known yet, so the watched
    # item shows the fee immediately (Klaravik/Fabeo/Netauktion learn it per item).
    if not doc.get("fee_known"):
        try:
            await crawler.learn_fee(payload.url)
            doc = await db.live_auctions.find_one({"url": payload.url}, {"_id": 0}) or doc
        except Exception:
            logger.exception("watch learn_fee failed")
    history = _bid_history(doc)
    auction = Auction(
        source=doc["source"], title=doc["title"], image=doc.get("image"),
        category=payload.category or "Övrigt", current_bid=doc["current_bid"],
        starting_price=doc.get("starting_price"),
        bid_count=_real_bid_count(doc),
        vat=doc.get("vat", 25), buyer_fee=doc.get("buyer_fee"),
        fee_known=doc.get("fee_known", True), fee_note=doc.get("fee_note"),
        total_now=doc.get("total_now", doc["current_bid"]),
        url=doc["url"], scraped=True, bid_history=history,
    )
    await db.auctions.insert_one({**auction.model_dump(), "device_id": device_id})
    return auction


app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await db.live_auctions.create_index("url", unique=True)
    await db.auctions.create_index("device_id")
    try:
        await db.auctions.create_index([("device_id", 1), ("url", 1)], unique=True)
    except Exception:
        logger.exception("could not create unique watch index")
    await db.devices.create_index("device_id", unique=True)
    crawler.start()
    asyncio.create_task(_watch_notifier())


async def _watch_notifier():
    """Per-device push for watched items: (1) when a new (higher) bid is placed,
    and (2) multi-step ending alerts at <60 min, <10 min and the last minute.
    Only the most urgent unseen ending-step fires, so users aren't spammed."""
    await asyncio.sleep(25)
    order = ["60", "10", "1"]
    while True:
        try:
            if commhub.enabled():
                now = datetime.now(timezone.utc)
                async for a in db.auctions.find({"device_id": {"$ne": None}}):
                    dev = await db.devices.find_one({"device_id": a.get("device_id")})
                    token = (dev or {}).get("token")
                    if not token:
                        continue
                    lv = await db.live_auctions.find_one({"url": a["url"]}, {"_id": 0})
                    if not lv:
                        continue
                    changes = {}
                    title = (lv.get("title") or a.get("title") or "Objekt")[:70]
                    data = {"url": a.get("url") or ""}

                    # (1) New bid
                    cur = lv.get("current_bid") or 0
                    last = a.get("notified_bid")
                    if last is None:
                        last = a.get("current_bid") or 0
                    if cur and cur > last:
                        try:
                            await commhub.send_to_token(
                                token, title="💰 Nytt bud",
                                body=f"{title}: nytt bud {_fmt_sek(cur)} kr", data=data)
                        except Exception:
                            logger.exception("new-bid push failed")
                        changes["notified_bid"] = cur

                    # (2) Multi-step ending alert (prefer the live end time)
                    end = _parse_iso(lv.get("ends_at") or a.get("ends_at"))
                    if end and end > now:
                        mins = (end - now).total_seconds() / 60
                        if mins <= 1:
                            step, label = "1", "slutar inom 1 minut!"
                        elif mins <= 10:
                            step, label = "10", "slutar inom 10 minuter"
                        elif mins <= 60:
                            step, label = "60", "slutar inom 1 timme"
                        else:
                            step = None
                        sent = set(a.get("notified_steps", []))
                        if step and step not in sent:
                            try:
                                await commhub.send_to_token(
                                    token, title="⏰ Auktion snart slut",
                                    body=f"{title} {label}", data=data)
                            except Exception:
                                logger.exception("ending push failed")
                            for s in order[: order.index(step) + 1]:
                                sent.add(s)
                            changes["notified_steps"] = list(sent)

                    if changes:
                        await db.auctions.update_one({"id": a["id"]}, {"$set": changes})
        except Exception:
            logger.exception("watch notifier loop")
        await asyncio.sleep(60)


def _fmt_sek(n):
    return f"{int(round(n)):,}".replace(",", " ")


def _parse_iso(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


@app.on_event("shutdown")
async def shutdown_db_client():
    await crawler.stop()
    client.close()
