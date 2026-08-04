import re
import asyncio
from pymongo.errors import DuplicateKeyError
import motor.motor_asyncio
from pymongo import MongoClient
from info import DATABASE_NAME, USER_DB_URI, OTHER_DB_URI, CUSTOM_FILE_CAPTION, IMDB, IMDB_TEMPLATE, MELCOW_NEW_USERS, BUTTON_MODE, SPELL_CHECK_REPLY, PROTECT_CONTENT, AUTO_DELETE, MAX_BTN, AUTO_FFILTER, SHORTLINK_API, SHORTLINK_URL, SHORTLINK_MODE, TUTORIAL, IS_TUTORIAL
import time
import datetime

my_client = MongoClient(OTHER_DB_URI)
mydb = my_client["referal_user"]

# NOTE: my_client above is a plain SYNCHRONOUS pymongo client (unlike `db`
# below, which uses motor's async driver). Every call on it below is
# wrapped in asyncio.to_thread() so a slow/laggy response from this
# cluster only blocks the one request waiting on it - not the bot's whole
# event loop (which would otherwise freeze every user's buttons at once
# for however long that one Mongo call takes).

async def referal_add_user(user_id, ref_user_id):
    user_db = mydb[str(user_id)]
    user = {'_id': ref_user_id}
    try:
        await asyncio.to_thread(user_db.insert_one, user)
        return True
    except DuplicateKeyError:
        return False
    

async def get_referal_all_users(user_id):
    user_db = mydb[str(user_id)]
    # Materialize to a list inside the thread - iterating a pymongo
    # cursor is itself blocking network I/O, so doing that on the main
    # event loop later would reintroduce the exact same freeze.
    return await asyncio.to_thread(lambda: list(user_db.find()))
    
async def get_referal_users_count(user_id):
    user_db = mydb[str(user_id)]
    count = await asyncio.to_thread(user_db.count_documents, {})
    return count
    

async def delete_all_referal_users(user_id):
    user_db = mydb[str(user_id)]
    await asyncio.to_thread(user_db.delete_many, {})

default_setgs = {
    'button': BUTTON_MODE,
    'file_secure': PROTECT_CONTENT,
    'imdb': IMDB,
    'spell_check': SPELL_CHECK_REPLY,
    'welcome': MELCOW_NEW_USERS,
    'auto_delete': AUTO_DELETE,
    'auto_ffilter': AUTO_FFILTER,
    'max_btn': MAX_BTN,
    'template': IMDB_TEMPLATE,
    'caption': CUSTOM_FILE_CAPTION,
    'shortlink': SHORTLINK_URL,
    'shortlink_api': SHORTLINK_API,
    'is_shortlink': SHORTLINK_MODE,
    'fsub': None,
    'tutorial': TUTORIAL,
    'is_tutorial': IS_TUTORIAL
}


class Database:
    
    def __init__(self, uri, database_name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.col = self.db.users
        self.grp = self.db.groups
        self.users = self.db.uersz
        self.bot = self.db.clone_bots


    def new_user(self, id, name):
        return dict(
            id = id,
            name = name,
            file_id=None,
            caption=None,
            message_command=None,
            save=False,
            ban_status=dict(
                is_banned=False,
                ban_reason="",
            ),
        )


    def new_group(self, id, title):
        return dict(
            id = id,
            title = title,
            chat_status=dict(
                is_disabled=False,
                reason="",
            ),
            settings=default_setgs
        )
    
    async def add_user(self, id, name):
        user = self.new_user(id, name)
        await self.col.insert_one(user)
    
    async def is_user_exist(self, id):
        user = await self.col.find_one({'id':int(id)})
        return bool(user)
    
    async def total_users_count(self):
        count = await self.col.count_documents({})
        return count

    async def add_clone_bot(self, bot_id, user_id, bot_token):
        settings = {
            'bot_id': bot_id,
            'bot_token': bot_token,
            'user_id': user_id,
            'url': None,
            'api': None,
            'tutorial': None,
            'update_channel_link': None
        }
        await self.bot.insert_one(settings)

    async def is_clone_exist(self, user_id):
        clone = await self.bot.find_one({'user_id': int(user_id)})
        return bool(clone)

    async def delete_clone(self, user_id):
        await self.bot.delete_many({'user_id': int(user_id)})

    async def get_clone(self, user_id):
        clone_data = await self.bot.find_one({"user_id": user_id})
        return clone_data
            
    async def update_clone(self, user_id, user_data):
        await self.bot.update_one({"user_id": user_id}, {"$set": user_data}, upsert=True)

    async def get_bot(self, bot_id):
        bot_data = await self.bot.find_one({"bot_id": bot_id})
        return bot_data
            
    async def update_bot(self, bot_id, bot_data):
        await self.bot.update_one({"bot_id": bot_id}, {"$set": bot_data}, upsert=True)
    
    async def get_all_bots(self):
        return self.bot.find({})
        
    async def remove_ban(self, id):
        ban_status = dict(
            is_banned=False,
            ban_reason=''
        )
        await self.col.update_one({'id': id}, {'$set': {'ban_status': ban_status}})
    
    async def ban_user(self, user_id, ban_reason="No Reason"):
        ban_status = dict(
            is_banned=True,
            ban_reason=ban_reason
        )
        await self.col.update_one({'id': user_id}, {'$set': {'ban_status': ban_status}})

    async def get_ban_status(self, id):
        default = dict(
            is_banned=False,
            ban_reason=''
        )
        user = await self.col.find_one({'id':int(id)})
        if not user:
            return default
        return user.get('ban_status', default)

    async def get_all_users(self):
        return self.col.find({})
    

    async def delete_user(self, user_id):
        await self.col.delete_many({'id': int(user_id)})


    async def get_banned(self):
        users = self.col.find({'ban_status.is_banned': True})
        chats = self.grp.find({'chat_status.is_disabled': True})
        b_chats = [chat['id'] async for chat in chats]
        b_users = [user['id'] async for user in users]
        return b_users, b_chats
    


    async def add_chat(self, chat, title):
        chat = self.new_group(chat, title)
        await self.grp.insert_one(chat)
    

    async def get_chat(self, chat):
        chat = await self.grp.find_one({'id':int(chat)})
        return False if not chat else chat.get('chat_status')
    

    async def re_enable_chat(self, id):
        chat_status=dict(
            is_disabled=False,
            reason="",
            )
        await self.grp.update_one({'id': int(id)}, {'$set': {'chat_status': chat_status}})
        
    async def update_settings(self, id, settings):
        await self.grp.update_one({'id': int(id)}, {'$set': {'settings': settings}})
        
    
    async def get_settings(self, id):
        chat = await self.grp.find_one({'id':int(id)})
        if chat:
            return chat.get('settings', default_setgs)
        return default_setgs
    

    async def disable_chat(self, chat, reason="No Reason"):
        chat_status=dict(
            is_disabled=True,
            reason=reason,
            )
        await self.grp.update_one({'id': int(chat)}, {'$set': {'chat_status': chat_status}})
    

    async def total_chat_count(self):
        count = await self.grp.count_documents({})
        return count
    

    async def get_all_chats(self):
        return self.grp.find({})


    async def get_db_size(self):
        return (await self.db.command("dbstats"))['dataSize']

    async def get_user(self, user_id):
        user_data = await self.users.find_one({"id": user_id})
        return user_data
            
    async def update_user(self, user_data):
        await self.users.update_one({"id": user_data["id"]}, {"$set": user_data}, upsert=True)

    async def has_premium_access(self, user_id):
        user_data = await self.get_user(user_id)
        if user_data:
            expiry_time = user_data.get("expiry_time")
            if expiry_time is None:
                # User previously used the free trial, but it has ended.
                return False
            elif isinstance(expiry_time, datetime.datetime) and datetime.datetime.now() <= expiry_time:
                return True
            else:
                await self.users.update_one({"id": user_id}, {"$set": {"expiry_time": None}})
        return False
    
    async def check_remaining_uasge(self, userid):
        user_id = userid
        user_data = await self.get_user(user_id)        
        expiry_time = user_data.get("expiry_time")
        # Calculate remaining time
        remaining_time = expiry_time - datetime.datetime.now()
        return remaining_time

    async def get_free_trial_status(self, user_id):
        user_data = await self.get_user(user_id)
        if user_data:
            return user_data.get("has_free_trial", False)
        return False

    async def give_free_trail(self, userid):        
        user_id = userid
        seconds = 5*60         
        expiry_time = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
        user_data = {"id": user_id, "expiry_time": expiry_time, "has_free_trial": True}
        await self.users.update_one({"id": user_id}, {"$set": user_data}, upsert=True)
    
    
    async def all_premium_users(self):
        count = await self.users.count_documents({
        "expiry_time": {"$gt": datetime.datetime.now()}
        })
        return count

    async def extend_all_premium_users(self, days):
        """Extend expiry_time by `days` for every user with CURRENTLY ACTIVE
        premium (used for e.g. compensating users after server downtime).
        Returns the list of user ids that were extended."""
        now = datetime.datetime.now()
        extended_ids = []
        cursor = self.users.find({"expiry_time": {"$gt": now}})
        async for user_data in cursor:
            uid = user_data["id"]
            old_expiry = user_data.get("expiry_time")
            if not isinstance(old_expiry, datetime.datetime):
                continue
            new_expiry = old_expiry + datetime.timedelta(days=days)
            await self.users.update_one({"id": uid}, {"$set": {"expiry_time": new_expiry}})
            extended_ids.append(uid)
        return extended_ids

    async def get_all_premium_users(self):
        """All users with currently-active premium, soonest-expiring first.
        Used by /premium_list."""
        now = datetime.datetime.now()
        cursor = self.users.find({"expiry_time": {"$gt": now}}).sort("expiry_time", 1)
        return [user_data async for user_data in cursor]

    async def get_users_expiring_within(self, seconds):
        """Active-premium users whose expiry falls within the next `seconds`
        who have NOT already been sent the expiry reminder. Used by the
        daily expiry-notifier loop to send a one-time 'ends tomorrow' notice."""
        now = datetime.datetime.now()
        soon = now + datetime.timedelta(seconds=seconds)
        cursor = self.users.find({
            "expiry_time": {"$gt": now, "$lte": soon},
            "expiry_reminder_sent": {"$ne": True}
        })
        return [user_data async for user_data in cursor]

    async def mark_expiry_reminder_sent(self, user_id):
        await self.users.update_one({"id": user_id}, {"$set": {"expiry_reminder_sent": True}})

    async def get_recently_expired_users(self, seconds):
        """Users whose premium expired within the last `seconds` who have
        NOT already been sent the 'premium ended' notice. Used by the daily
        expiry-notifier loop to send a one-time thank-you/upsell message."""
        now = datetime.datetime.now()
        cutoff = now - datetime.timedelta(seconds=seconds)
        cursor = self.users.find({
            "expiry_time": {"$lte": now, "$gt": cutoff},
            "expired_notified": {"$ne": True}
        })
        return [user_data async for user_data in cursor]

    async def mark_expired_notified(self, user_id):
        await self.users.update_one({"id": user_id}, {"$set": {"expired_notified": True}})

    # ================== [PERSISTENT DAILY VERIFICATION] ==================
    # These mirror the free-trial/premium pattern above but store the
    # "verified until" date in Mongo instead of an in-memory dict, so
    # verification status survives bot restarts/redeploys.
    async def set_verified(self, user_id, expiry_date_str):
        await self.users.update_one(
            {"id": user_id},
            {"$set": {"verified_until": expiry_date_str}},
            upsert=True
        )

    async def is_verified_today(self, user_id):
        user_data = await self.get_user(user_id)
        if not user_data:
            return False
        expiry_date_str = user_data.get("verified_until")
        if not expiry_date_str:
            return False
        try:
            years, month, day = expiry_date_str.split('-')
            comp = datetime.date(int(years), int(month), int(day))
        except Exception:
            return False
        return comp >= datetime.date.today()

    async def get_all_verified_users(self):
        """All users whose persisted daily verification is still valid
        today (mirrors the same comp >= today logic as is_verified_today).
        Used by the owner-only /verification command."""
        today = datetime.date.today()
        cursor = self.users.find({"verified_until": {"$exists": True, "$ne": None}})
        verified = []
        async for user_data in cursor:
            expiry_date_str = user_data.get("verified_until")
            try:
                years, month, day = expiry_date_str.split('-')
                comp = datetime.date(int(years), int(month), int(day))
            except Exception:
                continue
            if comp >= today:
                verified.append(user_data)
        return verified

    async def set_thumbnail(self, id, file_id):
        await self.col.update_one({'id': int(id)}, {'$set': {'file_id': file_id}})

    async def get_thumbnail(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('file_id', None)

    async def set_caption(self, id, caption):
        await self.col.update_one({'id': int(id)}, {'$set': {'caption': caption}})

    async def get_caption(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('caption', None)

    async def set_msg_command(self, id, com):
        await self.col.update_one({'id': int(id)}, {'$set': {'message_command': com}})

    async def get_msg_command(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('message_command', None)

    async def set_save(self, id, save):
        await self.col.update_one({'id': int(id)}, {'$set': {'save': save}})

    async def get_save(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('save', False) 

    async def get_bot_setting(self, bot_id, setting_key, default_value):
        bot = await self.col.find_one({'id': int(bot_id)}, {setting_key: 1, '_id': 0})
        return bot[setting_key] if bot and setting_key in bot else default_value

    async def update_bot_setting(self, bot_id, setting_key, value):
        await self.col.update_one(
            {'id': int(bot_id)},
            {'$set': {setting_key: value}},
            upsert=True
        )

    async def movie_update_status(self, bot_id):
        from info import MOVIE_UPDATE_NOTIFICATION
        return await self.get_bot_setting(bot_id, 'MOVIE_UPDATE_NOTIFICATION', MOVIE_UPDATE_NOTIFICATION)

    async def update_movie_update_status(self, bot_id, enable):
        await self.update_bot_setting(bot_id, 'MOVIE_UPDATE_NOTIFICATION', enable)


db = Database(USER_DB_URI, DATABASE_NAME)
