import os
import time
import logging
import sqlite3
import requests
import threading
from datetime import datetime, timedelta
from typing import Dict, List
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

@app.route('/webhook', methods=['POST'])
def webhook():
    """Webhook endpoint for Telegram"""
    try:
        update = request.get_json()
        if BOT_INSTANCE:
            BOT_INSTANCE.bot.process_update(update)
        return 'OK'
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return 'OK'

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

# ==================== DATABASE MANAGER ====================

class DatabaseManager:
    def __init__(self):
        self.setup_database()
    
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
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pending_deletions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT,
                message_id INTEGER,
                delete_at DATETIME,
                processed BOOLEAN DEFAULT FALSE
            )
        ''')
        
        self.conn.commit()
        logger.info("Database setup completed")
    
    def add_channel(self, channel_id, channel_title):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO channels 
            (channel_id, channel_title, bot_added_date, delete_interval, is_active)
            VALUES (?, ?, ?, ?, ?)
        ''', (channel_id, channel_title, datetime.now(), 300, True))
        self.conn.commit()
    
    def update_delete_interval(self, channel_id, interval):
        cursor = self.conn.cursor()
        cursor.execute('UPDATE channels SET delete_interval = ? WHERE channel_id = ?', (interval, channel_id))
        self.conn.commit()
    
    def add_admin(self, channel_id, user_id, username, full_name, added_by):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO allowed_admins 
            (channel_id, user_id, username, full_name, added_by, added_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (channel_id, user_id, username, full_name, added_by, datetime.now()))
        self.conn.commit()
    
    def remove_admin(self, channel_id, user_id):
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM allowed_admins WHERE channel_id = ? AND user_id = ?', (channel_id, user_id))
        self.conn.commit()
        return cursor.rowcount > 0
    
    def get_admins(self, channel_id):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT user_id, username, full_name, added_date 
            FROM allowed_admins 
            WHERE channel_id = ? 
            ORDER BY added_date
        ''', (channel_id,))
        return cursor.fetchall()
    
    def schedule_deletion(self, channel_id, message_id, delete_after_seconds):
        delete_at = datetime.now() + timedelta(seconds=delete_after_seconds)
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO pending_deletions 
            (channel_id, message_id, delete_at)
            VALUES (?, ?, ?)
        ''', (channel_id, message_id, delete_at))
        self.conn.commit()
        return cursor.lastrowid
    
    def get_pending_deletions(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT id, channel_id, message_id 
            FROM pending_deletions 
            WHERE delete_at <= ? AND processed = FALSE
        ''', (datetime.now(),))
        return cursor.fetchall()
    
    def mark_deletion_processed(self, deletion_id):
        cursor = self.conn.cursor()
        cursor.execute('UPDATE pending_deletions SET processed = TRUE WHERE id = ?', (deletion_id,))
        self.conn.commit()
    
    def is_admin(self, channel_id, user_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT 1 FROM allowed_admins WHERE channel_id = ? AND user_id = ?', (channel_id, user_id))
        return cursor.fetchone() is not None
    
    def get_channel_settings(self, channel_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT bot_added_date, delete_interval FROM channels WHERE channel_id = ? AND is_active = TRUE', (channel_id,))
        result = cursor.fetchone()
        if result:
            return {
                'bot_added_date': datetime.fromisoformat(result[0]),
                'delete_interval': result[1]
            }
        return None

# ==================== TELEGRAM BOT HANDLER ====================

class TelegramBotHandler:
    def __init__(self, token, db_manager):
        self.token = token
        self.db = db_manager
        self.bot_url = f"https://api.telegram.org/bot{token}"
        
        # Store user states for conversation flow
        self.user_states = {}
        
        # Store bot info
        self.bot_info = None
        self.get_bot_info()
    
    def get_bot_info(self):
        """Get bot information"""
        try:
            response = requests.get(f"{self.bot_url}/getMe")
            if response.status_code == 200:
                self.bot_info = response.json()['result']
                logger.info(f"Bot initialized: @{self.bot_info['username']}")
            else:
                logger.error("Failed to get bot info")
        except Exception as e:
            logger.error(f"Error getting bot info: {e}")
    
    def set_user_state(self, user_id, state, data=None):
        """Set user conversation state"""
        self.user_states[user_id] = {'state': state, 'data': data or {}}
    
    def get_user_state(self, user_id):
        """Get user conversation state"""
        return self.user_states.get(user_id)
    
    def clear_user_state(self, user_id):
        """Clear user conversation state"""
        if user_id in self.user_states:
            del self.user_states[user_id]
    
    def send_message(self, chat_id, text, reply_markup=None, parse_mode='HTML'):
        """Send message to chat"""
        try:
            data = {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': parse_mode
            }
            if reply_markup:
                data['reply_markup'] = reply_markup
            
            response = requests.post(f"{self.bot_url}/sendMessage", json=data)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False
    
    def delete_message(self, chat_id, message_id):
        """Delete a message"""
        try:
            data = {
                'chat_id': chat_id,
                'message_id': message_id
            }
            response = requests.post(f"{self.bot_url}/deleteMessage", json=data)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Error deleting message: {e}")
            return False
    
    def get_chat_administrators(self, chat_id):
        """Get chat administrators"""
        try:
            response = requests.get(f"{self.bot_url}/getChatAdministrators", params={'chat_id': chat_id})
            if response.status_code == 200:
                return response.json()['result']
            return []
        except Exception as e:
            logger.error(f"Error getting chat admins: {e}")
            return []
    
    def process_update(self, update):
        """Process incoming update from webhook"""
        try:
            if 'message' in update:
                self.handle_message(update['message'])
            elif 'callback_query' in update:
                self.handle_callback_query(update['callback_query'])
        except Exception as e:
            logger.error(f"Error processing update: {e}")
    
    def handle_message(self, message):
        """Handle incoming message"""
        chat_id = message['chat']['id']
        user_id = message['from']['id']
        message_id = message['message_id']
        
        # Check user state first (for conversation flow)
        user_state = self.get_user_state(user_id)
        if user_state and 'text' in message:
            self.handle_user_state(user_id, chat_id, message['text'])
            return
        
        # Check if it's a command
        if 'text' in message and message['text'].startswith('/'):
            self.handle_command(message)
            return
        
        # Check if message should be deleted
        channel_settings = self.db.get_channel_settings(str(chat_id))
        if not channel_settings:
            return  # Bot not setup in this channel
        
        # Check if user is admin
        if self.db.is_admin(str(chat_id), user_id):
            return  # Admin can post freely
        
        # Check message time
        message_date = datetime.fromtimestamp(message['date'])
        if message_date < channel_settings['bot_added_date']:
            return  # Message before bot was added
        
        # Schedule deletion
        self.db.schedule_deletion(str(chat_id), message_id, channel_settings['delete_interval'])
        logger.info(f"Scheduled deletion for message {message_id} in channel {chat_id}")
    
    def handle_user_state(self, user_id, chat_id, text):
        """Handle user in conversation state"""
        state = self.get_user_state(user_id)
        if not state:
            return
        
        if state['state'] == 'waiting_admin_username':
            self.process_add_admin(chat_id, user_id, text)
        elif state['state'] == 'waiting_remove_admin':
            self.process_remove_admin(chat_id, user_id, text)
        elif state['state'] == 'waiting_interval':
            self.process_set_interval(chat_id, user_id, text)
    
    def handle_command(self, message):
        """Handle bot commands"""
        chat_id = message['chat']['id']
        user_id = message['from']['id']
        text = message['text']
        
        if text == '/start':
            self.send_main_menu(chat_id)
        elif text == '/setup':
            self.send_setup_message(chat_id)
        elif text == '/admins':
            self.send_admin_management(chat_id)
        elif text == '/settings':
            self.send_settings(chat_id)
        elif text == '/stats':
            self.send_stats_message(chat_id)
    
    def handle_callback_query(self, callback_query):
        """Handle callback queries"""
        chat_id = callback_query['message']['chat']['id']
        user_id = callback_query['from']['id']
        data = callback_query['data']
        
        # Answer callback query
        requests.post(f"{self.bot_url}/answerCallbackQuery", json={'callback_query_id': callback_query['id']})
        
        if data == 'main_menu':
            self.send_main_menu(chat_id)
        elif data == 'setup_bot':
            self.send_setup_message(chat_id)
        elif data == 'confirm_setup':
            self.confirm_setup(chat_id, user_id)
        elif data == 'manage_admins':
            self.send_admin_management(chat_id)
        elif data == 'add_admin':
            self.prompt_add_admin(chat_id, user_id)
        elif data == 'remove_admin':
            self.prompt_remove_admin(chat_id, user_id)
        elif data == 'list_admins':
            self.show_admin_list(chat_id)
        elif data == 'settings':
            self.send_settings(chat_id)
        elif data == 'set_interval':
            self.prompt_set_interval(chat_id, user_id)
        elif data == 'show_stats':
            self.send_stats_message(chat_id)
        elif data.startswith('interval_'):
            interval = int(data.replace('interval_', ''))
            self.set_delete_interval(chat_id, user_id, interval)
    
    def send_main_menu(self, chat_id):
        """Send main menu"""
        text = """
🤖 <b>Auto Delete Bot - Main Menu</b>

Choose an option below to manage your channel protection:
        """
        keyboard = {
            'inline_keyboard': [
                [{'text': '🛠️ Setup Bot', 'callback_data': 'setup_bot'}],
                [{'text': '👥 Manage Admins', 'callback_data': 'manage_admins'}],
                [{'text': '⚙️ Settings', 'callback_data': 'settings'}],
                [{'text': '📊 Statistics', 'callback_data': 'show_stats'}]
            ]
        }
        self.send_message(chat_id, text, keyboard)
    
    def send_setup_message(self, chat_id):
        """Send setup message"""
        text = """
🛠️ <b>Bot Setup</b>

To setup auto-deletion in your channel:

1. <b>Add me as Admin</b> with <b>Delete Messages</b> permission
2. <b>Click Confirm Setup</b> below
3. <b>Add allowed admins</b> using the admin management menu

I will only delete messages sent after I was added to the channel.
        """
        keyboard = {
            'inline_keyboard': [
                [{'text': '✅ Confirm Setup', 'callback_data': 'confirm_setup'}],
                [{'text': '❌ Cancel', 'callback_data': 'main_menu'}]
            ]
        }
        self.send_message(chat_id, text, keyboard)
    
    def confirm_setup(self, chat_id, user_id):
        """Confirm bot setup"""
        # Get chat info
        try:
            response = requests.get(f"{self.bot_url}/getChat", params={'chat_id': chat_id})
            if response.status_code != 200:
                self.send_message(chat_id, "❌ Error: Could not get chat information")
                return
            
            chat_info = response.json()['result']
            channel_title = chat_info.get('title', 'Unknown Channel')
            
            # Setup channel in database
            self.db.add_channel(str(chat_id), channel_title)
            self.db.add_admin(str(chat_id), user_id, "user", "User", user_id)
            
            success_text = f"""
✅ <b>Setup Complete!</b>

<b>Channel:</b> {channel_title}
<b>Auto-deletion:</b> 🟢 Enabled
<b>Deletion Interval:</b> 5 minutes
<b>Allowed Admins:</b> 1 (you)

You can now manage settings and add more allowed admins using the menus.
            """
            self.send_message(chat_id, success_text)
            
        except Exception as e:
            logger.error(f"Error in setup: {e}")
            self.send_message(chat_id, "❌ Error during setup")
    
    def send_admin_management(self, chat_id):
        """Send admin management menu"""
        text = """
👥 <b>Admin Management</b>

Manage users who are allowed to post messages without deletion.

<b>Options:</b>
• <b>Add Admin</b> - Add a user to allowed list
• <b>Remove Admin</b> - Remove a user from allowed list  
• <b>List Admins</b> - View current allowed users

Choose an option below:
        """
        keyboard = {
            'inline_keyboard': [
                [{'text': '➕ Add Admin', 'callback_data': 'add_admin'}],
                [{'text': '➖ Remove Admin', 'callback_data': 'remove_admin'}],
                [{'text': '📋 List Admins', 'callback_data': 'list_admins'}],
                [{'text': '🔙 Back to Main', 'callback_data': 'main_menu'}]
            ]
        }
        self.send_message(chat_id, text, keyboard)
    
    def prompt_add_admin(self, chat_id, user_id):
        """Prompt user to add admin"""
        text = """
➕ <b>Add Admin</b>

To add an admin, please reply with the username:

<b>Format:</b> <code>@username</code> or <code>username</code>

<b>Example:</b> <code>@johnsmith</code> or <code>johnsmith</code>

Please reply with the username now:
        """
        self.set_user_state(user_id, 'waiting_admin_username')
        keyboard = {
            'inline_keyboard': [
                [{'text': '🔙 Back', 'callback_data': 'manage_admins'}]
            ]
        }
        self.send_message(chat_id, text, keyboard)
    
    def process_add_admin(self, chat_id, user_id, username_input):
        """Process adding an admin"""
        username = username_input.lstrip('@')
        
        # Get channel admins to find the user
        admins = self.get_chat_administrators(chat_id)
        target_user = None
        
        for admin in admins:
            admin_user = admin['user']
            if (admin_user.get('username', '').lower() == username.lower() or 
                admin_user.get('first_name', '').lower() == username.lower()):
                target_user = admin_user
                break
        
        if not target_user:
            self.send_message(chat_id, f"❌ User @{username} not found or not an admin in this channel.")
            self.clear_user_state(user_id)
            return
        
        # Add to database
        full_name = f"{target_user.get('first_name', '')} {target_user.get('last_name', '')}".strip()
        self.db.add_admin(str(chat_id), target_user['id'], target_user.get('username', ''), full_name, user_id)
        
        success_text = f"""
✅ <b>Admin Added Successfully!</b>

<b>User:</b> {full_name} (@{target_user.get('username', 'N/A')})
<b>ID:</b> <code>{target_user['id']}</code>

This user can now post messages without them being deleted.
        """
        self.clear_user_state(user_id)
        self.send_message(chat_id, success_text)
    
    def prompt_remove_admin(self, chat_id, user_id):
        """Prompt user to remove admin"""
        text = """
➖ <b>Remove Admin</b>

To remove an admin, please reply with the username:

<b>Format:</b> <code>@username</code> or <code>username</code>

<b>Example:</b> <code>@johnsmith</code> or <code>johnsmith</code>

Please reply with the username now:
        """
        self.set_user_state(user_id, 'waiting_remove_admin')
        keyboard = {
            'inline_keyboard': [
                [{'text': '🔙 Back', 'callback_data': 'manage_admins'}]
            ]
        }
        self.send_message(chat_id, text, keyboard)
    
    def process_remove_admin(self, chat_id, user_id, username_input):
        """Process removing an admin"""
        username = username_input.lstrip('@')
        
        # Find admin by username
        admins = self.db.get_admins(str(chat_id))
        target_admin = None
        
        for admin_user_id, admin_username, full_name, added_date in admins:
            if admin_username and admin_username.lower() == username.lower():
                target_admin = (admin_user_id, admin_username, full_name)
                break
        
        if not target_admin:
            self.send_message(chat_id, f"❌ User @{username} not found in allowed admins list.")
            self.clear_user_state(user_id)
            return
        
        # Remove from database
        admin_user_id, admin_username, full_name = target_admin
        self.db.remove_admin(str(chat_id), admin_user_id)
        
        success_text = f"""
✅ <b>Admin Removed Successfully!</b>

<b>User:</b> {full_name} (@{admin_username})

This user's messages will now be auto-deleted.
        """
        self.clear_user_state(user_id)
        self.send_message(chat_id, success_text)
    
    def show_admin_list(self, chat_id):
        """Show list of allowed admins"""
        admins = self.db.get_admins(str(chat_id))
        
        if not admins:
            text = "❌ No allowed admins found.\n\nUse the 'Add Admin' button to add users."
        else:
            text = "✅ <b>Allowed Admins:</b>\n\n"
            for i, (user_id, username, full_name, added_date) in enumerate(admins, 1):
                date_str = datetime.fromisoformat(added_date).strftime('%Y-%m-%d')
                text += f"{i}. <b>{full_name}</b> (@{username})\n   Added: {date_str}\n\n"
        
        keyboard = {
            'inline_keyboard': [
                [{'text': '🔙 Back to Admin Management', 'callback_data': 'manage_admins'}]
            ]
        }
        self.send_message(chat_id, text, keyboard)
    
    def send_settings(self, chat_id):
        """Send settings menu"""
        channel_settings = self.db.get_channel_settings(str(chat_id))
        
        if not channel_settings:
            self.send_message(chat_id, "❌ Bot is not setup in this channel. Please run setup first.")
            return
        
        interval = channel_settings['delete_interval']
        interval_minutes = interval // 60
        
        text = f"""
⚙️ <b>Bot Settings</b>

<b>Deletion Interval:</b> {interval} seconds ({interval_minutes} minutes)

Messages from non-approved users will be deleted after this delay.

Choose an option below to manage settings:
        """
        keyboard = {
            'inline_keyboard': [
                [{'text': f'⏰ Change Interval ({interval_minutes}min)', 'callback_data': 'set_interval'}],
                [{'text': '🔙 Back to Main', 'callback_data': 'main_menu'}]
            ]
        }
        self.send_message(chat_id, text, keyboard)
    
    def prompt_set_interval(self, chat_id, user_id):
        """Prompt user to set deletion interval"""
        text = """
⏰ <b>Deletion Interval</b>

Select how long to wait before deleting messages from non-approved users:

• <b>1 minute</b> - Quick deletion
• <b>5 minutes</b> - Recommended (default)
• <b>10 minutes</b> - More lenient
• <b>15 minutes</b> - Very lenient
• <b>30 minutes</b> - Maximum leniency

Choose an option below:
        """
        keyboard = {
            'inline_keyboard': [
                [{'text': '1 minute', 'callback_data': 'interval_60'}],
                [{'text': '5 minutes', 'callback_data': 'interval_300'}],
                [{'text': '10 minutes', 'callback_data': 'interval_600'}],
                [{'text': '15 minutes', 'callback_data': 'interval_900'}],
                [{'text': '30 minutes', 'callback_data': 'interval_1800'}],
                [{'text': '🔙 Back to Settings', 'callback_data': 'settings'}]
            ]
        }
        self.send_message(chat_id, text, keyboard)
    
    def set_delete_interval(self, chat_id, user_id, interval):
        """Set deletion interval"""
        self.db.update_delete_interval(str(chat_id), interval)
        minutes = interval // 60
        
        success_text = f"""
✅ <b>Deletion Interval Updated!</b>

<b>New Interval:</b> {interval} seconds ({minutes} minutes)

Messages from non-approved users will be deleted after this delay.
        """
        self.send_message(chat_id, success_text)
    
    def send_stats_message(self, chat_id):
        """Send statistics message"""
        text = """
📊 <b>Bot Statistics</b>

<b>Status:</b> 🟢 Running
<b>Service:</b> Auto Delete Bot

Use the main menu to manage your channel settings.
        """
        keyboard = {
            'inline_keyboard': [
                [{'text': '🔙 Back to Main', 'callback_data': 'main_menu'}]
            ]
        }
        self.send_message(chat_id, text, keyboard)

# ==================== DELETION WORKER ====================

class DeletionWorker:
    def __init__(self, bot_handler, db_manager):
        self.bot = bot_handler
        self.db = db_manager
        self.is_running = False
    
    def start(self):
        """Start the deletion worker"""
        self.is_running = True
        
        def worker_loop():
            while self.is_running:
                try:
                    self.process_pending_deletions()
                    time.sleep(10)  # Check every 10 seconds
                except Exception as e:
                    logger.error(f"Error in deletion worker: {e}")
                    time.sleep(30)
        
        thread = threading.Thread(target=worker_loop, daemon=True)
        thread.start()
        logger.info("Deletion worker started")
    
    def process_pending_deletions(self):
        """Process pending message deletions"""
        pending = self.db.get_pending_deletions()
        for deletion_id, channel_id, message_id in pending:
            try:
                if self.bot.delete_message(int(channel_id), message_id):
                    logger.info(f"Successfully deleted message {message_id} from channel {channel_id}")
                self.db.mark_deletion_processed(deletion_id)
            except Exception as e:
                logger.error(f"Error processing deletion {deletion_id}: {e}")

# ==================== MAIN APPLICATION ====================

class AutoDeleteBot:
    def __init__(self, token: str):
        self.token = token
        self.db = DatabaseManager()
        self.bot = TelegramBotHandler(token, self.db)
        self.worker = DeletionWorker(self.bot, self.db)
        self.keep_alive = None
        
        logger.info("Auto Delete Bot initialized successfully")
    
    def start_keep_alive(self):
        """Start keep-alive service"""
        try:
            render_url = os.environ.get('RENDER_EXTERNAL_URL')
            if render_url:
                health_url = f"{render_url}/health"
            else:
                health_url = f"http://localhost:{os.environ.get('PORT', 8080)}/health"
            
            def ping_loop():
                while True:
                    try:
                        requests.get(health_url, timeout=10)
                        logger.info("Keep-alive ping successful")
                    except:
                        pass
                    time.sleep(300)
            
            thread = threading.Thread(target=ping_loop, daemon=True)
            thread.start()
            logger.info("Keep-alive service started")
            return True
        except Exception as e:
            logger.error(f"Failed to start keep-alive: {e}")
            return False
    
    def run_polling(self):
        """Run with polling"""
        def poll_loop():
            offset = 0
            while True:
                try:
                    response = requests.get(
                        f"{self.bot.bot_url}/getUpdates",
                        params={'offset': offset, 'timeout': 30}
                    )
                    if response.status_code == 200:
                        updates = response.json()['result']
                        for update in updates:
                            self.bot.process_update(update)
                            offset = update['update_id'] + 1
                except Exception as e:
                    logger.error(f"Error in polling: {e}")
                    time.sleep(10)
        
        thread = threading.Thread(target=poll_loop, daemon=True)
        thread.start()
        logger.info("Polling started")
    
    def run(self):
        """Start the bot"""
        # Start health check server
        start_health_check()
        
        # Start keep-alive
        self.start_keep_alive()
        
        # Start deletion worker
        self.worker.start()
        
        # Start bot polling
        self.run_polling()
        
        logger.info("🤖 Auto Delete Bot is now running!")
        
        # Keep main thread alive
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")

# ==================== GLOBAL INSTANCE ====================

BOT_INSTANCE = None

# ==================== MAIN EXECUTION ====================

if __name__ == "__main__":
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN environment variable is required!")
        exit(1)
    
    try:
        BOT_INSTANCE = AutoDeleteBot(BOT_TOKEN)
        BOT_INSTANCE.run()
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        exit(1)
