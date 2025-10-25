import os
import time
import logging
import sqlite3
import requests
import threading
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List
from telegram import Bot, Update, ChatMember, Message, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.error import TelegramError, BadRequest
from flask import Flask, jsonify
from threading import Thread

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== HEALTH CHECK SERVER ====================

app = Flask(__name__)

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': time.time()}), 200

@app.route('/')
def home():
    return jsonify({'service': 'Telegram Auto Delete Bot', 'status': 'running'})

def run_health_server():
    try:
        port = int(os.environ.get('PORT', 8080))
        logger.info(f"Starting health server on port {port}")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Health server error: {e}")
        time.sleep(5)
        run_health_server()

def start_health_check():
    def health_wrapper():
        while True:
            try:
                run_health_server()
            except Exception as e:
                logger.error(f"Health server crashed: {e}")
                time.sleep(10)
    
    t = Thread(target=health_wrapper, daemon=True)
    t.start()

# ==================== KEEP-ALIVE SERVICE ====================

class KeepAliveService:
    def __init__(self, health_url=None):
        self.health_url = health_url or f"http://localhost:{os.environ.get('PORT', 8080)}/health"
        self.is_running = False
        
    def start(self):
        self.is_running = True
        
        def ping_loop():
            while self.is_running:
                try:
                    response = requests.get(self.health_url, timeout=10)
                    if response.status_code == 200:
                        logger.info(f"Keep-alive ping: {datetime.now().strftime('%H:%M:%S')}")
                except Exception as e:
                    logger.warning(f"Keep-alive error: {e}")
                time.sleep(240)
        
        thread = threading.Thread(target=ping_loop, daemon=True)
        thread.start()
        logger.info("Keep-alive service started")

# ==================== AUTO DELETE BOT ====================

class AutoDeleteBot:
    def __init__(self, token: str):
        self.token = token
        
        # Simple Application initialization
        self.application = Application.builder().token(token).build()
        
        self.setup_database()
        
        # Store data
        self.channel_admins: Dict[str, List[int]] = {}
        self.bot_join_times: Dict[str, datetime] = {}
        self.delete_intervals: Dict[str, int] = {}
        
        # Setup handlers
        self.setup_handlers()
        
        self.bot = Bot(token=token)
        self.keep_alive = None
        
        # Statistics
        self.stats = {
            'messages_checked': 0,
            'messages_deleted': 0,
            'errors': 0,
            'start_time': datetime.now()
        }
        
        self.load_channel_data()
        logger.info("Auto Delete Bot initialized")

    def setup_handlers(self):
        """Setup all message handlers"""
        # Command handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("setup", self.setup_command))
        self.application.add_handler(CommandHandler("addadmin", self.add_admin_command))
        self.application.add_handler(CommandHandler("removeadmin", self.remove_admin_command))
        self.application.add_handler(CommandHandler("listadmins", self.list_admins_command))
        self.application.add_handler(CommandHandler("settings", self.settings_command))
        self.application.add_handler(CommandHandler("setinterval", self.set_interval_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        
        # Callback query handler
        self.application.add_handler(CallbackQueryHandler(self.handle_callback_query))
        
        # Message handler
        self.application.add_handler(MessageHandler(filters.ALL, self.handle_message))

    def setup_database(self):
        """Initialize SQLite database"""
        self.conn = sqlite3.connect('auto_delete_bot.db', check_same_thread=False)
        cursor = self.conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                channel_id TEXT PRIMARY KEY,
                channel_title TEXT,
                bot_added_date DATETIME,
                delete_interval INTEGER DEFAULT 300,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS allowed_admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT,
                user_id INTEGER,
                username TEXT,
                full_name TEXT,
                added_by INTEGER,
                added_date DATETIME
            )
        ''')
        
        self.conn.commit()

    def load_channel_data(self):
        """Load channel data from database"""
        cursor = self.conn.cursor()
        
        cursor.execute('SELECT channel_id, bot_added_date, delete_interval FROM channels WHERE is_active = 1')
        for channel_id, added_date, interval in cursor.fetchall():
            self.bot_join_times[channel_id] = datetime.fromisoformat(added_date)
            self.delete_intervals[channel_id] = interval
        
        cursor.execute('SELECT channel_id, user_id FROM allowed_admins')
        for channel_id, user_id in cursor.fetchall():
            if channel_id not in self.channel_admins:
                self.channel_admins[channel_id] = []
            self.channel_admins[channel_id].append(user_id)

    def start_keep_alive(self):
        """Start the keep-alive service"""
        try:
            render_url = os.environ.get('RENDER_EXTERNAL_URL')
            if render_url:
                health_url = f"{render_url}/health"
            else:
                health_url = f"http://localhost:{os.environ.get('PORT', 8080)}/health"
            
            self.keep_alive = KeepAliveService(health_url)
            self.keep_alive.start()
            return True
        except Exception as e:
            logger.error(f"Failed to start keep-alive: {e}")
            return False

    # ==================== KEYBOARD METHODS ====================

    def create_main_menu_keyboard(self):
        keyboard = [
            [InlineKeyboardButton("🛠️ Setup Bot", callback_data="setup_bot")],
            [InlineKeyboardButton("👥 Manage Admins", callback_data="manage_admins")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="show_settings")],
            [InlineKeyboardButton("📊 Statistics", callback_data="show_stats")],
        ]
        return InlineKeyboardMarkup(keyboard)

    def create_setup_keyboard(self):
        keyboard = [
            [InlineKeyboardButton("✅ Confirm Setup", callback_data="confirm_setup")],
            [InlineKeyboardButton("❌ Cancel", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    # ==================== COMMAND HANDLERS ====================

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        welcome_text = """
🤖 **Auto Delete Bot**

I automatically delete ALL messages except those from specified admins in channels.

Use the buttons below to get started!
        """
        await update.message.reply_text(welcome_text, parse_mode='Markdown', reply_markup=self.create_main_menu_keyboard())

    async def setup_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        setup_text = """
🛠️ **Bot Setup**

To setup auto-deletion:
1. Make me an Admin with Delete Messages permission
2. Click Confirm Setup below
3. Add allowed admins

I will only delete messages sent after I was added.
        """
        await update.message.reply_text(setup_text, parse_mode='Markdown', reply_markup=self.create_setup_keyboard())

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        callback_data = query.data
        
        if callback_data == "main_menu":
            await self.show_main_menu(query)
        elif callback_data == "setup_bot":
            await self.show_setup_menu(query)
        elif callback_data == "confirm_setup":
            await self.confirm_setup(query, context)
        elif callback_data == "show_stats":
            await self.show_stats(query)

    async def show_main_menu(self, query):
        welcome_text = "🤖 **Auto Delete Bot - Main Menu**"
        await query.edit_message_text(welcome_text, parse_mode='Markdown', reply_markup=self.create_main_menu_keyboard())

    async def show_setup_menu(self, query):
        setup_text = "🛠️ **Bot Setup** - Click Confirm Setup below"
        await query.edit_message_text(setup_text, parse_mode='Markdown', reply_markup=self.create_setup_keyboard())

    async def confirm_setup(self, query: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = query.message.chat
        
        if chat.type not in ['channel', 'group', 'supergroup']:
            await query.edit_message_text("❌ Please use this in a channel or group.")
            return

        try:
            bot_member = await chat.get_member(self.bot.id)
            if not bot_member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                await query.edit_message_text("❌ I need to be an admin with delete permissions.")
                return
        except TelegramError as e:
            await query.edit_message_text(f"❌ Error: {e}")
            return

        # Setup channel
        channel_id = str(chat.id)
        channel_title = chat.title or "Unknown"
        bot_added_date = datetime.now()

        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO channels 
            (channel_id, channel_title, bot_added_date, delete_interval, is_active)
            VALUES (?, ?, ?, ?, ?)
        ''', (channel_id, channel_title, bot_added_date, 300, True))
        
        # Add user as admin
        cursor.execute('''
            INSERT OR IGNORE INTO allowed_admins 
            (channel_id, user_id, username, full_name, added_by, added_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            channel_id, 
            query.from_user.id,
            query.from_user.username,
            query.from_user.full_name,
            query.from_user.id,
            datetime.now()
        ))
        
        self.conn.commit()

        # Update memory
        self.bot_join_times[channel_id] = bot_added_date
        self.delete_intervals[channel_id] = 300
        
        if channel_id not in self.channel_admins:
            self.channel_admins[channel_id] = []
        self.channel_admins[channel_id].append(query.from_user.id)

        success_text = f"""
✅ **Setup Complete!**

**Channel:** {channel_title}
**Auto-deletion:** Enabled
**Deletion Interval:** 5 minutes
**Allowed Admins:** 1 (you)
        """
        await query.edit_message_text(success_text, parse_mode='Markdown', reply_markup=self.create_main_menu_keyboard())

    async def show_stats(self, query: Update):
        uptime = datetime.now() - self.stats['start_time']
        uptime_str = f"{uptime.days}d {uptime.seconds//3600}h"
        
        stats_text = f"""
📊 **Bot Statistics**

**Uptime:** {uptime_str}
**Active Channels:** {len(self.bot_join_times)}
**Messages Checked:** {self.stats['messages_checked']:,}
**Messages Deleted:** {self.stats['messages_deleted']:,}
**Errors:** {self.stats['errors']:,}
        """
        await query.edit_message_text(stats_text, parse_mode='Markdown', reply_markup=self.create_main_menu_keyboard())

    # ==================== MESSAGE HANDLER ====================

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        if not message:
            return

        self.stats['messages_checked'] += 1
        
        chat = message.chat
        channel_id = str(chat.id)
        
        # Only process configured channels
        if channel_id not in self.bot_join_times:
            return

        # Check if user is allowed
        user_id = message.from_user.id if message.from_user else None
        if not user_id:
            return

        if channel_id in self.channel_admins and user_id in self.channel_admins[channel_id]:
            return  # User is allowed

        # Check message time
        message_date = message.date.replace(tzinfo=None)
        if message_date < self.bot_join_times[channel_id]:
            return  # Message before bot was added

        # Get deletion interval
        delete_interval = self.delete_intervals.get(channel_id, 300)
        
        # Schedule deletion
        message_age = (datetime.now() - message_date).total_seconds()
        if message_age < delete_interval:
            remaining_time = delete_interval - message_age
            asyncio.create_task(self.delete_message_after_delay(message, remaining_time))
        else:
            await self.delete_message(message)

    async def delete_message_after_delay(self, message: Message, delay: float):
        try:
            await asyncio.sleep(delay)
            await self.delete_message(message)
        except Exception as e:
            logger.error(f"Error in scheduled deletion: {e}")
            self.stats['errors'] += 1

    async def delete_message(self, message: Message):
        try:
            await message.delete()
            self.stats['messages_deleted'] += 1
        except BadRequest as e:
            if "message to delete not found" not in str(e).lower():
                logger.error(f"Error deleting message: {e}")
                self.stats['errors'] += 1
        except TelegramError as e:
            logger.error(f"Telegram error: {e}")
            self.stats['errors'] += 1

    def run(self):
        """Start the bot"""
        start_health_check()
        self.start_keep_alive()
        logger.info("Starting Auto Delete Bot...")
        self.application.run_polling()

# ==================== MAIN EXECUTION ====================

if __name__ == "__main__":
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is required!")
        exit(1)
    
    try:
        bot = AutoDeleteBot(BOT_TOKEN)
        logger.info("Bot initialized successfully")
        bot.run()
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        exit(1)
