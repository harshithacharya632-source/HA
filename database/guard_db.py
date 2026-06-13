from database.connections_mdb import mondb
from datetime import datetime

def guard_col():
    return mondb()["guard_settings"]

def warns_col():
    return mondb()["user_warns"]

def banned_col():
    return mondb()["guard_banned"]


# ── Settings ──────────────────────────────────────────────────────────────────

async def get_settings(chat_id: int) -> dict:
    doc = await guard_col().find_one({"chat_id": chat_id})
    if not doc:
        # Default settings
        return {
            "chat_id": chat_id,
            "enabled": False,
            "link_guard": True,
            "forward_guard": True,
            "longmsg_guard": True,
            "word_limit": 100,
            "warn1_mute": 30,
            "warn2_mute": 180,
        }
    return doc

async def update_settings(chat_id: int, data: dict):
    await guard_col().update_one(
        {"chat_id": chat_id},
        {"$set": data},
        upsert=True
    )


# ── Warns ─────────────────────────────────────────────────────────────────────

async def add_warn(chat_id: int, user_id: int) -> int:
    doc = await warns_col().find_one_and_update(
        {"chat_id": chat_id, "user_id": user_id},
        {"$inc": {"warns": 1}},
        upsert=True,
        return_document=True
    )
    return doc["warns"] if doc else 1

async def get_warns(chat_id: int, user_id: int) -> int:
    doc = await warns_col().find_one({"chat_id": chat_id, "user_id": user_id})
    return doc["warns"] if doc else 0

async def reset_warns(chat_id: int, user_id: int):
    await warns_col().delete_one({"chat_id": chat_id, "user_id": user_id})


# ── Ban log ───────────────────────────────────────────────────────────────────

async def log_ban(chat_id: int, user_id: int):
    await banned_col().update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {"$set": {
            "chat_id": chat_id,
            "user_id": user_id,
            "banned_at": datetime.utcnow()
        }},
        upsert=True
    )

async def remove_ban_log(chat_id: int, user_id: int):
    await banned_col().delete_one({"chat_id": chat_id, "user_id": user_id})

async def get_all_banned(chat_id: int) -> list:
    cursor = banned_col().find({"chat_id": chat_id}).sort("banned_at", -1)
    return await cursor.to_list(length=None)
