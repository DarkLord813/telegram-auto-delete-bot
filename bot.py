import asyncio
import logging
import os
import time
import inspect
from typing import List, Dict, Any

import aiosqlite
from aiohttp import web
from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType, ChatMemberStatus, ParseMode
from telegram.error import TelegramError
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
# Config (all from environment)
# --------------------------------------------------------------------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL", "NCK_Dev")  # username, no @
FORCE_JOIN_CHANNEL_LINK = os.getenv("FORCE_JOIN_CHANNEL_LINK", "https://t.me/NCK_Dev")
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot.db")
PORT = int(os.getenv("PORT", "8080"))
KEEP_ALIVE_ENABLED = os.getenv("KEEP_ALIVE_ENABLED", "true").lower() == "true"
DEFAULT_DELETE_DELAY = int(os.getenv("DEFAULT_DELETE_DELAY", "300"))  # 5 minutes

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Set it in your environment or a .env file.")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("autodelete-bot")

TIMER_PRESETS = [
    ("Instant", 0),
    ("1 min", 60),
    ("5 min", 300),
    ("15 min", 900),
    ("1 hour", 3600),
]

# Default banned keywords (apply to non-approved admins only)
DEFAULT_BANNED_KEYWORDS = [
    "sex", "18+", "porn", "xxx", "nsfw", "adult", "nude", "naked",
    "fuck", "shit", "asshole", "bitch", "cunt", "dick", "pussy",
    "penis", "vagina", "boobs", "tits", "cum", "semen", "orgasm",
    "masturbate", "incest", "rape", "drugs", "cocaine", "heroin",
    "meth", "crack", "weed", "marijuana", "lsd", "mdma", "ecstasy"
]

MANAGEABLE_TYPES = (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL)

# in-memory state for "waiting on a free-text reply" flows (custom timer / add keyword)
# key: (reply_chat_id, user_id) -> (action, target_chat_id)
PENDING_INPUT: dict[tuple[int, int], tuple[str, int]] = {}

db: aiosqlite.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id      INTEGER PRIMARY KEY,
    title        TEXT,
    type         TEXT,
    delete_delay INTEGER NOT NULL DEFAULT 300,
    force_join   INTEGER NOT NULL DEFAULT 1,
    enabled      INTEGER NOT NULL DEFAULT 1,
    bot_is_admin INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS approved_admins (
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    name      TEXT,
    signature TEXT,
    is_bot    INTEGER DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS banned_keywords (
    chat_id INTEGER NOT NULL,
    keyword TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (chat_id, keyword)
);
"""


# --------------------------------------------------------------------------
# DB helpers
# --------------------------------------------------------------------------

async def init_db():
    global db
    db = await aiosqlite.connect(DATABASE_PATH)
    await db.executescript(SCHEMA)
    await db.commit()


async def ensure_chat(chat_id: int, title: str | None = None, chat_type: str | None = None):
    await db.execute(
        "INSERT OR IGNORE INTO chats (chat_id, title, type, delete_delay) VALUES (?, ?, ?, ?)",
        (chat_id, title, chat_type, DEFAULT_DELETE_DELAY),
    )
    if title:
        await db.execute("UPDATE chats SET title = ? WHERE chat_id = ?", (title, chat_id))
    if chat_type:
        await db.execute("UPDATE chats SET type = ? WHERE chat_id = ?", (chat_type, chat_id))
    await db.commit()


async def mark_bot_admin(chat_id: int, title: str | None, chat_type: str, is_admin: bool):
    await ensure_chat(chat_id, title, chat_type)
    await db.execute(
        "UPDATE chats SET bot_is_admin = ? WHERE chat_id = ?", (int(is_admin), chat_id)
    )
    await db.commit()


async def list_known_chats() -> list[tuple[int, str, str]]:
    """Chats where the bot currently believes it's an admin."""
    cur = await db.execute(
        "SELECT chat_id, title, type FROM chats WHERE bot_is_admin = 1 ORDER BY title"
    )
    rows = await cur.fetchall()
    await cur.close()
    return rows


async def get_settings(chat_id: int) -> dict:
    await ensure_chat(chat_id)
    cur = await db.execute(
        "SELECT delete_delay, force_join, enabled FROM chats WHERE chat_id = ?", (chat_id,)
    )
    row = await cur.fetchone()
    await cur.close()
    return {"delete_delay": row[0], "force_join": bool(row[1]), "enabled": bool(row[2])}


async def set_delete_delay(chat_id: int, seconds: int):
    await ensure_chat(chat_id)
    await db.execute("UPDATE chats SET delete_delay = ? WHERE chat_id = ?", (seconds, chat_id))
    await db.commit()


async def toggle_force_join(chat_id: int) -> bool:
    s = await get_settings(chat_id)
    new_val = not s["force_join"]
    await db.execute(
        "UPDATE chats SET force_join = ? WHERE chat_id = ?", (int(new_val), chat_id)
    )
    await db.commit()
    return new_val


async def toggle_enabled(chat_id: int) -> bool:
    s = await get_settings(chat_id)
    new_val = not s["enabled"]
    await db.execute("UPDATE chats SET enabled = ? WHERE chat_id = ?", (int(new_val), chat_id))
    await db.commit()
    return new_val


async def is_approved(chat_id: int, user_id: int) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM approved_admins WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
    )
    row = await cur.fetchone()
    await cur.close()
    return row is not None


async def toggle_approved(chat_id: int, user_id: int, name: str, signature: str | None, is_bot: bool = False):
    if await is_approved(chat_id, user_id):
        await db.execute(
            "DELETE FROM approved_admins WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        )
    else:
        await db.execute(
            "INSERT OR REPLACE INTO approved_admins (chat_id, user_id, name, signature, is_bot) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, user_id, name, signature, int(is_bot)),
        )
    await db.commit()


async def is_signature_approved(chat_id: int, signature: str | None) -> bool:
    if not signature:
        return False
    cur = await db.execute(
        "SELECT 1 FROM approved_admins WHERE chat_id = ? AND signature = ? COLLATE NOCASE",
        (chat_id, signature),
    )
    row = await cur.fetchone()
    await cur.close()
    return row is not None


async def list_keywords(chat_id: int) -> list[str]:
    cur = await db.execute(
        "SELECT keyword FROM banned_keywords WHERE chat_id = ? ORDER BY keyword", (chat_id,)
    )
    rows = await cur.fetchall()
    await cur.close()
    return [r[0] for r in rows]


async def add_keyword(chat_id: int, keyword: str):
    await ensure_chat(chat_id)
    keyword = keyword.strip().lower()[:40]
    if keyword:
        await db.execute(
            "INSERT OR IGNORE INTO banned_keywords (chat_id, keyword) VALUES (?, ?)",
            (chat_id, keyword),
        )
        await db.commit()


async def remove_keyword_by_index(chat_id: int, index: int):
    kws = await list_keywords(chat_id)
    if 0 <= index < len(kws):
        await db.execute(
            "DELETE FROM banned_keywords WHERE chat_id = ? AND keyword = ?",
            (chat_id, kws[index]),
        )
        await db.commit()


async def init_default_keywords(chat_id: int):
    """Initialize default banned keywords for a new chat."""
    existing = await list_keywords(chat_id)
    for kw in DEFAULT_BANNED_KEYWORDS:
        if kw not in existing:
            await add_keyword(chat_id, kw)


# --------------------------------------------------------------------------
# Function listing utility
# --------------------------------------------------------------------------

def list_all_functions() -> Dict[str, List[Dict[str, Any]]]:
    """
    Returns a categorized list of all functions in this module.
    Useful for documentation and debugging.
    """
    current_module = inspect.getmodule(inspect.currentframe())
    functions = {
        "Database Operations": [],
        "Telegram Helpers": [],
        "UI/Menu Builders": [],
        "Command Handlers": [],
        "Moderation": [],
        "Utility": [],
        "Entrypoint": [],
        "Web Server": [],
    }
    
    for name, obj in inspect.getmembers(current_module):
        if inspect.isfunction(obj) and obj.__module__ == current_module.__name__:
            func_info = {
                "name": name,
                "doc": inspect.getdoc(obj) or "No description",
                "signature": str(inspect.signature(obj)),
            }
            
            # Categorize functions
            if name.startswith(("init_db", "ensure_chat", "mark_bot_admin", "list_known_chats",
                               "get_settings", "set_delete_delay", "toggle_force_join",
                               "toggle_enabled", "is_approved", "toggle_approved",
                               "is_signature_approved", "list_keywords", "add_keyword",
                               "remove_keyword_by_index", "init_default_keywords")):
                functions["Database Operations"].append(func_info)
            elif name in ("user_is_chat_admin", "user_joined_force_channel", 
                         "force_join_keyboard", "fmt_delay"):
                functions["Telegram Helpers"].append(func_info)
            elif name in ("main_menu_markup", "admins_menu_markup", "timer_menu_markup",
                         "keywords_menu_markup", "build_chat_picker"):
                functions["UI/Menu Builders"].append(func_info)
            elif name in ("start_cmd", "callback_router", "text_and_moderation_handler"):
                functions["Command Handlers"].append(func_info)
            elif name in ("moderate_message", "delete_job"):
                functions["Moderation"].append(func_info)
            elif name in ("list_all_functions",):
                functions["Utility"].append(func_info)
            elif name in ("_keep_alive_root", "start_keep_alive_server"):
                functions["Web Server"].append(func_info)
            elif name == "main":
                functions["Entrypoint"].append(func_info)
    
    return functions


# --------------------------------------------------------------------------
# Telegram helpers
# --------------------------------------------------------------------------

async def user_is_chat_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except TelegramError:
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
        # Bot probably isn't admin in the force-join channel yet — fail open
        # so a misconfiguration doesn't lock everyone out, but log loudly.
        log.warning("Force-join check failed (%s) — allowing through", e)
        return True


def force_join_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 Join Channel", url=FORCE_JOIN_CHANNEL_LINK)],
            [InlineKeyboardButton("✅ I've Joined", callback_data="check_join")],
        ]
    )


def fmt_delay(seconds: int) -> str:
    if seconds == 0:
        return "Instant"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} hr"


# --------------------------------------------------------------------------
# Menu builders (every callback_data below carries the TARGET chat_id so
# these work identically whether triggered inside the chat itself or from
# a private-chat picker)
# --------------------------------------------------------------------------

async def main_menu_markup(chat_id: int, in_dm: bool) -> InlineKeyboardMarkup:
    s = await get_settings(chat_id)
    rows = [
        [InlineKeyboardButton("👮 Approved Admins", callback_data=f"adm:{chat_id}")],
        [InlineKeyboardButton(f"⏱ Deletion Timer ({fmt_delay(s['delete_delay'])})",
                               callback_data=f"tmr:{chat_id}")],
        [InlineKeyboardButton("🚫 Banned Keywords", callback_data=f"kw:{chat_id}")],
        [InlineKeyboardButton(
            f"{'🟢' if s['enabled'] else '🔴'} Auto-Delete: {'ON' if s['enabled'] else 'OFF'}",
            callback_data=f"te:{chat_id}")],
        [InlineKeyboardButton(
            f"{'🟢' if s['force_join'] else '🔴'} Force-Join: {'ON' if s['force_join'] else 'OFF'}",
            callback_data=f"tf:{chat_id}")],
    ]
    if in_dm:
        rows.append([InlineKeyboardButton("🔙 My Chats", callback_data="chats")])
    else:
        rows.append([InlineKeyboardButton("❌ Close", callback_data=f"close:{chat_id}")])
    return InlineKeyboardMarkup(rows)


async def admins_menu_markup(bot, chat_id: int, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except TelegramError:
        return "Couldn't fetch admin list — is the bot an admin here?", InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back", callback_data=f"menu:{chat_id}")]]
        )

    # Include all admins including bots
    admin_list = []
    for m in admins:
        is_bot = m.user.is_bot
        approved = await is_approved(chat_id, m.user.id)
        name = m.user.full_name or (f"@{m.user.username}" if m.user.username else str(m.user.id))
        if is_bot:
            name = f"🤖 {name}"
        admin_list.append({
            "user_id": m.user.id,
            "name": name,
            "approved": approved,
            "is_bot": is_bot,
            "status": m.status
        })

    # Pagination: show 10 per page
    ITEMS_PER_PAGE = 10
    total_pages = (len(admin_list) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    if page >= total_pages:
        page = 0
    
    start_idx = page * ITEMS_PER_PAGE
    end_idx = min(start_idx + ITEMS_PER_PAGE, len(admin_list))
    page_admins = admin_list[start_idx:end_idx]

    rows = []
    for admin in page_admins:
        label = f"{'✅' if admin['approved'] else '⬜'} {admin['name']}"
        if admin['status'] == ChatMemberStatus.OWNER:
            label = f"👑 {label}"
        rows.append([InlineKeyboardButton(
            label, 
            callback_data=f"at:{chat_id}:{admin['user_id']}"
        )])

    # Add pagination buttons if needed
    nav_buttons = []
    if total_pages > 1:
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"ap:{chat_id}:{page-1}"))
        nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"ap:{chat_id}:{page+1}"))
        rows.append(nav_buttons)

    rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"menu:{chat_id}")])
    
    text = (
        "*All Admins (Including Bots)*\n\n"
        "Tap an admin to toggle approval. ✅ Approved admins' messages/posts "
        "are never auto-deleted. Anyone left ⬜ un-approved gets their "
        "messages removed after the configured timer.\n\n"
        f"👑 Owner | 🤖 Bot | {len(admin_list)} total admins\n\n"
        "_Note: anonymous/channel posts are matched by signature (custom "
        "title). Give each admin a distinct custom title, and make sure "
        "\"Sign messages\" is on, or per-admin filtering can't work there._"
    )
    return text, InlineKeyboardMarkup(rows)


def timer_menu_markup(chat_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"ts:{chat_id}:{secs}")]
        for label, secs in TIMER_PRESETS
    ]
    rows.append([InlineKeyboardButton("✏️ Custom", callback_data=f"tc:{chat_id}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"menu:{chat_id}")])
    return InlineKeyboardMarkup(rows)


async def keywords_menu_markup(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    kws = await list_keywords(chat_id)
    rows = [
        [InlineKeyboardButton(f"❌ {kw}", callback_data=f"kd:{chat_id}:{i}")]
        for i, kw in enumerate(kws)
    ]
    rows.append([InlineKeyboardButton("➕ Add Keyword", callback_data=f"ka:{chat_id}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"menu:{chat_id}")])
    text = "*Banned Keywords*\n\nMessages containing any of these from non-approved admins are deleted instantly.\n\n"
    text += ("Current: " + ", ".join(kws)) if kws else "_No keywords banned yet._"
    return text, InlineKeyboardMarkup(rows)


async def build_chat_picker(bot, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    known = await list_known_chats()
    rows = []
    for chat_id, title, ctype in known:
        if await user_is_chat_admin(bot, chat_id, user_id):
            icon = "📢" if ctype == ChatType.CHANNEL else "👥"
            rows.append(
                [InlineKeyboardButton(f"{icon} {title or chat_id}", callback_data=f"sel:{chat_id}")]
            )
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="chats")])
    if rows[:-1]:
        text = "*Your Chats*\n\nPick a group or channel to manage:"
    else:
        text = (
            "*Your Chats*\n\n"
            "No manageable chats found yet. Add me to a group or channel and "
            "promote me to admin, then tap Refresh.\n\n"
            "_If you added me before, promote/demote me once so I re-register "
            "the chat._"
        )
    return text, InlineKeyboardMarkup(rows)


# --------------------------------------------------------------------------
# /start
# --------------------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if user is None:
        # Channel post with no attributable sender — nothing we can do.
        return

    joined = await user_joined_force_channel(context.bot, user.id)
    if not joined:
        await update.effective_message.reply_text(
            "🔒 Please join our channel first to use this bot.",
            reply_markup=force_join_keyboard(),
        )
        return

    if chat.type == ChatType.PRIVATE:
        text, markup_rows = (
            "👋 *Admin Auto-Delete Bot*\n\n"
            "I remove posts from group/channel admins who aren't on your "
            "approved list, after a timer you set (default 5 minutes). I "
            "can also auto-delete messages containing banned keywords, "
            "from anyone.\n\n"
            "Add me to a group or channel and promote me to admin (with "
            "*Delete Messages* permission). Manage everything from here.",
            None,
        )
        picker_text, picker_markup = await build_chat_picker(context.bot, user.id)
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(
                    "➕ Add me to a group/channel",
                    url=f"https://t.me/{context.bot.username}?startgroup=true",
                )]]
            ),
        )
        await update.effective_message.reply_text(
            picker_text, parse_mode=ParseMode.MARKDOWN, reply_markup=picker_markup
        )
        return

    # Groups, supergroups, and channels: no in-chat interaction at all.
    # Settings only ever open in a private DM with the bot — here we just
    # quietly clean up the stray /start so it doesn't clutter the chat.
    try:
        await update.effective_message.delete()
    except TelegramError:
        pass


# --------------------------------------------------------------------------
# Track chats the bot is admin in, so the DM picker can list them.
# --------------------------------------------------------------------------

async def my_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    chat = result.chat
    if chat.type not in MANAGEABLE_TYPES:
        return
    new_status = result.new_chat_member.status
    is_admin_now = new_status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    await mark_bot_admin(chat.id, chat.title, chat.type, is_admin_now)
    
    # Initialize default keywords when bot is first added
    if is_admin_now:
        await init_default_keywords(chat.id)


# --------------------------------------------------------------------------
# Callback query router (all inline-button navigation)
# --------------------------------------------------------------------------

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    reply_chat = update.effective_chat   # chat the button message lives in
    user = update.effective_user

    if data == "check_join":
        if user and await user_joined_force_channel(context.bot, user.id):
            await query.answer("✅ Verified, thanks!")
            await query.edit_message_text("✅ You're verified. Send /start again.")
        else:
            await query.answer("You haven't joined yet.", show_alert=True)
        return

    if user is None:
        await query.answer()
        return

    # --- mandatory join gate for absolutely everything else ---
    if not await user_joined_force_channel(context.bot, user.id):
        await query.answer("🔒 Join our channel first — see /start.", show_alert=True)
        try:
            await query.edit_message_text(
                "🔒 Please join our channel first to use this bot.",
                reply_markup=force_join_keyboard(),
            )
        except TelegramError:
            pass
        return

    if data == "chats":
        await query.answer()
        text, markup = await build_chat_picker(context.bot, user.id)
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
        except TelegramError:
            title = str(target_chat_id)
        await query.edit_message_text(
            f"⚙️ *Managing:* {title}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await main_menu_markup(target_chat_id, in_dm=True),
        )
        return

    # every remaining action carries the target chat_id as parts[1]
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await query.answer()
        return
    target_chat_id = int(parts[1])

    if not await user_is_chat_admin(context.bot, target_chat_id, user.id):
        await query.answer("Only admins of that chat can do this.", show_alert=True)
        return

    await query.answer()

    if action == "menu":
        await query.edit_message_text(
            "⚙️ *Auto-Delete Settings*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await main_menu_markup(target_chat_id, in_dm),
        )

    elif action == "adm":
        page = int(parts[2]) if len(parts) > 2 else 0
        text, markup = await admins_menu_markup(context.bot, target_chat_id, page)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    elif action == "ap":  # admin page navigation
        page = int(parts[2])
        text, markup = await admins_menu_markup(context.bot, target_chat_id, page)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    elif action == "at":  # admin toggle: at:<chat_id>:<user_id>
        target_user_id = int(parts[2])
        try:
            member = await context.bot.get_chat_member(target_chat_id, target_user_id)
            name = member.user.full_name or (f"@{member.user.username}" if member.user.username else str(member.user.id))
            signature = member.custom_title
            is_bot = member.user.is_bot
        except TelegramError:
            name, signature, is_bot = str(target_user_id), None, False
        await toggle_approved(target_chat_id, target_user_id, name, signature, is_bot)
        text, markup = await admins_menu_markup(context.bot, target_chat_id, 0)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    elif action == "tmr":
        await query.edit_message_text(
            "⏱ *Deletion Timer*\nHow long after posting should an "
            "un-approved admin's message be deleted?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=timer_menu_markup(target_chat_id),
        )

    elif action == "ts":  # timer set: ts:<chat_id>:<seconds>
        seconds = int(parts[2])
        await set_delete_delay(target_chat_id, seconds)
        await query.edit_message_text(
            f"✅ Deletion timer set to *{fmt_delay(seconds)}*.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await main_menu_markup(target_chat_id, in_dm),
        )

    elif action == "tc":  # timer custom prompt
        PENDING_INPUT[(reply_chat.id, user.id)] = ("custom_timer", target_chat_id)
        await query.edit_message_text(
            "✏️ Send the delay in *minutes* as a message here (e.g. `10`).",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Cancel", callback_data=f"tmr:{target_chat_id}")]]
            ),
        )

    elif action == "kw":
        text, markup = await keywords_menu_markup(target_chat_id)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    elif action == "ka":  # keyword add prompt
        PENDING_INPUT[(reply_chat.id, user.id)] = ("add_keyword", target_chat_id)
        await query.edit_message_text(
            "✏️ Send the keyword or phrase to ban, as a message here.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Cancel", callback_data=f"kw:{target_chat_id}")]]
            ),
        )

    elif action == "kd":  # keyword delete: kd:<chat_id>:<index>
        index = int(parts[2])
        await remove_keyword_by_index(target_chat_id, index)
        text, markup = await keywords_menu_markup(target_chat_id)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    elif action == "tf":  # toggle force-join
        await toggle_force_join(target_chat_id)
        await query.edit_message_text(
            "⚙️ *Auto-Delete Settings*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await main_menu_markup(target_chat_id, in_dm),
        )

    elif action == "te":  # toggle enabled
        await toggle_enabled(target_chat_id)
        await query.edit_message_text(
            "⚙️ *Auto-Delete Settings*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await main_menu_markup(target_chat_id, in_dm),
        )

    elif action == "close":
        if in_dm:
            text, markup = await build_chat_picker(context.bot, user.id)
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
        else:
            await query.delete_message()
        PENDING_INPUT.pop((reply_chat.id, user.id), None)


# --------------------------------------------------------------------------
# Free-text handler — only used for the two "type a value" flows above,
# and otherwise falls through to group/channel message moderation.
# --------------------------------------------------------------------------

async def text_and_moderation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if message is None:
        return

    key = (chat.id, user.id) if user else None

    # --- pending "type a value" flow from an admin menu (always in a
    # regular chat with a real user — DM or the group itself) ---
    if key and key in PENDING_INPUT:
        action, target_chat_id = PENDING_INPUT.pop(key)
        text = (message.text or "").strip()

        if action == "custom_timer":
            if text.isdigit() and int(text) >= 0:
                seconds = int(text) * 60
                await set_delete_delay(target_chat_id, seconds)
                await message.reply_text(f"✅ Deletion timer set to {fmt_delay(seconds)}.")
            else:
                await message.reply_text("That didn't look like a number of minutes. Try again from the menu.")
            if chat.type != ChatType.PRIVATE:
                try:
                    await message.delete()
                except TelegramError:
                    pass
            return

        if action == "add_keyword":
            if text:
                await add_keyword(target_chat_id, text)
                await message.reply_text(f"✅ Banned keyword added: {text}")
            if chat.type != ChatType.PRIVATE:
                try:
                    await message.delete()
                except TelegramError:
                    pass
            return

    # --- otherwise: normal group/channel message moderation ---
    if chat.type == ChatType.PRIVATE:
        return
    await moderate_message(update, context)


async def moderate_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat.type not in MANAGEABLE_TYPES:
        return

    settings = await get_settings(chat.id)
    if not settings["enabled"]:
        return

    text = message.text or message.caption or ""

    # 1) Check if sender is an admin (for keyword filtering - applies to non-approved admins only)
    is_admin = False
    is_approved_admin = False
    
    if message.sender_chat and message.sender_chat.id == chat.id:
        # Anonymous group admin or channel post
        signature = message.author_signature
        if signature:
            is_admin = True
            is_approved_admin = await is_signature_approved(chat.id, signature)
    elif message.from_user and not message.from_user.is_bot:
        if await user_is_chat_admin(context.bot, chat.id, message.from_user.id):
            is_admin = True
            is_approved_admin = await is_approved(chat.id, message.from_user.id)

    # Keywords: Only apply to non-approved admins
    if is_admin and not is_approved_admin and text:
        lowered = text.lower()
        for kw in await list_keywords(chat.id):
            if kw in lowered:
                try:
                    await message.delete()
                    log.info(f"Deleted keyword '{kw}' from non-approved admin in chat {chat.id}")
                    return
                except TelegramError as e:
                    log.warning("Couldn't delete keyword-flagged message: %s", e)
                    return

    # 2) admin-approval check (delete all messages from non-approved admins)
    if not is_admin:
        return
    
    if is_approved_admin:
        return

    delay = settings["delete_delay"]
    if delay <= 0:
        try:
            await message.delete()
            log.info(f"Deleted message from non-approved admin in chat {chat.id}")
        except TelegramError as e:
            log.warning("Couldn't delete message: %s", e)
    else:
        context.job_queue.run_once(
            delete_job, when=delay, data={"chat_id": chat.id, "message_id": message.message_id}
        )
        log.info(f"Scheduled deletion for non-approved admin message in chat {chat.id} (delay: {delay}s)")


async def delete_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    try:
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["message_id"])
        log.info(f"Scheduled deletion executed for chat {data['chat_id']}")
    except TelegramError as e:
        log.info("Scheduled delete skipped (%s)", e)


# --------------------------------------------------------------------------
# Keep-alive web server (uptime pingers / host health checks)
# --------------------------------------------------------------------------

_start_time = time.time()


async def _keep_alive_root(request):
    return web.json_response({"status": "ok", "uptime_seconds": int(time.time() - _start_time)})


async def start_keep_alive_server():
    app = web.Application()
    app.router.add_get("/", _keep_alive_root)
    app.router.add_get("/health", _keep_alive_root)
    app.router.add_get("/functions", list_functions_endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    log.info("Keep-alive server listening on :%s", PORT)
    return runner


async def list_functions_endpoint(request):
    """Web endpoint to list all functions."""
    return web.json_response(list_all_functions())


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------

async def main():
    await init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(ChatMemberHandler(my_chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER))
    # Any non-command message: pending-input capture, else group/channel moderation.
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, text_and_moderation_handler)
    )

    async with application:
        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        keep_alive_runner = None
        if KEEP_ALIVE_ENABLED:
            keep_alive_runner = await start_keep_alive_server()

        log.info("Bot is up.")
        try:
            await asyncio.Event().wait()  # run forever
        finally:
            if keep_alive_runner:
                await keep_alive_runner.cleanup()
            await application.updater.stop()
            await application.stop()
            await db.close()


if __name__ == "__main__":
    asyncio.run(main())