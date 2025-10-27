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
        logger.info(f"Added channel: {channel_title} ({channel_id})")
    
    def update_delete_interval(self, channel_id, interval):
        cursor = self.conn.cursor()
        cursor.execute('UPDATE channels SET delete_interval = ? WHERE channel_id = ?', (interval, channel_id))
        self.conn.commit()
        logger.info(f"Updated interval for {channel_id}: {interval}s")
    
    def add_admin(self, channel_id, user_id, username, full_name, added_by):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO allowed_admins 
            (channel_id, user_id, username, full_name, added_by, added_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (channel_id, user_id, username, full_name, added_by, datetime.now()))
        self.conn.commit()
        logger.info(f"Added admin {username} to channel {channel_id}")
    
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
        logger.info(f"Scheduled deletion for message {message_id} in {channel_id} after {delete_after_seconds}s")
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
    
    def is_channel_setup(self, channel_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT 1 FROM channels WHERE channel_id = ? AND is_active = TRUE', (channel_id,))
        return cursor.fetchone() is not None

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
                logger.info(f"Bot initialized: @{self.bot_info['username']} (ID: {self.bot_info['id']})")
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
            if response.status_code == 200:
                return True
            else:
                logger.error(f"Failed to send message: {response.text}")
                return False
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
            if response.status_code == 200:
                logger.info(f"Successfully deleted message {message_id} from chat {chat_id}")
                return True
            else:
                logger.warning(f"Could not delete message {message_id}: {response.text}")
                return False
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
    
    def get_chat(self, chat_id):
        """Get chat information"""
        try:
            response = requests.get(f"{self.bot_url}/getChat", params={'chat_id': chat_id})
            if response.status_code == 200:
                return response.json()['result']
            return None
        except Exception as e:
            logger.error(f"Error getting chat info: {e}")
            return None
    
    def get_chat_member(self, chat_id, user_id):
        """Get chat member information"""
        try:
            response = requests.get(f"{self.bot_url}/getChatMember", params={'chat_id': chat_id, 'user_id': user_id})
            if response.status_code == 200:
                return response.json()['result']
            return None
        except Exception as e:
            logger.error(f"Error getting chat member: {e}")
            return None
    
    def process_updates(self, updates):
        """Process multiple updates"""
        for update in updates:
            self.process_update(update)
    
    def process_update(self, update):
        """Process incoming update"""
        try:
            logger.info(f"Processing update: {update.get('update_id')}")
            
            # Handle bot being added to a group/channel
            if 'my_chat_member' in update:
                self.handle_chat_member_update(update['my_chat_member'])
                return
            
            # Handle regular messages and callbacks
            if 'message' in update:
                self.handle_message(update['message'])
            elif 'callback_query' in update:
                self.handle_callback_query(update['callback_query'])
                
        except Exception as e:
            logger.error(f"Error processing update: {e}")
    
    def handle_chat_member_update(self, chat_member_update):
        """Handle when bot is added/removed from chats"""
        try:
            chat = chat_member_update['chat']
            new_status = chat_member_update['new_chat_member']['status']
            old_status = chat_member_update['old_chat_member']['status']
            
            chat_id = str(chat['id'])
            chat_title = chat.get('title', 'Unknown')
            chat_type = chat['type']
            
            logger.info(f"Bot status changed in {chat_title} ({chat_id}): {old_status} -> {new_status}")
            
            # Bot was added to a group/channel
            if new_status in ['administrator', 'member'] and old_status == 'left':
                if chat_type in ['group', 'supergroup', 'channel']:
                    self.handle_bot_added(chat_id, chat_title, chat_type)
            
            # Bot was removed from a group/channel
            elif new_status == 'left' and old_status in ['administrator', 'member']:
                logger.info(f"Bot was removed from {chat_title}")
                
        except Exception as e:
            logger.error(f"Error handling chat member update: {e}")
    
    def handle_bot_added(self, chat_id, chat_title, chat_type):
        """Handle when bot is added to a chat"""
        try:
            logger.info(f"Bot was added to {chat_title} ({chat_id})")
            
            # Check if bot has admin permissions (for channels)
            if chat_type == 'channel':
                bot_member = self.get_chat_member(chat_id, self.bot_info['id'])
                if not bot_member or bot_member.get('status') != 'administrator':
                    logger.warning(f"Bot is not admin in channel {chat_title}")
                    self.send_message(chat_id, 
                        "⚠️ <b>Please make me an administrator with delete permissions!</b>\n\n"
                        "I need to be an admin with <b>delete messages</b> permission to work properly.\n"
                        "Use /setup to configure the bot after making me an admin.",
                        parse_mode='HTML')
                    return
            
            # Auto-setup the channel
            self.db.add_channel(str(chat_id), chat_title)
            
            # Send welcome message
            welcome_text = f"""
🤖 <b>Auto Delete Bot has been added to {chat_title}!</b>

I will automatically delete all messages except those from specified admins.

<b>Next steps:</b>
1. Use /setup to complete configuration
2. Add allowed admins using /admins
3. Configure deletion timing using /settings

<b>Important:</b> Make sure I have <b>delete messages</b> permission!
            """
            self.send_message(chat_id, welcome_text)
            
        except Exception as e:
            logger.error(f"Error handling bot added: {e}")
    
    def handle_message(self, message):
        """Handle incoming message"""
        try:
            chat_id = message['chat']['id']
            user_id = message['from']['id'] if 'from' in message else None
            message_id = message['message_id']
            chat_type = message['chat']['type']
            
            # Ignore messages from channels (they don't have 'from' field)
            if not user_id:
                return
            
            # Check user state first (for conversation flow)
            user_state = self.get_user_state(user_id)
            if user_state and 'text' in message:
                self.handle_user_state(user_id, chat_id, message['text'])
                return
            
            # Check if it's a command
            if 'text' in message and message['text'].startswith('/'):
                self.handle_command(message)
                return
            
            # Only process messages from groups/channels where bot is setup
            if chat_type not in ['group', 'supergroup', 'channel']:
                return
            
            # Check if channel is setup
            if not self.db.is_channel_setup(str(chat_id)):
                return
            
            # Check if user is admin
            if self.db.is_admin(str(chat_id), user_id):
                return  # Admin can post freely
            
            # Get channel settings
            channel_settings = self.db.get_channel_settings(str(chat_id))
            if not channel_settings:
                return
            
            # Check message time (only delete messages after bot was added)
            message_date = datetime.fromtimestamp(message['date'])
            if message_date < channel_settings['bot_added_date']:
                return  # Message before bot was added
            
            # Schedule deletion
            delete_interval = channel_settings['delete_interval']
            self.db.schedule_deletion(str(chat_id), message_id, delete_interval)
            logger.info(f"Scheduled deletion for message {message_id} in {chat_id} after {delete_interval}s")
            
        except Exception as e:
            logger.error(f"Error handling message: {e}")
    
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
            self.send_setup_message(chat_id, user_id)
        elif text == '/admins':
            self.send_admin_management(chat_id, user_id)
        elif text == '/settings':
            self.send_settings(chat_id, user_id)
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
            self.send_setup_message(chat_id, user_id)
        elif data == 'confirm_setup':
            self.confirm_setup(chat_id, user_id)
        elif data == 'manage_admins':
            self.send_admin_management(chat_id, user_id)
        elif data == 'add_admin':
            self.prompt_add_admin(chat_id, user_id)
        elif data == 'remove_admin':
            self.prompt_remove_admin(chat_id, user_id)
        elif data == 'list_admins':
            self.show_admin_list(chat_id)
        elif data == 'settings':
            self.send_settings(chat_id, user_id)
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
    
    def send_setup_message(self, chat_id, user_id):
        """Send setup message"""
        # Check if user is admin in the chat
        user_member = self.get_chat_member(chat_id, user_id)
        if not user_member or user_member['status'] not in ['administrator', 'creator']:
            self.send_message(chat_id, "❌ You need to be an administrator in this chat to setup the bot.")
            return
        
        text = """
🛠️ <b>Bot Setup</b>

To setup auto-deletion in this chat:

1. <b>Make sure I'm an admin</b> with <b>Delete Messages</b> permission
2. <b>Click Confirm Setup</b> below
3. <b>Add allowed admins</b> using the admin management menu

I will only delete messages sent after I was added.
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
        try:
            # Get chat info
            chat_info = self.get_chat(chat_id)
            if not chat_info:
                self.send_message(chat_id, "❌ Error: Could not get chat information")
                return
            
            chat_title = chat_info.get('title', 'Unknown Chat')
            
            # Check if bot is admin
            bot_member = self.get_chat_member(chat_id, self.bot_info['id'])
            if not bot_member or bot_member['status'] not in ['administrator', 'creator']:
                self.send_message(chat_id, 
                    "❌ I need to be an <b>administrator</b> with <b>delete messages</b> permission!\n\n"
                    "Please make me an admin first, then try setup again.",
                    parse_mode='HTML')
                return
            
            # Setup channel in database
            self.db.add_channel(str(chat_id), chat_title)
            self.db.add_admin(str(chat_id), user_id, "user", "User", user_id)
            
            success_text = f"""
✅ <b>Setup Complete!</b>

<b>Chat:</b> {chat_title}
<b>Auto-deletion:</b> 🟢 Enabled
<b>Deletion Interval:</b> 5 minutes
<b>Allowed Admins:</b> 1 (you)

You can now manage settings and add more allowed admins using the menus.
            """
            self.send_message(chat_id, success_text)
            
        except Exception as e:
            logger.error(f"Error in setup: {e}")
            self.send_message(chat_id, "❌ Error during setup")
    
    def send_admin_management(self, chat_id, user_id):
        """Send admin management menu"""
        # Check if channel is setup
        if not self.db.is_channel_setup(str(chat_id)):
            self.send_message(chat_id, "❌ Bot is not setup in this chat. Please run /setup first.")
            return
        
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
        
        # Get chat admins to find the user
        admins = self.get_chat_administrators(chat_id)
        target_user = None
        
        for admin in admins:
            admin_user = admin['user']
            if (admin_user.get('username', '').lower() == username.lower() or 
                admin_user.get('first_name', '').lower() == username.lower()):
                target_user = admin_user
                break
        
        if not target_user:
            self.send_message(chat_id, f"❌ User @{username} not found or not an admin in this chat.")
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
    
    def send_settings(self, chat_id, user_id):
        """Send settings menu"""
        if not self.db.is_channel_setup(str(chat_id)):
            self.send_message(chat_id, "❌ Bot is not setup in this chat. Please run /setup first.")
            return
        
        channel_settings = self.db.get_channel_settings(str(chat_id))
        
        if not channel_settings:
            self.send_message(chat_id, "❌ Bot is not setup in this chat. Please run /setup first.")
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

Use the main menu to manage your chat settings.
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
                    time.sleep(5)  # Check every 5 seconds for faster response
                except Exception as e:
                    logger.error(f"Error in deletion worker: {e}")
                    time.sleep(30)
        
        thread = threading.Thread(target=worker_loop, daemon=True)
        thread.start()
        logger.info("Deletion worker started")
    
    def process_pending_deletions(self):
        """Process pending message deletions"""
        try:
            pending = self.db.get_pending_deletions()
            if pending:
                logger.info(f"Processing {len(pending)} pending deletions")
                
            for deletion_id, channel_id, message_id in pending:
                try:
                    if self.bot.delete_message(int(channel_id), message_id):
                        logger.info(f"Successfully deleted message {message_id} from channel {channel_id}")
                    else:
                        logger.warning(f"Failed to delete message {message_id} from channel {channel_id}")
                    
                    self.db.mark_deletion_processed(deletion_id)
                    
                except Exception as e:
                    logger.error(f"Error processing deletion {deletion_id}: {e}")
                    # Mark as processed to avoid infinite retry
                    self.db.mark_deletion_processed(deletion_id)
                    
        except Exception as e:
            logger.error(f"Error in process_pending_deletions: {e}")

# ==================== BOT POLLING MANAGER ====================

class BotPollingManager:
    def __init__(self, bot_handler):
        self.bot = bot_handler
        self.is_running = False
        self.last_update_id = 0
    
    def start_polling(self):
        """Start polling with proper single instance management"""
        self.is_running = True
        
        def polling_loop():
            while self.is_running:
                try:
                    # Get updates with long polling
                    response = requests.get(
                        f"{self.bot.bot_url}/getUpdates",
                        params={
                            'offset': self.last_update_id + 1,
                            'timeout': 30,
                            'allowed_updates': ['message', 'callback_query', 'my_chat_member']
                        },
                        timeout=35  # Slightly more than timeout parameter
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if data['ok']:
                            updates = data['result']
                            if updates:
                                logger.info(f"Received {len(updates)} updates")
                                self.bot.process_updates(updates)
                                # Update the last processed update ID
                                self.last_update_id = max(update['update_id'] for update in updates)
                        else:
                            logger.error(f"Telegram API error: {data}")
                    else:
                        logger.error(f"HTTP error: {response.status_code} - {response.text}")
                        time.sleep(10)  # Wait before retry on HTTP error
                        
                except requests.exceptions.Timeout:
                    # Timeout is normal for long polling
                    continue
                except requests.exceptions.ConnectionError:
                    logger.error("Connection error, retrying in 10 seconds...")
                    time.sleep(10)
                except Exception as e:
                    logger.error(f"Unexpected error in polling: {e}")
                    time.sleep(10)
        
        thread = threading.Thread(target=polling_loop, daemon=True)
        thread.start()
        logger.info("Bot polling started (single instance)")
    
    def stop_polling(self):
        """Stop polling"""
        self.is_running = False

# ==================== MAIN APPLICATION ====================

class AutoDeleteBot:
    def __init__(self, token: str):
        self.token = token
        self.db = DatabaseManager()
        self.bot = TelegramBotHandler(token, self.db)
        self.worker = DeletionWorker(self.bot, self.db)
        self.polling_manager = BotPollingManager(self.bot)
        
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
    
    def run(self):
        """Start the bot"""
        # Start health check server in main thread
        health_thread = Thread(target=run_health_server, daemon=True)
        health_thread.start()
        logger.info("Health server started")
        
        # Start keep-alive
        self.start_keep_alive()
        
        # Start deletion worker
        self.worker.start()
        
        # Start bot polling (single instance)
        self.polling_manager.start_polling()
        
        logger.info("🤖 Auto Delete Bot is now running!")
        
        # Keep main thread alive
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            self.polling_manager.stop_polling()

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
