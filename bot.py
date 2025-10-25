import os
import time
import logging
import sqlite3
import requests
import threading
from datetime import datetime
from typing import Dict, List

from telegram import Bot, Update, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackContext, MessageHandler, Filters, CallbackQueryHandler
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

def start_health_check():
    def health_wrapper():
        run_health_server()
    
    t = Thread(target=health_wrapper, daemon=True)
    t.start()
    logger.info("Health check server started")

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
                        logger.info(f"Keep-alive ping successful")
                except Exception as e:
                    logger.warning(f"Keep-alive error: {e}")
                time.sleep(300)  # 5 minutes
        
        thread = threading.Thread(target=ping_loop, daemon=True)
        thread.start()
        logger.info("Keep-alive service started")

# ==================== AUTO DELETE BOT ====================

class AutoDeleteBot:
    def __init__(self, token: str):
        self.token = token
        
        # Use Updater for python-telegram-bot v13.x
        self.updater = Updater(token=token, use_context=True)
        self.dispatcher = self.updater.dispatcher
        self.bot = self.updater.bot
        
        self.setup_database()
        
        # Store data
        self.channel_admins: Dict[str, List[int]] = {}
        self.bot_join_times: Dict[str, datetime] = {}
        self.delete_intervals: Dict[str, int] = {}
        
        # Setup handlers
        self.setup_handlers()
        
        self.keep_alive = None
        
        # Statistics
        self.stats = {
            'messages_checked': 0,
            'messages_deleted': 0,
            'errors': 0,
            'start_time': datetime.now()
        }
        
        self.load_channel_data()
        logger.info("Auto Delete Bot initialized successfully")

    def setup_handlers(self):
        """Setup all message handlers"""
        # Command handlers
        self.dispatcher.add_handler(CommandHandler("start", self.start_command))
        self.dispatcher.add_handler(CommandHandler("setup", self.setup_command))
        self.dispatcher.add_handler(CommandHandler("stats", self.stats_command))
        
        # Callback query handler
        self.dispatcher.add_handler(CallbackQueryHandler(self.handle_callback_query))
        
        # Message handler for all messages
        self.dispatcher.add_handler(MessageHandler(Filters.all, self.handle_message))

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
        logger.info("Database setup completed")

    def load_channel_data(self):
        """Load channel data from database"""
        cursor = self.conn.cursor()
        
        cursor.execute('SELECT channel_id, bot_added_date, delete_interval FROM channels WHERE is_active = 1')
        channels = cursor.fetchall()
        for channel_id, added_date, interval in channels:
            self.bot_join_times[channel_id] = datetime.fromisoformat(added_date)
            self.delete_intervals[channel_id] = interval
        
        cursor.execute('SELECT channel_id, user_id FROM allowed_admins')
        admins = cursor.fetchall()
        for channel_id, user_id in admins:
            if channel_id not in self.channel_admins:
                self.channel_admins[channel_id] = []
            self.channel_admins[channel_id].append(user_id)
        
        logger.info(f"Loaded {len(channels)} channels and {len(admins)} admins")

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
            logger.info("Keep-alive service activated")
            return True
        except Exception as e:
            logger.error(f"Failed to start keep-alive: {e}")
            return False

    # ==================== KEYBOARD METHODS ====================

    def create_main_menu_keyboard(self):
        keyboard = [
            [InlineKeyboardButton("🛠️ Setup Bot", callback_data="setup_bot")],
            [InlineKeyboardButton("📊 Statistics", callback_data="show_stats")],
            [InlineKeyboardButton("ℹ️ Help", callback_data="show_help")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def create_setup_keyboard(self):
        keyboard = [
            [InlineKeyboardButton("✅ Confirm Setup", callback_data="confirm_setup")],
            [InlineKeyboardButton("❌ Cancel", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def create_back_keyboard(self):
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    # ==================== COMMAND HANDLERS ====================

    def start_command(self, update: Update, context: CallbackContext):
        welcome_text = """
🤖 **Auto Delete Bot**

I automatically delete ALL messages except those from specified admins in channels.

**Features:**
• Auto-delete messages from non-approved users
• Only approved admins can post
• Configurable deletion timing

Use the buttons below to get started!
        """
        update.message.reply_text(welcome_text, parse_mode='Markdown', reply_markup=self.create_main_menu_keyboard())

    def setup_command(self, update: Update, context: CallbackContext):
        setup_text = """
🛠️ **Bot Setup**

To setup auto-deletion in your channel:

1. **Add me as Admin** with **Delete Messages** permission
2. **Click Confirm Setup** below
3. **Start adding allowed admins**

I will only delete messages sent after I was added to the channel.
        """
        update.message.reply_text(setup_text, parse_mode='Markdown', reply_markup=self.create_setup_keyboard())

    def stats_command(self, update: Update, context: CallbackContext):
        uptime = datetime.now() - self.stats['start_time']
        uptime_str = f"{uptime.days}d {uptime.seconds//3600}h {(uptime.seconds//60)%60}m"
        
        stats_text = f"""
📊 **Bot Statistics**

**Uptime:** {uptime_str}
**Active Channels:** {len(self.bot_join_times)}
**Messages Checked:** {self.stats['messages_checked']:,}
**Messages Deleted:** {self.stats['messages_deleted']:,}
**Errors:** {self.stats['errors']:,}
        """
        update.message.reply_text(stats_text, parse_mode='Markdown', reply_markup=self.create_back_keyboard())

    def handle_callback_query(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        
        callback_data = query.data
        logger.info(f"Callback received: {callback_data}")
        
        if callback_data == "main_menu":
            self.show_main_menu(query)
        elif callback_data == "setup_bot":
            self.show_setup_menu(query)
        elif callback_data == "confirm_setup":
            self.confirm_setup(query, context)
        elif callback_data == "show_stats":
            self.show_stats(query)
        elif callback_data == "show_help":
            self.show_help(query)

    def show_main_menu(self, query):
        welcome_text = "🤖 **Auto Delete Bot - Main Menu**\n\nChoose an option below:"
        query.edit_message_text(welcome_text, parse_mode='Markdown', reply_markup=self.create_main_menu_keyboard())

    def show_setup_menu(self, query):
        setup_text = "🛠️ **Setup Auto-Deletion**\n\nClick Confirm Setup to configure the bot in this channel."
        query.edit_message_text(setup_text, parse_mode='Markdown', reply_markup=self.create_setup_keyboard())

    def confirm_setup(self, query: Update, context: CallbackContext):
        chat = query.message.chat
        
        # Check if in a channel/group
        if chat.type not in ['channel', 'group', 'supergroup']:
            query.edit_message_text("❌ Please use this command in a channel or group where you want to setup auto-deletion.", reply_markup=self.create_back_keyboard())
            return

        try:
            # Check if bot is admin
            bot_member = chat.get_member(self.bot.id)
            if bot_member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                query.edit_message_text(
                    "❌ I need to be an **admin** in this channel with **delete messages** permission.\n\nPlease make me an admin first, then try setup again.",
                    parse_mode='Markdown',
                    reply_markup=self.create_back_keyboard()
                )
                return
                
            # Check if user is admin
            user_member = chat.get_member(query.from_user.id)
            if user_member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                query.edit_message_text("❌ You need to be an admin in this channel to setup the bot.", reply_markup=self.create_back_keyboard())
                return
                
        except TelegramError as e:
            query.edit_message_text(f"❌ Error checking permissions: {e}", reply_markup=self.create_back_keyboard())
            return

        # Setup channel in database
        channel_id = str(chat.id)
        channel_title = chat.title or "Unknown Channel"
        bot_added_date = datetime.now()

        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO channels 
            (channel_id, channel_title, bot_added_date, delete_interval, is_active)
            VALUES (?, ?, ?, ?, ?)
        ''', (channel_id, channel_title, bot_added_date, 300, True))
        
        # Add user as first admin
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

        # Update in-memory data
        self.bot_join_times[channel_id] = bot_added_date
        self.delete_intervals[channel_id] = 300
        
        if channel_id not in self.channel_admins:
            self.channel_admins[channel_id] = []
        self.channel_admins[channel_id].append(query.from_user.id)

        success_text = f"""
✅ **Setup Complete!**

**Channel:** {channel_title}
**Auto-deletion:** 🟢 Enabled
**Deletion Interval:** 5 minutes
**Allowed Admins:** 1 (you)

The bot is now active and will auto-delete messages from non-approved users.
        """
        query.edit_message_text(success_text, parse_mode='Markdown', reply_markup=self.create_main_menu_keyboard())

    def show_stats(self, query):
        uptime = datetime.now() - self.stats['start_time']
        uptime_str = f"{uptime.days}d {uptime.seconds//3600}h {(uptime.seconds//60)%60}m"
        
        stats_text = f"""
📊 **Bot Statistics**

**Uptime:** {uptime_str}
**Active Channels:** {len(self.bot_join_times)}
**Messages Checked:** {self.stats['messages_checked']:,}
**Messages Deleted:** {self.stats['messages_deleted']:,}
**Errors:** {self.stats['errors']:,}

**Keep-alive:** {'🟢 Active' if self.keep_alive and self.keep_alive.is_running else '🔴 Inactive'}
        """
        query.edit_message_text(stats_text, parse_mode='Markdown', reply_markup=self.create_back_keyboard())

    def show_help(self, query):
        help_text = """
ℹ️ **Help & Information**

**How It Works:**
1. Setup bot in your channel as admin
2. Bot auto-deletes all messages from non-approved users
3. Only messages sent after bot was added are deleted

**Commands:**
• `/start` - Show main menu
• `/setup` - Setup bot in current channel
• `/stats` - Show bot statistics

**Need Help?**
Contact the bot administrator for assistance.
        """
        query.edit_message_text(help_text, parse_mode='Markdown', reply_markup=self.create_back_keyboard())

    # ==================== MESSAGE HANDLER ====================

    def handle_message(self, update: Update, context: CallbackContext):
        message = update.effective_message
        if not message:
            return

        self.stats['messages_checked'] += 1
        
        chat = message.chat
        channel_id = str(chat.id)
        
        # Only process messages from configured channels
        if channel_id not in self.bot_join_times:
            return

        # Check if message is from an allowed admin
        user_id = message.from_user.id if message.from_user else None
        if not user_id:
            return

        # Check if user is in allowed admins list
        if channel_id in self.channel_admins and user_id in self.channel_admins[channel_id]:
            return  # User is allowed, don't delete

        # Check if message was sent after bot was added to channel
        message_date = message.date.replace(tzinfo=None)
        if message_date < self.bot_join_times[channel_id]:
            return  # Message was sent before bot was added

        # Get deletion interval for this channel
        delete_interval = self.delete_intervals.get(channel_id, 300)
        
        # Schedule deletion
        try:
            context.job_queue.run_once(
                self.delete_message_callback, 
                delete_interval, 
                context=message.chat_id,
                name=f"delete_{message.message_id}"
            )
        except Exception as e:
            logger.error(f"Error scheduling deletion: {e}")
            self.stats['errors'] += 1

    def delete_message_callback(self, context: CallbackContext):
        """Callback for deleting messages"""
        job = context.job
        try:
            context.bot.delete_message(chat_id=job.context, message_id=job.name.replace("delete_", ""))
            self.stats['messages_deleted'] += 1
            logger.info(f"Deleted message {job.name} from channel {job.context}")
        except BadRequest as e:
            if "message to delete not found" not in str(e).lower():
                logger.error(f"Error deleting message: {e}")
                self.stats['errors'] += 1
        except TelegramError as e:
            logger.error(f"Telegram error deleting message: {e}")
            self.stats['errors'] += 1

    def run(self):
        """Start the bot with all services"""
        # Start health check server
        start_health_check()
        
        # Start keep-alive service
        self.start_keep_alive()
        
        # Start the bot
        logger.info("🤖 Starting Auto Delete Bot...")
        self.updater.start_polling()
        logger.info("✅ Bot is now running and polling for messages")
        self.updater.idle()

# ==================== MAIN EXECUTION ====================

if __name__ == "__main__":
    # Get bot token from environment
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN environment variable is required!")
        logger.error("Please set BOT_TOKEN in your Render environment variables")
        exit(1)
    
    try:
        # Create and run the bot
        logger.info("🚀 Initializing Auto Delete Bot...")
        bot = AutoDeleteBot(BOT_TOKEN)
        logger.info("✅ Bot initialized successfully")
        bot.run()
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        exit(1)
