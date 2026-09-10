import asyncio
import logging
import os
import time
import inspect
import threading
import sys
from typing import List, Dict, Any, Optional

import aiosqlite
from flask import Flask, request
from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    ChatMember,
)
from telegram.constants import ChatType, ChatMemberStatus, ParseMode
from telegram.error import TelegramError, NetworkError, TimedOut, Conflict
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL", "NCK_Dev")
FORCE_JOIN_CHANNEL_LINK = os.getenv("FORCE_JOIN_CHANNEL_LINK", "https://t.me/NCK_Dev")
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot.db")
PORT = int(os.getenv("PORT", "8080"))
KEEP_ALIVE_ENABLED = os.getenv("KEEP_ALIVE_ENABLED", "true").lower() == "true"
DEFAULT_DELETE_DELAY = int(os.getenv("DEFAULT_DELETE_DELAY", "300"))

DEBUG_MODE = os.getenv("DEBUG_MODE", "true").lower() == "true"

WATCHDOG_INTERVAL = 90
WATCHDOG_MAX_FAILURES = 3

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set.")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
log = logging.getLogger("autodelete-bot")

TIMER_PRESETS = [
    ("Instant", 0),
    ("30 secs", 30),
    ("1 min", 60),
    ("5 min", 300),
    ("15 min", 900),
    ("1 hour", 3600),
]

DEFAULT_BANNED_KEYWORDS = [
    "sex", "18+", "porn", "xxx", "nsfw", "adult", "nude", "naked",
    "fuck", "shit", "asshole", "bitch", "cunt", "dick", "pussy",
    "penis", "vagina", "boobs", "tits", "cum", "semen", "orgasm",
    "masturbate", "incest", "rape", "drugs", "cocaine", "heroin",
    "meth", "crack", "weed", "marijuana", "lsd", "mdma", "ecstasy"
]

MANAGEABLE_TYPES = (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL)

PENDING_INPUT: dict[tuple[int, int], tuple[str, int]] = {}

db: aiosqlite.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id      INTEGER PRIMARY KEY,
    title        TEXT,
    type         TEXT,
    delete_delay INTEGER NOT NULL DEFAULT 300,
    force_join   INTEGER NOT NULL DEFAULT 1,
    enabled      INTEGER NOT NULL DEFAULT 0,
    bot_is_admin INTEGER NOT NULL DEFAULT 1,
    managing_started INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS approved_admins (
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    name      TEXT,
    username  TEXT,
    signature TEXT,
    is_bot    INTEGER DEFAULT 0,
    admin_type TEXT DEFAULT 'member',
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS banned_keywords (
    chat_id INTEGER NOT NULL,
    keyword TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (chat_id, keyword)
);

CREATE TABLE IF NOT EXISTS whitelist_keywords (
    chat_id INTEGER NOT NULL,
    keyword TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (chat_id, keyword)
);

CREATE TABLE IF NOT EXISTS delete_notify_subscribers (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS removed_chats (
    chat_id INTEGER PRIMARY KEY,
    removed_at INTEGER,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS all_admins (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    name TEXT,
    username TEXT,
    is_bot INTEGER DEFAULT 0,
    status TEXT,
    last_updated INTEGER,
    PRIMARY KEY (chat_id, user_id)
);
"""


# ==========================================================================
# DEBUG HELPERS
# ==========================================================================

def get_forward_origin_info(message) -> dict:
    """Extract forward info from a message (Bot API 7.0+ and legacy)."""
    info = {
        "is_forward": False,
        "original_sender_id": None,
        "original_sender_name": None,
        "original_sender_username": None,
        "original_sender_is_bot": None,
        "original_chat_id": None,
        "original_chat_title": None,
        "forward_origin_type": None,
    }

    fo = getattr(message, "forward_origin", None)
    if fo:
        info["is_forward"] = True
        info["forward_origin_type"] = fo.type

        if hasattr(fo, "sender_user") and fo.sender_user:
            info["original_sender_id"] = fo.sender_user.id
            info["original_sender_name"] = fo.sender_user.full_name
            info["original_sender_username"] = fo.sender_user.username
            info["original_sender_is_bot"] = fo.sender_user.is_bot
        elif hasattr(fo, "sender_user_name") and fo.sender_user_name:
            info["original_sender_name"] = fo.sender_user_name
        elif hasattr(fo, "sender_chat") and fo.sender_chat:
            info["original_chat_id"] = fo.sender_chat.id
            info["original_chat_title"] = fo.sender_chat.title
        elif hasattr(fo, "chat") and fo.chat:
            info["original_chat_id"] = fo.chat.id
            info["original_chat_title"] = fo.chat.title

    if getattr(message, "forward_from", None):
        info["is_forward"] = True
        info["original_sender_id"] = message.forward_from.id
        info["original_sender_name"] = message.forward_from.full_name
        info["original_sender_username"] = message.forward_from.username
        info["original_sender_is_bot"] = message.forward_from.is_bot

    if getattr(message, "forward_from_chat", None):
        info["is_forward"] = True
        info["original_chat_id"] = message.forward_from_chat.id
        info["original_chat_title"] = message.forward_from_chat.title

    if getattr(message, "forward_sender_name", None):
        info["is_forward"] = True
        info["original_sender_name"] = message.forward_sender_name

    return info


def log_message_debug(prefix: str, message, extra: dict | None = None):
    """Log ALL fields of a message for debugging."""
    if not DEBUG_MODE:
        return

    if message is None:
        log.info(f"🔍 {prefix}: message is None")
        return

    log.info(f"🔍 {prefix}")
    log.info(f"    message_id={message.message_id}")
    log.info(f"    chat_id={message.chat.id if message.chat else None}")
    log.info(f"    chat_type={message.chat.type if message.chat else None}")
    log.info(f"    chat_title={message.chat.title if message.chat else None}")

    if message.from_user:
        log.info(f"    from_user: id={message.from_user.id}, "
                 f"name={message.from_user.full_name!r}, "
                 f"username=@{message.from_user.username}, "
                 f"is_bot={message.from_user.is_bot}")
    else:
        log.info(f"    from_user=None")

    if message.sender_chat:
        log.info(f"    sender_chat: id={message.sender_chat.id}, "
                 f"title={message.sender_chat.title!r}")
    else:
        log.info(f"    sender_chat=None")

    log.info(f"    author_signature={message.author_signature!r}")

    fwd = get_forward_origin_info(message)
    if fwd["is_forward"]:
        log.info(f"    🔄 FORWARDED MESSAGE:")
        log.info(f"        origin_type={fwd['forward_origin_type']}")
        log.info(f"        original_sender_id={fwd['original_sender_id']}")
        log.info(f"        original_sender_name={fwd['original_sender_name']!r}")
        log.info(f"        original_sender_username={fwd['original_sender_username']}")
        log.info(f"        original_sender_is_bot={fwd['original_sender_is_bot']}")
        log.info(f"        original_chat_id={fwd['original_chat_id']}")
        log.info(f"        original_chat_title={fwd['original_chat_title']!r}")

    log.info(f"    has_text={bool(message.text)}")
    log.info(f"    has_caption={bool(message.caption)}")
    if message.text:
        log.info(f"    text={message.text[:100]!r}")
    if message.caption:
        log.info(f"    caption={message.caption[:100]!r}")

    if extra:
        for k, v in extra.items():
            log.info(f"    {k}={v!r}")


# ==========================================================================
# FLASK HEALTH SERVER (daemon thread)
# ==========================================================================

health_app = Flask(__name__)
_start_time = time.time()


@health_app.route('/')
@health_app.route('/health')
@health_app.route('/health/')
def health_check():
    return "OK", 200


@health_app.route('/status')
def status_check():
    return {"status": "ok", "uptime_seconds": int(time.time() - _start_time)}, 200


@health_app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/health'):
        return "OK", 200
    return "Not Found", 404


def run_health_server():
    log.info(f"✅ Health check server starting on port {PORT}")
    health_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False, threaded=True)


# --------------------------------------------------------------------------
# DB helpers
# --------------------------------------------------------------------------

async def init_db():
    global db
    db = await aiosqlite.connect(DATABASE_PATH)
    await db.executescript(SCHEMA)
    await db.commit()
    log.info("Database initialized")


async def ensure_chat(chat_id: int, title: str | None = None, chat_type: str | None = None):
    try:
        await db.execute(
            "INSERT OR IGNORE INTO chats (chat_id, title, type, delete_delay, enabled, managing_started) VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, title, chat_type, DEFAULT_DELETE_DELAY, 0, 0),
        )
        if title:
            await db.execute("UPDATE chats SET title = ? WHERE chat_id = ?", (title, chat_id))
        if chat_type:
            await db.execute("UPDATE chats SET type = ? WHERE chat_id = ?", (chat_type, chat_id))
        await db.commit()
    except Exception as e:
        log.error(f"ensure_chat failed: {e}")


async def mark_bot_admin(chat_id: int, title: str | None, chat_type: str, is_admin: bool):
    await ensure_chat(chat_id, title, chat_type)
    try:
        await db.execute("UPDATE chats SET bot_is_admin = ? WHERE chat_id = ?", (int(is_admin), chat_id))
        await db.commit()
    except Exception as e:
        log.error(f"mark_bot_admin failed: {e}")


async def list_known_chats() -> list[tuple[int, str, str]]:
    try:
        cur = await db.execute("SELECT chat_id, title, type FROM chats WHERE bot_is_admin = 1 ORDER BY title")
        rows = await cur.fetchall()
        await cur.close()
        return rows
    except Exception as e:
        log.error(f"list_known_chats failed: {e}")
        return []


async def get_settings(chat_id: int) -> dict:
    await ensure_chat(chat_id)
    try:
        cur = await db.execute(
            "SELECT delete_delay, force_join, enabled, managing_started FROM chats WHERE chat_id = ?",
            (chat_id,)
        )
        row = await cur.fetchone()
        await cur.close()
        return {
            "delete_delay": row[0],
            "force_join": bool(row[1]),
            "enabled": bool(row[2]),
            "managing_started": bool(row[3])
        }
    except Exception as e:
        log.error(f"get_settings failed: {e}")
        return {"delete_delay": 300, "force_join": True, "enabled": False, "managing_started": False}


async def set_delete_delay(chat_id: int, seconds: int):
    await ensure_chat(chat_id)
    try:
        await db.execute("UPDATE chats SET delete_delay = ? WHERE chat_id = ?", (seconds, chat_id))
        await db.commit()
    except Exception as e:
        log.error(f"set_delete_delay failed: {e}")


async def toggle_force_join(chat_id: int) -> bool:
    s = await get_settings(chat_id)
    new_val = not s["force_join"]
    try:
        await db.execute("UPDATE chats SET force_join = ? WHERE chat_id = ?", (int(new_val), chat_id))
        await db.commit()
    except Exception as e:
        log.error(f"toggle_force_join failed: {e}")
    return new_val


async def toggle_enabled(chat_id: int) -> bool:
    s = await get_settings(chat_id)
    new_val = not s["enabled"]
    try:
        await db.execute("UPDATE chats SET enabled = ? WHERE chat_id = ?", (int(new_val), chat_id))
        await db.commit()
    except Exception as e:
        log.error(f"toggle_enabled failed: {e}")
    return new_val


async def start_managing(chat_id: int) -> bool:
    try:
        await db.execute("UPDATE chats SET enabled = 1, managing_started = 1 WHERE chat_id = ?", (chat_id,))
        await db.commit()
    except Exception as e:
        log.error(f"start_managing failed: {e}")
    return True


async def is_managing_started(chat_id: int) -> bool:
    s = await get_settings(chat_id)
    return s["managing_started"]


async def is_approved(chat_id: int, user_id: int) -> bool:
    try:
        cur = await db.execute("SELECT 1 FROM approved_admins WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        row = await cur.fetchone()
        await cur.close()
        return row is not None
    except Exception as e:
        log.error(f"is_approved failed: {e}")
        return False


async def list_all_approved(chat_id: int) -> list[dict]:
    try:
        cur = await db.execute(
            "SELECT user_id, name, username, signature, is_bot FROM approved_admins WHERE chat_id = ?",
            (chat_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [
            {"user_id": r[0], "name": r[1], "username": r[2], "signature": r[3], "is_bot": bool(r[4])}
            for r in rows
        ]
    except Exception as e:
        log.error(f"list_all_approved failed: {e}")
        return []


async def is_approved_multi(chat_id: int, user_id: int | None, username: str | None,
                             full_name: str | None, signature: str | None = None) -> tuple[bool, str]:
    try:
        if user_id is not None:
            cur = await db.execute("SELECT 1 FROM approved_admins WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
            if await cur.fetchone():
                await cur.close()
                return True, "user_id"
            await cur.close()

        if username:
            cur = await db.execute(
                "SELECT 1 FROM approved_admins WHERE chat_id = ? AND username = ? COLLATE NOCASE",
                (chat_id, username.lstrip("@")),
            )
            if await cur.fetchone():
                await cur.close()
                return True, "username"
            await cur.close()

        if signature:
            cur = await db.execute(
                "SELECT 1 FROM approved_admins WHERE chat_id = ? AND signature = ? COLLATE NOCASE",
                (chat_id, signature),
            )
            if await cur.fetchone():
                await cur.close()
                return True, "signature"
            await cur.close()

        if full_name:
            cur = await db.execute(
                "SELECT 1 FROM approved_admins WHERE chat_id = ? AND name = ? COLLATE NOCASE",
                (chat_id, full_name),
            )
            if await cur.fetchone():
                await cur.close()
                return True, "name"
            await cur.close()
    except Exception as e:
        log.error(f"is_approved_multi failed: {e}")

    return False, ""


async def toggle_approved(chat_id: int, user_id: int, name: str, username: str | None, signature: str | None, is_bot: bool = False):
    try:
        if await is_approved(chat_id, user_id):
            await db.execute("DELETE FROM approved_admins WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        else:
            await db.execute(
                "INSERT OR REPLACE INTO approved_admins (chat_id, user_id, name, username, signature, is_bot) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, user_id, name, username, signature, int(is_bot)),
            )
        await db.commit()
    except Exception as e:
        log.error(f"toggle_approved failed: {e}")


async def is_signature_approved(chat_id: int, signature: str | None) -> bool:
    if not signature:
        return False
    try:
        cur = await db.execute(
            "SELECT 1 FROM approved_admins WHERE chat_id = ? AND signature = ? COLLATE NOCASE",
            (chat_id, signature),
        )
        row = await cur.fetchone()
        await cur.close()
        return row is not None
    except Exception as e:
        log.error(f"is_signature_approved failed: {e}")
        return False


async def list_keywords(chat_id: int) -> list[str]:
    try:
        cur = await db.execute("SELECT keyword FROM banned_keywords WHERE chat_id = ? ORDER BY keyword", (chat_id,))
        rows = await cur.fetchall()
        await cur.close()
        return [r[0] for r in rows]
    except Exception as e:
        log.error(f"list_keywords failed: {e}")
        return []


async def add_keyword(chat_id: int, keyword: str):
    await ensure_chat(chat_id)
    keyword = keyword.strip().lower()[:40]
    if keyword:
        try:
            await db.execute(
                "INSERT OR IGNORE INTO banned_keywords (chat_id, keyword) VALUES (?, ?)",
                (chat_id, keyword),
            )
            await db.commit()
        except Exception as e:
            log.error(f"add_keyword failed: {e}")


async def remove_keyword_by_index(chat_id: int, index: int):
    kws = await list_keywords(chat_id)
    if 0 <= index < len(kws):
        try:
            await db.execute("DELETE FROM banned_keywords WHERE chat_id = ? AND keyword = ?", (chat_id, kws[index]))
            await db.commit()
        except Exception as e:
            log.error(f"remove_keyword_by_index failed: {e}")


async def list_whitelist(chat_id: int) -> list[str]:
    try:
        cur = await db.execute("SELECT keyword FROM whitelist_keywords WHERE chat_id = ? ORDER BY keyword", (chat_id,))
        rows = await cur.fetchall()
        await cur.close()
        return [r[0] for r in rows]
    except Exception as e:
        log.error(f"list_whitelist failed: {e}")
        return []


async def add_whitelist_keyword(chat_id: int, keyword: str):
    await ensure_chat(chat_id)
    keyword = keyword.strip().lower()[:40]
    if keyword:
        try:
            await db.execute(
                "INSERT OR IGNORE INTO whitelist_keywords (chat_id, keyword) VALUES (?, ?)",
                (chat_id, keyword),
            )
            await db.commit()
        except Exception as e:
            log.error(f"add_whitelist_keyword failed: {e}")


async def remove_whitelist_keyword_by_index(chat_id: int, index: int):
    kws = await list_whitelist(chat_id)
    if 0 <= index < len(kws):
        try:
            await db.execute("DELETE FROM whitelist_keywords WHERE chat_id = ? AND keyword = ?", (chat_id, kws[index]))
            await db.commit()
        except Exception as e:
            log.error(f"remove_whitelist_keyword_by_index failed: {e}")


async def approve_admin(chat_id: int, user_id: int, name: str, username: str | None,
                         signature: str | None, is_bot: bool = False):
    await ensure_chat(chat_id)
    try:
        await db.execute(
            "INSERT OR REPLACE INTO approved_admins (chat_id, user_id, name, username, signature, is_bot) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, user_id, name, username, signature, int(is_bot)),
        )
        await db.commit()
        log.info(f"✅ Approved admin {user_id} (@{username}) signature '{signature}' in chat {chat_id}")
    except Exception as e:
        log.error(f"approve_admin failed: {e}")


async def is_notify_subscribed(chat_id: int, user_id: int) -> bool:
    try:
        cur = await db.execute(
            "SELECT 1 FROM delete_notify_subscribers WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        row = await cur.fetchone()
        await cur.close()
        return row is not None
    except Exception as e:
        log.error(f"is_notify_subscribed failed: {e}")
        return False


async def toggle_notify_subscription(chat_id: int, user_id: int) -> bool:
    try:
        if await is_notify_subscribed(chat_id, user_id):
            await db.execute("DELETE FROM delete_notify_subscribers WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
            await db.commit()
            return False
        await db.execute(
            "INSERT OR IGNORE INTO delete_notify_subscribers (chat_id, user_id) VALUES (?, ?)",
            (chat_id, user_id),
        )
        await db.commit()
        return True
    except Exception as e:
        log.error(f"toggle_notify_subscription failed: {e}")
        return False


async def list_notify_subscribers(chat_id: int) -> list[int]:
    try:
        cur = await db.execute("SELECT user_id FROM delete_notify_subscribers WHERE chat_id = ?", (chat_id,))
        rows = await cur.fetchall()
        await cur.close()
        return [r[0] for r in rows]
    except Exception as e:
        log.error(f"list_notify_subscribers failed: {e}")
        return []


async def init_default_keywords(chat_id: int):
    existing = await list_keywords(chat_id)
    for kw in DEFAULT_BANNED_KEYWORDS:
        if kw not in existing:
            await add_keyword(chat_id, kw)


async def remove_chat_from_management(chat_id: int, reason: str = "User removed"):
    try:
        await db.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM approved_admins WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM banned_keywords WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM all_admins WHERE chat_id = ?", (chat_id,))
        await db.execute(
            "INSERT OR REPLACE INTO removed_chats (chat_id, removed_at, reason) VALUES (?, ?, ?)",
            (chat_id, int(time.time()), reason)
        )
        await db.commit()
        log.info(f"Chat {chat_id} removed from management. Reason: {reason}")
    except Exception as e:
        log.error(f"remove_chat_from_management failed: {e}")


async def is_chat_removed(chat_id: int) -> bool:
    try:
        cur = await db.execute("SELECT 1 FROM removed_chats WHERE chat_id = ?", (chat_id,))
        row = await cur.fetchone()
        await cur.close()
        return row is not None
    except Exception as e:
        log.error(f"is_chat_removed failed: {e}")
        return False


async def restore_chat(chat_id: int):
    try:
        await db.execute("DELETE FROM removed_chats WHERE chat_id = ?", (chat_id,))
        await db.commit()
        log.info(f"Chat {chat_id} restored to management")
    except Exception as e:
        log.error(f"restore_chat failed: {e}")


async def list_removed_chats() -> list[tuple[int, str, int]]:
    try:
        cur = await db.execute("SELECT chat_id, reason, removed_at FROM removed_chats ORDER BY removed_at DESC")
        rows = await cur.fetchall()
        await cur.close()
        return rows
    except Exception as e:
        log.error(f"list_removed_chats failed: {e}")
        return []


async def store_all_admins(chat_id: int, admins: List[ChatMember]):
    try:
        for admin in admins:
            await db.execute(
                """INSERT OR REPLACE INTO all_admins 
                   (chat_id, user_id, name, username, is_bot, status, last_updated) 
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    chat_id,
                    admin.user.id,
                    admin.user.full_name or admin.user.username or str(admin.user.id),
                    admin.user.username,
                    int(admin.user.is_bot),
                    admin.status,
                    int(time.time())
                )
            )
        await db.commit()
        log.info(f"Stored {len(admins)} admins for chat {chat_id}")
    except Exception as e:
        log.error(f"store_all_admins failed: {e}")


async def get_all_admins(chat_id: int) -> list[dict]:
    try:
        cur = await db.execute(
            "SELECT user_id, name, username, is_bot, status FROM all_admins WHERE chat_id = ?",
            (chat_id,)
        )
        rows = await cur.fetchall()
        await cur.close()
        return [
            {"user_id": row[0], "name": row[1], "username": row[2], "is_bot": bool(row[3]), "status": row[4]}
            for row in rows
        ]
    except Exception as e:
        log.error(f"get_all_admins failed: {e}")
        return []


# --------------------------------------------------------------------------
# Telegram helpers
# --------------------------------------------------------------------------

async def user_is_chat_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except TelegramError as e:
        log.debug(f"user_is_chat_admin({chat_id}, {user_id}) failed: {e}")
        return False


async def user_joined_force_channel(bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(f"@{FORCE_JOIN_CHANNEL}", user_id)
        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except TelegramError as e:
        log.warning("Force-join check failed (%s) — allowing through", e)
        return True


def force_join_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=FORCE_JOIN_CHANNEL_LINK)],
        [InlineKeyboardButton("✅ I've Joined", callback_data="check_join")],
    ])


def fmt_delay(seconds: int) -> str:
    if seconds == 0:
        return "Instant"
    elif seconds == 30:
        return "30 secs"
    elif seconds < 60:
        return f"{seconds} secs"
    elif seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} hr"


# --------------------------------------------------------------------------
# Menu builders (shortened for brevity — same as before)
# --------------------------------------------------------------------------

async def main_menu_markup(chat_id: int, in_dm: bool, user_id: int) -> InlineKeyboardMarkup:
    s = await get_settings(chat_id)
    rows = []

    if not s["managing_started"]:
        rows.append([InlineKeyboardButton("🚀 START MANAGING", callback_data=f"start_mgmt:{chat_id}")])
        rows.append([InlineKeyboardButton("ℹ️ Setup Required", callback_data="noop")])

    rows.append([InlineKeyboardButton("👮 Approved Admins", callback_data=f"adm:{chat_id}")])
    rows.append([InlineKeyboardButton(f"⏱ Deletion Timer ({fmt_delay(s['delete_delay'])})", callback_data=f"tmr:{chat_id}")])
    rows.append([InlineKeyboardButton("🚫 Blacklist", callback_data=f"kw:{chat_id}")])
    rows.append([InlineKeyboardButton("✅ Whitelist", callback_data=f"wl:{chat_id}")])

    if s["managing_started"]:
        rows.append([InlineKeyboardButton(
            f"{'🟢' if s['enabled'] else '🔴'} Auto-Delete: {'ON' if s['enabled'] else 'OFF'}",
            callback_data=f"te:{chat_id}")])
        rows.append([InlineKeyboardButton(
            f"{'🟢' if s['force_join'] else '🔴'} Force-Join: {'ON' if s['force_join'] else 'OFF'}",
            callback_data=f"tf:{chat_id}")])
    else:
        rows.append([InlineKeyboardButton("⏳ Waiting for Start", callback_data="noop")])

    notify_on = await is_notify_subscribed(chat_id, user_id)
    rows.append([InlineKeyboardButton(
        f"{'🔔' if notify_on else '🔕'} Delete Notifications: {'ON' if notify_on else 'OFF'}",
        callback_data=f"tn:{chat_id}")])

    if in_dm:
        rows.append([InlineKeyboardButton("🔄 Refresh Admins", callback_data=f"refresh_admins:{chat_id}")])
        rows.append([InlineKeyboardButton("🗑 Remove Chat", callback_data=f"rm:{chat_id}")])
        rows.append([InlineKeyboardButton("🔙 My Chats", callback_data="chats")])
    else:
        rows.append([InlineKeyboardButton("❌ Close", callback_data=f"close:{chat_id}")])
    return InlineKeyboardMarkup(rows)


async def admins_menu_markup(bot, chat_id: int, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    try:
        admins = await bot.get_chat_administrators(chat_id)
        await store_all_admins(chat_id, admins)
    except TelegramError as e:
        log.error(f"Couldn't fetch admin list: {e}")
        stored_admins = await get_all_admins(chat_id)
        if not stored_admins:
            return "Couldn't fetch admin list.", InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back", callback_data=f"menu:{chat_id}")]])
        admins = []
        for admin in stored_admins:
            class User:
                def __init__(self, d):
                    self.id = d['user_id']; self.full_name = d['name']
                    self.username = d['username']; self.is_bot = d['is_bot']
            class Member:
                def __init__(self, d):
                    self.user = User(d); self.status = d['status']; self.custom_title = ""
            admins.append(Member(admin))

    bot_user = await bot.get_me()
    bot_id = bot_user.id

    admin_list = []
    for m in admins:
        is_bot = m.user.is_bot
        is_self_bot = m.user.id == bot_id
        approved = await is_approved(chat_id, m.user.id)
        name = m.user.full_name or str(m.user.id)
        username = f"@{m.user.username}" if m.user.username else ""
        display_name = name + (f" ({username})" if username else "")
        if is_self_bot:
            display_name = f"🤖 {display_name} (Self)"
        elif is_bot:
            display_name = f"🤖 {display_name}"
        admin_list.append({
            "user_id": m.user.id, "name": display_name, "full_name": name,
            "username": username, "approved": approved, "is_bot": is_bot,
            "is_self_bot": is_self_bot, "status": m.status,
            "custom_title": m.custom_title or ""
        })

    seen_ids = {a["user_id"] for a in admin_list}
    for row in await list_all_approved(chat_id):
        if row["user_id"] in seen_ids:
            continue
        name = row["name"] or str(row["user_id"])
        username = f"@{row['username']}" if row["username"] else ""
        display = f"{name} ({username})" if username else name
        display = f"🔧 {display} (manually approved)"
        admin_list.append({
            "user_id": row["user_id"], "name": display, "full_name": name,
            "username": username, "approved": True, "is_bot": row["is_bot"],
            "is_self_bot": False, "status": "manual",
            "custom_title": row["signature"] or "",
        })

    admin_list.sort(key=lambda x: (
        0 if x['status'] == ChatMemberStatus.OWNER else 1,
        0 if x['is_self_bot'] else 1,
        x['full_name'].lower()
    ))

    ITEMS_PER_PAGE = 10
    total_pages = (len(admin_list) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    if page >= total_pages:
        page = 0

    start_idx = page * ITEMS_PER_PAGE
    page_admins = admin_list[start_idx:start_idx + ITEMS_PER_PAGE]

    rows = []
    for admin in page_admins:
        label = f"{'✅' if admin['approved'] else '⬜'} {admin['name']}"
        if admin['status'] == ChatMemberStatus.OWNER:
            label = f"👑 {label}"
        if admin['custom_title']:
            label = f"{label} [{admin['custom_title']}]"
        if admin['is_self_bot']:
            rows.append([InlineKeyboardButton(label, callback_data="noop")])
        else:
            rows.append([InlineKeyboardButton(label, callback_data=f"at:{chat_id}:{admin['user_id']}")])

    nav = []
    if total_pages > 1:
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"ap:{chat_id}:{page-1}"))
        nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"ap:{chat_id}:{page+1}"))
        rows.append(nav)

    rows.append([InlineKeyboardButton("➕ Approve Bot/Admin by Username", callback_data=f"aub:{chat_id}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"menu:{chat_id}")])

    bot_count = sum(1 for a in admin_list if a['is_bot'])
    human_count = len(admin_list) - bot_count

    text = (
        "*All Admins (Including Bots)*\n\n"
        f"👤 Humans: {human_count} | 🤖 Bots: {bot_count}\n\n"
        "Tap an admin to toggle approval."
    )
    return text, InlineKeyboardMarkup(rows)


def timer_menu_markup(chat_id: int) -> InlineKeyboardMarkup:
    rows = []
    half = len(TIMER_PRESETS) // 2 + len(TIMER_PRESETS) % 2
    for i in range(half):
        row = []
        if i < len(TIMER_PRESETS):
            label, secs = TIMER_PRESETS[i]
            row.append(InlineKeyboardButton(label, callback_data=f"ts:{chat_id}:{secs}"))
        if i + half < len(TIMER_PRESETS):
            label, secs = TIMER_PRESETS[i + half]
            row.append(InlineKeyboardButton(label, callback_data=f"ts:{chat_id}:{secs}"))
        if row:
            rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Custom", callback_data=f"tc:{chat_id}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"menu:{chat_id}")])
    return InlineKeyboardMarkup(rows)


async def keywords_menu_markup(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    kws = await list_keywords(chat_id)
    rows = []
    for i, kw in enumerate(kws):
        rows.append([InlineKeyboardButton(f"❌ {kw}", callback_data=f"kd:{chat_id}:{i}")])
    rows.append([InlineKeyboardButton("➕ Add Keyword(s)", callback_data=f"ka:{chat_id}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"menu:{chat_id}")])
    text = "*🚫 Blacklist*\n\nMultiple keywords: one per line.\n\n"
    text += ("Current: " + ", ".join(kws)) if kws else "_No blacklist keywords yet._"
    return text, InlineKeyboardMarkup(rows)


async def whitelist_menu_markup(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    kws = await list_whitelist(chat_id)
    rows = []
    for i, kw in enumerate(kws):
        rows.append([InlineKeyboardButton(f"❌ {kw}", callback_data=f"wd:{chat_id}:{i}")])
    rows.append([InlineKeyboardButton("➕ Add Keyword(s)", callback_data=f"wa:{chat_id}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"menu:{chat_id}")])
    text = "*✅ Whitelist*\n\nMultiple keywords: one per line.\n\n"
    text += ("Current: " + ", ".join(kws)) if kws else "_No whitelist keywords yet._"
    return text, InlineKeyboardMarkup(rows)


async def build_chat_picker(bot, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    known = await list_known_chats()
    removed = await list_removed_chats()
    removed_ids = [r[0] for r in removed]
    rows = []
    for chat_id, title, ctype in known:
        if chat_id in removed_ids:
            continue
        if await user_is_chat_admin(bot, chat_id, user_id):
            icon = "📢" if ctype == ChatType.CHANNEL else "👥"
            managing_started = await is_managing_started(chat_id)
            status = "✅" if managing_started else "⏳"
            rows.append([InlineKeyboardButton(f"{icon} {status} {title or chat_id}", callback_data=f"sel:{chat_id}")])
    if removed:
        rows.append([InlineKeyboardButton("—" * 20, callback_data="noop")])
        rows.append([InlineKeyboardButton("🗑 Removed Chats", callback_data="removed_list")])
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="chats")])
    if rows[:-1]:
        text = "*Your Chats*\n\nPick a group or channel to manage:"
    else:
        text = "*Your Chats*\n\nNo manageable chats found yet."
    return text, InlineKeyboardMarkup(rows)


async def removed_chats_markup() -> tuple[str, InlineKeyboardMarkup]:
    removed = await list_removed_chats()
    if not removed:
        return "*Removed Chats*\n\nNo chats have been removed.", InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back", callback_data="chats")]])
    rows = []
    for chat_id, reason, removed_at in removed:
        time_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(removed_at))
        rows.append([InlineKeyboardButton(f"🔄 Restore {chat_id}", callback_data=f"restore:{chat_id}")])
        rows.append([InlineKeyboardButton(f"  📝 {reason[:30]} ({time_str})", callback_data="noop")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="chats")])
    return "*🗑 Removed Chats*\n\nTap 'Restore' to add a chat back.", InlineKeyboardMarkup(rows)


# --------------------------------------------------------------------------
# /start
# --------------------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info(f"🎯 /start called by user {update.effective_user.id if update.effective_user else 'unknown'}")
    user = update.effective_user
    chat = update.effective_chat
    if user is None:
        return

    joined = await user_joined_force_channel(context.bot, user.id)
    if not joined:
        await update.effective_message.reply_text(
            "🔒 Please join our channel first.",
            reply_markup=force_join_keyboard(),
        )
        return

    if chat.type == ChatType.PRIVATE:
        text = (
            "👋 *Admin Auto-Delete Bot*\n\n"
            "I remove posts from admins not on your approved list.\n\n"
            "Add me to a group/channel and make me admin."
        )
        picker_text, picker_markup = await build_chat_picker(context.bot, user.id)
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{context.bot.username}?startgroup=true")],
                [InlineKeyboardButton("📢 Add to Channel", url=f"https://t.me/{context.bot.username}?startchannel=true")],
            ])
        )
        await update.effective_message.reply_text(
            picker_text, parse_mode=ParseMode.MARKDOWN, reply_markup=picker_markup
        )
        return

    try:
        await update.effective_message.delete()
    except TelegramError:
        pass


# --------------------------------------------------------------------------
# Track chats the bot is admin in
# --------------------------------------------------------------------------

async def my_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    chat = result.chat
    log.info(f"👤 my_chat_member: chat={chat.id} ({chat.type}) new_status={result.new_chat_member.status}")
    if chat.type not in MANAGEABLE_TYPES:
        return
    new_status = result.new_chat_member.status
    is_admin_now = new_status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    if is_admin_now and await is_chat_removed(chat.id):
        await restore_chat(chat.id)
    await mark_bot_admin(chat.id, chat.title, chat.type, is_admin_now)
    if is_admin_now:
        await init_default_keywords(chat.id)
        try:
            admins = await context.bot.get_chat_administrators(chat.id)
            await store_all_admins(chat.id, admins)
        except Exception as e:
            log.error(f"Failed to store admins: {e}")


# --------------------------------------------------------------------------
# Callback router
# --------------------------------------------------------------------------

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    reply_chat = update.effective_chat
    user = update.effective_user

    log.info(f"🎯 callback: {data} from user {user.id if user else 'unknown'}")

    if data == "check_join":
        if user and await user_joined_force_channel(context.bot, user.id):
            await query.answer("✅ Verified!")
            await query.edit_message_text("✅ You're verified. Send /start again.")
        else:
            await query.answer("You haven't joined yet.", show_alert=True)
        return

    if user is None:
        await query.answer()
        return

    if data == "noop":
        await query.answer()
        return

    if not await user_joined_force_channel(context.bot, user.id):
        await query.answer("🔒 Join our channel first.", show_alert=True)
        try:
            await query.edit_message_text("🔒 Please join our channel first.", reply_markup=force_join_keyboard())
        except TelegramError:
            pass
        return

    if data == "chats":
        await query.answer()
        text, markup = await build_chat_picker(context.bot, user.id)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
        return

    if data == "removed_list":
        await query.answer()
        text, markup = await removed_chats_markup()
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
        return

    parts = data.split(":")
    action = parts[0]
    in_dm = reply_chat.type == ChatType.PRIVATE

    if action == "sel":
        target_chat_id = int(parts[1])
        if not await user_is_chat_admin(context.bot, target_chat_id, user.id):
            await query.answer("You're not an admin there.", show_alert=True)
            return
        await query.answer()
        await ensure_chat(target_chat_id)
        try:
            info = await context.bot.get_chat(target_chat_id)
            title = info.title or str(target_chat_id)
            chat_type = info.type
        except TelegramError:
            title = str(target_chat_id)
            chat_type = "Unknown"
        managing_started = await is_managing_started(target_chat_id)
        status_text = "✅ Active" if managing_started else "⏳ Setup Required"
        await query.edit_message_text(
            f"⚙️ *Managing:* {title}\n📋 Type: {chat_type}\n📊 Status: {status_text}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await main_menu_markup(target_chat_id, in_dm=True, user_id=user.id),
        )
        return

    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await query.answer()
        return
    target_chat_id = int(parts[1])

    if not await user_is_chat_admin(context.bot, target_chat_id, user.id):
        await query.answer("Only admins can do this.", show_alert=True)
        return

    await query.answer()

    if action == "refresh_admins":
        try:
            admins = await context.bot.get_chat_administrators(target_chat_id)
            await store_all_admins(target_chat_id, admins)
            await query.edit_message_text(
                f"✅ Admins refreshed! Found {len(admins)} admins.",
                reply_markup=await main_menu_markup(target_chat_id, in_dm, user.id))
        except Exception as e:
            await query.answer(f"Error: {str(e)[:50]}", show_alert=True)
        return

    if action == "start_mgmt":
        await start_managing(target_chat_id)
        try:
            admins = await context.bot.get_chat_administrators(target_chat_id)
            await store_all_admins(target_chat_id, admins)
        except Exception:
            pass
        await query.edit_message_text(
            "✅ *Managing Started!*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await main_menu_markup(target_chat_id, in_dm, user.id))
        return

    if action == "menu":
        await query.edit_message_text("⚙️ *Settings*", parse_mode=ParseMode.MARKDOWN,
                                       reply_markup=await main_menu_markup(target_chat_id, in_dm, user.id))

    elif action == "adm":
        page = int(parts[2]) if len(parts) > 2 else 0
        text, markup = await admins_menu_markup(context.bot, target_chat_id, page)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    elif action == "ap":
        page = int(parts[2])
        text, markup = await admins_menu_markup(context.bot, target_chat_id, page)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    elif action == "at":
        target_user_id = int(parts[2])
        bot_user = await context.bot.get_me()
        if target_user_id == bot_user.id:
            await query.answer("Cannot toggle the bot itself!", show_alert=True)
            return
        try:
            member = await context.bot.get_chat_member(target_chat_id, target_user_id)
            name = member.user.full_name or str(member.user.id)
            username = member.user.username
            custom_title = member.custom_title
            signature = custom_title or name
            is_bot = member.user.is_bot
        except TelegramError:
            name, username, signature, is_bot = str(target_user_id), None, None, False
        await toggle_approved(target_chat_id, target_user_id, name, username, signature, is_bot)
        text, markup = await admins_menu_markup(context.bot, target_chat_id, 0)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    elif action == "tmr":
        await query.edit_message_text(
            "⏱ *Deletion Timer*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=timer_menu_markup(target_chat_id))

    elif action == "ts":
        seconds = int(parts[2])
        await set_delete_delay(target_chat_id, seconds)
        await query.edit_message_text(
            f"✅ Timer set to *{fmt_delay(seconds)}*.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await main_menu_markup(target_chat_id, in_dm, user.id))

    elif action == "tc":
        PENDING_INPUT[(reply_chat.id, user.id)] = ("custom_timer", target_chat_id)
        await query.edit_message_text(
            "✏️ Send the delay in *minutes*.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Cancel", callback_data=f"tmr:{target_chat_id}")]]))

    elif action == "kw":
        text, markup = await keywords_menu_markup(target_chat_id)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    elif action == "ka":
        PENDING_INPUT[(reply_chat.id, user.id)] = ("add_keyword", target_chat_id)
        await query.edit_message_text(
            "✏️ Send keyword(s) to blacklist. One per line for multiple.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Cancel", callback_data=f"kw:{target_chat_id}")]]))

    elif action == "kd":
        index = int(parts[2])
        await remove_keyword_by_index(target_chat_id, index)
        text, markup = await keywords_menu_markup(target_chat_id)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    elif action == "wl":
        text, markup = await whitelist_menu_markup(target_chat_id)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    elif action == "wa":
        PENDING_INPUT[(reply_chat.id, user.id)] = ("add_whitelist", target_chat_id)
        await query.edit_message_text(
            "✏️ Send keyword(s) to whitelist. One per line for multiple.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Cancel", callback_data=f"wl:{target_chat_id}")]]))

    elif action == "wd":
        index = int(parts[2])
        await remove_whitelist_keyword_by_index(target_chat_id, index)
        text, markup = await whitelist_menu_markup(target_chat_id)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    elif action == "aub":
        PENDING_INPUT[(reply_chat.id, user.id)] = ("approve_bot_username", target_chat_id)
        await query.edit_message_text(
            "✏️ Send username(s) of bot/admin to approve. One per line for multiple.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Cancel", callback_data=f"adm:{target_chat_id}")]]))

    elif action == "tf":
        await toggle_force_join(target_chat_id)
        await query.edit_message_text("⚙️ *Settings*", parse_mode=ParseMode.MARKDOWN,
                                       reply_markup=await main_menu_markup(target_chat_id, in_dm, user.id))

    elif action == "te":
        await toggle_enabled(target_chat_id)
        await query.edit_message_text("⚙️ *Settings*", parse_mode=ParseMode.MARKDOWN,
                                       reply_markup=await main_menu_markup(target_chat_id, in_dm, user.id))

    elif action == "tn":
        await toggle_notify_subscription(target_chat_id, user.id)
        await query.edit_message_text("⚙️ *Settings*", parse_mode=ParseMode.MARKDOWN,
                                       reply_markup=await main_menu_markup(target_chat_id, in_dm, user.id))

    elif action == "rm":
        await query.edit_message_text(
            "⚠️ *Remove Chat?*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Yes", callback_data=f"rm_confirm:{target_chat_id}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"menu:{target_chat_id}")]
            ]))

    elif action == "rm_confirm":
        await remove_chat_from_management(target_chat_id, "Removed by user")
        await query.edit_message_text(
            "✅ Chat removed.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back", callback_data="chats")]]))

    elif action == "restore":
        chat_id_to_restore = target_chat_id
        await restore_chat(chat_id_to_restore)
        try:
            bot_member = await context.bot.get_chat_member(chat_id_to_restore, context.bot.id)
            if bot_member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                await mark_bot_admin(chat_id_to_restore, None, None, True)
                await init_default_keywords(chat_id_to_restore)
                await query.edit_message_text(
                    f"✅ Chat restored!",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🔙 Back", callback_data="chats")]]))
            else:
                await query.edit_message_text(
                    "⚠️ Chat restored but bot is no longer admin there.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🔙 Back", callback_data="chats")]]))
        except TelegramError:
            await query.edit_message_text(
                "⚠️ Chat restored, couldn't verify admin status.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Back", callback_data="chats")]]))

    elif action == "close":
        if in_dm:
            text, markup = await build_chat_picker(context.bot, user.id)
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
        else:
            await query.delete_message()
        PENDING_INPUT.pop((reply_chat.id, user.id), None)


# --------------------------------------------------------------------------
# Free-text handler
# --------------------------------------------------------------------------

async def text_and_moderation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if message is None:
        return

    log_message_debug("📨 INCOMING MESSAGE", message)

    key = (chat.id, user.id) if user else None

    if key and key in PENDING_INPUT:
        action, target_chat_id = PENDING_INPUT.pop(key)
        raw_text = (message.text or "").strip()

        if action == "custom_timer":
            if raw_text.isdigit() and int(raw_text) >= 0:
                seconds = int(raw_text) * 60
                await set_delete_delay(target_chat_id, seconds)
                await message.reply_text(f"✅ Deletion timer set to {fmt_delay(seconds)}.")
            else:
                await message.reply_text("That didn't look like a number of minutes.")
            if chat.type != ChatType.PRIVATE:
                try:
                    await message.delete()
                except TelegramError:
                    pass
            return

        if action == "add_keyword":
            lines = [ln.strip() for ln in raw_text.replace(",", "\n").split("\n")]
            keywords = [ln.lower() for ln in lines if ln]
            if keywords:
                added = 0
                for kw in keywords:
                    await add_keyword(target_chat_id, kw)
                    added += 1
                if added == 1:
                    await message.reply_text(f"✅ Blacklist keyword added: {keywords[0]}")
                else:
                    preview = "\n".join(f"• {k}" for k in keywords[:20])
                    await message.reply_text(
                        f"✅ Added *{added}* blacklist keywords:\n\n{preview}",
                        parse_mode=ParseMode.MARKDOWN)
            else:
                await message.reply_text("No valid keywords found.")
            if chat.type != ChatType.PRIVATE:
                try:
                    await message.delete()
                except TelegramError:
                    pass
            return

        if action == "add_whitelist":
            lines = [ln.strip() for ln in raw_text.replace(",", "\n").split("\n")]
            keywords = [ln.lower() for ln in lines if ln]
            if keywords:
                added = 0
                for kw in keywords:
                    await add_whitelist_keyword(target_chat_id, kw)
                    added += 1
                if added == 1:
                    await message.reply_text(f"✅ Whitelist keyword added: {keywords[0]}")
                else:
                    preview = "\n".join(f"• {k}" for k in keywords[:20])
                    await message.reply_text(
                        f"✅ Added *{added}* whitelist keywords:\n\n{preview}",
                        parse_mode=ParseMode.MARKDOWN)
            else:
                await message.reply_text("No valid keywords found.")
            if chat.type != ChatType.PRIVATE:
                try:
                    await message.delete()
                except TelegramError:
                    pass
            return

        if action == "approve_bot_username":
            lines = [ln.strip() for ln in raw_text.replace(",", "\n").split("\n")]
            usernames = [ln.lstrip("@").strip() for ln in lines if ln]
            if not usernames:
                await message.reply_text("Please send at least one valid username.")
                return

            approved = []
            failed = []

            for username in usernames:
                try:
                    resolved = await context.bot.get_chat(f"@{username}")
                except TelegramError:
                    failed.append(f"❌ @{username} — not found")
                    continue

                try:
                    member = await context.bot.get_chat_member(target_chat_id, resolved.id)
                except TelegramError:
                    failed.append(f"❌ @{username} — not a member")
                    continue

                if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                    failed.append(f"⚠️ @{username} — not an admin")
                    continue

                display_name = member.user.full_name or username
                custom_title = member.custom_title
                signature = custom_title or display_name

                await approve_admin(
                    target_chat_id, resolved.id, display_name,
                    member.user.username, signature, member.user.is_bot)

                kind = "bot" if member.user.is_bot else "admin"
                sig_info = f" (sig: '{signature}')" if custom_title else ""
                approved.append(f"✅ @{username} ({kind}, ID: {resolved.id}){sig_info}")

                await asyncio.sleep(0.3)

            parts_out = []
            if approved:
                parts_out.append(f"*Approved {len(approved)}:*\n" + "\n".join(approved))
            if failed:
                parts_out.append(f"*Failed {len(failed)}:*\n" + "\n".join(failed))

            if parts_out:
                await message.reply_text("\n\n".join(parts_out), parse_mode=ParseMode.MARKDOWN)
            else:
                await message.reply_text("Nothing was approved.")
            return

    if chat.type == ChatType.PRIVATE:
        return
    await moderate_message(update, context)


# --------------------------------------------------------------------------
# Moderate message — WITH FORWARD FIX
# --------------------------------------------------------------------------

async def moderate_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat.type not in MANAGEABLE_TYPES:
        return

    if await is_chat_removed(chat.id):
        log.debug(f"Chat {chat.id} removed - skipping")
        return

    if not await is_managing_started(chat.id):
        log.debug(f"Chat {chat.id} not managing - skipping")
        return

    settings = await get_settings(chat.id)
    if not settings["enabled"]:
        log.debug(f"Chat {chat.id} disabled - skipping")
        return

    text = message.text or message.caption or ""

    bot_user = await context.bot.get_me()
    if message.from_user and message.from_user.id == bot_user.id:
        return

    # Forward info
    fwd = get_forward_origin_info(message)

    is_admin = False
    is_approved_admin = False
    signature = None
    sender_id = None
    sender_username = None
    sender_name = None

    if message.sender_chat and message.sender_chat.id == chat.id:
        # Channel post
        signature = message.author_signature
        sender_name = signature or "Anonymous"
        sender_id = message.sender_chat.id

        if signature:
            is_admin = True
            is_approved_admin = await is_signature_approved(chat.id, signature)
            if not is_approved_admin:
                is_approved_admin, method = await is_approved_multi(
                    chat.id, None, None, signature, signature)
                if is_approved_admin:
                    log.info(f"✅ Channel admin '{signature}' approved via {method}")
        else:
            log.info("Channel post with no signature - leaving alone")
            return
    elif message.from_user:
        sender_id = message.from_user.id
        sender_username = message.from_user.username
        sender_name = message.from_user.full_name

        is_admin = await user_is_chat_admin(context.bot, chat.id, sender_id)

        if is_admin:
            is_approved_admin = await is_approved(chat.id, sender_id)
            if not is_approved_admin:
                # Check by forward origin too (if forwarded from an approved admin)
                if fwd["is_forward"] and fwd["original_sender_id"]:
                    is_approved_admin = await is_approved(chat.id, fwd["original_sender_id"])
                    if is_approved_admin:
                        log.info(f"✅ Forward from approved admin {fwd['original_sender_id']}")
                if not is_approved_admin:
                    is_approved_admin, method = await is_approved_multi(
                        chat.id, sender_id, sender_username, sender_name, None)
                    if is_approved_admin:
                        log.info(f"✅ Admin {sender_id} approved via {method}")
                        await approve_admin(
                            chat.id, sender_id, sender_name or str(sender_id),
                            sender_username, sender_name, message.from_user.is_bot)

    log_message_debug(
        "📊 MODERATION CHECK",
        message,
        extra={
            "is_admin": is_admin,
            "is_approved_admin": is_approved_admin,
            "signature": signature,
            "sender_id": sender_id,
            "settings": settings,
        }
    )

    if not is_admin:
        log.info(f"⏭️ Not an admin - skipping moderation")
        return

    if is_approved_admin:
        log.info(f"✅ Admin is approved - NOT deleting")
        return

    parts_label = []
    if sender_name:
        parts_label.append(sender_name)
    if sender_username:
        parts_label.append(f"@{sender_username}")
    if sender_id:
        parts_label.append(f"ID: {sender_id}")
    sender_label = " / ".join(parts_label)

    # Whitelist
    if text:
        lowered = text.lower()
        for wl in await list_whitelist(chat.id):
            if wl in lowered:
                log.info(f"📝 Whitelist match - keeping message")
                return

    # Blacklist
    if text:
        lowered = text.lower()
        for kw in await list_keywords(chat.id):
            if kw in lowered:
                try:
                    await message.delete()
                    log.info(f"🗑️ Deleted keyword '{kw}'")
                    await notify_deletion(context, chat.id, chat.title or str(chat.id),
                                          sender_label, f"blacklisted keyword: {kw}", text)
                    return
                except TelegramError as e:
                    log.warning(f"Delete failed: {e}")
                    return

    # Non-approved admin — delete
    delay = settings["delete_delay"]
    if delay <= 0:
        try:
            await message.delete()
            log.info(f"🗑️ Deleted immediately")
            await notify_deletion(context, chat.id, chat.title or str(chat.id),
                                  sender_label, "non-approved admin", text)
        except TelegramError as e:
            log.warning(f"Delete failed: {e}")
    else:
        context.job_queue.run_once(
            delete_job, when=delay,
            data={
                "chat_id": chat.id,
                "chat_title": chat.title or str(chat.id),
                "message_id": message.message_id,
                "sender_id": sender_id,
                "sender_username": sender_username,
                "sender_name": sender_name,
                "signature": signature,
                "snippet": text,
            })
        log.info(f"⏰ Scheduled deletion (delay={delay}s)")


async def delete_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id = data["chat_id"]

    approved = False
    if data.get("signature"):
        approved = await is_signature_approved(chat_id, data["signature"])
        if not approved:
            approved, _ = await is_approved_multi(chat_id, None, None, data["signature"], data["signature"])
    else:
        approved, _ = await is_approved_multi(
            chat_id, data.get("sender_id"), data.get("sender_username"),
            data.get("sender_name"), None)

    if approved:
        log.info(f"⏭️ Skipped - approved during delay")
        return

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=data["message_id"])
        log.info(f"✅ Scheduled deletion executed")
        parts = []
        if data.get("sender_name"): parts.append(data["sender_name"])
        if data.get("sender_username"): parts.append(f"@{data['sender_username']}")
        if data.get("signature"): parts.append(f"Sig: {data['signature']}")
        if data.get("sender_id"): parts.append(f"ID: {data['sender_id']}")
        await notify_deletion(context, chat_id, data.get("chat_title", str(chat_id)),
                              " / ".join(parts), "non-approved admin", data.get("snippet", ""))
    except TelegramError as e:
        log.info(f"⏭️ Delete skipped ({e})")


async def notify_deletion(context, chat_id, chat_title, sender_label, reason, snippet):
    subscribers = await list_notify_subscribers(chat_id)
    if not subscribers:
        return
    snippet = (snippet or "").strip()[:200]
    text = (
        f"🗑 *Message deleted*\n\n"
        f"*Chat:* {chat_title}\n"
        f"*Sender:* {sender_label}\n"
        f"*Reason:* {reason}"
    )
    if snippet:
        text += f"\n*Content:* {snippet}"
    for user_id in subscribers:
        try:
            await asyncio.wait_for(
                context.bot.send_message(user_id, text, parse_mode=ParseMode.MARKDOWN),
                timeout=10)
        except Exception as e:
            log.debug(f"DM failed to {user_id}: {e}")


# --------------------------------------------------------------------------
# Error handler — CRITICAL for auto-recovery
# --------------------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, Conflict):
        log.error(f"💥 CONFLICT: Another instance is polling! Force-restarting...")
        os._exit(1)  # Force process restart — Render will spin up a fresh dyno
    if isinstance(err, (NetworkError, TimedOut)):
        log.warning(f"⚠️ Network error (will retry): {err}")
        return
    log.error(f"❌ Exception handling update: {err}", exc_info=err)


# --------------------------------------------------------------------------
# Bot runtime
# --------------------------------------------------------------------------

async def run_bot():
    await init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .get_updates_read_timeout(40)
        .get_updates_connect_timeout(15)
        .build()
    )

    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(ChatMemberHandler(my_chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, text_and_moderation_handler)
    )
    application.add_error_handler(error_handler)

    async with application:
        await application.start()
        await application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            poll_interval=1.0,
            timeout=30,
        )
        log.info("✅ Bot is up and polling.")

        # Watchdog — ACTIVE Telegram ping
        async def watchdog():
            failures = 0
            while True:
                await asyncio.sleep(WATCHDOG_INTERVAL)
                try:
                    me = await asyncio.wait_for(application.bot.get_me(), timeout=15)
                    if me:
                        failures = 0
                        log.debug("✅ Watchdog: Telegram reachable")
                except (TimedOut, NetworkError) as e:
                    failures += 1
                    log.warning(f"⚠️ Watchdog failure #{failures}: {e}")
                    if failures >= WATCHDOG_MAX_FAILURES:
                        log.error("💀 Watchdog: polling dead — forcing exit")
                        os._exit(1)
                except Exception as e:
                    log.error(f"Watchdog error: {e}")
                    failures += 1
                    if failures >= WATCHDOG_MAX_FAILURES:
                        os._exit(1)

        watchdog_task = asyncio.create_task(watchdog())

        try:
            await asyncio.Event().wait()
        finally:
            watchdog_task.cancel()
            try:
                await application.updater.stop()
            except Exception:
                pass
            try:
                await application.stop()
            except Exception:
                pass
            if db:
                try:
                    await db.close()
                except Exception:
                    pass


async def run_bot_with_recovery():
    max_retries = 10
    retry_delay = 10
    for attempt in range(max_retries):
        try:
            log.info(f"🚀 Starting bot (attempt {attempt + 1}/{max_retries})...")
            await run_bot()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error(f"❌ Bot crashed: {type(e).__name__}: {e}", exc_info=True)
            if attempt < max_retries - 1:
                log.info(f"⏳ Retry in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
            else:
                raise


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------

def main():
    log.info("=" * 60)
    log.info("  Admin Auto-Delete Bot — Starting")
    log.info("=" * 60)

    if KEEP_ALIVE_ENABLED:
        threading.Thread(target=run_health_server, daemon=True).start()
        log.info(f"✅ Health server thread started on port {PORT}")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_bot_with_recovery())
    except KeyboardInterrupt:
        log.info("Bot stopped by user")
    except Exception as e:
        log.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()