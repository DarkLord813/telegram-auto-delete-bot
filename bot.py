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
    
    def add_admin(self, channel_id, user_id, username, full_name, added_by):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO allowed_admins 
            (channel_id, user_id, username, full_name, added_by, added_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (channel_id, user_id, username, full_name, added_by, datetime.now()))
        self.conn.commit()
    
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

# ==================== TELEGRAM BOT SIMPLE HANDLER ====================

class TelegramBotHandler:
    def __init__(self, token, db_manager):
        self.token = token
        self.db = db_manager
        self.bot_url = f"https://api.telegram.org/bot{token}"
        
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
    
    def send_message(self, chat_id, text, reply_markup=None):
        """Send message to chat"""
        try:
            data = {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'HTML'
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
    
    def handle_command(self, message):
        """Handle bot commands"""
        chat_id = message['chat']['id']
        text = message['text']
        
        if text == '/start':
            self.send_start_message(chat_id)
        elif text == '/setup':
            self.send_setup_message(chat_id)
        elif text == '/stats':
            self.send_stats_message(chat_id)
    
    def handle_callback_query(self, callback_query):
        """Handle callback queries"""
        chat_id = callback_query['message']['chat']['id']
        user_id = callback_query['from']['id']
        data = callback_query['data']
        
        # Answer callback query
        requests.post(f"{self.bot_url}/answerCallbackQuery", json={'callback_query_id': callback_query['id']})
        
        if data == 'setup_bot':
            self.send_setup_message(chat_id)
        elif data == 'confirm_setup':
            self.confirm_setup(chat_id, user_id)
        elif data == 'show_stats':
            self.send_stats_message(chat_id)
    
    def send_start_message(self, chat_id):
        """Send start message"""
        text = """
🤖 <b>Auto Delete Bot</b>

I automatically delete ALL messages except those from specified admins in channels.

<b>Features:</b>
• Auto-delete messages from non-approved users
• Only approved admins can post
• Configurable deletion timing

Use /setup to configure the bot in this channel.
        """
        keyboard = {
            'inline_keyboard': [
                [{'text': '🛠️ Setup Bot', 'callback_data': 'setup_bot'}],
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
3. Start adding allowed admins

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

The bot is now active and will auto-delete messages from non-approved users.
            """
            self.send_message(chat_id, success_text)
            
        except Exception as e:
            logger.error(f"Error in setup: {e}")
            self.send_message(chat_id, "❌ Error during setup")
    
    def send_stats_message(self, chat_id):
        """Send statistics message"""
        # Simple stats for now
        text = """
📊 <b>Bot Statistics</b>

<b>Status:</b> 🟢 Running
<b>Service:</b> Auto Delete Bot

Use /setup to configure the bot in this channel.
        """
        self.send_message(chat_id, text)

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
    
    def run_webhook(self):
        """Run with webhook (for production)"""
        # Set webhook
        webhook_url = f"{os.environ.get('RENDER_EXTERNAL_URL')}/webhook"
        try:
            response = requests.post(
                f"{self.bot.bot_url}/setWebhook",
                json={'url': webhook_url}
            )
            if response.status_code == 200:
                logger.info(f"Webhook set to: {webhook_url}")
            else:
                logger.error("Failed to set webhook")
        except Exception as e:
            logger.error(f"Error setting webhook: {e}")
    
    def run_polling(self):
        """Run with polling (for development)"""
        # Simple polling implementation
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
        
        # Start bot (use polling for simplicity)
        self.run_polling()
        
        logger.info("🤖 Auto Delete Bot is now running!")
        
        # Keep main thread alive
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")

# ==================== MAIN EXECUTION ====================

if __name__ == "__main__":
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN environment variable is required!")
        exit(1)
    
    try:
        bot = AutoDeleteBot(BOT_TOKEN)
        bot.run()
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        exit(1)
