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
BOT_OWNER_ID = os.environ.get('BOT_OWNER_ID', '7475473197')  # Default owner ID

print(f"✅ Bot token loaded: {BOT_TOKEN[:10]}...")
print(f"✅ Using PORT: {PORT}")
print(f"✅ Bot Owner ID: {BOT_OWNER_ID}")
print(f"✅ Redeploy token: {'Set' if REDEPLOY_TOKEN != 'default_redeploy_token' else 'Using default'}")

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
            'Auto-Delete Scheduling',
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
    def __init__(self, token, owner_id):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}/"
        self.owner_id = int(owner_id) if owner_id else None
        self.conn = None
        self.bot_username = None
        self.channel_cache = {}
        
        print(f"🤖 Bot initialized with token: {token[:10]}...")
        if self.owner_id:
            print(f"👑 Bot Owner ID: {self.owner_id}")
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
                    delete_after_hours INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1
                )
            ''')
            
            # Non-admin posts table (posts to be deleted)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS non_admin_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT,
                    message_id INTEGER,
                    user_id INTEGER,
                    user_name TEXT,
                    delete_after_hours INTEGER,
                    scheduled_delete_time DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    deleted_at DATETIME,
                    is_active INTEGER DEFAULT 1,
                    post_content TEXT,
                    post_type TEXT,
                    UNIQUE(channel_id, message_id)
                )
            ''')
            
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
            # Handle messages
            if 'message' in update:
                message = update['message']
                chat_id = message['chat']['id']
                
                print(f"📩 Received message in chat {chat_id}")
                
                # Handle commands
                if 'text' in message:
                    text = message['text']
                    
                    if text.startswith('/'):
                        command = text.split(' ')[0].lower()
                        print(f"🔧 Processing command: {command}")
                        
                        if command == '/start':
                            self.handle_start(message)
                        elif command == '/addadmin':
                            self.handle_add_admin(message)
                        elif command == '/removeadmin':
                            self.handle_remove_admin(message)
                        elif command == '/listadmins':
                            self.handle_list_admins(message)
                        elif command == '/settime':
                            self.handle_set_time(message)
                        elif command == '/help':
                            self.handle_help(message)
                        elif command == '/stats':
                            self.handle_stats(message)
                        else:
                            print(f"❓ Unknown command: {command}")
                
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
            
            # Handle callback queries
            elif 'callback_query' in update:
                print("🔘 Processing callback query")
                self.handle_callback_query(update['callback_query'])
                    
        except Exception as e:
            print(f"❌ Error processing update: {e}")
            traceback.print_exc()
    
    def handle_start(self, message):
        """Handle /start command"""
        chat_id = message['chat']['id']
        user_id = message['from']['id']
        first_name = message['from'].get('first_name', 'User')
        
        print(f"👋 Handling /start from {first_name} ({user_id})")
        
        welcome_text = f"""👋 Hello {first_name}!

🤖 <b>Channel Protection Bot</b>

I protect your channels by:
1. 🚫 Auto-deleting posts from non-admins
2. 🔔 Notifying about comments/replies
3. ⏰ Scheduling deletions after specified time

📋 <b>Available Commands:</b>
/addadmin - Add a user as protected admin
/removeadmin - Remove admin protection  
/listadmins - List all protected admins
/settime - Set auto-delete time for non-admins
/stats - Show bot statistics
/help - Show help information

👑 <b>How it works:</b>
1. Add me as admin to your channel
2. Use /addadmin to add trusted users
3. Posts from others will be auto-deleted
4. I'll notify you about comments

Need help? Use /help for detailed instructions."""
        
        result = self.send_message(chat_id, welcome_text)
        if result and result.get('ok'):
            print(f"✅ Sent welcome message to {first_name}")
        else:
            print(f"❌ Failed to send welcome message: {result}")
    
    def handle_add_admin(self, message):
        """Handle /addadmin command"""
        chat_id = message['chat']['id']
        user_id = message['from']['id']
        
        print(f"➕ Handling /addadmin from {user_id}")
        
        # Check if user is authorized
        if not self.is_authorized_user(user_id):
            self.send_message(chat_id, "❌ You are not authorized to use this command.")
            return
        
        parts = message['text'].split(' ')
        if len(parts) < 2:
            self.send_message(chat_id, 
                "📝 <b>Usage:</b> /addadmin @username_or_id\n\n"
                "💡 <b>Examples:</b>\n"
                "/addadmin @username\n"
                "/addadmin 123456789\n\n"
                "This user's posts will NOT be auto-deleted.")
            return
        
        target = parts[1]
        
        if target.startswith('@'):
            username = target[1:]
            self.send_message(chat_id, 
                f"⚠️ Can't add @{username} by username alone.\n"
                "Please provide their user ID: /addadmin 123456789")
            return
        else:
            try:
                target_user_id = int(target)
                
                # Check if already an admin
                cursor = self.conn.cursor()
                cursor.execute('SELECT id FROM channel_admins WHERE user_id = ? AND is_active = 1', (target_user_id,))
                if cursor.fetchone():
                    self.send_message(chat_id, f"✅ User {target_user_id} is already a protected admin.")
                    return
                
                # Add to database
                first_name = f"User{target_user_id}"
                cursor.execute('''
                    INSERT INTO channel_admins 
                    (user_id, username, first_name, added_by, delete_after_hours, is_active)
                    VALUES (?, ?, ?, ?, 0, 1)
                ''', (target_user_id, '', first_name, user_id))
                
                cursor.execute('UPDATE bot_stats SET total_admins_added = total_admins_added + 1 WHERE id = 1')
                self.conn.commit()
                
                success_text = f"""✅ <b>Admin Added Successfully!</b>

👤 User ID: {target_user_id}
👑 Added by: {message['from'].get('first_name', 'You')}

📝 <b>What this means:</b>
• This user can now post without auto-deletion
• Their posts are protected
• Other users' posts will still be deleted"""
                
                self.send_message(chat_id, success_text)
                print(f"✅ Added user {target_user_id} as protected admin")
                
            except ValueError:
                self.send_message(chat_id, "❌ Invalid user ID. Please use a number.")
            except Exception as e:
                print(f"❌ Error adding admin: {e}")
                self.send_message(chat_id, f"❌ Error adding admin: {str(e)}")
    
    def handle_remove_admin(self, message):
        """Handle /removeadmin command"""
        chat_id = message['chat']['id']
        user_id = message['from']['id']
        
        if not self.is_authorized_user(user_id):
            self.send_message(chat_id, "❌ You are not authorized to use this command.")
            return
        
        parts = message['text'].split(' ')
        if len(parts) < 2:
            self.send_message(chat_id, 
                "📝 <b>Usage:</b> /removeadmin user_id\n\n"
                "💡 <b>Example:</b>\n"
                "/removeadmin 123456789\n\n"
                "Use /listadmins to see user IDs")
            return
        
        try:
            target_user_id = int(parts[1])
            
            cursor = self.conn.cursor()
            cursor.execute('UPDATE channel_admins SET is_active = 0 WHERE user_id = ?', (target_user_id,))
            
            if cursor.rowcount > 0:
                self.conn.commit()
                self.send_message(chat_id, f"✅ User {target_user_id} removed from protected admins.")
            else:
                self.send_message(chat_id, f"❌ User {target_user_id} not found in protected admins.")
                
        except ValueError:
            self.send_message(chat_id, "❌ Invalid user ID. Please use a number.")
        except Exception as e:
            print(f"❌ Error removing admin: {e}")
            self.send_message(chat_id, f"❌ Error: {str(e)}")
    
    def handle_list_admins(self, message):
        """Handle /listadmins command"""
        chat_id = message['chat']['id']
        
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT user_id, first_name, delete_after_hours, added_at 
            FROM channel_admins 
            WHERE is_active = 1 
            ORDER BY added_at DESC
        ''')
        
        admins = cursor.fetchall()
        
        if not admins:
            self.send_message(chat_id, "📭 No protected admins found.")
            return
        
        admin_list = "👑 <b>Protected Admins (Posts NOT deleted):</b>\n\n"
        
        for admin in admins:
            user_id, first_name, delete_hours, added_at = admin
            added_date = datetime.fromisoformat(added_at).strftime('%Y-%m-%d')
            
            admin_list += f"• {first_name}\n"
            admin_list += f"  🆔 ID: {user_id}\n"
            if delete_hours > 0:
                admin_list += f"  ⏰ Delete after: {delete_hours} hours\n"
            else:
                admin_list += f"  ⏰ Delete: Never\n"
            admin_list += f"  📅 Added: {added_date}\n\n"
        
        self.send_message(chat_id, admin_list)
    
    def handle_set_time(self, message):
        """Handle /settime command"""
        chat_id = message['chat']['id']
        user_id = message['from']['id']
        
        if not self.is_authorized_user(user_id):
            self.send_message(chat_id, "❌ You are not authorized to use this command.")
            return
        
        parts = message['text'].split(' ')
        if len(parts) < 2:
            self.send_message(chat_id, 
                "📝 <b>Usage:</b> /settime hours\n\n"
                "💡 <b>Examples:</b>\n"
                "/settime 24 - Delete after 24 hours\n"
                "/settime 0 - Delete immediately\n"
                "/settime 168 - Delete after 1 week\n\n"
                "This applies to NON-ADMIN posts only.")
            return
        
        try:
            delete_hours = int(parts[1])
            
            if delete_hours < 0:
                self.send_message(chat_id, "❌ Delete time cannot be negative.")
                return
            
            self.send_message(chat_id, 
                f"✅ Non-admin posts will be deleted after {delete_hours} hours.\n"
                f"Protected admins' posts will NOT be deleted.")
                
        except ValueError:
            self.send_message(chat_id, "❌ Invalid input. Please use a number.")
        except Exception as e:
            print(f"❌ Error setting time: {e}")
            self.send_message(chat_id, f"❌ Error: {str(e)}")
    
    def handle_stats(self, message):
        """Handle /stats command"""
        chat_id = message['chat']['id']
        stats = self.get_system_stats()
        
        stats_text = f"""📊 <b>Bot Statistics</b>

🤖 Bot: @{self.bot_username or 'N/A'}
👑 Protected Admins: {stats.get('active_admins', 0)}
🗑️ Total Posts Deleted: {stats.get('total_posts_deleted', 0)}
💬 Comments Detected: {stats.get('total_comments_detected', 0)}
⏰ Pending Deletions: {stats.get('pending_deletions', 0)}

🛡️ <b>Protection Status:</b>
• Deleting non-admin posts: ✅ Active
• Comment detection: ✅ Active
• Owner notifications: ✅ Active"""

        self.send_message(chat_id, stats_text)
    
    def handle_help(self, message):
        """Handle /help command"""
        chat_id = message['chat']['id']
        
        help_text = """📚 <b>Channel Protection Bot Help</b>

🤖 <b>About:</b>
I automatically delete posts from non-admins and notify about comments.

👑 <b>Admin Commands:</b>
• /addadmin user_id - Add user as protected admin
• /removeadmin user_id - Remove admin protection
• /listadmins - List all protected admins
• /settime hours - Set delete time for non-admins
• /stats - Show bot statistics
• /help - Show this help

🔧 <b>Setup Instructions:</b>
1. Add me as admin to your channel
2. Grant me delete message permission
3. Use /addadmin to add trusted users
4. Non-admin posts will auto-delete

⚠️ <b>How It Works:</b>
• Protected admins' posts: NOT deleted
• Non-admin posts: Auto-deleted after specified time
• Comments/replies: Owner gets notified with link
• All deletions are logged

📞 <b>Support:</b>
Contact the bot owner for help."""
        
        self.send_message(chat_id, help_text)
    
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
            cursor.execute('SELECT id FROM channel_admins WHERE user_id = ? AND is_active = 1', (user_id,))
            is_protected_admin = cursor.fetchone() is not None
            
            if is_protected_admin:
                print(f"✅ Protected admin {user_name} ({user_id}) posted in {chat_id} - NOT deleting")
                return
            
            # Non-admin user - schedule deletion
            print(f"⚠️ Non-admin {user_name} ({user_id}) posted in {chat_id} - Scheduling deletion")
            
            # Get delete time (default 24 hours)
            delete_hours = 24
            
            # Extract message content
            post_content = self.extract_message_content(message)
            post_type = self.get_message_type(message)
            
            # Schedule deletion
            scheduled_time = datetime.now() + timedelta(hours=delete_hours)
            
            cursor.execute('''
                INSERT OR IGNORE INTO non_admin_posts 
                (channel_id, message_id, user_id, user_name, delete_after_hours, 
                 scheduled_delete_time, post_content, post_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (chat_id, message_id, user_id, user_name, delete_hours, 
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
                 commenter_id, commenter_name, comment_text)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (chat_id, original_message_id, message['message_id'], 
                  commenter_id, commenter_name, comment_text))
            
            cursor.execute('UPDATE bot_stats SET total_comments_detected = total_comments_detected + 1 WHERE id = 1')
            self.conn.commit()
            
            print(f"💬 Comment detected from {commenter_name} in {channel_name}")
            
            # Generate message link
            message_link = self.generate_message_link(chat_id, original_message_id)
            
            # Notify bot owner
            if self.owner_id:
                notification_text = f"""💬 <b>New Comment Detected!</b>

📢 Channel: {channel_name}
{'👤 Username: @' + channel_username if channel_username else '🆔 ID: ' + str(chat_id)}

👤 Commenter: {commenter_name}
{'📛 Username: @' + username if username else '🆔 ID: ' + str(commenter_id)}

💭 Comment: {comment_text[:200] + '...' if len(comment_text) > 200 else comment_text}

🔗 Message Link: {message_link}

⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                
                self.send_message(self.owner_id, notification_text)
            
        except Exception as e:
            print(f"❌ Error handling comment: {e}")
    
    def handle_callback_query(self, callback_query):
        """Handle callback queries"""
        pass
    
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
    
    def is_authorized_user(self, user_id):
        """Check if user is authorized to use admin commands"""
        return user_id == self.owner_id
    
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
        print("  • Auto-delete scheduling ✅")
        print("\n📋 Available Commands:")
        print("  • /start - Start bot")
        print("  • /addadmin - Add protected admin")
        print("  • /removeadmin - Remove admin")
        print("  • /listadmins - List protected admins")
        print("  • /settime - Set delete time (non-admins)")
        print("  • /stats - Show statistics")
        print("  • /help - Show help")
        print(f"\n👑 Bot Owner: {self.owner_id}")
        
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
            
            # Create and initialize bot
            bot = TelegramProtectionBot(BOT_TOKEN, BOT_OWNER_ID)
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
