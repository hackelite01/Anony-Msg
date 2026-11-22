"""Async MongoDB layer (motor). All read/write helpers live here."""
import logging
from datetime import datetime, timezone
from math import ceil
from typing import Any, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING

import config

log = logging.getLogger(__name__)

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def init_db() -> AsyncIOMotorDatabase:
    """Connect, create indexes, seed config doc. Idempotent."""
    global _client, _db
    if _db is not None:
        return _db
    if not config.MONGODB_URI:
        raise RuntimeError("MONGODB_URI is not configured")

    _client = AsyncIOMotorClient(config.MONGODB_URI, serverSelectionTimeoutMS=8000)
    _db = _client[config.MONGODB_DB_NAME]

    await _db.bot_users.create_index([("telegram_id", ASCENDING)], unique=True)
    await _db.user_keys.create_index([("code", ASCENDING)], unique=True)
    await _db.user_keys.create_index([("telegram_id", ASCENDING)])
    await _db.provider_keys.create_index(
        [("reserved", ASCENDING), ("exhausted", ASCENDING)]
    )
    await _db.purchase_requests.create_index([("status", ASCENDING)])
    await _db.logs.create_index([("timestamp", ASCENDING)])

    existing = await _db.config.find_one({"_id": "singleton"})
    if not existing:
        await _db.config.insert_one(
            {
                "_id": "singleton",
                "auto_approve": True,
                "ton_wallet": None,
                "admin_username": None,
            }
        )
    log.info("MongoDB initialized: %s", config.MONGODB_DB_NAME)
    return _db


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("DB not initialized — call init_db() first")
    return _db


async def close_db() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None


# ---------- bot_users ----------

async def upsert_user(telegram_id: int, username: str, first_name: str) -> tuple[dict, bool]:
    """Returns (user_doc, is_newly_created)."""
    db = get_db()
    res = await db.bot_users.update_one(
        {"telegram_id": telegram_id},
        {
            "$set": {"username": username or "", "first_name": first_name or ""},
            "$setOnInsert": {
                "joined_at": now_utc(),
                "is_banned": False,
                "telegram_id": telegram_id,
            },
        },
        upsert=True,
    )
    is_new = res.upserted_id is not None
    doc = await db.bot_users.find_one({"telegram_id": telegram_id})
    return doc, is_new


async def get_user(telegram_id: int) -> Optional[dict]:
    return await get_db().bot_users.find_one({"telegram_id": telegram_id})


async def is_banned(telegram_id: int) -> bool:
    doc = await get_user(telegram_id)
    return bool(doc and doc.get("is_banned"))


async def count_users() -> int:
    return await get_db().bot_users.count_documents({})


async def all_user_ids() -> list[int]:
    cur = get_db().bot_users.find({"is_banned": {"$ne": True}}, {"telegram_id": 1})
    return [d["telegram_id"] async for d in cur]


# ---------- config ----------

async def get_config() -> dict:
    doc = await get_db().config.find_one({"_id": "singleton"})
    return doc or {}


async def update_config(**fields) -> None:
    await get_db().config.update_one({"_id": "singleton"}, {"$set": fields}, upsert=True)


# ---------- plans ----------

async def list_plans(active_only: bool = True) -> list[dict]:
    q = {"active": True} if active_only else {}
    cur = get_db().plans.find(q).sort("price_stars", ASCENDING)
    return [d async for d in cur]


async def get_plan(plan_id: str | ObjectId) -> Optional[dict]:
    if isinstance(plan_id, str):
        plan_id = ObjectId(plan_id)
    return await get_db().plans.find_one({"_id": plan_id})


async def add_plan(name: str, sms_limit: int, price_stars: int) -> dict:
    doc = {
        "name": name,
        "sms_limit": sms_limit,
        "price_stars": price_stars,
        "active": True,
        "created_at": now_utc(),
    }
    res = await get_db().plans.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def deactivate_plan(plan_id: str | ObjectId) -> bool:
    if isinstance(plan_id, str):
        plan_id = ObjectId(plan_id)
    res = await get_db().plans.update_one({"_id": plan_id}, {"$set": {"active": False}})
    return res.modified_count > 0


# ---------- provider_keys ----------

async def add_provider_keys(items: list[tuple[str, str]]) -> int:
    if not items:
        return 0
    docs = [
        {
            "seven_io_key": k,
            "label": label,
            "sms_capacity": config.SMS_PER_PROVIDER_KEY,
            "sms_used": 0,
            "exhausted": False,
            "reserved": False,
            "reserved_for": None,
            "added_at": now_utc(),
        }
        for label, k in items
    ]
    res = await get_db().provider_keys.insert_many(docs)
    return len(res.inserted_ids)


async def list_provider_keys(limit: int = 50) -> list[dict]:
    cur = get_db().provider_keys.find({}).sort("added_at", ASCENDING).limit(limit)
    return [d async for d in cur]


async def pool_status() -> dict:
    db = get_db()
    total = await db.provider_keys.count_documents({})
    free = await db.provider_keys.count_documents({"reserved": False, "exhausted": False})
    reserved = await db.provider_keys.count_documents({"reserved": True, "exhausted": False})
    exhausted = await db.provider_keys.count_documents({"exhausted": True})
    return {"total": total, "free": free, "reserved": reserved, "exhausted": exhausted}


async def free_keys_count() -> int:
    return await get_db().provider_keys.count_documents(
        {"reserved": False, "exhausted": False}
    )


async def reserve_keys_for(user_key_code: str, count: int) -> list[ObjectId]:
    """Atomically reserve `count` free keys. Returns ids of reserved keys.
    Releases reservations on partial failure."""
    db = get_db()
    reserved_ids: list[ObjectId] = []
    try:
        for _ in range(count):
            doc = await db.provider_keys.find_one_and_update(
                {"reserved": False, "exhausted": False},
                {"$set": {"reserved": True, "reserved_for": user_key_code}},
            )
            if not doc:
                raise RuntimeError("Pool drained mid-reservation")
            reserved_ids.append(doc["_id"])
        return reserved_ids
    except Exception:
        if reserved_ids:
            await db.provider_keys.update_many(
                {"_id": {"$in": reserved_ids}},
                {"$set": {"reserved": False, "reserved_for": None}},
            )
        raise


async def get_provider_key(pk_id: ObjectId) -> Optional[dict]:
    return await get_db().provider_keys.find_one({"_id": pk_id})


async def increment_provider_key_usage(pk_id: ObjectId) -> dict:
    """Increment sms_used and mark exhausted if at capacity. Returns updated doc."""
    db = get_db()
    doc = await db.provider_keys.find_one_and_update(
        {"_id": pk_id},
        {"$inc": {"sms_used": 1}},
        return_document=True,
    )
    if doc and doc["sms_used"] >= doc.get("sms_capacity", config.SMS_PER_PROVIDER_KEY):
        doc = await db.provider_keys.find_one_and_update(
            {"_id": pk_id},
            {"$set": {"exhausted": True}},
            return_document=True,
        )
    return doc


# ---------- user_keys ----------

async def code_exists(code: str) -> bool:
    return await get_db().user_keys.count_documents({"code": code}, limit=1) > 0


async def create_user_key(
    code: str,
    telegram_id: int,
    plan: dict,
    reserved_key_ids: list[ObjectId],
) -> dict:
    doc = {
        "code": code,
        "telegram_id": telegram_id,
        "plan_id": plan["_id"],
        "plan_name": plan["name"],
        "sms_limit": plan["sms_limit"],
        "sms_used": 0,
        "reserved_key_ids": reserved_key_ids,
        "active_provider_key_index": 0,
        "active": True,
        "assigned_at": now_utc(),
    }
    await get_db().user_keys.insert_one(doc)
    return doc


async def list_user_keys(telegram_id: int, active_only: bool = False) -> list[dict]:
    q: dict[str, Any] = {"telegram_id": telegram_id}
    if active_only:
        q["active"] = True
    cur = get_db().user_keys.find(q).sort("assigned_at", ASCENDING)
    return [d async for d in cur]


async def get_user_key(code: str) -> Optional[dict]:
    return await get_db().user_keys.find_one({"code": code})


async def increment_user_key_usage(code: str) -> dict:
    """+1 sms_used; if over limit, mark inactive. Returns updated doc."""
    db = get_db()
    doc = await db.user_keys.find_one_and_update(
        {"code": code},
        {"$inc": {"sms_used": 1}},
        return_document=True,
    )
    if doc and doc["sms_used"] >= doc["sms_limit"]:
        doc = await db.user_keys.find_one_and_update(
            {"code": code},
            {"$set": {"active": False}},
            return_document=True,
        )
    return doc


async def advance_user_key_provider(code: str) -> dict:
    return await get_db().user_keys.find_one_and_update(
        {"code": code},
        {"$inc": {"active_provider_key_index": 1}},
        return_document=True,
    )


async def count_active_keys() -> int:
    return await get_db().user_keys.count_documents({"active": True})


# ---------- purchase_requests ----------

async def create_purchase_request(
    telegram_id: int,
    username: str,
    plan: dict,
    stars_paid: int,
    payment_charge_id: str,
) -> dict:
    doc = {
        "telegram_id": telegram_id,
        "username": username or "",
        "plan_id": plan["_id"],
        "plan_name": plan["name"],
        "sms_limit": plan["sms_limit"],
        "keys_needed": ceil(plan["sms_limit"] / config.SMS_PER_PROVIDER_KEY),
        "stars_paid": stars_paid,
        "payment_charge_id": payment_charge_id,
        "status": "pending",
        "reject_reason": None,
        "requested_at": now_utc(),
        "resolved_at": None,
    }
    res = await get_db().purchase_requests.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def get_purchase_request(req_id: str | ObjectId) -> Optional[dict]:
    if isinstance(req_id, str):
        req_id = ObjectId(req_id)
    return await get_db().purchase_requests.find_one({"_id": req_id})


async def set_purchase_status(
    req_id: str | ObjectId,
    status: str,
    reject_reason: Optional[str] = None,
) -> None:
    if isinstance(req_id, str):
        req_id = ObjectId(req_id)
    update: dict[str, Any] = {"status": status, "resolved_at": now_utc()}
    if reject_reason is not None:
        update["reject_reason"] = reject_reason
    await get_db().purchase_requests.update_one({"_id": req_id}, {"$set": update})


async def list_pending_requests(limit: int = 50) -> list[dict]:
    cur = (
        get_db()
        .purchase_requests.find({"status": "pending"})
        .sort("requested_at", ASCENDING)
        .limit(limit)
    )
    return [d async for d in cur]


async def total_revenue_stars() -> int:
    cur = get_db().purchase_requests.aggregate(
        [
            {"$match": {"status": "approved"}},
            {"$group": {"_id": None, "total": {"$sum": "$stars_paid"}}},
        ]
    )
    async for d in cur:
        return int(d.get("total", 0))
    return 0


# ---------- logs ----------

async def log_sms(
    telegram_id: int,
    user_key_code: str,
    provider_key_id: ObjectId,
    destination: str,
    message: str,
    status: str,
    error: Optional[str] = None,
) -> None:
    await get_db().logs.insert_one(
        {
            "telegram_id": telegram_id,
            "user_key_code": user_key_code,
            "provider_key_id": provider_key_id,
            "destination": destination,
            "message": message,
            "status": status,
            "error": error,
            "timestamp": now_utc(),
        }
    )


async def list_logs(skip: int = 0, limit: int = 10) -> list[dict]:
    cur = get_db().logs.find({}).sort("timestamp", -1).skip(skip).limit(limit)
    return [d async for d in cur]


async def count_logs(status: Optional[str] = None) -> int:
    q = {"status": status} if status else {}
    return await get_db().logs.count_documents(q)
