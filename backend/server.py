from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import asyncio
import logging
import uuid
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import scraper
import valuation

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI(title="Auktionsbevakning API")
api_router = APIRouter(prefix="/api")

logger = logging.getLogger("auktion")
logging.basicConfig(level=logging.INFO)

SOURCE_LIST = list(scraper.SOURCES.values())


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
    bid_count: int
    currency: str = "SEK"
    vat: int = 25
    buyer_fee: int = 0
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
async def list_auctions(source: Optional[str] = None):
    query = {}
    if source and source != "Alla":
        query["source"] = source
    docs = await db.auctions.find(query).sort("created_at", -1).to_list(500)
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
async def delete_auction(auction_id: str):
    res = await db.auctions.delete_one({"id": auction_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Bevakning hittades inte.")
    return {"ok": True}


@api_router.get("/stats")
async def stats():
    docs = await db.auctions.find({}, {"_id": 0}).to_list(500)
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


app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
