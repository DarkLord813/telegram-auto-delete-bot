import os
import time
import logging
import sqlite3
import requests
import threading
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Set
from telegram import Bot, Update, ChatMember, Message, User, InlineKeyboardButton, InlineKeyboardMarkup
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
    """Health check endpoint for monitoring"""
    try:
        health_status = {
            'status': 'healthy',
            'timestamp': time.time(),
            'service': 'telegram-auto-delete-bot',
            'version': '1.0.0',
            'checks': {
                'bot_online': {'status': 'healthy', 'message': 'Bot is running'},
                'system': {'status': 'healthy', 'message': 'System operational'}
            }
        }
        return jsonify(health_status), 200
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': time.time()
        }), 500

@app.route('/')
def home():
    """Root endpoint"""
    return jsonify({
        'service': 'Telegram Auto Delete Bot',
        'status': 'running',
        'version': '1.0.0',
        'endpoints': {
            'health': '/health',
            'features': ['Auto message deletion', 'Admin management', 'Channel protection']
        }
    })

def run_health_server():
    """Run the health check server with error handling"""
    try:
        port = int(os.environ.get('PORT', 8080))
        logger.info(f"🔄 Starting health server on port {port}")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"❌ Health server error: {e}")
        time.sleep(5)
        run_health_server()

def start_health_check():
    """Start health check server in background with restart capability"""
    def health_wrapper():
        while True:
            try:
                run_health_server()
            except Exception as e:
                logger.error(f"❌ Health server crashed, restarting: {e}")
                time.sleep(10)
    
    t = Thread(target=health_wrapper, daemon=True)
    t.start()
    logger.info("✅ Health check server started on port 8080")

# ==================== KEEP-ALIVE SERVICE ====================

class KeepAliveService:
    def __init__(self, health_url=None):
        self.health_url = health_url or f"http://localhost:{os.environ.get('PORT', 8080)}/health"
        self.is_running = False
        self.ping_count = 0
        
    def start(self):
        """Start keep-alive service to prevent sleep"""
        self.is_running = True
        
        def ping_loop():
            while self.is_running:
                try:
                    self.ping_count += 1
                    response = requests.get(self.health_url, timeout=10)
                    
                    if response.status_code == 200:
                        logger.info(f"✅ Keep-alive ping #{self.ping_count}: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    else:
                        logger.error(f"❌ Keep-alive failed: Status {response.status_code}")
                        
                except requests.exceptions.ConnectionError:
                    logger.warning(f"🔌 Keep-alive connection error - server may be starting")
                except requests.exceptions.Timeout:
                    logger.warning(f"⏰ Keep-alive timeout - retrying later")
                except Exception as e:
                    logger.error(f"❌ Keep-alive error: {e}")
                
                # Wait 4 minutes (Render sleeps after 15 min inactivity)
                time.sleep(240)  # 4 minutes
        
        thread = threading.Thread(target=ping_loop, daemon=True)
        thread.start()
        logger.info(f"🔄 Keep-alive service started - pinging every 4 minutes")
        logger.info(f"🌐 Health endpoint: {self.health_url}")
        
    def stop(self):
        """Stop keep-alive service"""
        self.is_running = False
        logger.info("🛑 Keep-alive service stopped")

# ==================== AUTO DELETE BOT ====================

class AutoDeleteBot:
    def __init__(self, token: str):
        self.token = token
        self.application = Application.builder().token(token).build()
        self.setup_database()
        
        # Store admin lists and settings
        self.channel_admins: Dict[str, List[int]] = {}
        self.bot_join_times: Dict[str, datetime] = {}
        self.delete_intervals: Dict[str, int] = {}
        
        # Add handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("setup", self.setup_command))
        self.application.add_handler(CommandHandler("addadmin", self.add_admin_command))
        self.application.add_handler(CommandHandler("removeadmin", self.remove_admin_command))
        self.application.add_handler(CommandHandler("listadmins", self.list_admins_command))
        self.application.add_handler(CommandHandler("settings", self.settings_command))
        self.application.add_handler(CommandHandler("setinterval", self.set_interval_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        
        # Callback query handler for inline keyboards
        self.application.add_handler(CallbackQueryHandler(self.handle_callback_query))
        
        # Message handler for all messages
        self.application.add_handler(MessageHandler(filters.ALL, self.handle_message))
        
        # Store the bot instance
        self.bot = Bot(token=token)
        
        # Initialize keep-alive service
        self.keep_alive = None
        
        # Statistics
        self.stats = {
            'messages_checked': 0,
            'messages_deleted': 0,
            'errors': 0,
            'start_time': datetime.now()
        }
        
        # Load existing data from database
        self.load_channel_data()
        
        logger.info("🤖 Auto Delete Bot initialized")

    def setup_database(self):
        """Initialize SQLite database for storing settings"""
        self.conn = sqlite3.connect('auto_delete_bot.db', check_same_thread=False)
        cursor = self.conn.cursor()
        
        # Create channels table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                channel_id TEXT PRIMARY KEY,
                channel_title TEXT,
                bot_added_date DATETIME,
                delete_interval INTEGER DEFAULT 300,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')
        
        # Create admins table (users who are allowed to post)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS allowed_admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT,
                user_id INTEGER,
                username TEXT,
                full_name TEXT,
                added_by INTEGER,
                added_date DATETIME,
                FOREIGN KEY (channel_id) REFERENCES channels (channel_id)
            )
        ''')
        
        self.conn.commit()
        logger.info("✅ Database setup completed")

    def load_channel_data(self):
        """Load channel data from database on startup"""
        cursor = self.conn.cursor()
        
        # Load channels
        cursor.execute('SELECT channel_id, bot_added_date, delete_interval FROM channels WHERE is_active = 1')
        channels = cursor.fetchall()
        
        for channel_id, added_date, interval in channels:
            self.bot_join_times[channel_id] = datetime.fromisoformat(added_date)
            self.delete_intervals[channel_id] = interval
        
        # Load admins
        cursor.execute('SELECT channel_id, user_id FROM allowed_admins')
        admins = cursor.fetchall()
        
        for channel_id, user_id in admins:
            if channel_id not in self.channel_admins:
                self.channel_admins[channel_id] = []
            self.channel_admins[channel_id].append(user_id)
        
        logger.info(f"📊 Loaded {len(channels)} channels and {len(admins)} admins from database")

    def start_keep_alive(self):
        """Start the keep-alive service"""
        try:
            # Get the actual Render URL from environment or use local
            render_url = os.environ.get('RENDER_EXTERNAL_URL')
            if render_url:
                health_url = f"{render_url}/health"
            else:
                health_url = f"http://localhost:{os.environ.get('PORT', 8080)}/health"
            
            self.keep_alive = KeepAliveService(health_url)
            self.keep_alive.start()
            logger.info("🔋 Keep-alive service activated - bot will stay awake!")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to start keep-alive: {e}")
            return False

    # ==================== INLINE KEYBOARD CREATION METHODS ====================

    def create_main_menu_keyboard(self):
        """Create main menu inline keyboard"""
        keyboard = [
            [InlineKeyboardButton("🛠️ Setup Bot", callback_data="setup_bot")],
            [InlineKeyboardButton("👥 Manage Admins", callback_data="manage_admins")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="show_settings")],
            [InlineKeyboardButton("📊 Statistics", callback_data="show_stats")],
            [InlineKeyboardButton("ℹ️ Help", callback_data="show_help")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def create_setup_keyboard(self):
        """Create setup confirmation keyboard"""
        keyboard = [
            [InlineKeyboardButton("✅ Confirm Setup", callback_data="confirm_setup")],
            [InlineKeyboardButton("❌ Cancel", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def create_admin_management_keyboard(self):
        """Create admin management keyboard"""
        keyboard = [
            [InlineKeyboardButton("➕ Add Admin", callback_data="add_admin")],
            [InlineKeyboardButton("➖ Remove Admin", callback_data="remove_admin")],
            [InlineKeyboardButton("📋 List Admins", callback_data="list_admins")],
            [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def create_settings_keyboard(self, channel_id=None):
        """Create settings management keyboard"""
        if channel_id and channel_id in self.delete_intervals:
            current_interval = self.delete_intervals[channel_id]
        else:
            current_interval = 300
            
        keyboard = [
            [InlineKeyboardButton(f"⏰ Deletion Interval ({current_interval}s)", callback_data="change_interval")],
            [InlineKeyboardButton("🔄 Refresh Settings", callback_data="show_settings")],
            [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def create_interval_keyboard(self):
        """Create interval selection keyboard"""
        keyboard = [
            [InlineKeyboardButton("1 minute", callback_data="interval_60")],
            [InlineKeyboardButton("5 minutes", callback_data="interval_300")],
            [InlineKeyboardButton("10 minutes", callback_data="interval_600")],
            [InlineKeyboardButton("15 minutes", callback_data="interval_900")],
            [InlineKeyboardButton("30 minutes", callback_data="interval_1800")],
            [InlineKeyboardButton("🔙 Back to Settings", callback_data="show_settings")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def create_back_to_main_keyboard(self):
        """Create simple back to main menu keyboard"""
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    # ==================== COMMAND HANDLERS ====================

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send welcome message with inline keyboard"""
        welcome_text = """
🤖 **Auto Delete Bot**

I automatically delete ALL messages except those from specified admins in channels.

**🔒 Key Features:**
• Auto-delete ALL messages from non-approved users
• Only approved admins can post messages
• Configurable deletion window
• Admin management system
• Only deletes messages from after bot was added

Use the buttons below to get started! 👇
        """
        
        await update.message.reply_text(
            welcome_text, 
            parse_mode='Markdown',
            reply_markup=self.create_main_menu_keyboard()
        )

    async def setup_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Setup bot in a channel"""
        await self.show_setup_menu(update, context)

    async def show_setup_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show setup menu"""
        setup_text = """
🛠️ **Bot Setup**

To setup auto-deletion in this channel:

1. **Make me an Admin** in this channel with **Delete Messages** permission
2. **Click Confirm Setup** below
3. **Add allowed admins** using the admin management menu

I will only delete messages sent after I was added to the channel.
        """
        
        if isinstance(update, Update) and update.message:
            await update.message.reply_text(
                setup_text,
                parse_mode='Markdown',
                reply_markup=self.create_setup_keyboard()
            )
        else:
            # Handle callback query edits
            query = update.callback_query
            await query.edit_message_text(
                setup_text,
                parse_mode='Markdown',
                reply_markup=self.create_setup_keyboard()
            )

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard callbacks"""
        query = update.callback_query
        await query.answer()
        
        callback_data = query.data
        chat = query.message.chat
        
        logger.info(f"📨 Callback: {callback_data} from {chat.id}")
        
        # Route callback to appropriate handler
        if callback_data == "main_menu":
            await self.show_main_menu(query)
        elif callback_data == "setup_bot":
            await self.show_setup_menu(query, context)
        elif callback_data == "confirm_setup":
            await self.confirm_setup(query, context)
        elif callback_data == "manage_admins":
            await self.show_admin_management(query, context)
        elif callback_data == "add_admin":
            await self.prompt_add_admin(query, context)
        elif callback_data == "remove_admin":
            await self.prompt_remove_admin(query, context)
        elif callback_data == "list_admins":
            await self.show_admin_list(query, context)
        elif callback_data == "show_settings":
            await self.show_settings(query, context)
        elif callback_data == "change_interval":
            await self.show_interval_menu(query, context)
        elif callback_data.startswith("interval_"):
            await self.set_interval_from_callback(query, context)
        elif callback_data == "show_stats":
            await self.show_stats(query, context)
        elif callback_data == "show_help":
            await self.show_help(query)

    async def show_main_menu(self, query):
        """Show main menu"""
        welcome_text = """
🤖 **Auto Delete Bot - Main Menu**

Choose an option below to manage your channel protection:
        """
        
        await query.edit_message_text(
            welcome_text,
            parse_mode='Markdown',
            reply_markup=self.create_main_menu_keyboard()
        )

    async def confirm_setup(self, query: Update, context: ContextTypes.DEFAULT_TYPE):
        """Confirm and complete bot setup"""
        chat = query.message.chat
        
        # Check if the command is used in a channel
        if chat.type not in ['channel', 'group', 'supergroup']:
            await query.edit_message_text(
                "❌ Please use this command in the channel where you want to setup auto-deletion.",
                reply_markup=self.create_back_to_main_keyboard()
            )
            return

        # Check if bot is admin in the channel
        try:
            bot_member = await chat.get_member(self.bot.id)
            if not bot_member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                await query.edit_message_text(
                    "❌ I need to be an **admin** in this channel with **delete messages** permission.\n\n"
                    "Please make me an admin first, then try setup again.",
                    parse_mode='Markdown',
                    reply_markup=self.create_back_to_main_keyboard()
                )
                return
        except TelegramError as e:
            await query.edit_message_text(
                f"❌ Error checking admin status: {e}",
                reply_markup=self.create_back_to_main_keyboard()
            )
            return

        # Check if user is admin in the channel
        try:
            user_member = await chat.get_member(query.from_user.id)
            if user_member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                await query.edit_message_text(
                    "❌ You need to be an admin in this channel to setup the bot.",
                    reply_markup=self.create_back_to_main_keyboard()
                )
                return
        except TelegramError as e:
            await query.edit_message_text(
                f"❌ Error checking your admin status: {e}",
                reply_markup=self.create_back_to_main_keyboard()
            )
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
        
        self.conn.commit()

        # Update in-memory data
        self.bot_join_times[channel_id] = bot_added_date
        self.delete_intervals[channel_id] = 300
        
        # Add the user who setup the bot as first admin
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

        # Update admin list
        if channel_id not in self.channel_admins:
            self.channel_admins[channel_id] = []
        self.channel_admins[channel_id].append(query.from_user.id)

        success_text = f"""
✅ **Setup Complete!**

**Channel:** {channel_title}
**Auto-deletion:** 🟢 Enabled
**Deletion Interval:** 5 minutes
**Allowed Admins:** 1 (you)

You can now manage settings and add more allowed admins using the menus below.
        """
        
        await query.edit_message_text(
            success_text,
            parse_mode='Markdown',
            reply_markup=self.create_main_menu_keyboard()
        )

    async def show_admin_management(self, query: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show admin management menu"""
        chat = query.message.chat
        channel_id = str(chat.id)

        # Check if channel is setup
        if channel_id not in self.bot_join_times:
            await query.edit_message_text(
                "❌ Bot is not setup in this channel. Please run setup first.",
                reply_markup=self.create_back_to_main_keyboard()
            )
            return

        management_text = """
👥 **Admin Management**

Manage users who are allowed to post messages without deletion.

**Options:**
• **Add Admin** - Add a user to allowed list
• **Remove Admin** - Remove a user from allowed list  
• **List Admins** - View current allowed users

Choose an option below:
        """
        
        await query.edit_message_text(
            management_text,
            parse_mode='Markdown',
            reply_markup=self.create_admin_management_keyboard()
        )

    async def prompt_add_admin(self, query: Update, context: ContextTypes.DEFAULT_TYPE):
        """Prompt user to add admin"""
        prompt_text = """
➕ **Add Admin**

To add an admin, please:

1. **Reply to this message** with the username
2. **Format:** `@username` or `username`
3. **User must be an admin** in this channel

Example: `@johnsmith` or `johnsmith`

Please reply with the username now:
        """
        
        await query.edit_message_text(
            prompt_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="manage_admins")]])
        )
        
        # Store state for next message
        context.user_data['waiting_for_admin'] = True
        context.user_data['action'] = 'add_admin'

    async def prompt_remove_admin(self, query: Update, context: ContextTypes.DEFAULT_TYPE):
        """Prompt user to remove admin"""
        prompt_text = """
➖ **Remove Admin**

To remove an admin, please:

1. **Reply to this message** with the username
2. **Format:** `@username` or `username`

Example: `@johnsmith` or `johnsmith`

Please reply with the username now:
        """
        
        await query.edit_message_text(
            prompt_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="manage_admins")]])
        )
        
        # Store state for next message
        context.user_data['waiting_for_admin'] = True
        context.user_data['action'] = 'remove_admin'

    async def show_admin_list(self, query: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show list of allowed admins"""
        chat = query.message.chat
        channel_id = str(chat.id)

        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT username, full_name, added_date 
            FROM allowed_admins 
            WHERE channel_id = ? 
            ORDER BY added_date
        ''', (channel_id,))
        
        admins = cursor.fetchall()

        if not admins:
            admin_list_text = "❌ No allowed admins found.\n\nUse the 'Add Admin' button to add users."
        else:
            admin_list_text = "✅ **Allowed Admins:**\n\n"
            for i, (username, full_name, added_date) in enumerate(admins, 1):
                date_str = datetime.fromisoformat(added_date).strftime('%Y-%m-%d')
                admin_list_text += f"{i}. **{full_name}** (@{username})\n   Added: {date_str}\n\n"

        await query.edit_message_text(
            admin_list_text,
            parse_mode='Markdown',
            reply_markup=self.create_admin_management_keyboard()
        )

    async def show_settings(self, query: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show current settings"""
        chat = query.message.chat
        channel_id = str(chat.id)

        if channel_id not in self.bot_join_times:
            await query.edit_message_text(
                "❌ Bot is not setup in this channel. Please run setup first.",
                reply_markup=self.create_back_to_main_keyboard()
            )
            return

        cursor = self.conn.cursor()
        cursor.execute('SELECT channel_title, bot_added_date, delete_interval FROM channels WHERE channel_id = ?', (channel_id,))
        channel_data = cursor.fetchone()
        
        cursor.execute('SELECT COUNT(*) FROM allowed_admins WHERE channel_id = ?', (channel_id,))
        admin_count = cursor.fetchone()[0]

        if channel_data:
            channel_title, added_date, interval = channel_data
            added_date_str = datetime.fromisoformat(added_date).strftime('%Y-%m-%d %H:%M:%S')
            
            settings_text = f"""
⚙️ **Bot Settings**

**Channel:** {channel_title}
**Setup Date:** {added_date_str}
**Deletion Interval:** {interval} seconds ({interval//60} minutes)
**Allowed Admins:** {admin_count} user(s)

Use the buttons below to manage settings:
            """
            
            await query.edit_message_text(
                settings_text,
                parse_mode='Markdown',
                reply_markup=self.create_settings_keyboard(channel_id)
            )

    async def show_interval_menu(self, query: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show interval selection menu"""
        interval_text = """
⏰ **Deletion Interval**

Select how long to wait before deleting messages from non-approved users:

• **1 minute** - Quick deletion
• **5 minutes** - Recommended (default)
• **10 minutes** - More lenient
• **15 minutes** - Very lenient
• **30 minutes** - Maximum leniency

Choose an option below:
        """
        
        await query.edit_message_text(
            interval_text,
            parse_mode='Markdown',
            reply_markup=self.create_interval_keyboard()
        )

    async def set_interval_from_callback(self, query: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set interval from callback selection"""
        chat = query.message.chat
        channel_id = str(chat.id)
        
        interval_str = query.data.replace("interval_", "")
        interval = int(interval_str)

        # Update interval
        cursor = self.conn.cursor()
        cursor.execute('UPDATE channels SET delete_interval = ? WHERE channel_id = ?', (interval, channel_id))
        self.conn.commit()
        
        self.delete_intervals[channel_id] = interval

        success_text = f"""
✅ **Deletion Interval Updated!**

**New Interval:** {interval} seconds ({interval//60} minutes)

Messages from non-approved users will be deleted after this delay.
        """
        
        await query.edit_message_text(
            success_text,
            parse_mode='Markdown',
            reply_markup=self.create_settings_keyboard(channel_id)
        )

    async def show_stats(self, query: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show bot statistics"""
        uptime = datetime.now() - self.stats['start_time']
        uptime_str = f"{uptime.days}d {uptime.seconds//3600}h {(uptime.seconds//60)%60}m"
        
        stats_text = f"""
📊 **Bot Statistics**

**General:**
• Uptime: {uptime_str}
• Active Channels: {len(self.bot_join_times)}
• Total Allowed Admins: {sum(len(admins) for admins in self.channel_admins.values())}

**Message Processing:**
• Messages Checked: {self.stats['messages_checked']:,}
• Messages Deleted: {self.stats['messages_deleted']:,}
• Error Count: {self.stats['errors']:,}
• Success Rate: {(self.stats['messages_checked'] - self.stats['errors']) / max(1, self.stats['messages_checked']) * 100:.1f}%

**Health:**
• Keep-alive: {'🟢 Active' if self.keep_alive and self.keep_alive.is_running else '🔴 Inactive'}
• Health Server: 🟢 Running
        """
        
        await query.edit_message_text(
            stats_text,
            parse_mode='Markdown',
            reply_markup=self.create_back_to_main_keyboard()
        )

    async def show_help(self, query: Update):
        """Show help information"""
        help_text = """
ℹ️ **Help & Information**

**How It Works:**
1. Setup bot in your channel as admin
2. Add allowed admins who can post
3. Bot auto-deletes all other messages
4. Only messages after bot was added are deleted

**Commands Available:**
• `/start` - Show main menu
• `/setup` - Setup bot in current channel
• `/settings` - Show current settings
• `/stats` - Show bot statistics

**Need Help?**
Use the menus above or contact the bot administrator.
        """
        
        await query.edit_message_text(
            help_text,
            parse_mode='Markdown',
            reply_markup=self.create_back_to_main_keyboard()
        )

    # ==================== MESSAGE HANDLERS ====================

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all messages and delete those from non-approved users"""
        message = update.effective_message
        if not message:
            return

        # Check if we're waiting for admin username input
        if (context.user_data.get('waiting_for_admin') and 
            message.text and 
            not message.text.startswith('/')):
            
            await self.handle_admin_username_input(update, context)
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
        
        # Wait for the specified interval before deletion
        message_age = (datetime.now() - message_date).total_seconds()
        if message_age < delete_interval:
            # Schedule deletion after remaining time
            remaining_time = delete_interval - message_age
            asyncio.create_task(self.delete_message_after_delay(message, remaining_time))
        else:
            # Delete immediately if message is older than interval
            await self.delete_message(message)

    async def handle_admin_username_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle admin username input from user"""
        message = update.effective_message
        username = message.text.lstrip('@')
        action = context.user_data.get('action')
        chat = message.chat
        channel_id = str(chat.id)

        # Clear the waiting state
        context.user_data['waiting_for_admin'] = False
        context.user_data['action'] = None

        if action == 'add_admin':
            await self.process_add_admin(message, username, channel_id)
        elif action == 'remove_admin':
            await self.process_remove_admin(message, username, channel_id)

    async def process_add_admin(self, message: Message, username: str, channel_id: str):
        """Process adding an admin"""
        try:
            # Get user ID from username by checking channel admins
            chat = message.chat
            chat_members = await chat.get_administrators()
            target_user = None
            
            for member in chat_members:
                if (member.user.username and member.user.username.lower() == username.lower()) or \
                   (member.user.full_name and member.user.full_name.lower() == username.lower()):
                    target_user = member.user
                    break
            
            if not target_user:
                await message.reply_text(
                    f"❌ User @{username} not found or not an admin in this channel.",
                    reply_markup=self.create_back_to_main_keyboard()
                )
                return

            # Add to database
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO allowed_admins 
                (channel_id, user_id, username, full_name, added_by, added_date)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                channel_id, 
                target_user.id,
                target_user.username,
                target_user.full_name,
                message.from_user.id,
                datetime.now()
            ))
            
            self.conn.commit()

            # Update in-memory list
            if channel_id not in self.channel_admins:
                self.channel_admins[channel_id] = []
            
            if target_user.id not in self.channel_admins[channel_id]:
                self.channel_admins[channel_id].append(target_user.id)

            await message.reply_text(
                f"✅ **Admin Added Successfully!**\n\n"
                f"**User:** {target_user.full_name} (@{target_user.username})\n"
                f"**ID:** `{target_user.id}`\n\n"
                f"This user can now post messages without them being deleted.",
                parse_mode='Markdown',
                reply_markup=self.create_admin_management_keyboard()
            )

        except TelegramError as e:
            await message.reply_text(
                f"❌ Error adding admin: {e}",
                reply_markup=self.create_back_to_main_keyboard()
            )

    async def process_remove_admin(self, message: Message, username: str, channel_id: str):
        """Process removing an admin"""
        # Remove from database
        cursor = self.conn.cursor()
        cursor.execute('''
            DELETE FROM allowed_admins 
            WHERE channel_id = ? AND (username = ? OR LOWER(username) = LOWER(?))
        ''', (channel_id, username, username))
        
        rows_affected = cursor.rowcount
        self.conn.commit()

        if rows_affected > 0:
            # Update in-memory list
            if channel_id in self.channel_admins:
                # Find and remove the user
                cursor.execute('SELECT user_id FROM allowed_admins WHERE channel_id = ? AND (username = ? OR LOWER(username) = LOWER(?))', 
                             (channel_id, username, username))
                result = cursor.fetchone()
                if result:
                    user_id = result[0]
                    if user_id in self.channel_admins[channel_id]:
                        self.channel_admins[channel_id].remove(user_id)

            await message.reply_text(
                f"✅ **Admin Removed Successfully!**\n\n"
                f"**User:** @{username}\n\n"
                f"This user's messages will now be auto-deleted.",
                reply_markup=self.create_admin_management_keyboard()
            )
        else:
            await message.reply_text(
                f"❌ User @{username} not found in allowed admins list.",
                reply_markup=self.create_back_to_main_keyboard()
            )

    async def delete_message_after_delay(self, message: Message, delay: float):
        """Delete a message after a specified delay"""
        try:
            await asyncio.sleep(delay)
            await self.delete_message(message)
        except Exception as e:
            logger.error(f"Error in scheduled deletion: {e}")
            self.stats['errors'] += 1

    async def delete_message(self, message: Message):
        """Delete a message with error handling"""
        try:
            await message.delete()
            self.stats['messages_deleted'] += 1
            logger.info(f"✅ Deleted message from user {message.from_user.id} in channel {message.chat.id}")
        except BadRequest as e:
            if "message to delete not found" not in str(e).lower():
                logger.error(f"❌ Error deleting message: {e}")
                self.stats['errors'] += 1
        except TelegramError as e:
            logger.error(f"❌ Telegram error deleting message: {e}")
            self.stats['errors'] += 1

    def run(self):
        """Start the bot with all services"""
        # Start health check server
        start_health_check()
        
        # Start keep-alive service
        self.start_keep_alive()
        
        # Start the bot
        logger.info("🤖 Starting Auto Delete Bot...")
        self.application.run_polling()

# ==================== MAIN EXECUTION ====================

if __name__ == "__main__":
    # Get bot token from environment
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN environment variable is required!")
        exit(1)
    
    # Create and run the bot
    bot = AutoDeleteBot(BOT_TOKEN)
    bot.run()
