# Kurigram compatibility patch
#
# pyrofork ships a built-in Client.ask()/Client.listen() (via bundled pyromod).
# Kurigram does NOT include this, so every `bot.ask(...)` / `client.ask(...)`
# call in this codebase (broadcast.py, genlink.py, commands.py, clone.py,
# Extra/*.py, rename/*.py, index.py, ...) fails with:
#     AttributeError: 'Client' object has no attribute 'ask'
# Since that call happens BEFORE any try/except in most of these handlers,
# the exception is swallowed by pyrogram's dispatcher and the user sees the
# command do nothing at all - exactly the "no response" broadcast symptom.
#
# This module re-implements a minimal listen()/ask() on top of Kurigram's
# own Client, so every existing `.ask(...)` call keeps working unchanged.
#
# Usage: import and call install_ask_patch() once, before the Client that
# will receive updates is started (see TechVJ/bot/__init__.py).

import asyncio
import logging

from pyrogram import Client, ContinuePropagation
from pyrogram.handlers import MessageHandler

logger = logging.getLogger(__name__)

_PATCHED = False


def install_ask_patch():
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True

    async def listen(self, chat_id, filters=None, timeout=None):
        if not hasattr(self, "_ask_listeners"):
            self._ask_listeners = {}
        future = asyncio.get_event_loop().create_future()
        self._ask_listeners[chat_id] = {"future": future, "filters": filters}
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._ask_listeners.pop(chat_id, None)

    async def ask(self, chat_id, text, filters=None, timeout=None, *args, **kwargs):
        await self.send_message(chat_id, text, *args, **kwargs)
        return await self.listen(chat_id, filters=filters, timeout=timeout)

    async def _resolve_listener(client, message):
        listeners = getattr(client, "_ask_listeners", None)
        data = listeners.get(message.chat.id) if listeners else None
        if not data or data["future"].done():
            raise ContinuePropagation

        f = data["filters"]
        if f is not None:
            try:
                matched = await f(client, message)
            except TypeError:
                matched = f(client, message)
            if not matched:
                raise ContinuePropagation

        data["future"].set_result(message)

    Client.listen = listen
    Client.ask = ask
    Client._resolve_listener = staticmethod(_resolve_listener)


def attach_listener(client: Client):
    """Register the resolver on a specific Client instance at group=-1 so it
    runs before every normal command/message handler."""
    install_ask_patch()
    client.add_handler(MessageHandler(client._resolve_listener), group=-1)
