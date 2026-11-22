"""Async MongoDB layer (motor). All read/write helpers live here."""
import logging
import re
from datetime import datetime, timezone
from math import ceil
from typing import Any, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

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
    await _db.ton_orders.create_index([("memo", ASCENDING)], unique=True)
    await _db.ton_orders.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    await _db.ton_payments.create_index([("tx_hash", ASCENDING)], unique=True)
    # one purchase request per TON order (defense in depth against double fulfilment)
    await _db.purchase_requests.create_index(
        [("ton_order_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"ton_order_id": {"$exists": True}},
    )
    # BTC: an amount identifies an order, so it must be unique among open orders
    await _db.btc_orders.create_index(
        [("amount_sats", ASCENDING)],
        unique=True,
        partialFilterExpression={"status": "awaiting"},
    )
    await _db.btc_orders.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    await _db.btc_payments.create_index(
        [("txid", ASCENDING), ("vout", ASCENDING)], unique=True
    )
    await _db.purchase_requests.create_index(
        [("btc_order_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"btc_order_id": {"$exists": True}},
    )
    # promo codes / free plans
    await _db.promo_codes.create_index([("code", ASCENDING)], unique=True)
    await _db.promo_redemptions.create_index(
        [("code", ASCENDING), ("telegram_id", ASCENDING)], unique=True
    )
    await _db.promo_applied.create_index([("telegram_id", ASCENDING)], unique=True)
    await _db.free_claims.create_index(
        [("telegram_id", ASCENDING), ("plan_id", ASCENDING)], unique=True
    )
    # UPI: one purchase request per UTR (stops the same payment being claimed twice)
    await _db.purchase_requests.create_index(
        [("upi_utr", ASCENDING)],
        unique=True,
        partialFilterExpression={"upi_utr": {"$exists": True}},
    )

    existing = await _db.config.find_one({"_id": "singleton"})
    if not existing:
        await _db.config.insert_one(
            {
                "_id": "singleton",
                "auto_approve": True,
                "ton_wallet": None,
                "ton_enabled": False,
                "stars_enabled": True,
                "btc_enabled": False,
                "btc_address": None,
                "upi_enabled": False,
                "upi_id": None,
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
    q: dict[str, Any] = {"deleted": {"$ne": True}}
    if active_only:
        q["active"] = True
    cur = get_db().plans.find(q).sort("price_stars", ASCENDING)
    return [d async for d in cur]


async def get_plan(plan_id: str | ObjectId) -> Optional[dict]:
    if isinstance(plan_id, str):
        plan_id = ObjectId(plan_id)
    return await get_db().plans.find_one({"_id": plan_id})


async def add_plan(
    name: str,
    sms_limit: int,
    price_stars: int,
    price_ton_nano: Optional[int] = None,
    price_btc_sats: Optional[int] = None,
    price_inr_paise: Optional[int] = None,
    is_free: bool = False,
) -> dict:
    doc = {
        "is_free": is_free,
        "name": name,
        "sms_limit": sms_limit,
        "price_stars": price_stars,
        "price_ton_nano": price_ton_nano,
        "price_btc_sats": price_btc_sats,
        "price_inr_paise": price_inr_paise,
        "active": True,
        "created_at": now_utc(),
    }
    res = await get_db().plans.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


PRICE_FIELDS = ("price_ton_nano", "price_btc_sats", "price_inr_paise")


async def set_plan_price(plan_id: str | ObjectId, field: str, value: Optional[int]) -> bool:
    """Set (or clear with None) a plan's price in one currency's smallest unit."""
    if field not in PRICE_FIELDS:
        raise ValueError(f"unknown price field: {field}")
    if isinstance(plan_id, str):
        plan_id = ObjectId(plan_id)
    res = await get_db().plans.update_one({"_id": plan_id}, {"$set": {field: value}})
    return res.matched_count > 0


async def set_plan_free(plan_id: str | ObjectId, is_free: bool) -> bool:
    if isinstance(plan_id, str):
        plan_id = ObjectId(plan_id)
    res = await get_db().plans.update_one({"_id": plan_id}, {"$set": {"is_free": is_free}})
    return res.matched_count > 0


async def set_plan_ton_price(plan_id: str | ObjectId, price_ton_nano: Optional[int]) -> bool:
    """Set (or clear with None) a plan's TON price, in nanoTON."""
    if isinstance(plan_id, str):
        plan_id = ObjectId(plan_id)
    res = await get_db().plans.update_one(
        {"_id": plan_id}, {"$set": {"price_ton_nano": price_ton_nano}}
    )
    return res.matched_count > 0


async def delete_plan(plan_id: str | ObjectId) -> bool:
    """Soft delete: hidden from every list and unbuyable, but the record is kept so
    payments already in flight (open Stars invoice / TON order) can still be fulfilled."""
    if isinstance(plan_id, str):
        plan_id = ObjectId(plan_id)
    res = await get_db().plans.update_one(
        {"_id": plan_id, "deleted": {"$ne": True}},
        {"$set": {"active": False, "deleted": True, "deleted_at": now_utc()}},
    )
    return res.modified_count > 0


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


async def total_revenue_ton_nano() -> int:
    cur = get_db().purchase_requests.aggregate(
        [
            {"$match": {"status": "approved", "payment_method": "ton"}},
            {"$group": {"_id": None, "total": {"$sum": "$ton_amount_nano"}}},
        ]
    )
    async for d in cur:
        return int(d.get("total", 0))
    return 0


# ---------- TON orders / payments ----------

async def create_ton_order(
    telegram_id: int,
    username: str,
    plan: dict,
    amount_nano: int,
    memo: str,
    wallet: str,
    promo: Optional[str] = None,
) -> dict:
    doc = {
        "telegram_id": telegram_id,
        "username": username or "",
        "plan_id": plan["_id"],
        "plan_name": plan["name"],
        "sms_limit": plan["sms_limit"],
        "amount_nano": amount_nano,
        "memo": memo,
        "wallet": wallet,
        "promo": promo,
        # awaiting -> paid (tx claimed) -> fulfilled ; or cancelled / error
        "status": "awaiting",
        "tx_hash": None,
        "paid_nano": None,
        "created_at": now_utc(),
        "paid_at": None,
    }
    res = await get_db().ton_orders.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def get_ton_order(order_id: str | ObjectId) -> Optional[dict]:
    if isinstance(order_id, str):
        order_id = ObjectId(order_id)
    return await get_db().ton_orders.find_one({"_id": order_id})


async def find_open_ton_order(
    telegram_id: int, plan_id: ObjectId, newer_than: datetime
) -> Optional[dict]:
    """Reuse the user's still-valid order for this plan instead of minting a new memo."""
    return await get_db().ton_orders.find_one(
        {
            "telegram_id": telegram_id,
            "plan_id": plan_id,
            "status": "awaiting",
            "created_at": {"$gte": newer_than},
        }
    )


async def list_watchable_ton_orders(since: datetime) -> list[dict]:
    cur = get_db().ton_orders.find({"status": "awaiting", "created_at": {"$gte": since}})
    return [d async for d in cur]


async def list_paid_unfulfilled_ton_orders() -> list[dict]:
    cur = get_db().ton_orders.find({"status": "paid"})
    return [d async for d in cur]


async def claim_ton_order(
    order_id: ObjectId, tx_hash: str, paid_nano: int
) -> Optional[dict]:
    """Atomically awaiting -> paid. Returns the order only for the single winning caller."""
    return await get_db().ton_orders.find_one_and_update(
        {"_id": order_id, "status": "awaiting"},
        {"$set": {"status": "paid", "tx_hash": tx_hash, "paid_nano": paid_nano, "paid_at": now_utc()}},
        return_document=ReturnDocument.AFTER,
    )


async def set_ton_order_status(order_id: ObjectId, status: str) -> None:
    await get_db().ton_orders.update_one({"_id": order_id}, {"$set": {"status": status}})


async def cancel_ton_order(order_id: str | ObjectId, telegram_id: int) -> bool:
    if isinstance(order_id, str):
        order_id = ObjectId(order_id)
    res = await get_db().ton_orders.update_one(
        {"_id": order_id, "telegram_id": telegram_id, "status": "awaiting"},
        {"$set": {"status": "cancelled"}},
    )
    return res.modified_count > 0


async def record_ton_payment(tx: dict, status: str, order_id: Optional[ObjectId] = None) -> bool:
    """Remember a transfer we flagged (underpaid/unmatched). False if already recorded."""
    try:
        await get_db().ton_payments.insert_one(
            {
                "tx_hash": tx["hash"],
                "memo": tx["memo"],
                "value_nano": tx["value"],
                "source": tx["source"],
                "status": status,
                "order_id": order_id,
                "recorded_at": now_utc(),
            }
        )
        return True
    except DuplicateKeyError:
        return False


async def create_ton_purchase_request(order: dict, plan: dict) -> dict:
    """Purchase request for a paid TON order. Idempotent per order."""
    coll = get_db().purchase_requests
    existing = await coll.find_one({"ton_order_id": order["_id"]})
    if existing:
        return existing
    doc = {
        "telegram_id": order["telegram_id"],
        "username": order.get("username") or "",
        "plan_id": plan["_id"],
        "plan_name": plan["name"],
        "sms_limit": plan["sms_limit"],
        "keys_needed": ceil(plan["sms_limit"] / config.SMS_PER_PROVIDER_KEY),
        "stars_paid": 0,
        "payment_method": "ton",
        "ton_amount_nano": order["paid_nano"],
        "ton_tx_hash": order["tx_hash"],
        "ton_order_id": order["_id"],
        "payment_charge_id": order["tx_hash"],
        "status": "pending",
        "reject_reason": None,
        "requested_at": now_utc(),
        "resolved_at": None,
    }
    try:
        res = await coll.insert_one(doc)
    except DuplicateKeyError:
        return await coll.find_one({"ton_order_id": order["_id"]})
    doc["_id"] = res.inserted_id
    return doc


async def total_revenue_method(method: str, field: str) -> int:
    """Sum of `field` over approved purchase requests paid with `method`."""
    cur = get_db().purchase_requests.aggregate(
        [
            {"$match": {"status": "approved", "payment_method": method}},
            {"$group": {"_id": None, "total": {"$sum": f"${field}"}}},
        ]
    )
    async for d in cur:
        return int(d.get("total", 0))
    return 0


# ---------- BTC orders ----------

async def create_btc_order(
    telegram_id: int,
    username: str,
    plan: dict,
    amount_sats: int,
    base_sats: int,
    address: str,
    promo: Optional[str] = None,
) -> dict:
    """Insert an awaiting order. Raises DuplicateKeyError if another open order has this amount."""
    doc = {
        "telegram_id": telegram_id,
        "username": username or "",
        "plan_id": plan["_id"],
        "plan_name": plan["name"],
        "sms_limit": plan["sms_limit"],
        "base_sats": base_sats,
        "amount_sats": amount_sats,
        "address": address,
        "promo": promo,
        # awaiting -> paid (output claimed) -> fulfilled ; or cancelled / error
        "status": "awaiting",
        "seen_txid": None,
        "txid": None,
        "vout": None,
        "paid_sats": None,
        "created_at": now_utc(),
        "paid_at": None,
    }
    res = await get_db().btc_orders.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def get_btc_order(order_id: str | ObjectId) -> Optional[dict]:
    if isinstance(order_id, str):
        order_id = ObjectId(order_id)
    return await get_db().btc_orders.find_one({"_id": order_id})


async def find_open_btc_order(
    telegram_id: int, plan_id: ObjectId, newer_than: datetime
) -> Optional[dict]:
    return await get_db().btc_orders.find_one(
        {
            "telegram_id": telegram_id,
            "plan_id": plan_id,
            "status": "awaiting",
            "created_at": {"$gte": newer_than},
        }
    )


async def list_watchable_btc_orders(since: datetime) -> list[dict]:
    cur = get_db().btc_orders.find({"status": "awaiting", "created_at": {"$gte": since}})
    return [d async for d in cur]


async def list_paid_unfulfilled_btc_orders() -> list[dict]:
    cur = get_db().btc_orders.find({"status": "paid"})
    return [d async for d in cur]


async def claim_btc_order(
    order_id: ObjectId, txid: str, vout: int, paid_sats: int
) -> Optional[dict]:
    """Atomically awaiting -> paid. Returns the order only for the single winning caller."""
    return await get_db().btc_orders.find_one_and_update(
        {"_id": order_id, "status": "awaiting"},
        {
            "$set": {
                "status": "paid",
                "txid": txid,
                "vout": vout,
                "paid_sats": paid_sats,
                "paid_at": now_utc(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def set_btc_order_status(order_id: ObjectId, status: str) -> None:
    await get_db().btc_orders.update_one({"_id": order_id}, {"$set": {"status": status}})


async def cancel_btc_order(order_id: str | ObjectId, telegram_id: int) -> bool:
    if isinstance(order_id, str):
        order_id = ObjectId(order_id)
    res = await get_db().btc_orders.update_one(
        {"_id": order_id, "telegram_id": telegram_id, "status": "awaiting"},
        {"$set": {"status": "cancelled"}},
    )
    return res.modified_count > 0


async def mark_btc_seen(order_id: ObjectId, txid: str) -> bool:
    """True only the first time an unconfirmed payment is noticed for this order."""
    doc = await get_db().btc_orders.find_one_and_update(
        {"_id": order_id, "status": "awaiting", "seen_txid": None},
        {"$set": {"seen_txid": txid}},
    )
    return doc is not None


async def record_btc_output(out: dict, status: str, order_id: Optional[ObjectId] = None) -> bool:
    """Remember an output we've used/flagged. False if (txid, vout) was already recorded."""
    try:
        await get_db().btc_payments.insert_one(
            {
                "txid": out["txid"],
                "vout": out["vout"],
                "value_sats": out["value"],
                "status": status,
                "order_id": order_id,
                "recorded_at": now_utc(),
            }
        )
        return True
    except DuplicateKeyError:
        return False


async def create_btc_purchase_request(order: dict, plan: dict) -> dict:
    """Purchase request for a paid BTC order. Idempotent per order."""
    coll = get_db().purchase_requests
    existing = await coll.find_one({"btc_order_id": order["_id"]})
    if existing:
        return existing
    doc = {
        "telegram_id": order["telegram_id"],
        "username": order.get("username") or "",
        "plan_id": plan["_id"],
        "plan_name": plan["name"],
        "sms_limit": plan["sms_limit"],
        "keys_needed": ceil(plan["sms_limit"] / config.SMS_PER_PROVIDER_KEY),
        "stars_paid": 0,
        "payment_method": "btc",
        "btc_amount_sats": order["paid_sats"],
        "btc_txid": order["txid"],
        "btc_order_id": order["_id"],
        "payment_charge_id": order["txid"],
        "status": "pending",
        "reject_reason": None,
        "requested_at": now_utc(),
        "resolved_at": None,
    }
    try:
        res = await coll.insert_one(doc)
    except DuplicateKeyError:
        return await coll.find_one({"btc_order_id": order["_id"]})
    doc["_id"] = res.inserted_id
    return doc


# ---------- UPI (manual approval) ----------

async def count_pending_upi(telegram_id: int) -> int:
    return await get_db().purchase_requests.count_documents(
        {"telegram_id": telegram_id, "payment_method": "upi", "status": "pending"}
    )


async def create_upi_purchase_request(
    telegram_id: int,
    username: str,
    plan: dict,
    utr: str,
    amount_paise: int,
    screenshot_file_id: Optional[str],
    promo_code: Optional[str] = None,
) -> Optional[dict]:
    """Pending request awaiting admin approval. None if this UTR was already submitted."""
    doc = {
        "telegram_id": telegram_id,
        "username": username or "",
        "plan_id": plan["_id"],
        "plan_name": plan["name"],
        "sms_limit": plan["sms_limit"],
        "keys_needed": ceil(plan["sms_limit"] / config.SMS_PER_PROVIDER_KEY),
        "stars_paid": 0,
        "payment_method": "upi",
        "upi_amount_paise": amount_paise,
        "upi_utr": utr,
        "screenshot_file_id": screenshot_file_id,
        "promo_code": promo_code,
        "payment_charge_id": utr,
        "status": "pending",
        "reject_reason": None,
        "requested_at": now_utc(),
        "resolved_at": None,
    }
    try:
        res = await get_db().purchase_requests.insert_one(doc)
    except DuplicateKeyError:
        return None
    doc["_id"] = res.inserted_id
    return doc


# ---------- users lookup ----------

async def find_user_by_username(username: str) -> Optional[dict]:
    name = username.strip().lstrip("@")
    if not name:
        return None
    return await get_db().bot_users.find_one(
        {"username": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}
    )


# ---------- grants: gift / free plan / 100% promo ----------

async def create_grant_request(
    telegram_id: int, username: str, plan: dict, method: str, extra: Optional[dict] = None
) -> dict:
    """An unpaid key grant (method: gift | free | promo). Created pending, approved on assignment."""
    doc = {
        "telegram_id": telegram_id,
        "username": username or "",
        "plan_id": plan["_id"],
        "plan_name": plan["name"],
        "sms_limit": plan["sms_limit"],
        "keys_needed": ceil(plan["sms_limit"] / config.SMS_PER_PROVIDER_KEY),
        "stars_paid": 0,
        "payment_method": method,
        "payment_charge_id": f"{method}:{now_utc().isoformat()}",
        "status": "pending",
        "reject_reason": None,
        "requested_at": now_utc(),
        "resolved_at": None,
        **(extra or {}),
    }
    res = await get_db().purchase_requests.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def has_free_claim(telegram_id: int, plan_id: str) -> bool:
    return await get_db().free_claims.count_documents(
        {"telegram_id": telegram_id, "plan_id": str(plan_id)}, limit=1
    ) > 0


async def claim_free(telegram_id: int, plan_id: str) -> bool:
    """True if this user hadn't claimed this free plan yet (and now has)."""
    try:
        await get_db().free_claims.insert_one(
            {"telegram_id": telegram_id, "plan_id": str(plan_id), "at": now_utc()}
        )
        return True
    except DuplicateKeyError:
        return False


async def release_free_claim(telegram_id: int, plan_id: str) -> None:
    await get_db().free_claims.delete_one({"telegram_id": telegram_id, "plan_id": str(plan_id)})


# ---------- promo codes ----------

async def create_promo(
    code: str,
    percent_off: int,
    max_uses: Optional[int],
    expires_at: Optional[datetime],
    only_user_id: Optional[int],
    created_by: int,
) -> Optional[dict]:
    doc = {
        "code": code,
        "percent_off": percent_off,
        "max_uses": max_uses,
        "uses": 0,
        "expires_at": expires_at,
        "only_user_id": only_user_id,
        "active": True,
        "created_by": created_by,
        "created_at": now_utc(),
    }
    try:
        await get_db().promo_codes.insert_one(doc)
    except DuplicateKeyError:
        return None
    return doc


async def get_promo(code: str) -> Optional[dict]:
    return await get_db().promo_codes.find_one({"code": code})


async def list_promos(limit: int = 30) -> list[dict]:
    cur = get_db().promo_codes.find({}).sort("created_at", -1).limit(limit)
    return [d async for d in cur]


async def set_promo_active(code: str, active: bool) -> bool:
    res = await get_db().promo_codes.update_one({"code": code}, {"$set": {"active": active}})
    return res.matched_count > 0


async def has_active_promos() -> bool:
    return await get_db().promo_codes.count_documents(
        {
            "active": True,
            "$or": [{"expires_at": None}, {"expires_at": {"$gt": now_utc().replace(tzinfo=None)}}],
        },
        limit=1,
    ) > 0


async def has_redeemed_promo(code: str, telegram_id: int) -> bool:
    return await get_db().promo_redemptions.count_documents(
        {"code": code, "telegram_id": telegram_id}, limit=1
    ) > 0


async def consume_promo(code: str, telegram_id: int) -> bool:
    """Atomically count one use for this user. False if already used by them or exhausted."""
    d = get_db()
    try:
        await d.promo_redemptions.insert_one(
            {"code": code, "telegram_id": telegram_id, "at": now_utc()}
        )
    except DuplicateKeyError:
        return False
    doc = await d.promo_codes.find_one_and_update(
        {
            "code": code,
            "active": True,
            "$or": [{"max_uses": None}, {"$expr": {"$lt": ["$uses", "$max_uses"]}}],
        },
        {"$inc": {"uses": 1}},
    )
    if doc is None:
        await d.promo_redemptions.delete_one({"code": code, "telegram_id": telegram_id})
        return False
    return True


async def release_promo(code: str, telegram_id: int) -> None:
    """Undo a consume (e.g. the key grant failed afterwards)."""
    d = get_db()
    res = await d.promo_redemptions.delete_one({"code": code, "telegram_id": telegram_id})
    if res.deleted_count:
        await d.promo_codes.update_one({"code": code, "uses": {"$gt": 0}}, {"$inc": {"uses": -1}})


async def set_applied_promo(
    telegram_id: int, plan_id: str, code: str, percent: int, expires_at: datetime
) -> None:
    await get_db().promo_applied.update_one(
        {"telegram_id": telegram_id},
        {
            "$set": {
                "plan_id": str(plan_id),
                "code": code,
                "percent": percent,
                "expires_at": expires_at.replace(tzinfo=None),
            }
        },
        upsert=True,
    )


async def get_applied_promo(telegram_id: int, plan_id: str) -> Optional[dict]:
    return await get_db().promo_applied.find_one(
        {
            "telegram_id": telegram_id,
            "plan_id": str(plan_id),
            "expires_at": {"$gt": now_utc().replace(tzinfo=None)},
        }
    )


async def clear_applied_promo(telegram_id: int) -> None:
    await get_db().promo_applied.delete_one({"telegram_id": telegram_id})


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
