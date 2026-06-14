# Don't Remove Credit @VJ_Botz
# Goflix Guard DB - by Harshi

import pymongo
from datetime import datetime
from info import OTHER_DB_URI, DATABASE_NAME

myclient = pymongo.MongoClient(OTHER_DB_URI)
mydb     = myclient[DATABASE_NAME]

guard_settings_col = mydb["guard_settings"]
user_warns_col     = mydb["user_warns"]
guard_banned_col   = mydb["guard_banned"]


# ── Settings ──────────────────────────────────────────────────────────────────

async def get_settings(chat_id: int) -> dict:
    doc = guard_settings_col.find_one({"chat_id": chat_id})
    if not doc:
        return {
            "chat_id":       chat_id,
            "enabled":       False,
            "link_guard":    True,
            "forward_guard": True,
            "longmsg_guard": True,
            "word_limit":    100,
            "warn1_mute":    30,
            "warn2_mute":    180,
        }
    return doc

async def update_settings(chat_id: int, data: dict):
    guard_settings_col.update_one(
        {"chat_id": chat_id},
        {"$set": data},
        upsert=True
    )

async def get_pending_chats(admin_id: int) -> list:
    return list(guard_settings_col.find({
        "_pending_admin": admin_id,
        "_pending_field": {"$exists": True, "$ne": None}
    }))


# ── Warns ─────────────────────────────────────────────────────────────────────

async def add_warn(chat_id: int, user_id: int) -> int:
    doc = user_warns_col.find_one({"chat_id": chat_id, "user_id": user_id})
    if doc:
        new_count = doc["warns"] + 1
        user_warns_col.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {"$set": {"warns": new_count}}
        )
        return new_count
    else:
        user_warns_col.insert_one({
            "chat_id": chat_id,
            "user_id": user_id,
            "warns":   1
        })
        return 1

async def get_warns(chat_id: int, user_id: int) -> int:
    doc = user_warns_col.find_one({"chat_id": chat_id, "user_id": user_id})
    return doc["warns"] if doc else 0

async def reset_warns(chat_id: int, user_id: int):
    user_warns_col.delete_one({"chat_id": chat_id, "user_id": user_id})


# ── Ban log ───────────────────────────────────────────────────────────────────

async def log_ban(chat_id: int, user_id: int):
    guard_banned_col.update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {"$set": {
            "chat_id":   chat_id,
            "user_id":   user_id,
            "banned_at": datetime.utcnow()
        }},
        upsert=True
    )

async def remove_ban_log(chat_id: int, user_id: int):
    guard_banned_col.delete_one({"chat_id": chat_id, "user_id": user_id})

async def get_all_banned(chat_id: int) -> list:
    return list(guard_banned_col.find({"chat_id": chat_id}).sort("banned_at", -1))
