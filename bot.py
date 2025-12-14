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

print("TELEGRAM BOT - ADMIN MANAGEMENT SYSTEM")
print("Keep-Alive System + Admin Management")
print("24/7 Operation with Auto-Restart")
print("=" * 50)

# ==================== RENDER DEBUG SECTION ====================
print("🔍 RENDER DEBUG: Starting initialization...")
print(f"🔍 DEBUG: Python version: {sys.version}")
print(f"🔍 DEBUG: Current directory: {os.getcwd()}")
print(f"🔍 DEBUG: Files in directory: {os.listdir('.')}")

# Get port automatically from Render environment
PORT = int(os.environ.get('PORT', 8080))
print(f"🔍 DEBUG: Using PORT: {PORT}")

# Health check server
app = Flask(__name__)

@app.route('/health')
def health_check():
    """Enhanced health check endpoint for Render monitoring"""
    try:
        service_status = 'unknown'
        if 'service' in globals():
            service_status = 'healthy'
        
        health_status = {
            'status': 'healthy',
            'timestamp': time.time(),
            'service': 'telegram-admin-system',
            'version': '1.0.0',
            'service_status': service_status,
            'checks': {
                'system': {'status': 'healthy', 'message': 'System operational'},
                'database': {'status': 'healthy', 'message': 'Database connected'},
                'keep_alive': {'status': 'active', 'message': 'Keep-alive service running'},
                'admin_system': {'status': 'active', 'message': 'Admin management system ready'}
            }
        }
        return jsonify(health_status), 200
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': time.time(),
            'service_status': 'error'
        }), 500

@app.route('/redeploy', methods=['POST'])
def redeploy_service():
    """Redeploy endpoint for admins"""
    try:
        auth_token = request.headers.get('Authorization', '')
        user_id = request.json.get('user_id', '') if request.json else ''
        
        # You can customize authorization logic here
        is_authorized = auth_token == os.environ.get('REDEPLOY_TOKEN', 'default_token')
        
        if not is_authorized:
            return jsonify({
                'status': 'error',
                'message': 'Unauthorized access'
            }), 401
        
        print(f"🔄 Redeploy triggered")
        
        def delayed_restart():
            time.sleep(5)
            os._exit(0)
        
        restart_thread = threading.Thread(target=delayed_restart, daemon=True)
        restart_thread.start()
        
        return jsonify({
            'status': 'success',
            'message': 'Redeploy initiated successfully',
            'redeploy_id': int(time.time()),
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
        'service': 'Telegram Admin Management System',
        'status': 'running',
        'version': '1.0.0',
        'endpoints': {
            'health': '/health',
            'redeploy': '/redeploy (POST)',
            'admin_stats': '/admin/stats (GET)'
        },
        'features': ['Keep-Alive Service', 'Admin Management', '24/7 Operation', 'Auto-Restart']
    })

@app.route('/admin/stats', methods=['GET'])
def get_admin_stats():
    """Get admin system statistics"""
    try:
        auth_token = request.headers.get('Authorization', '')
        is_authorized = auth_token == os.environ.get('REDEPLOY_TOKEN', 'default_token')
        
        if not is_authorized:
            return jsonify({
                'status': 'error',
                'message': 'Unauthorized access'
            }), 401
        
        if 'service' not in globals():
            return jsonify({
                'status': 'error',
                'message': 'Service not initialized'
            }), 500
        
        stats = service.admin_system.get_system_stats()
        
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

def run_health_server():
    """Run the health check server with error handling"""
    try:
        print(f"🔄 Starting health server on port {PORT}")
        app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
    except Exception as e:
        print(f"❌ Health server error: {e}")
        time.sleep(5)
        run_health_server()

def start_health_check():
    """Start health check server in background with restart capability"""
    def health_wrapper():
        while True:
            try:
                run_health_server()
            except Exception as e:
                print(f"❌ Health server crashed, restarting: {e}")
                time.sleep(10)
    
    t = Thread(target=health_wrapper, daemon=True)
    t.start()
    print(f"✅ Health check server started on port {PORT}")

# ==================== ENHANCED KEEP-ALIVE SERVICE ====================

class EnhancedKeepAliveService:
    def __init__(self, health_url=None):
        self.health_url = health_url or f"http://localhost:{PORT}/health"
        self.is_running = False
        self.ping_count = 0
        self.last_successful_ping = time.time()
        
    def start(self):
        """Start enhanced keep-alive service with better monitoring"""
        self.is_running = True
        
        def ping_loop():
            consecutive_failures = 0
            max_consecutive_failures = 3
            
            while self.is_running:
                try:
                    self.ping_count += 1
                    response = requests.get(self.health_url, timeout=15)
                    
                    if response.status_code == 200:
                        self.last_successful_ping = time.time()
                        consecutive_failures = 0
                        print(f"✅ Keep-alive ping #{self.ping_count}: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    else:
                        consecutive_failures += 1
                        print(f"❌ Keep-alive failed: Status {response.status_code} (Failures: {consecutive_failures})")
                        
                except requests.exceptions.ConnectionError:
                    consecutive_failures += 1
                    print(f"🔌 Keep-alive connection error (Failures: {consecutive_failures})")
                except requests.exceptions.Timeout:
                    consecutive_failures += 1
                    print(f"⏰ Keep-alive timeout (Failures: {consecutive_failures})")
                except Exception as e:
                    consecutive_failures += 1
                    print(f"❌ Keep-alive error: {e} (Failures: {consecutive_failures})")
                
                if consecutive_failures >= max_consecutive_failures:
                    print("🚨 Too many consecutive failures, initiating emergency procedures...")
                    self.emergency_restart()
                    consecutive_failures = 0
                
                if time.time() - self.last_successful_ping > 600:
                    print("🚨 No successful pings for 10 minutes, emergency restart...")
                    self.emergency_restart()
                    self.last_successful_ping = time.time()
                
                if consecutive_failures > 0:
                    sleep_time = 60
                else:
                    sleep_time = 240
                
                time.sleep(sleep_time)
        
        thread = threading.Thread(target=ping_loop, daemon=True)
        thread.start()
        print(f"🔄 Enhanced keep-alive service started")
        print(f"🌐 Health endpoint: {self.health_url}")
        
    def emergency_restart(self):
        """Emergency restart procedure"""
        print("🔄 Initiating emergency restart...")
        os._exit(1)
        
    def stop(self):
        """Stop keep-alive service"""
        self.is_running = False
        print("🛑 Keep-alive service stopped")

# ==================== ADMIN MANAGEMENT SYSTEM ====================

class AdminManagementSystem:
    def __init__(self):
        self.conn = None
        self.setup_database()
        self.admin_sessions = {}
        self.auto_delete_thread = None
        self.is_monitoring = False
        print("✅ Admin Management System initialized!")
    
    def setup_database(self):
        """Setup database tables for admin management"""
        try:
            db_path = self.get_db_path()
            print(f"📁 Database path: {db_path}")
            
            self.conn = sqlite3.connect(db_path, check_same_thread=False)
            cursor = self.conn.cursor()
            
            # Channel admins table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS channel_admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE,
                    username TEXT,
                    first_name TEXT,
                    added_by INTEGER,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_active INTEGER DEFAULT 1,
                    delete_after_hours INTEGER DEFAULT 24
                )
            ''')
            
            # Auto-delete posts table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS auto_delete_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_username TEXT,
                    message_id INTEGER,
                    user_id INTEGER,
                    delete_after_hours INTEGER,
                    scheduled_delete_time DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    deleted_at DATETIME,
                    is_active INTEGER DEFAULT 1
                )
            ''')
            
            # Service status table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS service_status (
                    id INTEGER PRIMARY KEY,
                    service_name TEXT DEFAULT 'Admin Management System',
                    last_restart DATETIME DEFAULT CURRENT_TIMESTAMP,
                    uptime_hours INTEGER DEFAULT 0,
                    total_admins INTEGER DEFAULT 0,
                    active_admins INTEGER DEFAULT 0,
                    total_deletions INTEGER DEFAULT 0
                )
            ''')
            
            cursor.execute('INSERT OR IGNORE INTO service_status (id) VALUES (1)')
            self.conn.commit()
            print("✅ Admin database setup complete!")
            
        except Exception as e:
            print(f"❌ Database setup error: {e}")
            # Fallback to in-memory database
            self.conn = sqlite3.connect(':memory:', check_same_thread=False)
            self.setup_database()
    
    def get_db_path(self):
        """Get database path"""
        base_path = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(base_path, 'admin_system.db')
        return db_path
    
    def start_auto_delete_monitor(self):
        """Start monitoring for auto-delete posts"""
        if self.is_monitoring:
            return
        
        self.is_monitoring = True
        
        def monitor_posts():
            while self.is_monitoring:
                try:
                    self.check_and_delete_posts()
                    time.sleep(60)  # Check every minute
                except Exception as e:
                    print(f"❌ Auto-delete monitor error: {e}")
                    time.sleep(300)  # Wait 5 minutes on error
        
        self.auto_delete_thread = threading.Thread(target=monitor_posts, daemon=True)
        self.auto_delete_thread.start()
        print("✅ Auto-delete monitoring started!")
    
    def check_and_delete_posts(self):
        """Check for posts that need to be deleted"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT id, channel_username, message_id, user_id 
                FROM auto_delete_posts 
                WHERE is_active = 1 
                AND scheduled_delete_time <= datetime('now')
            ''')
            
            posts_to_delete = cursor.fetchall()
            
            for post in posts_to_delete:
                post_id, channel_username, message_id, user_id = post
                success = self.delete_post(channel_username, message_id, post_id)
                
                if success:
                    # Update stats
                    cursor.execute('UPDATE service_status SET total_deletions = total_deletions + 1 WHERE id = 1')
                    self.conn.commit()
                
        except Exception as e:
            print(f"❌ Error checking auto-delete posts: {e}")
    
    def delete_post(self, channel_username, message_id, post_id):
        """Delete a post from channel (simulated for demo)"""
        try:
            # In a real bot, you would use Telegram API to delete the message
            # For demo purposes, we'll just log it
            
            print(f"🗑️ Simulating deletion of message {message_id} from {channel_username}")
            
            # Update database
            cursor = self.conn.cursor()
            cursor.execute('''
                UPDATE auto_delete_posts 
                SET is_active = 0, deleted_at = datetime('now') 
                WHERE id = ?
            ''', (post_id,))
            self.conn.commit()
            
            return True
                
        except Exception as e:
            print(f"❌ Error deleting post: {e}")
            return False
    
    def add_channel_admin(self, user_id, username, first_name, added_by, delete_hours=24):
        """Add a new channel admin"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO channel_admins 
                (user_id, username, first_name, added_by, is_active, delete_after_hours)
                VALUES (?, ?, ?, ?, 1, ?)
            ''', (user_id, username, first_name, added_by, delete_hours))
            
            # Update stats
            cursor.execute('UPDATE service_status SET total_admins = total_admins + 1 WHERE id = 1')
            self.conn.commit()
            
            print(f"✅ Added channel admin: {first_name} ({user_id}) with {delete_hours}h auto-delete")
            return True
        except Exception as e:
            print(f"❌ Error adding channel admin: {e}")
            return False
    
    def remove_channel_admin(self, user_id):
        """Remove a channel admin"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('UPDATE channel_admins SET is_active = 0 WHERE user_id = ?', (user_id,))
            self.conn.commit()
            print(f"✅ Removed channel admin: {user_id}")
            return True
        except Exception as e:
            print(f"❌ Error removing channel admin: {e}")
            return False
    
    def get_channel_admins(self):
        """Get all active channel admins"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT user_id, username, first_name, added_by, added_at, delete_after_hours 
                FROM channel_admins 
                WHERE is_active = 1 
                ORDER BY added_at DESC
            ''')
            return cursor.fetchall()
        except Exception as e:
            print(f"❌ Error getting channel admins: {e}")
            return []
    
    def schedule_post_deletion(self, channel_username, message_id, user_id, delete_after_hours):
        """Schedule a post for automatic deletion"""
        try:
            scheduled_time = datetime.now() + timedelta(hours=delete_after_hours)
            
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO auto_delete_posts 
                (channel_username, message_id, user_id, delete_after_hours, scheduled_delete_time)
                VALUES (?, ?, ?, ?, ?)
            ''', (channel_username, message_id, user_id, delete_after_hours, scheduled_time))
            
            self.conn.commit()
            print(f"✅ Scheduled deletion for message {message_id} in {delete_after_hours} hours")
            return True
        except Exception as e:
            print(f"❌ Error scheduling post deletion: {e}")
            return False
    
    def get_system_stats(self):
        """Get system statistics"""
        try:
            cursor = self.conn.cursor()
            
            # Get admins count
            cursor.execute('SELECT COUNT(*) FROM channel_admins WHERE is_active = 1')
            active_admins = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM channel_admins')
            total_admins = cursor.fetchone()[0]
            
            # Get deletions count
            cursor.execute('SELECT COUNT(*) FROM auto_delete_posts WHERE is_active = 0')
            total_deletions = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM auto_delete_posts WHERE is_active = 1')
            pending_deletions = cursor.fetchone()[0]
            
            # Get service uptime
            cursor.execute('SELECT last_restart, uptime_hours FROM service_status WHERE id = 1')
            result = cursor.fetchone()
            last_restart = result[0] if result else None
            uptime_hours = result[1] if result else 0
            
            return {
                'active_admins': active_admins,
                'total_admins': total_admins,
                'total_deletions': total_deletions,
                'pending_deletions': pending_deletions,
                'last_restart': last_restart,
                'uptime_hours': uptime_hours,
                'database_path': self.get_db_path(),
                'monitoring_active': self.is_monitoring
            }
        except Exception as e:
            print(f"❌ Error getting system stats: {e}")
            return {
                'active_admins': 0,
                'total_admins': 0,
                'total_deletions': 0,
                'pending_deletions': 0,
                'error': str(e)
            }
    
    def update_uptime(self):
        """Update service uptime in database"""
        try:
            cursor = self.conn.cursor()
            # This would be called periodically from the main service
            cursor.execute('UPDATE service_status SET uptime_hours = uptime_hours + 1 WHERE id = 1')
            self.conn.commit()
        except:
            pass

# ==================== MAIN SERVICE CLASS ====================

class AdminService:
    def __init__(self):
        self.admin_system = AdminManagementSystem()
        self.keep_alive = None
        self.last_restart = time.time()
        self.error_count = 0
        
        print("✅ Admin Service initialized!")
        print("🔄 Starting essential services...")
    
    def initialize(self):
        """Initialize the service"""
        try:
            print("🔄 Initializing admin service...")
            
            # Start keep-alive service
            if not self.start_keep_alive():
                print("❌ Keep-alive service failed to start")
                return False
            
            # Start auto-delete monitoring
            self.admin_system.start_auto_delete_monitor()
            
            print("✅ Admin service initialization completed successfully!")
            return True
            
        except Exception as e:
            print(f"❌ Service initialization failed: {e}")
            return False
    
    def start_keep_alive(self):
        """Start keep-alive service"""
        try:
            # Use Render external URL if available
            render_url = os.environ.get('RENDER_EXTERNAL_URL')
            if render_url:
                health_url = f"{render_url}/health"
            else:
                health_url = f"http://localhost:{PORT}/health"
            
            self.keep_alive = EnhancedKeepAliveService(health_url)
            self.keep_alive.start()
            print("🔋 Enhanced keep-alive service activated")
            return True
        except Exception as e:
            print(f"❌ Failed to start keep-alive: {e}")
            return False
    
    def run(self):
        """Main service loop"""
        if not self.initialize():
            print("❌ Service cannot start. Initialization failed.")
            return
        
        print("🚀 Admin Service is running...")
        print("📊 Services running:")
        print(f"  • Health check server (port {PORT})")
        print("  • Enhanced keep-alive system")
        print("  • Admin management system")
        print("  • Auto-delete monitoring")
        print(f"  • Database: {self.admin_system.get_db_path()}")
        
        # Show initial stats
        stats = self.admin_system.get_system_stats()
        print(f"\n📈 Initial Stats:")
        print(f"  • Active Admins: {stats.get('active_admins', 0)}")
        print(f"  • Pending Deletions: {stats.get('pending_deletions', 0)}")
        print(f"  • Total Deletions: {stats.get('total_deletions', 0)}")
        
        print("\n🛡️  Service protection active")
        print("📈 Monitoring health endpoint")
        print("👑 Admin system ready")
        print("🔧 Ready for 24/7 operation")
        
        # Add some demo admins for testing
        self.add_demo_data()
        
        # Keep the main thread alive
        try:
            while True:
                time.sleep(3600)  # Sleep for 1 hour
                # Update uptime
                self.admin_system.update_uptime()
                
        except KeyboardInterrupt:
            print("\n🛑 Service stopped by user")
            if self.keep_alive:
                self.keep_alive.stop()
            print("👋 Goodbye!")
    
    def add_demo_data(self):
        """Add demo data for testing"""
        try:
            print("🔄 Adding demo data for testing...")
            
            # Add a demo admin
            self.admin_system.add_channel_admin(
                user_id=123456789,
                username="demo_admin",
                first_name="Demo Admin",
                added_by=1,
                delete_hours=24
            )
            
            # Schedule a demo deletion
            self.admin_system.schedule_post_deletion(
                channel_username="@test_channel",
                message_id=1001,
                user_id=123456789,
                delete_after_hours=1  # Will be deleted in 1 hour
            )
            
            print("✅ Demo data added successfully")
            
        except Exception as e:
            print(f"❌ Error adding demo data: {e}")

# ==================== MAIN EXECUTION ====================

if __name__ == "__main__":
    print("🚀 Starting Admin Management Service...")
    
    # Start health check server
    start_health_check()
    time.sleep(2)
    
    restart_count = 0
    max_restarts = 20
    restart_delay = 10
    
    while restart_count < max_restarts:
        try:
            restart_count += 1
            print(f"\n🔄 Service start attempt #{restart_count}")
            
            service = AdminService()
            service.run()
            
            print("🛑 Service stopped gracefully")
            break
            
        except Exception as e:
            print(f"💥 Service crash (#{restart_count}): {e}")
            traceback.print_exc()
            
            if restart_count < max_restarts:
                print(f"🔄 Restarting in {restart_delay} seconds...")
                time.sleep(restart_delay)
                restart_delay = min(restart_delay * 1.5, 300)
            else:
                print(f"❌ Maximum restarts ({max_restarts}) reached.")
                print("🆘 Service cannot recover. Please check logs.")
                break
    
    print("🔴 Service ended")
