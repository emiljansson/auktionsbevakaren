from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import re
import asyncio
import logging
import time
import uuid
import bcrypt
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
import auctionet
import commhub
from fastapi import Header
from crawler import Crawler

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]
crawler = Crawler(db)

app = FastAPI(title="Auktiomera API")
api_router = APIRouter(prefix="/api")

logger = logging.getLogger("auktion")
logging.basicConfig(level=logging.INFO)

SOURCE_LIST = list(scraper.SOURCES.values())
_tradera_seen = {}  # q(lower) -> last upsert timestamp (throttles Tradera fetch)
_blocket_seen = {}  # q(lower) -> last upsert timestamp (throttles Blocket fetch)
_auctionet_seen = {}  # q(lower) -> last upsert timestamp (throttles Auctionet fetch)


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


class AccountNameReq(BaseModel):
    name: str
    pin: Optional[str] = None


def _hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode("ascii"), bcrypt.gensalt(rounds=12)).decode("ascii")


def _verify_pin(pin: str, stored: str) -> bool:
    try:
        return bcrypt.checkpw(pin.encode("ascii"), stored.encode("ascii"))
    except (ValueError, TypeError, UnicodeError):
        return False


async def _hash_pin_async(pin: str) -> str:
    return await asyncio.to_thread(_hash_pin, pin)


async def _verify_pin_async(pin: str, stored: str) -> bool:
    return await asyncio.to_thread(_verify_pin, pin, stored)


class LiveAnalyzeReq(BaseModel):
    url: str


class KeywordWatchCreate(BaseModel):
    keyword: str


class DeleteSoldReq(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None
    source: Optional[str] = None


def _key_tokens(title: str):
    """Classify a title's tokens so comparisons lock onto the exact model:
      - model: real model codes (letters+digits, >=3 chars, e.g. R730, FH16,
        EC220, S60) — these are brand-specific and the key discriminator.
      - brand: ALL-CAPS makes (SCANIA, VOLVO, GMC).
      - weak: years/quantities (2019), axle configs (8x4), short codes (V8).
    Generic category words ('Lastbil', 'schaktflak', 'med') are ignored."""
    model, brand, weak = [], [], []
    for t in re.findall(r"[A-Za-z0-9ÅÄÖåäö]+", title or ""):
        if len(t) < 2:
            continue
        tl = t.lower()
        has_alpha = any(c.isalpha() for c in t)
        has_digit = any(c.isdigit() for c in t)
        if re.fullmatch(r"\d+x\d+", tl):
            weak.append(tl)
        elif has_alpha and has_digit and len(t) >= 3:
            model.append(tl)
        elif has_digit:
            weak.append(tl)
        elif t.isupper():
            brand.append(tl)
    return model, brand, weak


# Item type/category so a veteran CAR isn't matched with TRUCKS just because both
# are VOLVO. The leading Swedish type-noun ("Veteranbil", "Kranbil", "Traktor")
# maps to a coarse group; comps in a different recognised group are excluded.
_CATEGORY_GROUPS = {
    # passenger cars
    "bil": "bil", "personbil": "bil", "veteranbil": "bil", "sportbil": "bil",
    "sedan": "bil", "kombi": "bil", "halvkombi": "bil", "herrgårdsvagn": "bil",
    "cab": "bil", "cabriolet": "bil", "coupe": "bil", "coupé": "bil", "suv": "bil",
    "elbil": "bil", "sportbilar": "bil",
    # trucks / heavy road vehicles
    "lastbil": "lastbil", "kranbil": "lastbil", "betongbil": "lastbil",
    "skåpbil": "lastbil", "flakbil": "lastbil", "tankbil": "lastbil",
    "dragbil": "lastbil", "sopbil": "lastbil", "timmerbil": "lastbil",
    "budbil": "lastbil", "tippbil": "lastbil", "lastväxlare": "lastbil",
    "transportbil": "lastbil", "pickup": "lastbil", "lätt": "lastbil",
    # tractors
    "traktor": "traktor", "veterantraktor": "traktor", "traktorgrävare": "traktor",
    # heavy machines
    "grävmaskin": "maskin", "bandgrävare": "maskin", "hjulgrävare": "maskin",
    "minigrävare": "maskin", "hjullastare": "maskin", "grävlastare": "maskin",
    "lastmaskin": "maskin", "dumper": "maskin", "vält": "maskin", "väghyvel": "maskin",
    "teleskoplastare": "maskin", "gaffeltruck": "maskin", "truck": "maskin",
    # trailers / wagons
    "släp": "slap", "släpvagn": "slap", "traktorvagn": "slap", "kärra": "slap",
    "trailer": "slap", "vagn": "slap", "husvagn": "slap",
    # boats, buses, RVs
    "båt": "bat", "motorbåt": "bat", "segelbåt": "bat", "jolle": "bat",
    "buss": "buss", "minibuss": "buss", "husbil": "husbil",
    # toys / scale models / collectibles — so a real "veteranbil" is NOT matched
    # with a toy "modellbil" from Auctionet (wildly different price levels).
    "modellbil": "leksak", "modellbilar": "leksak", "modellfordon": "leksak",
    "leksaksbil": "leksak", "leksak": "leksak", "modell": "leksak",
    "plåtbil": "leksak", "plastbil": "leksak",
}


def _category(title: str):
    """First recognised Swedish type-noun in the title → coarse group, else None."""
    for t in re.findall(r"[A-Za-zÅÄÖåäö]+", title or ""):
        g = _CATEGORY_GROUPS.get(t.lower())
        if g:
            return g
    return None


async def _find_comps(title: str, source: Optional[str] = None, limit: int = 8):
    """Find real sold auctions that match `title` NARROWLY. We text-search a broad
    candidate pool, then require each candidate to share the exact model code
    (or the brand when no model code exists), so 'Lastbil med schaktflak 8x4
    SCANIA R730' only matches other SCANIA R730 trucks — never every lastbil."""
    if not title or not title.strip():
        return []
    model, brand, weak = _key_tokens(title)
    terms = model + brand + weak
    search = " ".join(terms) if terms else title
    try:
        cursor = db.sold_items.find(
            {"$text": {"$search": search}},
            {"score": {"$meta": "textScore"}, "_id": 0, "url": 1, "title": 1, "source": 1,
             "final_bid": 1, "total_final": 1, "bid_count": 1, "ends_at": 1},
        ).sort([("score", {"$meta": "textScore"})]).limit(80)
        cands = await cursor.to_list(80)
    except Exception:
        logger.exception("find_comps failed")
        return []
    if not model and not brand:
        qcat0 = _category(title)
        if qcat0:
            cands = [c for c in cands if _category(c.get("title", "")) in (None, qcat0)]
        return cands[:limit]
    model_set, brand_set, weak_set = set(model), set(brand), set(weak)
    qcat = _category(title)
    scored = []
    for c in cands:
        ctitle = c.get("title", "")
        ccat = _category(ctitle)
        # Different recognised item type (car vs truck vs tractor) → never a comp.
        if qcat and ccat and qcat != ccat:
            continue
        cm, cb, cw = _key_tokens(ctitle)
        shared_model = model_set & set(cm)
        shared_brand = brand_set & set(cb)
        shared_weak = weak_set & set(cw)
        if model_set:
            if not shared_model:
                continue
        elif not shared_brand:
            continue
        scored.append((len(shared_model) * 4 + len(shared_brand) * 2 + len(shared_weak), c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:limit]]


async def _ingest_auctionet_sold(title: str):
    """Ground valuations in Auctionet's REAL ended sales: fetch the archive for
    this item's key terms (brand+model) and upsert the genuine sales into
    `sold_items` so `_find_comps` can use them. Runs on-demand per valuation."""
    if not title or not title.strip():
        return
    model, brand, weak = _key_tokens(title)
    terms = (model + brand) or weak
    q = " ".join(terms[:4]) if terms else title.strip()[:60]
    try:
        sold = await auctionet.fetch_ended(q)
    except Exception:
        logger.exception("Auctionet ended ingest failed for %r", q)
        return
    now = datetime.now(timezone.utc).isoformat()
    for it in sold:
        await db.sold_items.update_one(
            {"url": it["url"]},
            {"$set": {
                "url": it["url"], "source": "Auctionet", "title": it["title"],
                "image": it.get("image"), "final_bid": it["final_bid"],
                "total_final": it.get("total_final") or it["final_bid"],
                "bid_count": it.get("bid_count"), "ends_at": it.get("ends_at"),
                "sold_at": now,
            }},
            upsert=True,
        )



async def require_device(x_device_id: Optional[str] = Header(None)) -> str:
    if not x_device_id:
        raise HTTPException(status_code=400, detail="Enhets-ID saknas.")
    return x_device_id


# ---------- Lightweight per-IP rate limiting (in-memory, single instance) ----------
_rate_buckets = {}


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit(request: Request, name: str, limit: int, window: int):
    key = (_client_ip(request), name)
    now = time.time()
    arr = [t for t in _rate_buckets.get(key, []) if now - t < window]
    if len(arr) >= limit:
        raise HTTPException(status_code=429, detail="För många förfrågningar – vänta en stund och försök igen.")
    arr.append(now)
    _rate_buckets[key] = arr


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
async def push_register(payload: PushRegister, request: Request, device_id: str = Depends(require_device)):
    _rate_limit(request, "push_register", 20, 60)
    if not commhub.enabled():
        raise HTTPException(status_code=503, detail="Push är inte konfigurerat.")
    token = (payload.token or "").strip()
    if not (20 <= len(token) <= 500):
        raise HTTPException(status_code=400, detail="Ogiltig push-token.")
    first_device = await db.devices.count_documents({}) == 0
    try:
        await commhub.register_token(token, device_id, payload.platform or "web")
    except Exception:
        logger.exception("push register")
        raise HTTPException(status_code=502, detail="Kunde inte registrera för notiser.")
    now = datetime.now(timezone.utc).isoformat()
    await db.devices.update_one(
        {"device_id": device_id},
        {"$set": {"token": token, "platform": payload.platform or "web", "updated_at": now},
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


@api_router.get("/account/name")
async def get_account_name(x_device_id: Optional[str] = Header(None)):
    dev = await db.devices.find_one({"device_id": x_device_id}) if x_device_id else None
    name = (dev or {}).get("account_name")
    has_pin = False
    if name:
        acc = await db.accounts.find_one({"name_lower": name.lower()})
        has_pin = bool(acc and acc.get("pin_hash"))
    return {"name": name, "has_pin": has_pin}


@api_router.post("/account/name")
async def set_account_name(payload: AccountNameReq, request: Request, device_id: str = Depends(require_device)):
    """Claim a personal account name on this device (optionally protected by a
    4-digit PIN) so bevakningar can be restored on a new device by typing the name."""
    _rate_limit(request, "account_name", 15, 60)
    name = re.sub(r"\s+", " ", (payload.name or "").strip())[:40]
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Namnet måste vara minst 2 tecken.")
    pin = (payload.pin or "").strip()
    if pin and not re.fullmatch(r"\d{4}", pin):
        raise HTTPException(status_code=400, detail="PIN-koden måste vara exakt 4 siffror.")
    now = datetime.now(timezone.utc).isoformat()
    acc = await db.accounts.find_one({"name_lower": name.lower()})
    if acc:
        if acc.get("pin_hash"):
            # Name is taken and PIN-protected: must prove ownership to use it.
            if not pin or not await _verify_pin_async(pin, acc["pin_hash"]):
                raise HTTPException(status_code=403, detail="Fel PIN-kod för detta namn.")
        elif pin:
            await db.accounts.update_one({"_id": acc["_id"]}, {"$set": {"pin_hash": await _hash_pin_async(pin)}})
        has_pin = bool(acc.get("pin_hash") or pin)
    else:
        doc = {"name": name, "name_lower": name.lower(), "created_at": now}
        if pin:
            doc["pin_hash"] = await _hash_pin_async(pin)
        await db.accounts.insert_one(doc)
        has_pin = bool(pin)
    await db.devices.update_one(
        {"device_id": device_id},
        {"$set": {"account_name": name, "account_name_lower": name.lower(), "updated_at": now},
         "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {"ok": True, "name": name, "has_pin": has_pin}


@api_router.post("/account/restore")
async def restore_by_name(payload: AccountNameReq, request: Request, device_id: str = Depends(require_device)):
    """Restore bevakningar + sökbevakningar onto THIS device from every other
    device sharing the account name (dedup). PIN-protected names require the PIN."""
    _rate_limit(request, "account_restore", 10, 60)
    name = re.sub(r"\s+", " ", (payload.name or "").strip())[:40]
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Ange ditt kontonamn (minst 2 tecken).")
    pin = (payload.pin or "").strip()
    acc = await db.accounts.find_one({"name_lower": name.lower()})
    has_devices = await db.devices.find_one({"account_name_lower": name.lower()})
    if not acc and not has_devices:
        return {"ok": True, "found": False, "watches": 0, "keywords": 0, "pin_required": False}
    if acc and acc.get("pin_hash"):
        if not pin:
            raise HTTPException(status_code=403, detail="Det här namnet är skyddat med PIN-kod.")
        if not await _verify_pin_async(pin, acc["pin_hash"]):
            raise HTTPException(status_code=403, detail="Fel PIN-kod.")
    src_ids = []
    async for d in db.devices.find({"account_name_lower": name.lower()}, {"device_id": 1}):
        did = d.get("device_id")
        if did and did != device_id:
            src_ids.append(did)
    now = datetime.now(timezone.utc).isoformat()
    await db.devices.update_one(
        {"device_id": device_id},
        {"$set": {"account_name": name, "account_name_lower": name.lower(), "updated_at": now},
         "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    if not src_ids:
        return {"ok": True, "found": False, "watches": 0, "keywords": 0, "pin_required": False}
    existing = set()
    async for a in db.auctions.find({"device_id": device_id}, {"url": 1}):
        existing.add(a.get("url"))
    w = 0
    async for a in db.auctions.find({"device_id": {"$in": src_ids}}):
        if a.get("url") in existing:
            continue
        a.pop("_id", None)
        a["device_id"] = device_id
        await db.auctions.insert_one(a)
        existing.add(a.get("url"))
        w += 1
    existing_kw = set()
    async for k in db.keyword_watches.find({"device_id": device_id}, {"keyword_lower": 1}):
        existing_kw.add(k.get("keyword_lower"))
    kc = 0
    async for k in db.keyword_watches.find({"device_id": {"$in": src_ids}}):
        kl = k.get("keyword_lower") or (k.get("keyword") or "").lower().strip()
        if kl in existing_kw:
            continue
        k.pop("_id", None)
        k["device_id"] = device_id
        k["id"] = str(uuid.uuid4())
        await db.keyword_watches.insert_one(k)
        existing_kw.add(kl)
        kc += 1
    return {"ok": True, "found": True, "watches": w, "keywords": kc}



# ---------- Routes ----------
@api_router.get("/")
async def root():
    return {"message": "Auktiomera API", "sources": SOURCE_LIST}


@api_router.get("/sources")
async def get_sources():
    return [{"name": n} for n in SOURCE_LIST]


@api_router.post("/scrape/preview")
async def scrape_preview(payload: WatchCreate, request: Request):
    _rate_limit(request, "scrape", 20, 60)
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
async def get_auction(auction_id: str, device_id: str = Depends(require_device)):
    doc = await db.auctions.find_one({"id": auction_id, "device_id": device_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Bevakning hittades inte.")
    return Auction(**_clean(doc))


@api_router.post("/auctions", response_model=Auction)
async def create_auction(payload: WatchCreate, request: Request, device_id: str = Depends(require_device)):
    _rate_limit(request, "scrape", 20, 60)
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
    await db.auctions.insert_one({**auction.model_dump(), "device_id": device_id})
    return auction


@api_router.post("/auctions/{auction_id}/analyze", response_model=Auction)
async def analyze_auction(auction_id: str, request: Request, device_id: str = Depends(require_device)):
    _rate_limit(request, "analyze", 30, 60)
    doc = await db.auctions.find_one({"id": auction_id, "device_id": device_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Bevakning hittades inte.")
    doc = _clean(doc)
    try:
        await _ingest_auctionet_sold(doc["title"])
        comps = await _find_comps(doc["title"], doc["source"])
        val = await valuation.valuate(
            doc["title"], doc["current_bid"], doc["source"], doc["total_now"], comps
        )
    except Exception:
        logger.exception("Valuation failed")
        raise HTTPException(status_code=502, detail="AI-analysen misslyckades. Försök igen senare.")
    now = datetime.now(timezone.utc).isoformat()
    await db.auctions.update_one(
        {"id": auction_id}, {"$set": {"valuation": val, "updated_at": now}}
    )
    doc["valuation"] = val
    doc["updated_at"] = now
    return Auction(**doc)


@api_router.post("/auctions/{auction_id}/refresh", response_model=Auction)
async def refresh_auction(auction_id: str, request: Request, device_id: str = Depends(require_device)):
    _rate_limit(request, "scrape", 20, 60)
    doc = await db.auctions.find_one({"id": auction_id, "device_id": device_id})
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
async def live_auctions(request: Request, source: Optional[str] = None, q: Optional[str] = None, limit: int = 120):
    limit = max(1, min(limit, 500))
    # On search, augment the feed with live Tradera + Blocket results (not background-crawled).
    if q and len((q or "").strip()) >= 2:
        _rate_limit(request, "search", 15, 20)  # block burst searches, keep normal polling free
        key = q.strip().lower()[:100]
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
        if time.time() - _auctionet_seen.get(key, 0) > 120:
            try:
                for it in await auctionet.search(q):
                    it["search_q"] = key
                    await crawler._upsert(it)
                _auctionet_seen[key] = time.time()
            except Exception:
                logger.exception("Auctionet augment failed")
    query = {}
    if source and source != "Alla":
        query["source"] = source
    elif not q:
        query["source"] = {"$nin": ["Tradera", "Blocket", "Auctionet"]}  # on-demand sources only appear on search
    if q:
        key = q.strip().lower()[:100]
        # Match on title OR items specifically fetched for this search (Tradera/Blocket
        # titles don't always contain the query term, e.g. "Yamaha" for "moped").
        # re.escape prevents ReDoS / regex injection via the user query.
        query["$or"] = [
            {"title": {"$regex": re.escape(q.strip()[:100]), "$options": "i"}},
            {"search_q": key},
        ]
    docs = await db.live_auctions.find(query, {"_id": 0}).to_list(5000)
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
async def live_sync(request: Request):
    _rate_limit(request, "sync", 5, 60)
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


@api_router.post("/live/analyze")
async def live_analyze(payload: LiveAnalyzeReq, request: Request, device_id: str = Depends(require_device)):
    """AI valuation for ANY live-feed item (no watch required), grounded on real
    sold prices from our database. Cached on the live doc for 24h to save credits."""
    _rate_limit(request, "analyze", 30, 60)
    doc = await db.live_auctions.find_one({"url": payload.url}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Objektet hittades inte.")
    cached, vat = doc.get("valuation"), doc.get("valuation_at")
    if cached and vat:
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(vat) < timedelta(hours=24):
                return {"valuation": cached}
        except Exception:
            pass
    try:
        await _ingest_auctionet_sold(doc["title"])
        comps = await _find_comps(doc["title"], doc["source"])
        val = await valuation.valuate(
            doc["title"], doc.get("current_bid") or 0, doc["source"],
            doc.get("total_now") or doc.get("current_bid") or 0, comps
        )
    except Exception:
        logger.exception("Live valuation failed")
        raise HTTPException(status_code=502, detail="AI-analysen misslyckades. Försök igen senare.")
    await db.live_auctions.update_one(
        {"url": payload.url},
        {"$set": {"valuation": val, "valuation_at": datetime.now(timezone.utc).isoformat()}},
    )
    return {"valuation": val}


async def _keyword_matches(keyword: str) -> list:
    """All matching live items across ALL sources for `keyword`: refresh the
    on-demand sources (Tradera/Blocket/Auctionet) live, then match the DB on
    title or the search tag. Returns a list of matching URLs."""
    key = keyword.strip().lower()[:100]
    for mod in (tradera, blocket, auctionet):
        try:
            for it in await mod.search(keyword):
                it["search_q"] = key
                await crawler._upsert(it)
        except Exception:
            logger.exception("keyword live search failed (%s)", getattr(mod, "__name__", "?"))
    regex = re.escape(keyword.strip()[:100])
    query = {"$or": [{"title": {"$regex": regex, "$options": "i"}}, {"search_q": key}]}
    docs = await db.live_auctions.find(query, {"_id": 0, "url": 1}).to_list(5000)
    return [d["url"] for d in docs]


@api_router.post("/keyword-watches")
async def create_keyword_watch(payload: KeywordWatchCreate, device_id: str = Depends(require_device)):
    dev = await db.devices.find_one({"device_id": device_id})
    if not (dev and dev.get("token")):
        raise HTTPException(status_code=403, detail="Aktivera pushnotiser för att kunna sökbevaka.")
    kw = (payload.keyword or "").strip()[:100]
    if len(kw) < 2:
        raise HTTPException(status_code=400, detail="Ange ett sökord med minst 2 tecken.")
    if await db.keyword_watches.find_one({"device_id": device_id, "keyword_lower": kw.lower()}):
        raise HTTPException(status_code=409, detail="Du sökbevakar redan detta ord.")
    urls = await _keyword_matches(kw)
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()), "device_id": device_id, "keyword": kw,
        "keyword_lower": kw.lower(), "created_at": now, "last_check": now,
        "seen_urls": urls, "matches": [], "unseen_count": 0, "active": True,
    }
    await db.keyword_watches.insert_one(doc)
    return {"id": doc["id"], "keyword": kw, "created_at": now, "last_check": now,
            "unseen_count": 0, "match_count": 0, "baseline": len(urls)}


@api_router.get("/keyword-watches")
async def list_keyword_watches(device_id: str = Depends(require_device)):
    out = []
    async for w in db.keyword_watches.find({"device_id": device_id}).sort("created_at", -1):
        out.append({"id": w["id"], "keyword": w["keyword"], "created_at": w["created_at"],
                    "last_check": w.get("last_check"), "unseen_count": w.get("unseen_count", 0),
                    "match_count": len(w.get("matches", []))})
    return out


@api_router.get("/keyword-watches/{wid}")
async def get_keyword_watch(wid: str, device_id: str = Depends(require_device)):
    w = await db.keyword_watches.find_one({"id": wid, "device_id": device_id})
    if not w:
        raise HTTPException(status_code=404, detail="Sökbevakning hittades inte.")
    matches = w.get("matches", [])
    urls = [m["url"] for m in matches]
    items = []
    if urls:
        # Matches are shown only while they still exist in the live feed. On-demand
        # search results are purged after 24h (crawler cleanup), so un-watched hits
        # drop off on their own and the list stays short — promote an item to a real
        # price-watch ("Bevaka") to keep it.
        found = {}
        async for d in db.live_auctions.find({"url": {"$in": urls}}, {"_id": 0}):
            found[d["url"]] = d
        for m in reversed(matches):  # newest found first
            d = found.get(m["url"])
            if d:
                d["found_at"] = m["found_at"]
                items.append(d)
    await db.keyword_watches.update_one({"id": wid}, {"$set": {"unseen_count": 0}})
    return {"id": w["id"], "keyword": w["keyword"], "created_at": w["created_at"],
            "last_check": w.get("last_check"), "items": items}


@api_router.delete("/keyword-watches/{wid}")
async def delete_keyword_watch(wid: str, device_id: str = Depends(require_device)):
    res = await db.keyword_watches.delete_one({"id": wid, "device_id": device_id})
    if not res.deleted_count:
        raise HTTPException(status_code=404, detail="Sökbevakning hittades inte.")
    return {"ok": True}


@api_router.delete("/sold-items")
async def delete_sold_item(payload: DeleteSoldReq, device_id: str = Depends(require_device)):
    """Remove a bad/mismatched sold reference so it is no longer used as a price
    comparable in valuations. Prefer the exact url; fall back to title (+source)
    for legacy comparables cached before url was tracked."""
    if payload.url:
        q = {"url": payload.url}
    elif payload.title:
        q = {"title": payload.title}
        if payload.source:
            q["source"] = payload.source
    else:
        raise HTTPException(status_code=400, detail="Saknar identifierare för objektet.")
    res = await db.sold_items.delete_many(q)
    return {"ok": True, "deleted": res.deleted_count}


app.include_router(api_router)
_cors_origins = os.environ.get('CORS_ORIGINS', '*').split(',')
app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,  # identity is a custom X-Device-Id header, not cookies
    allow_origins=_cors_origins,
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
    try:
        await db.sold_items.create_index("url", unique=True)
        await db.sold_items.create_index([("title", "text")])
    except Exception:
        logger.exception("could not create sold_items indexes")
    crawler.start()
    asyncio.create_task(_watch_notifier())
    try:
        await db.keyword_watches.create_index("device_id")
        await db.keyword_watches.create_index([("device_id", 1), ("keyword_lower", 1)], unique=True)
    except Exception:
        logger.exception("could not create keyword_watches index")
    asyncio.create_task(_keyword_watch_loop())


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


async def _run_keyword_check(w, now):
    urls = set(await _keyword_matches(w["keyword"]))
    seen = set(w.get("seen_urls", []))
    new = [u for u in urls if u not in seen]
    update = {"last_check": now.isoformat(), "seen_urls": list(seen | urls)}
    if new:
        matches = w.get("matches", []) + [{"url": u, "found_at": now.isoformat()} for u in new]
        update["matches"] = matches[-500:]
        update["unseen_count"] = (w.get("unseen_count", 0) or 0) + len(new)
        dev = await db.devices.find_one({"device_id": w["device_id"]})
        token = (dev or {}).get("token")
        if token and commhub.enabled():
            n = len(new)
            body = f"{n} ny{'a' if n != 1 else ''} produkt{'er' if n != 1 else ''} i auktionssystemen"
            try:
                await commhub.send_to_token(
                    token, title=f"🔎 Nya träffar för '{w['keyword']}'", body=body,
                    data={"type": "keyword_watch", "keyword_watch_id": w["id"]})
            except Exception:
                logger.exception("keyword push failed")
    await db.keyword_watches.update_one({"id": w["id"]}, {"$set": update})


async def _keyword_watch_loop():
    """Every 4h, re-run each saved keyword search across all sources and push the
    device when genuinely new items (unseen since the watch was created) appear."""
    await asyncio.sleep(40)
    while True:
        try:
            now = datetime.now(timezone.utc)
            async for w in db.keyword_watches.find({"active": {"$ne": False}}):
                lc = _parse_iso(w.get("last_check"))
                if lc and (now - lc) < timedelta(hours=4):
                    continue
                try:
                    await _run_keyword_check(w, now)
                except Exception:
                    logger.exception("keyword check failed for %s", w.get("keyword"))
        except Exception:
            logger.exception("keyword watch loop")
        await asyncio.sleep(900)  # re-check which watches are due every 15 min


@app.on_event("shutdown")
async def shutdown_db_client():
    await crawler.stop()
    client.close()
