import requests
import time
import os
import sys
import json
import threading
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, jsonify, request
from threading import Thread
import traceback
import urllib.parse

print("TELEGRAM BOT - ADMIN PROTECTION SYSTEM")
print("Delete Non-Admin Posts + Comment Detection")
print("24/7 Operation with Auto-Restart")
print("=" * 50)

# ==================== INITIALIZATION ====================
print("🔍 Starting initialization...")
print(f"🔍 DEBUG: Python version: {sys.version}")
print(f"🔍 DEBUG: Current directory: {os.getcwd()}")

# Get environment variables
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN environment variable is required!")
    sys.exit(1)

PORT = int(os.environ.get('PORT', 8080))
REDEPLOY_TOKEN = os.environ.get('REDEPLOY_TOKEN', 'default_redeploy_token')

# Parse multiple admin IDs from environment variable
admin_ids_raw = os.environ.get('BOT_OWNER_IDS', '7713987088 7475473197')
print(f"🔍 Raw admin IDs from env: {admin_ids_raw}")

# Parse admin IDs - handle space or newline separated
admin_ids = []
try:
    # Split by any whitespace (space, newline, tab) and filter empty strings
    for admin_id_str in admin_ids_raw.split():
        if admin_id_str.strip():  # Check if not empty after stripping
            admin_id = int(admin_id_str.strip())
            admin_ids.append(admin_id)
            print(f"✅ Parsed admin ID: {admin_id}")
    
    if not admin_ids:
        print("⚠️ No admin IDs found, using defaults")
        admin_ids = [7475473197, 7713987088]
except Exception as e:
    print(f"⚠️ Error parsing admin IDs: {e}, using defaults")
    admin_ids = [7475473197, 7713987088]

print(f"✅ Admin IDs loaded: {admin_ids}")
print(f"✅ Using PORT: {PORT}")
print(f"✅ Redeploy token: {'Set' if REDEPLOY_TOKEN != 'default_redeploy_token' else 'Using default'}")

# Delete time options (in seconds)
DELETE_TIME_OPTIONS = {
    '30s': 30,
    '1m': 60,
    '5m': 300,
    '10m': 600,
    '1h': 3600,
    '2h': 7200,
    '12h': 43200,
    '24h': 86400,
    'never': 0
}

# Health check server
app = Flask(__name__)

# Global bot instance
bot = None

@app.route('/health')
def health_check():
    """Health check endpoint"""
    try:
        bot_status = 'unknown'
        if bot is not None:
            bot_status = 'healthy' if bot.test_connection() else 'unhealthy'
        
        health_status = {
            'status': 'healthy',
            'timestamp': time.time(),
            'service': 'telegram-admin-protection-bot',
            'version': '1.0.0',
            'bot_status': bot_status,
            'checks': {
                'bot': {'status': bot_status, 'message': f'Bot is {bot_status}'},
                'system': {'status': 'healthy', 'message': 'System operational'},
                'database': {'status': 'healthy', 'message': 'Database connected'}
            }
        }
        return jsonify(health_status), 200
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': time.time(),
            'bot_status': 'error'
        }), 500

@app.route('/redeploy', methods=['POST'])
def redeploy_bot():
    """Redeploy endpoint"""
    try:
        auth_token = request.headers.get('Authorization', '')
        is_authorized = auth_token == REDEPLOY_TOKEN
        
        if not is_authorized:
            return jsonify({
                'status': 'error',
                'message': 'Unauthorized access'
            }), 401
        
        print(f"🔄 Redeploy triggered via API")
        
        def delayed_restart():
            time.sleep(3)
            os._exit(0)
        
        restart_thread = threading.Thread(target=delayed_restart, daemon=True)
        restart_thread.start()
        
        return jsonify({
            'status': 'success',
            'message': 'Redeploy initiated successfully',
            'timestamp': datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': time.time()
        }), 500

@app.route('/admin/stats', methods=['GET'])
def get_admin_stats():
    """Get admin system statistics"""
    try:
        auth_token = request.headers.get('Authorization', '')
        is_authorized = auth_token == REDEPLOY_TOKEN
        
        if not is_authorized:
            return jsonify({
                'status': 'error',
                'message': 'Unauthorized access'
            }), 401
        
        if bot is None:
            return jsonify({
                'status': 'error',
                'message': 'Bot not initialized'
            }), 500
        
        stats = bot.get_system_stats()
        
        return jsonify({
            'status': 'success',
            'stats': stats,
            'timestamp': datetime.now().isoformat()
        }), 200
        
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
        'service': 'Telegram Admin Protection Bot',
        'status': 'running',
        'version': '1.0.0',
        'endpoints': {
            'health': '/health',
            'redeploy': '/redeploy (POST)',
            'admin_stats': '/admin/stats (GET)',
            'webhook': '/webhook (POST)'
        },
        'features': [
            'Delete Non-Admin Posts',
            'Comment/Reply Detection',
            'Owner Notifications',
            'Auto-Delete Scheduling with Inline Buttons',
            '24/7 Keep-Alive'
        ]
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """Webhook endpoint for Telegram updates"""
    try:
        if bot is None:
            return jsonify({'status': 'error', 'message': 'Bot not initialized'}), 500
        
        update = request.get_json()
        if update:
            # Process update in a separate thread to avoid blocking
            threading.Thread(target=bot.process_update, args=(update,)).start()
            return 'ok', 200
        else:
            return 'no update', 400
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

def run_flask_server():
    """Run the Flask server"""
    try:
        print(f"🔄 Starting Flask server on port {PORT}")
        app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False, threaded=True)
    except Exception as e:
        print(f"❌ Flask server error: {e}")
        time.sleep(5)
        run_flask_server()

def start_flask_server():
    """Start Flask server in background"""
    def flask_wrapper():
        while True:
            try:
                run_flask_server()
            except Exception as e:
                print(f"❌ Flask server crashed, restarting: {e}")
                time.sleep(10)
    
    t = Thread(target=flask_wrapper, daemon=True)
    t.start()
    print(f"✅ Flask server started on port {PORT}")

# ==================== TELEGRAM BOT CLASS ====================

class TelegramProtectionBot:
    def __init__(self, token, owner_ids):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}/"
        self.owner_ids = owner_ids if isinstance(owner_ids, list) else [owner_ids]
        self.conn = None
        self.bot_username = None
        self.channel_cache = {}
        
        # User states for tracking input
        self.user_states = {}  # user_id -> {'state': 'waiting_for_admin_id', 'data': {}}
        
        print(f"🤖 Bot initialized with token: {token[:10]}...")
        print(f"👑 Bot Owner IDs: {self.owner_ids}")
        self.setup_database()
    
    def setup_database(self):
        """Setup database tables"""
        try:
            self.conn = sqlite3.connect('protection_bot.db', check_same_thread=False)
            cursor = self.conn.cursor()
            
            # Channel admins table (users who can post without deletion)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS channel_admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE,
                    username TEXT,
                    first_name TEXT,
                    added_by INTEGER,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    delete_after_seconds INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1
                )
            ''')
            
            # Bot owners table (stores the owner IDs)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bot_owners (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Initialize bot owners if table is empty
            cursor.execute('SELECT COUNT(*) FROM bot_owners')
            if cursor.fetchone()[0] == 0:
                for owner_id in self.owner_ids:
                    cursor.execute('INSERT OR IGNORE INTO bot_owners (user_id) VALUES (?)', (owner_id,))
                self.conn.commit()
                print(f"✅ Initialized {len(self.owner_ids)} bot owners in database")
            
            # Non-admin posts table (posts to be deleted)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS non_admin_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT,
                    message_id INTEGER,
                    user_id INTEGER,
                    user_name TEXT,
                    delete_after_seconds INTEGER,
                    scheduled_delete_time DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    deleted_at DATETIME,
                    is_active INTEGER DEFAULT 1,
                    post_content TEXT,
                    post_type TEXT,
                    UNIQUE(channel_id, message_id)
                )
            ''')
            
            # Global delete time setting
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS global_settings (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    global_delete_seconds INTEGER DEFAULT 86400
                )
            ''')
            
            # Initialize global settings
            cursor.execute('INSERT OR IGNORE INTO global_settings (id) VALUES (1)')
            
            # Comments/replies tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS comment_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT,
                    original_message_id INTEGER,
                    comment_message_id INTEGER,
                    commenter_id INTEGER,
                    commenter_name TEXT,
                    comment_text TEXT,
                    notified_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Bot stats table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bot_stats (
                    id INTEGER PRIMARY KEY,
                    total_admins_added INTEGER DEFAULT 0,
                    total_posts_deleted INTEGER DEFAULT 0,
                    total_comments_detected INTEGER DEFAULT 0,
                    last_restart DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('INSERT OR IGNORE INTO bot_stats (id) VALUES (1)')
            self.conn.commit()
            print("✅ Database setup complete!")
            
        except Exception as e:
            print(f"❌ Database setup error: {e}")
            raise
    
    def test_connection(self):
        """Test bot connection to Telegram API"""
        try:
            response = requests.get(f"{self.base_url}getMe", timeout=10)
            data = response.json()
            if data.get('ok'):
                bot_info = data['result']
                self.bot_username = bot_info['username']
                print(f"✅ Bot connected: @{bot_info['username']} ({bot_info['first_name']})")
                return True
            else:
                print(f"❌ Bot connection failed: {data.get('description')}")
                return False
        except Exception as e:
            print(f"❌ Connection error: {e}")
            return False
    
    def setup_webhook(self):
        """Setup webhook for Telegram updates"""
        try:
            # Get Render URL
            render_url = os.environ.get('RENDER_EXTERNAL_URL')
            if not render_url:
                print("⚠️ RENDER_EXTERNAL_URL not set, using long polling")
                return False
            
            webhook_url = f"{render_url}/webhook"
            print(f"🔗 Setting webhook to: {webhook_url}")
            
            response = requests.post(
                f"{self.base_url}setWebhook",
                data={'url': webhook_url},
                timeout=10
            )
            
            result = response.json()
            if result.get('ok'):
                print("✅ Webhook set successfully")
                return True
            else:
                print(f"❌ Failed to set webhook: {result.get('description')}")
                return False
                
        except Exception as e:
            print(f"❌ Webhook setup error: {e}")
            return False
    
    def send_message(self, chat_id, text, parse_mode='HTML', reply_markup=None):
        """Send message to Telegram chat"""
        try:
            data = {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': parse_mode
            }
            
            if reply_markup:
                data['reply_markup'] = json.dumps(reply_markup)
            
            response = requests.post(f"{self.base_url}sendMessage", data=data, timeout=10)
            return response.json()
        except Exception as e:
            print(f"❌ Error sending message: {e}")
            return None
    
    def edit_message_text(self, chat_id, message_id, text, parse_mode='HTML', reply_markup=None):
        """Edit message text"""
        try:
            data = {
                'chat_id': chat_id,
                'message_id': message_id,
                'text': text,
                'parse_mode': parse_mode
            }
            
            if reply_markup:
                data['reply_markup'] = json.dumps(reply_markup)
            
            response = requests.post(f"{self.base_url}editMessageText", data=data, timeout=10)
            return response.json()
        except Exception as e:
            print(f"❌ Error editing message: {e}")
            return None
    
    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        """Answer callback query"""
        try:
            data = {
                'callback_query_id': callback_query_id
            }
            
            if text:
                data['text'] = text
            if show_alert:
                data['show_alert'] = show_alert
            
            response = requests.post(f"{self.base_url}answerCallbackQuery", data=data, timeout=10)
            return response.json()
        except Exception as e:
            print(f"❌ Error answering callback: {e}")
            return None
    
    def delete_message(self, chat_id, message_id):
        """Delete a message from chat"""
        try:
            data = {
                'chat_id': chat_id,
                'message_id': message_id
            }
            
            response = requests.post(f"{self.base_url}deleteMessage", data=data, timeout=10)
            result = response.json()
            
            if result.get('ok'):
                print(f"✅ Deleted message {message_id} from {chat_id}")
                # Update stats
                cursor = self.conn.cursor()
                cursor.execute('UPDATE bot_stats SET total_posts_deleted = total_posts_deleted + 1 WHERE id = 1')
                self.conn.commit()
                return True
            else:
                print(f"❌ Failed to delete message: {result.get('description')}")
                return False
        except Exception as e:
            print(f"❌ Error deleting message: {e}")
            return False
    
    def get_chat(self, chat_id):
        """Get chat information"""
        try:
            response = requests.post(f"{self.base_url}getChat", 
                                   data={'chat_id': chat_id}, 
                                   timeout=10)
            result = response.json()
            return result.get('result') if result.get('ok') else None
        except:
            return None
    
    def get_chat_member(self, chat_id, user_id):
        """Get chat member information"""
        try:
            data = {
                'chat_id': chat_id,
                'user_id': user_id
            }
            response = requests.post(f"{self.base_url}getChatMember", data=data, timeout=10)
            result = response.json()
            return result.get('result') if result.get('ok') else None
        except:
            return None
    
    def is_user_admin_in_channel(self, chat_id, user_id):
        """Check if user is admin in a channel"""
        cache_key = f"{chat_id}_{user_id}"
        if cache_key in self.channel_cache:
            return self.channel_cache[cache_key]
        
        try:
            member = self.get_chat_member(chat_id, user_id)
            if member:
                status = member.get('status', '')
                is_admin = status in ['creator', 'administrator']
                self.channel_cache[cache_key] = is_admin
                return is_admin
        except:
            pass
        
        return False
    
    def is_bot_admin_in_channel(self, chat_id):
        """Check if bot is admin in a channel"""
        bot_id = self.get_bot_id()
        if bot_id:
            return self.is_user_admin_in_channel(chat_id, bot_id)
        return False
    
    def get_bot_id(self):
        """Get bot user ID"""
        try:
            response = requests.get(f"{self.base_url}getMe", timeout=5)
            data = response.json()
            if data.get('ok'):
                return data['result']['id']
        except:
            pass
        return None
    
    def process_update(self, update):
        """Process incoming update from webhook"""
        try:
            # Handle callback queries (inline buttons)
            if 'callback_query' in update:
                callback_query = update['callback_query']
                callback_data = callback_query.get('data', '')
                message = callback_query.get('message', {})
                from_user = callback_query.get('from', {})
                
                print(f"🔘 Processing callback: {callback_data}")
                
                # Answer callback query first
                self.answer_callback_query(callback_query['id'])
                
                # Process callback data
                self.process_callback_data(callback_data, message, from_user)
                return
            
            # Handle messages
            if 'message' in update:
                message = update['message']
                chat_id = message['chat']['id']
                user_id = message['from']['id'] if 'from' in message else None
                
                print(f"📩 Received message in chat {chat_id}")
                
                # Check if user is in a state waiting for input
                if user_id and user_id in self.user_states:
                    state_info = self.user_states[user_id]
                    if state_info['state'] == 'waiting_for_admin_id' and 'text' in message:
                        self.process_admin_id_input(message, user_id)
                        return
                
                # Handle commands
                if 'text' in message:
                    text = message['text']
                    
                    if text.startswith('/'):
                        command = text.split(' ')[0].lower()
                        print(f"🔧 Processing command: {command}")
                        
                        if command == '/start':
                            self.handle_start(message)
                        elif command == '/menu':
                            self.show_main_menu(message)
                        elif command == '/help':
                            self.show_help_menu(message)
                        elif command == '/addadmin' and len(text.split()) > 1:
                            # Handle /addadmin command with ID
                            try:
                                admin_id = int(text.split()[1])
                                self.add_admin_direct(message, admin_id)
                            except ValueError:
                                self.send_message(chat_id, "❌ Invalid admin ID. Please use a number.")
                        else:
                            self.show_main_menu(message)
                    else:
                        # Regular text message - show main menu
                        if user_id and self.is_authorized_user(user_id):
                            self.show_main_menu(message)
                
                # Handle non-admin posts in channels/groups
                if 'chat' in message and message['chat']['type'] in ['channel', 'group', 'supergroup']:
                    self.handle_group_channel_message(message)
            
            # Handle channel posts
            elif 'channel_post' in update:
                print("📢 Processing channel post")
                self.handle_channel_post(update['channel_post'])
            
            # Handle message edits
            elif 'edited_message' in update:
                print("📝 Processing edited message")
                self.handle_edited_message(update['edited_message'])
                    
        except Exception as e:
            print(f"❌ Error processing update: {e}")
            traceback.print_exc()
    
    def process_callback_data(self, callback_data, message, from_user):
        """Process callback data from inline buttons"""
        try:
            chat_id = message['chat']['id']
            message_id = message['message_id']
            user_id = from_user['id']
            
            if not self.is_authorized_user(user_id):
                self.edit_message_text(chat_id, message_id, 
                    "❌ You are not authorized to use this bot.\n\n"
                    "Only bot owners can access these controls.",
                    reply_markup=self.get_back_button())
                return
            
            if callback_data == 'main_menu':
                self.show_main_menu_via_callback(chat_id, message_id)
            
            elif callback_data == 'admins_menu':
                self.show_admins_menu(chat_id, message_id)
            
            elif callback_data == 'add_admin':
                self.show_add_admin_menu(chat_id, message_id)
            
            elif callback_data == 'list_admins':
                self.show_list_admins(chat_id, message_id)
            
            elif callback_data == 'remove_admin':
                self.show_remove_admin_menu(chat_id, message_id)
            
            elif callback_data == 'time_menu':
                self.show_time_menu(chat_id, message_id)
            
            elif callback_data.startswith('set_time_'):
                time_key = callback_data.replace('set_time_', '')
                self.set_global_delete_time(chat_id, message_id, time_key, user_id)
            
            elif callback_data.startswith('admin_time_'):
                parts = callback_data.replace('admin_time_', '').split('_')
                if len(parts) == 2:
                    admin_id = int(parts[0])
                    time_key = parts[1]
                    self.set_admin_delete_time(chat_id, message_id, admin_id, time_key, user_id)
            
            elif callback_data.startswith('select_admin_'):
                admin_id = int(callback_data.replace('select_admin_', ''))
                self.show_admin_time_menu(chat_id, message_id, admin_id)
            
            elif callback_data.startswith('delete_admin_'):
                admin_id = int(callback_data.replace('delete_admin_', ''))
                self.delete_admin(chat_id, message_id, admin_id, user_id)
            
            elif callback_data == 'stats_menu':
                self.show_stats(chat_id, message_id)
            
            elif callback_data == 'help_menu':
                self.show_help(chat_id, message_id)
            
            elif callback_data == 'confirm_add_admin':
                self.request_admin_id(chat_id, message_id, user_id)
            
            elif callback_data.startswith('process_admin_id_'):
                target_user_id = int(callback_data.replace('process_admin_id_', ''))
                self.add_admin(chat_id, message_id, target_user_id, user_id)
            
            elif callback_data == 'back':
                self.show_main_menu_via_callback(chat_id, message_id)
            
        except Exception as e:
            print(f"❌ Error processing callback: {e}")
            traceback.print_exc()
    
    def process_admin_id_input(self, message, user_id):
        """Process admin ID input from user"""
        chat_id = message['chat']['id']
        text = message['text'].strip()
        
        # Clear user state
        if user_id in self.user_states:
            del self.user_states[user_id]
        
        try:
            admin_id = int(text)
            # Call the existing add_admin method
            self.add_admin_direct(message, admin_id)
        except ValueError:
            self.send_message(chat_id, 
                f"❌ Invalid input: '{text}'\n\n"
                "Please enter a valid numeric User ID (e.g., 123456789).\n"
                "Use /menu to go back to the main menu.")
    
    def add_admin_direct(self, message, admin_id):
        """Add admin directly from command or text input"""
        chat_id = message['chat']['id']
        user_id = message['from']['id']
        
        if not self.is_authorized_user(user_id):
            self.send_message(chat_id, "❌ You are not authorized to use this command.")
            return
        
        try:
            cursor = self.conn.cursor()
            
            # Check if already an admin
            cursor.execute('SELECT id, first_name FROM channel_admins WHERE user_id = ? AND is_active = 1', (admin_id,))
            existing = cursor.fetchone()
            
            if existing:
                success_text = f"""✅ <b>Already Protected</b>

User ID: {admin_id}
Name: {existing[1]}
Status: Already a protected admin

Their posts will NOT be auto-deleted."""
                
                keyboard = {
                    'inline_keyboard': [
                        [{'text': '⚙️ Manage This Admin', 'callback_data': f'select_admin_{admin_id}'}],
                        [{'text': '📋 View All Admins', 'callback_data': 'list_admins'}],
                        [{'text': '🔙 Main Menu', 'callback_data': 'main_menu'}]
                    ]
                }
                self.send_message(chat_id, success_text, reply_markup=keyboard)
                return
            
            # Add to database
            first_name = f"User{admin_id}"
            cursor.execute('''
                INSERT INTO channel_admins 
                (user_id, first_name, added_by, delete_after_seconds, is_active)
                VALUES (?, ?, ?, 0, 1)
            ''', (admin_id, first_name, user_id))
            
            cursor.execute('UPDATE bot_stats SET total_admins_added = total_admins_added + 1 WHERE id = 1')
            self.conn.commit()
            
            success_text = f"""✅ <b>Admin Added Successfully!</b>

👤 User ID: {admin_id}
👑 Added by: Bot Owner
⏰ Default Delete Time: Never (protected)

📝 <b>What this means:</b>
• This user can now post without auto-deletion
• Their posts are protected
• You can set custom delete time per admin
• Other users' posts will still be deleted"""
            
            keyboard = {
                'inline_keyboard': [
                    [{'text': '⚙️ Set Delete Time for This Admin', 'callback_data': f'select_admin_{admin_id}'}],
                    [{'text': '📋 View All Admins', 'callback_data': 'list_admins'}],
                    [{'text': '➕ Add Another Admin', 'callback_data': 'add_admin'}],
                    [{'text': '🔙 Main Menu', 'callback_data': 'main_menu'}]
                ]
            }
            
            self.send_message(chat_id, success_text, reply_markup=keyboard)
            print(f"✅ Added user {admin_id} as protected admin")
            
            # Also send notification to all bot owners
            for owner_id in self.owner_ids:
                if owner_id != user_id:  # Don't notify the person who added
                    try:
                        notification = f"""👑 <b>New Admin Added</b>

User ID: {admin_id}
Added by: {message['from'].get('first_name', 'A bot owner')}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                        self.send_message(owner_id, notification)
                    except:
                        pass
            
        except Exception as e:
            error_text = f"""❌ <b>Error Adding Admin</b>

Error: {str(e)}

Please try again or contact support."""
            
            keyboard = self.get_back_button()
            self.send_message(chat_id, error_text, reply_markup=keyboard)
            print(f"❌ Error adding admin: {e}")
    
    def handle_start(self, message):
        """Handle /start command"""
        chat_id = message['chat']['id']
        user_id = message['from']['id']
        first_name = message['from'].get('first_name', 'User')
        
        print(f"👋 Handling /start from {first_name} ({user_id})")
        
        if not self.is_authorized_user(user_id):
            self.send_message(chat_id, 
                f"❌ Access Denied\n\n"
                f"Hello {first_name}!\n"
                f"You are not authorized to use this bot.\n"
                f"Only bot owners can access the controls.")
            return
        
        welcome_text = f"""👋 Welcome {first_name}!

🤖 <b>Channel Protection Bot</b>

I protect your channels by:
• 🚫 Auto-deleting posts from non-admins
• 🔔 Notifying about comments/replies
• ⏰ Scheduling deletions with inline buttons

👑 <b>Bot Owners:</b> {len(self.owner_ids)} users
🔧 <b>Status:</b> ✅ Active and ready

Use the buttons below to control the bot:"""
        
        keyboard = self.get_main_menu_keyboard()
        self.send_message(chat_id, welcome_text, reply_markup=keyboard)
    
    def show_main_menu(self, message):
        """Show main menu"""
        chat_id = message['chat']['id']
        user_id = message['from']['id']
        
        if not self.is_authorized_user(user_id):
            self.send_message(chat_id, 
                "❌ You are not authorized to use this bot.\n"
                "Only bot owners can access these controls.")
            return
        
        menu_text = """🤖 <b>Main Menu</b>

Select an option below:"""
        
        keyboard = self.get_main_menu_keyboard()
        self.send_message(chat_id, menu_text, reply_markup=keyboard)
    
    def show_main_menu_via_callback(self, chat_id, message_id):
        """Show main menu via callback"""
        menu_text = """🤖 <b>Main Menu</b>

Select an option below:"""
        
        keyboard = self.get_main_menu_keyboard()
        self.edit_message_text(chat_id, message_id, menu_text, reply_markup=keyboard)
    
    def show_admins_menu(self, chat_id, message_id):
        """Show admins management menu"""
        menu_text = """👑 <b>Admins Management</b>

Manage protected admins:
• Add new admins
• View current admins
• Remove admins
• Set individual delete times

Select an option:"""
        
        keyboard = {
            'inline_keyboard': [
                [{'text': '➕ Add Admin', 'callback_data': 'add_admin'}],
                [{'text': '📋 List Admins', 'callback_data': 'list_admins'}],
                [{'text': '🗑️ Remove Admin', 'callback_data': 'remove_admin'}],
                [{'text': '🔙 Back to Main', 'callback_data': 'main_menu'}]
            ]
        }
        
        self.edit_message_text(chat_id, message_id, menu_text, reply_markup=keyboard)
    
    def show_add_admin_menu(self, chat_id, message_id):
        """Show add admin menu"""
        menu_text = """➕ <b>Add Protected Admin</b>

To add a protected admin, you need their Telegram User ID.

What would you like to do?"""
        
        keyboard = {
            'inline_keyboard': [
                [{'text': '📝 Enter User ID Manually', 'callback_data': 'confirm_add_admin'}],
                [{'text': '❓ How to Get User ID', 'callback_data': 'help_menu'}],
                [{'text': '🔙 Back to Admins Menu', 'callback_data': 'admins_menu'}]
            ]
        }
        
        self.edit_message_text(chat_id, message_id, menu_text, reply_markup=keyboard)
    
    def request_admin_id(self, chat_id, message_id, user_id):
        """Request admin ID from user and set state"""
        menu_text = """📝 <b>Enter User ID</b>

Please send the User ID of the person you want to add as a protected admin.

Format: Just send the number (e.g., 123456789)

<i>Note: You can get User ID using @userinfobot or other Telegram bots.</i>"""
        
        keyboard = self.get_back_button()
        self.edit_message_text(chat_id, message_id, menu_text, reply_markup=keyboard)
        
        # Set user state to waiting for admin ID
        self.user_states[user_id] = {
            'state': 'waiting_for_admin_id',
            'chat_id': chat_id
        }
        print(f"✅ Set state for user {user_id}: waiting_for_admin_id")
    
    def add_admin(self, chat_id, message_id, target_user_id, added_by):
        """Add a new admin (via callback with pre-set ID)"""
        try:
            cursor = self.conn.cursor()
            
            # Check if already an admin
            cursor.execute('SELECT id, first_name FROM channel_admins WHERE user_id = ? AND is_active = 1', (target_user_id,))
            existing = cursor.fetchone()
            
            if existing:
                success_text = f"""✅ <b>Already Protected</b>

User ID: {target_user_id}
Name: {existing[1]}
Status: Already a protected admin

Their posts will NOT be auto-deleted."""
                
                keyboard = {
                    'inline_keyboard': [
                        [{'text': '⚙️ Manage This Admin', 'callback_data': f'select_admin_{target_user_id}'}],
                        [{'text': '📋 View All Admins', 'callback_data': 'list_admins'}],
                        [{'text': '🔙 Back to Main', 'callback_data': 'main_menu'}]
                    ]
                }
                self.edit_message_text(chat_id, message_id, success_text, reply_markup=keyboard)
                return
            
            # Add to database
            first_name = f"User{target_user_id}"
            cursor.execute('''
                INSERT INTO channel_admins 
                (user_id, first_name, added_by, delete_after_seconds, is_active)
                VALUES (?, ?, ?, 0, 1)
            ''', (target_user_id, first_name, added_by))
            
            cursor.execute('UPDATE bot_stats SET total_admins_added = total_admins_added + 1 WHERE id = 1')
            self.conn.commit()
            
            success_text = f"""✅ <b>Admin Added Successfully!</b>

👤 User ID: {target_user_id}
👑 Added by: Bot Owner
⏰ Default Delete Time: Never (protected)

📝 <b>What this means:</b>
• This user can now post without auto-deletion
• Their posts are protected
• You can set custom delete time per admin
• Other users' posts will still be deleted"""
            
            keyboard = {
                'inline_keyboard': [
                    [{'text': '⚙️ Set Delete Time for This Admin', 'callback_data': f'select_admin_{target_user_id}'}],
                    [{'text': '📋 View All Admins', 'callback_data': 'list_admins'}],
                    [{'text': '🔙 Back to Main', 'callback_data': 'main_menu'}]
                ]
            }
            
            self.edit_message_text(chat_id, message_id, success_text, reply_markup=keyboard)
            print(f"✅ Added user {target_user_id} as protected admin")
            
        except Exception as e:
            error_text = f"""❌ <b>Error Adding Admin</b>

Error: {str(e)}

Please try again or contact support."""
            
            keyboard = self.get_back_button()
            self.edit_message_text(chat_id, message_id, error_text, reply_markup=keyboard)
            print(f"❌ Error adding admin: {e}")
    
    def show_list_admins(self, chat_id, message_id):
        """Show list of all protected admins"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT user_id, first_name, delete_after_seconds, added_at 
            FROM channel_admins 
            WHERE is_active = 1 
            ORDER BY added_at DESC
        ''')
        
        admins = cursor.fetchall()
        
        if not admins:
            menu_text = """📭 <b>No Protected Admins</b>

There are no protected admins yet.
Add your first admin using the button below."""
            
            keyboard = {
                'inline_keyboard': [
                    [{'text': '➕ Add First Admin', 'callback_data': 'add_admin'}],
                    [{'text': '🔙 Back to Main', 'callback_data': 'main_menu'}]
                ]
            }
        else:
            admin_list = "👑 <b>Protected Admins</b>\n\n"
            
            for i, admin in enumerate(admins, 1):
                user_id, first_name, delete_seconds, added_at = admin
                added_date = datetime.fromisoformat(added_at).strftime('%Y-%m-%d')
                
                # Format delete time
                if delete_seconds == 0:
                    delete_time = "Never"
                else:
                    delete_time = self.format_seconds(delete_seconds)
                
                admin_list += f"{i}. {first_name}\n"
                admin_list += f"   🆔: {user_id}\n"
                admin_list += f"   ⏰: {delete_time}\n"
                admin_list += f"   📅: {added_date}\n\n"
            
            admin_list += f"<i>Total: {len(admins)} protected admin(s)</i>"
            menu_text = admin_list
            
            # Create buttons for each admin
            keyboard_rows = []
            for admin in admins[:10]:  # Limit to 10 admins for button space
                user_id, first_name, _, _ = admin
                keyboard_rows.append([
                    {'text': f"⚙️ {first_name}", 'callback_data': f'select_admin_{user_id}'}
                ])
            
            keyboard_rows.append([{'text': '🔙 Back to Admins Menu', 'callback_data': 'admins_menu'}])
            
            keyboard = {'inline_keyboard': keyboard_rows}
        
        self.edit_message_text(chat_id, message_id, menu_text, reply_markup=keyboard)
    
    def show_admin_time_menu(self, chat_id, message_id, admin_id):
        """Show time menu for a specific admin"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT first_name, delete_after_seconds FROM channel_admins WHERE user_id = ?', (admin_id,))
        admin = cursor.fetchone()
        
        if not admin:
            menu_text = "❌ Admin not found."
            keyboard = self.get_back_button()
        else:
            first_name, current_seconds = admin
            current_time = self.format_seconds(current_seconds) if current_seconds > 0 else "Never"
            
            menu_text = f"""⚙️ <b>Admin Settings: {first_name}</b>

🆔 User ID: {admin_id}
⏰ Current Delete Time: {current_time}

Select new delete time for this admin:"""
            
            keyboard = {
                'inline_keyboard': [
                    [{'text': '30 Seconds', 'callback_data': f'admin_time_{admin_id}_30s'}],
                    [{'text': '1 Minute', 'callback_data': f'admin_time_{admin_id}_1m'}],
                    [{'text': '5 Minutes', 'callback_data': f'admin_time_{admin_id}_5m'}],
                    [{'text': '10 Minutes', 'callback_data': f'admin_time_{admin_id}_10m'}],
                    [{'text': '1 Hour', 'callback_data': f'admin_time_{admin_id}_1h'}],
                    [{'text': '2 Hours', 'callback_data': f'admin_time_{admin_id}_2h'}],
                    [{'text': '12 Hours', 'callback_data': f'admin_time_{admin_id}_12h'}],
                    [{'text': '24 Hours', 'callback_data': f'admin_time_{admin_id}_24h'}],
                    [{'text': '❌ Never (Protected)', 'callback_data': f'admin_time_{admin_id}_never'}],
                    [{'text': '🗑️ Remove This Admin', 'callback_data': f'delete_admin_{admin_id}'}],
                    [{'text': '🔙 Back to Admin List', 'callback_data': 'list_admins'}]
                ]
            }
        
        self.edit_message_text(chat_id, message_id, menu_text, reply_markup=keyboard)
    
    def set_admin_delete_time(self, chat_id, message_id, admin_id, time_key, user_id):
        """Set delete time for a specific admin"""
        try:
            seconds = DELETE_TIME_OPTIONS.get(time_key, 0)
            
            cursor = self.conn.cursor()
            cursor.execute('SELECT first_name FROM channel_admins WHERE user_id = ?', (admin_id,))
            admin = cursor.fetchone()
            
            if not admin:
                self.edit_message_text(chat_id, message_id, 
                    "❌ Admin not found.",
                    reply_markup=self.get_back_button())
                return
            
            first_name = admin[0]
            cursor.execute('UPDATE channel_admins SET delete_after_seconds = ? WHERE user_id = ?', 
                         (seconds, admin_id))
            self.conn.commit()
            
            time_text = self.format_seconds(seconds) if seconds > 0 else "Never (protected)"
            
            success_text = f"""✅ <b>Delete Time Updated</b>

👤 Admin: {first_name}
🆔 User ID: {admin_id}
⏰ New Delete Time: {time_text}

📝 <b>Effect:</b>
• Posts from this admin will be deleted after {time_text.lower()}
• {f'Posts will be protected for {time_text}' if seconds > 0 else 'Posts will never be deleted'}
• Time applies only to this specific admin"""
            
            keyboard = {
                'inline_keyboard': [
                    [{'text': '⚙️ Back to Admin Settings', 'callback_data': f'select_admin_{admin_id}'}],
                    [{'text': '📋 View All Admins', 'callback_data': 'list_admins'}],
                    [{'text': '🔙 Main Menu', 'callback_data': 'main_menu'}]
                ]
            }
            
            self.edit_message_text(chat_id, message_id, success_text, reply_markup=keyboard)
            print(f"✅ Set delete time for admin {admin_id} to {time_key}")
            
        except Exception as e:
            self.edit_message_text(chat_id, message_id,
                f"❌ Error updating delete time: {str(e)}",
                reply_markup=self.get_back_button())
            print(f"❌ Error setting admin delete time: {e}")
    
    def delete_admin(self, chat_id, message_id, admin_id, user_id):
        """Delete an admin"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('SELECT first_name FROM channel_admins WHERE user_id = ?', (admin_id,))
            admin = cursor.fetchone()
            
            if not admin:
                self.edit_message_text(chat_id, message_id,
                    "❌ Admin not found.",
                    reply_markup=self.get_back_button())
                return
            
            first_name = admin[0]
            cursor.execute('UPDATE channel_admins SET is_active = 0 WHERE user_id = ?', (admin_id,))
            self.conn.commit()
            
            success_text = f"""🗑️ <b>Admin Removed</b>

👤 Admin: {first_name}
🆔 User ID: {admin_id}
⏰ Status: ❌ No longer protected

📝 <b>Effect:</b>
• This user's posts will now be auto-deleted
• They are removed from protected admins list
• Their posts follow global delete time settings"""
            
            keyboard = {
                'inline_keyboard': [
                    [{'text': '📋 View Remaining Admins', 'callback_data': 'list_admins'}],
                    [{'text': '➕ Add New Admin', 'callback_data': 'add_admin'}],
                    [{'text': '🔙 Main Menu', 'callback_data': 'main_menu'}]
                ]
            }
            
            self.edit_message_text(chat_id, message_id, success_text, reply_markup=keyboard)
            print(f"✅ Removed admin {admin_id}")
            
        except Exception as e:
            self.edit_message_text(chat_id, message_id,
                f"❌ Error removing admin: {str(e)}",
                reply_markup=self.get_back_button())
            print(f"❌ Error removing admin: {e}")
    
    def show_remove_admin_menu(self, chat_id, message_id):
        """Show remove admin menu"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT user_id, first_name FROM channel_admins WHERE is_active = 1 ORDER BY first_name')
        admins = cursor.fetchall()
        
        if not admins:
            menu_text = """📭 <b>No Admins to Remove</b>

There are no protected admins to remove.
Add some admins first."""
            
            keyboard = {
                'inline_keyboard': [
                    [{'text': '➕ Add First Admin', 'callback_data': 'add_admin'}],
                    [{'text': '🔙 Back to Admins Menu', 'callback_data': 'admins_menu'}]
                ]
            }
        else:
            menu_text = """🗑️ <b>Remove Admin</b>

Select an admin to remove from protection:

<i>Note: Removing an admin means their posts will be auto-deleted.</i>"""
            
            keyboard_rows = []
            for admin in admins[:10]:  # Limit to 10 for space
                user_id, first_name = admin
                keyboard_rows.append([
                    {'text': f"❌ Remove {first_name}", 'callback_data': f'delete_admin_{user_id}'}
                ])
            
            keyboard_rows.append([{'text': '🔙 Back to Admins Menu', 'callback_data': 'admins_menu'}])
            keyboard = {'inline_keyboard': keyboard_rows}
        
        self.edit_message_text(chat_id, message_id, menu_text, reply_markup=keyboard)
    
    def show_time_menu(self, chat_id, message_id):
        """Show global delete time menu"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT global_delete_seconds FROM global_settings WHERE id = 1')
        result = cursor.fetchone()
        current_seconds = result[0] if result else 86400
        current_time = self.format_seconds(current_seconds)
        
        menu_text = f"""⏰ <b>Global Delete Time Settings</b>

Current setting: {current_time}

This applies to ALL non-admin posts.
Protected admins have individual settings.

Select new global delete time:"""
        
        keyboard = {
            'inline_keyboard': [
                [{'text': '30 Seconds', 'callback_data': 'set_time_30s'}],
                [{'text': '1 Minute', 'callback_data': 'set_time_1m'}],
                [{'text': '5 Minutes', 'callback_data': 'set_time_5m'}],
                [{'text': '10 Minutes', 'callback_data': 'set_time_10m'}],
                [{'text': '1 Hour', 'callback_data': 'set_time_1h'}],
                [{'text': '2 Hours', 'callback_data': 'set_time_2h'}],
                [{'text': '12 Hours', 'callback_data': 'set_time_12h'}],
                [{'text': '24 Hours', 'callback_data': 'set_time_24h'}],
                [{'text': '🔙 Back to Main', 'callback_data': 'main_menu'}]
            ]
        }
        
        self.edit_message_text(chat_id, message_id, menu_text, reply_markup=keyboard)
    
    def set_global_delete_time(self, chat_id, message_id, time_key, user_id):
        """Set global delete time"""
        try:
            seconds = DELETE_TIME_OPTIONS.get(time_key, 86400)
            
            cursor = self.conn.cursor()
            cursor.execute('UPDATE global_settings SET global_delete_seconds = ? WHERE id = 1', (seconds,))
            self.conn.commit()
            
            time_text = self.format_seconds(seconds)
            
            success_text = f"""✅ <b>Global Delete Time Updated</b>

⏰ New Setting: {time_text}

📝 <b>Effect:</b>
• All non-admin posts will be deleted after {time_text.lower()}
• Protected admins' posts follow their individual settings
• New posts will use this setting immediately
• Existing scheduled posts will use their original settings"""
            
            keyboard = {
                'inline_keyboard': [
                    [{'text': '⚙️ Back to Time Settings', 'callback_data': 'time_menu'}],
                    [{'text': '👑 Manage Admin Times', 'callback_data': 'admins_menu'}],
                    [{'text': '🔙 Main Menu', 'callback_data': 'main_menu'}]
                ]
            }
            
            self.edit_message_text(chat_id, message_id, success_text, reply_markup=keyboard)
            print(f"✅ Set global delete time to {time_key}")
            
        except Exception as e:
            self.edit_message_text(chat_id, message_id,
                f"❌ Error updating delete time: {str(e)}",
                reply_markup=self.get_back_button())
            print(f"❌ Error setting global delete time: {e}")
    
    def show_stats(self, chat_id, message_id):
        """Show bot statistics"""
        stats = self.get_system_stats()
        
        cursor = self.conn.cursor()
        cursor.execute('SELECT global_delete_seconds FROM global_settings WHERE id = 1')
        global_time = cursor.fetchone()[0]
        global_time_text = self.format_seconds(global_time)
        
        cursor.execute('SELECT COUNT(*) FROM bot_owners')
        owner_count = cursor.fetchone()[0]
        
        stats_text = f"""📊 <b>Bot Statistics</b>

🤖 Bot: @{self.bot_username or 'N/A'}
👥 Bot Owners: {owner_count}
👑 Protected Admins: {stats.get('active_admins', 0)}
🗑️ Total Posts Deleted: {stats.get('total_posts_deleted', 0)}
💬 Comments Detected: {stats.get('total_comments_detected', 0)}
⏰ Pending Deletions: {stats.get('pending_deletions', 0)}

⚙️ <b>Settings:</b>
• Global Delete Time: {global_time_text}
• Comment Notifications: ✅ Active
• Auto-Deletion: ✅ Active

🛡️ <b>Protection Status:</b>
✅ All systems operational"""
        
        keyboard = {
            'inline_keyboard': [
                [{'text': '⏰ Change Global Time', 'callback_data': 'time_menu'}],
                [{'text': '🔄 Refresh Stats', 'callback_data': 'stats_menu'}],
                [{'text': '🔙 Back to Main', 'callback_data': 'main_menu'}]
            ]
        }
        
        self.edit_message_text(chat_id, message_id, stats_text, reply_markup=keyboard)
    
    def show_help_menu(self, message):
        """Show help menu via command"""
        chat_id = message['chat']['id']
        user_id = message['from']['id']
        
        if not self.is_authorized_user(user_id):
            self.send_message(chat_id, 
                "❌ You are not authorized to use this bot.\n"
                "Only bot owners can access these controls.")
            return
        
        self.show_help(chat_id, None)
    
    def show_help(self, chat_id, message_id):
        """Show help information"""
        help_text = """📚 <b>Channel Protection Bot Help</b>

🤖 <b>About:</b>
I automatically delete posts from non-admins and notify about comments.

👑 <b>Protected Admins:</b>
• Admins' posts are NOT deleted (or deleted after custom time)
• Each admin can have individual delete time
• Use the Admins menu to manage them

⏰ <b>Delete Times:</b>
• Global time applies to all non-admins
• Admin-specific time overrides global for that admin
• Times range from 30 seconds to 24 hours
• "Never" means posts are protected

🔔 <b>Notifications:</b>
• All bot owners get notified about comments
• Notifications include message links
• Comment detection is automatic

⚙️ <b>Setup:</b>
1. Add me as admin to your channel
2. Grant me delete message permission
3. Add trusted users as protected admins
4. Set delete times as needed

❓ <b>How to Get User ID:</b>
Use @userinfobot or forward a message from the user to @getidsbot

💡 <b>Adding Admins:</b>
1. Click "Add Admin" in the menu
2. Click "Enter User ID Manually"
3. Send the numeric User ID (e.g., 123456789)
4. The bot will confirm with success message"""

        keyboard = {
            'inline_keyboard': [
                [{'text': '👑 Manage Admins', 'callback_data': 'admins_menu'}],
                [{'text': '⏰ Set Global Time', 'callback_data': 'time_menu'}],
                [{'text': '🔙 Main Menu', 'callback_data': 'main_menu'}]
            ]
        }
        
        if message_id:
            self.edit_message_text(chat_id, message_id, help_text, reply_markup=keyboard)
        else:
            self.send_message(chat_id, help_text, reply_markup=keyboard)
    
    def handle_group_channel_message(self, message):
        """Handle messages in groups/channels"""
        try:
            chat_id = message['chat']['id']
            
            # Get message info
            message_id = message['message_id']
            
            # Get user who sent the message
            if 'from' in message:
                user_id = message['from']['id']
                user_name = message['from'].get('first_name', 'Unknown')
            elif 'sender_chat' in message:
                sender_chat = message['sender_chat']
                user_id = sender_chat['id']
                user_name = sender_chat.get('title', 'Channel')
            else:
                return
            
            # Check if this is a reply/comment to another message
            if 'reply_to_message' in message:
                self.handle_comment(message)
                return
            
            # Check if user is a protected admin
            cursor = self.conn.cursor()
            cursor.execute('SELECT delete_after_seconds FROM channel_admins WHERE user_id = ? AND is_active = 1', (user_id,))
            admin_result = cursor.fetchone()
            
            if admin_result:
                # User is protected admin - use their specific delete time
                delete_seconds = admin_result[0]
                print(f"✅ Protected admin {user_name} ({user_id}) posted in {chat_id}")
                
                if delete_seconds == 0:
                    print(f"   ⏰ Admin posts are protected - NOT deleting")
                    return
                else:
                    print(f"   ⏰ Admin posts will be deleted after {self.format_seconds(delete_seconds)}")
            else:
                # Non-admin user - use global delete time
                cursor.execute('SELECT global_delete_seconds FROM global_settings WHERE id = 1')
                delete_seconds = cursor.fetchone()[0]
                print(f"⚠️ Non-admin {user_name} ({user_id}) posted in {chat_id} - Will delete after {self.format_seconds(delete_seconds)}")
            
            # If delete_seconds is 0, don't schedule deletion
            if delete_seconds == 0:
                return
            
            # Extract message content
            post_content = self.extract_message_content(message)
            post_type = self.get_message_type(message)
            
            # Schedule deletion
            scheduled_time = datetime.now() + timedelta(seconds=delete_seconds)
            
            cursor.execute('''
                INSERT OR IGNORE INTO non_admin_posts 
                (channel_id, message_id, user_id, user_name, delete_after_seconds, 
                 scheduled_delete_time, post_content, post_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (chat_id, message_id, user_id, user_name, delete_seconds, 
                  scheduled_time, post_content, post_type))
            
            self.conn.commit()
            
        except Exception as e:
            print(f"❌ Error handling group/channel message: {e}")
    
    def handle_channel_post(self, post):
        """Handle posts in channels"""
        self.handle_group_channel_message(post)
    
    def handle_edited_message(self, message):
        """Handle edited messages"""
        self.handle_group_channel_message(message)
    
    def handle_comment(self, message):
        """Handle comments/replies to messages"""
        try:
            chat_id = message['chat']['id']
            
            # Get commenter info
            if 'from' in message:
                commenter_id = message['from']['id']
                commenter_name = message['from'].get('first_name', 'Unknown')
                username = message['from'].get('username', '')
            else:
                return
            
            # Get replied message info
            replied_message = message['reply_to_message']
            original_message_id = replied_message['message_id']
            
            # Get comment text
            comment_text = self.extract_message_content(message)
            
            # Get channel info
            chat_info = self.get_chat(chat_id)
            channel_name = chat_info.get('title', f"Chat {chat_id}") if chat_info else f"Chat {chat_id}"
            channel_username = chat_info.get('username', '')
            
            # Store comment notification
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO comment_notifications 
                (channel_id, original_message_id, comment_message_id, 
                 commenter_id, commenter_name, comment_text, notified_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ''', (chat_id, original_message_id, message['message_id'], 
                  commenter_id, commenter_name, comment_text))
            
            cursor.execute('UPDATE bot_stats SET total_comments_detected = total_comments_detected + 1 WHERE id = 1')
            self.conn.commit()
            
            print(f"💬 Comment detected from {commenter_name} in {channel_name}")
            
            # Generate message link
            message_link = self.generate_message_link(chat_id, original_message_id)
            
            # Notify ALL bot owners
            notification_sent = False
            for owner_id in self.owner_ids:
                try:
                    notification_text = f"""💬 <b>New Comment Detected!</b>

📢 Channel: {channel_name}
{'👤 Username: @' + channel_username if channel_username else '🆔 ID: ' + str(chat_id)}

👤 Commenter: {commenter_name}
{'📛 Username: @' + username if username else '🆔 ID: ' + str(commenter_id)}

💭 Comment: {comment_text[:200] + '...' if len(comment_text) > 200 else comment_text}

🔗 Message Link: {message_link}

⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                    
                    result = self.send_message(owner_id, notification_text)
                    if result and result.get('ok'):
                        notification_sent = True
                        print(f"✅ Comment notification sent to owner {owner_id}")
                except Exception as e:
                    print(f"❌ Error sending notification to owner {owner_id}: {e}")
            
            if notification_sent:
                print(f"✅ Comment notifications sent to {len(self.owner_ids)} owners")
            
        except Exception as e:
            print(f"❌ Error handling comment: {e}")
    
    def extract_message_content(self, message):
        """Extract text content from message"""
        if 'text' in message:
            return message['text']
        elif 'caption' in message:
            return message['caption']
        elif 'sticker' in message:
            return f"Sticker: {message['sticker'].get('emoji', '')}"
        elif 'photo' in message:
            return "[Photo]"
        elif 'video' in message:
            return "[Video]"
        elif 'document' in message:
            return f"Document: {message['document'].get('file_name', '')}"
        elif 'audio' in message:
            return "[Audio]"
        elif 'voice' in message:
            return "[Voice Message]"
        else:
            return "[Media Content]"
    
    def get_message_type(self, message):
        """Get message type"""
        if 'text' in message:
            return 'text'
        elif 'photo' in message:
            return 'photo'
        elif 'video' in message:
            return 'video'
        elif 'document' in message:
            return 'document'
        elif 'sticker' in message:
            return 'sticker'
        elif 'audio' in message:
            return 'audio'
        elif 'voice' in message:
            return 'voice'
        else:
            return 'unknown'
    
    def generate_message_link(self, chat_id, message_id):
        """Generate a link to a message"""
        try:
            chat_info = self.get_chat(chat_id)
            if chat_info and 'username' in chat_info:
                username = chat_info['username']
                return f"https://t.me/{username}/{message_id}"
            else:
                return f"Message ID: {message_id} in Chat: {chat_id}"
        except:
            return f"Message ID: {message_id}"
    
    def format_seconds(self, seconds):
        """Format seconds to human readable time"""
        if seconds < 60:
            return f"{seconds} seconds"
        elif seconds < 3600:
            minutes = seconds // 60
            return f"{minutes} minute{'s' if minutes > 1 else ''}"
        elif seconds < 86400:
            hours = seconds // 3600
            return f"{hours} hour{'s' if hours > 1 else ''}"
        else:
            days = seconds // 86400
            return f"{days} day{'s' if days > 1 else ''}"
    
    def get_main_menu_keyboard(self):
        """Get main menu keyboard"""
        return {
            'inline_keyboard': [
                [{'text': '👑 Manage Admins', 'callback_data': 'admins_menu'}],
                [{'text': '⏰ Set Delete Time', 'callback_data': 'time_menu'}],
                [{'text': '📊 View Stats', 'callback_data': 'stats_menu'}],
                [{'text': '❓ Help', 'callback_data': 'help_menu'}]
            ]
        }
    
    def get_back_button(self):
        """Get back button keyboard"""
        return {
            'inline_keyboard': [
                [{'text': '🔙 Back to Main', 'callback_data': 'main_menu'}]
            ]
        }
    
    def is_authorized_user(self, user_id):
        """Check if user is authorized to use admin commands"""
        return user_id in self.owner_ids
    
    def start_auto_delete_monitor(self):
        """Start monitoring for auto-delete posts"""
        def monitor_posts():
            while True:
                try:
                    self.check_and_delete_posts()
                    time.sleep(30)  # Check every 30 seconds
                except Exception as e:
                    print(f"❌ Auto-delete monitor error: {e}")
                    time.sleep(60)
        
        monitor_thread = threading.Thread(target=monitor_posts, daemon=True)
        monitor_thread.start()
        print("✅ Auto-delete monitoring started!")
    
    def check_and_delete_posts(self):
        """Check for posts that need to be deleted"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT id, channel_id, message_id, user_id, user_name 
                FROM non_admin_posts 
                WHERE is_active = 1 
                AND scheduled_delete_time <= datetime('now')
            ''')
            
            posts_to_delete = cursor.fetchall()
            
            for post in posts_to_delete:
                post_id, channel_id, message_id, user_id, user_name = post
                
                # Try to delete the message
                success = self.delete_message(channel_id, message_id)
                
                if success:
                    cursor.execute('''
                        UPDATE non_admin_posts 
                        SET is_active = 0, deleted_at = datetime('now') 
                        WHERE id = ?
                    ''', (post_id,))
                    self.conn.commit()
                    
                    print(f"✅ Successfully deleted non-admin post from {user_name} ({user_id})")
                    
        except Exception as e:
            print(f"❌ Error checking auto-delete posts: {e}")
    
    def get_system_stats(self):
        """Get system statistics"""
        try:
            cursor = self.conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM channel_admins WHERE is_active = 1')
            active_admins = cursor.fetchone()[0]
            
            cursor.execute('SELECT total_admins_added, total_posts_deleted, total_comments_detected FROM bot_stats WHERE id = 1')
            stats = cursor.fetchone()
            
            cursor.execute('SELECT COUNT(*) FROM non_admin_posts WHERE is_active = 1')
            pending_deletions = cursor.fetchone()[0]
            
            return {
                'active_admins': active_admins,
                'total_admins_added': stats[0] if stats else 0,
                'total_posts_deleted': stats[1] if stats else 0,
                'total_comments_detected': stats[2] if stats else 0,
                'pending_deletions': pending_deletions,
                'bot_username': self.bot_username or 'N/A'
            }
        except Exception as e:
            print(f"❌ Error getting stats: {e}")
            return {'error': str(e)}
    
    def run(self):
        """Initialize and run bot services"""
        # Test connection
        if not self.test_connection():
            print("❌ Bot cannot start. Connection test failed.")
            return False
        
        # Try to setup webhook (for Render)
        self.setup_webhook()
        
        # Start auto-delete monitoring
        self.start_auto_delete_monitor()
        
        print("🤖 Bot services initialized!")
        print("📊 Protection Features Active:")
        print("  • Delete non-admin posts ✅")
        print("  • Comment detection ✅")
        print("  • Owner notifications ✅")
        print("  • Auto-delete scheduling with inline buttons ✅")
        print("\n📋 Available Commands:")
        print("  • /start - Start bot")
        print("  • /menu - Show main menu")
        print("  • /help - Show help")
        print("  • /addadmin <user_id> - Add admin directly")
        print(f"\n👥 Bot Owners: {len(self.owner_ids)} users")
        
        return True

# ==================== MAIN EXECUTION ====================

if __name__ == "__main__":
    print("🚀 Starting Telegram Admin Protection Bot...")
    
    # Start Flask server
    start_flask_server()
    time.sleep(2)
    
    restart_count = 0
    max_restarts = 10
    restart_delay = 10
    
    while restart_count < max_restarts:
        try:
            restart_count += 1
            print(f"\n🔄 Bot start attempt #{restart_count}")
            
            # Create and initialize bot with multiple admin IDs
            bot = TelegramProtectionBot(BOT_TOKEN, admin_ids)
            if bot.run():
                print("✅ Bot services running successfully!")
                
                # Keep the main thread alive
                while True:
                    time.sleep(3600)  # Sleep for 1 hour
                    
            else:
                print("❌ Bot initialization failed")
                break
            
        except KeyboardInterrupt:
            print("\n🛑 Bot stopped by user")
            break
        except Exception as e:
            print(f"💥 Bot crash (#{restart_count}): {e}")
            traceback.print_exc()
            
            if restart_count < max_restarts:
                print(f"🔄 Restarting in {restart_delay} seconds...")
                time.sleep(restart_delay)
                restart_delay = min(restart_delay * 1.5, 120)
            else:
                print(f"❌ Maximum restarts ({max_restarts}) reached.")
                print("🆘 Bot cannot recover. Please check logs.")
                break
    
    print("🔴 Bot service ended")
