"""
MacAttack-Web v3.0 - Async Web Interface
- Real-time updates via WebSocket
- Persistent state across reloads
- Chunked processing for 300k+ MACs
- Intelligent proxy management
- Password protection and setup wizard
"""
import asyncio
import json
import time
import hashlib
import secrets
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import threading
from functools import wraps

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit
import aiohttp

from app import (
    load_config, save_config, load_state, save_state, add_log,
    ProxyScorer, RetryQueue, generate_mac, load_mac_list,
    process_mac_chunk, generate_unique_mac, estimate_mac_space
)

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(32)  # Generate random secret key
app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24 hours session timeout
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Security configuration
SECURITY_FILE = "security.json"

def load_security():
    """Load security configuration."""
    try:
        with open(SECURITY_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return None

def save_security(security_data):
    """Save security configuration."""
    with open(SECURITY_FILE, 'w') as f:
        json.dump(security_data, f, indent=2)

def hash_password(password: str) -> str:
    """Hash password with salt."""
    salt = secrets.token_hex(32)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return f"{salt}:{pwd_hash.hex()}"

def verify_password(password: str, hashed: str) -> bool:
    """Verify password against hash."""
    try:
        salt, pwd_hash = hashed.split(':')
        return hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000).hex() == pwd_hash
    except:
        return False

def login_required(f):
    """Decorator to require login."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        security = load_security()
        if not security:
            return redirect(url_for('setup'))
        
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        
        return f(*args, **kwargs)
    return decorated_function

def setup_required(f):
    """Decorator to require setup completion."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        security = load_security()
        if not security:
            return redirect(url_for('setup'))
        return f(*args, **kwargs)
    return decorated_function

# Global state
config = load_config()
state = load_state()
proxy_scorer = ProxyScorer()
retry_queue = RetryQueue(config["settings"]["max_retries"])
scanner_task = None
scanner_session = None
mac_list = None

# Load MAC list if available
if Path("macs.txt").exists():
    mac_list = load_mac_list("macs.txt")

# ============== ASYNC SCANNER INTEGRATION ==============

class AsyncScannerManager:
    """Manages the async scanner in a separate thread."""
    
    def __init__(self):
        self.loop = None
        self.thread = None
        self.running = False
        self.session = None
    
    def start(self):
        """Start the async scanner in a separate thread."""
        if self.running:
            return False
        
        self.thread = threading.Thread(target=self._run_scanner_thread, daemon=True)
        self.thread.start()
        return True
    
    def stop(self):
        """Stop the async scanner."""
        if not self.running:
            return False
        
        state["paused"] = True
        self.running = False
        
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._cleanup(), self.loop)
        
        return True
    
    def _run_scanner_thread(self):
        """Run the async scanner in its own event loop."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        try:
            self.loop.run_until_complete(self._run_scanner())
        except Exception as e:
            add_log(state, f"Scanner thread error: {e}", "error")
        finally:
            self.loop.close()
    
    async def _run_scanner(self):
        """Main scanner coroutine."""
        global config, state, proxy_scorer, retry_queue, mac_list
        
        self.running = True
        state["paused"] = False
        
        settings = config["settings"]
        portal_url = config["portal_url"]
        mac_prefix = config["mac_prefix"]
        chunk_size = settings["chunk_size"]
        
        # Setup session
        connector = aiohttp.TCPConnector(limit=settings["max_workers"] * 2, limit_per_host=50)
        timeout = aiohttp.ClientTimeout(total=settings["timeout"] + 10)
        
        self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        
        try:
            add_log(state, f"🚀 Starting async scanner - Portal: {portal_url}", "info")
            self._emit_update()
            
            if not state["session_stats"]["session_start"]:
                state["session_stats"]["session_start"] = datetime.now().isoformat()
            
            mac_index = state.get("mac_index", 0)
            
            while not state["paused"] and self.running:
                # Prepare MAC chunk
                mac_chunk = []
                
                # Priority 1: Retry queue
                while len(mac_chunk) < chunk_size and retry_queue.size() > 0:
                    retry_data = retry_queue.get_next()
                    if retry_data:
                        mac, retry_count, last_proxy, reason = retry_data
                        mac_chunk.append(mac)
                        add_log(state, f"🔄 Retrying MAC {mac} (attempt {retry_count}, reason: {reason})")
                
                # Priority 2: MAC list or random generation
                while len(mac_chunk) < chunk_size:
                    if mac_list and mac_index < len(mac_list):
                        # From list
                        mac = mac_list[mac_index]
                        mac_index += 1
                        
                        # Skip if already tested (for list mode too)
                        if mac not in state["tested_macs"]:
                            mac_chunk.append(mac)
                    else:
                        # Random generation - avoid duplicates
                        mac = generate_unique_mac(mac_prefix, state["tested_macs"])
                        if mac:
                            mac_chunk.append(mac)
                        else:
                            # MAC space might be exhausted
                            total_possible = estimate_mac_space(mac_prefix)
                            tested_count = len(state["tested_macs"])
                            coverage = (tested_count / total_possible * 100) if total_possible > 0 else 0
                            
                            add_log(state, f"⚠️ MAC space exhaustion! Tested {tested_count}/{total_possible} ({coverage:.1f}%)", "warning")
                            
                            # Clear tested MACs if coverage > 90% to allow retesting
                            if coverage > 90:
                                add_log(state, "🔄 Clearing tested MACs cache to allow retesting", "info")
                                state["tested_macs"].clear()
                                mac = generate_mac(mac_prefix)
                                mac_chunk.append(mac)
                            else:
                                break
                
                if not mac_chunk:
                    add_log(state, "✅ All MACs processed", "info")
                    break
                
                # Update state
                state["mac_index"] = mac_index
                state["current_mac"] = mac_chunk[0] if mac_chunk else None
                
                # Process chunk
                add_log(state, f"📦 Processing chunk of {len(mac_chunk)} MACs...")
                self._emit_update()
                
                chunk_hits = await process_mac_chunk(
                    self.session, config, state, proxy_scorer, retry_queue, mac_chunk
                )
                
                if chunk_hits:
                    add_log(state, f"🎯 Found {len(chunk_hits)} hits in chunk!")
                
                # Emit updates
                self._emit_update()
                
                # Auto-save periodically
                if settings.get("auto_save", True) and state["tested"] % 100 == 0:
                    save_config(config)
                    save_state(state)
                
                # Brief pause between chunks
                await asyncio.sleep(0.1)
        
        except Exception as e:
            add_log(state, f"❌ Scanner error: {e}", "error")
        finally:
            await self._cleanup()
            self.running = False
            state["paused"] = True
            self._emit_update()
    
    async def _cleanup(self):
        """Cleanup resources."""
        if self.session:
            await self.session.close()
            self.session = None
        
        # Final save
        save_config(config)
        save_state(state)
    
    def _emit_update(self):
        """Emit state update to web clients."""
        try:
            # Calculate rates
            elapsed = time.time() - (state.get("start_time", time.time()) or time.time())
            test_rate = state["tested"] / elapsed if elapsed > 0 else 0
            hit_rate = (state["hits"] / state["tested"] * 100) if state["tested"] > 0 else 0
            
            # Get proxy stats
            proxy_stats = proxy_scorer.get_stats()
            
            update_data = {
                "tested": state["tested"],
                "hits": state["hits"],
                "current_mac": state.get("current_mac"),
                "current_proxy": state.get("current_proxy"),
                "paused": state["paused"],
                "test_rate": f"{test_rate:.1f}/s",
                "hit_rate": f"{hit_rate:.2f}%",
                "found_macs": state["found_macs"][-10:],  # Last 10 hits
                "logs": state["logs"][-20:],  # Last 20 logs
                "retry_queue_size": retry_queue.size(),
                "proxy_stats": proxy_stats,
                "session_stats": state["session_stats"]
            }
            
            socketio.emit('scanner_update', update_data)
        except Exception as e:
            print(f"Error emitting update: {e}")

scanner_manager = AsyncScannerManager()

# ============== AUTHENTICATION ROUTES ==============

@app.route('/setup')
def setup():
    """Setup wizard for first-time configuration."""
    security = load_security()
    if security:
        return redirect(url_for('login'))
    return render_template('setup.html')

@app.route('/login')
def login():
    """Login page."""
    security = load_security()
    if not security:
        return redirect(url_for('setup'))
    
    if session.get('authenticated'):
        return redirect(url_for('index'))
    
    return render_template('login.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    """Handle login request."""
    security = load_security()
    if not security:
        return jsonify({"error": "Setup required"}), 400
    
    data = request.json
    password = data.get('password', '')
    
    if verify_password(password, security['password_hash']):
        session['authenticated'] = True
        session.permanent = True
        return jsonify({"success": True})
    else:
        return jsonify({"error": "Invalid password"}), 401

@app.route('/api/logout', methods=['POST'])
def api_logout():
    """Handle logout request."""
    session.clear()
    return jsonify({"success": True})

@app.route('/api/setup/security', methods=['POST'])
def setup_security():
    """Setup security configuration."""
    security = load_security()
    if security:
        return jsonify({"error": "Setup already completed"}), 400
    
    data = request.json
    password = data.get('password', '')
    
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    
    security_data = {
        "password_hash": hash_password(password),
        "setup_completed": True,
        "created_at": datetime.now().isoformat()
    }
    
    save_security(security_data)
    session['authenticated'] = True
    session.permanent = True
    
    return jsonify({"success": True})

@app.route('/api/setup/config', methods=['POST'])
def setup_config():
    """Setup initial configuration."""
    if not session.get('authenticated'):
        return jsonify({"error": "Authentication required"}), 401
    
    try:
        new_config = request.json
        
        # Validate required fields
        required_fields = ['portal_url', 'mac_prefix']
        for field in required_fields:
            if field not in new_config:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Update config
        global config
        config.update(new_config)
        config["found_macs"] = []  # Initialize empty found MACs
        save_config(config)
        
        add_log(state, "⚙️ Initial configuration completed", "info")
        
        return jsonify({"success": True})
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============== WEB ROUTES ==============

@app.route('/')
@setup_required
@login_required
def index():
    """Main dashboard."""
    return render_template('index.html')

@app.route('/api/config', methods=['GET'])
@login_required
def get_config():
    """Get current configuration."""
    return jsonify(config)

@app.route('/api/config', methods=['POST'])
@login_required
def update_config():
    """Update configuration."""
    global config
    
    try:
        new_config = request.json
        
        # Validate required fields
        required_fields = ['portal_url', 'mac_prefix']
        for field in required_fields:
            if field not in new_config:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Update config
        config.update(new_config)
        save_config(config)
        
        add_log(state, "⚙️ Configuration updated", "info")
        
        return jsonify({"success": True})
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/state', methods=['GET'])
@login_required
def get_state():
    """Get current state."""
    # Calculate rates
    elapsed = time.time() - (state.get("start_time", time.time()) or time.time())
    test_rate = state["tested"] / elapsed if elapsed > 0 else 0
    hit_rate = (state["hits"] / state["tested"] * 100) if state["tested"] > 0 else 0
    
    return jsonify({
        "tested": state["tested"],
        "hits": state["hits"],
        "current_mac": state.get("current_mac"),
        "current_proxy": state.get("current_proxy"),
        "paused": state["paused"],
        "test_rate": f"{test_rate:.1f}/s",
        "hit_rate": f"{hit_rate:.2f}%",
        "found_macs": state["found_macs"],
        "logs": state["logs"],
        "retry_queue_size": retry_queue.size(),
        "session_stats": state["session_stats"]
    })

@app.route('/api/start', methods=['POST'])
@login_required
def start_scanner():
    """Start the scanner."""
    global state
    
    if not state["start_time"]:
        state["start_time"] = time.time()
    
    if scanner_manager.start():
        add_log(state, "▶️ Scanner started", "info")
        return jsonify({"success": True})
    else:
        return jsonify({"error": "Scanner already running"}), 400

@app.route('/api/stop', methods=['POST'])
@login_required
def stop_scanner():
    """Stop the scanner."""
    if scanner_manager.stop():
        add_log(state, "⏹️ Scanner stopped", "warning")
        return jsonify({"success": True})
    else:
        return jsonify({"error": "Scanner not running"}), 400

@app.route('/api/pause', methods=['POST'])
@login_required
def pause_scanner():
    """Pause/unpause the scanner."""
    state["paused"] = not state["paused"]
    status = "paused" if state["paused"] else "resumed"
    add_log(state, f"⏸️ Scanner {status}", "info")
    return jsonify({"paused": state["paused"]})

@app.route('/api/reset', methods=['POST'])
@login_required
def reset_stats():
    """Reset statistics."""
    global state
    
    reset_type = request.json.get('type', 'stats') if request.json else 'stats'
    
    if reset_type == 'all':
        # Reset everything including tested MACs
        state["tested"] = 0
        state["hits"] = 0
        state["found_macs"] = []
        state["logs"] = []
        state["start_time"] = time.time()
        state["tested_macs"] = set()
        state["session_stats"] = {
            "session_tested": 0,
            "session_hits": 0,
            "session_start": datetime.now().isoformat()
        }
        add_log(state, "🔄 All statistics and tested MACs reset", "info")
    else:
        # Reset only stats, keep tested MACs
        state["tested"] = 0
        state["hits"] = 0
        state["found_macs"] = []
        state["logs"] = []
        state["start_time"] = time.time()
        state["session_stats"] = {
            "session_tested": 0,
            "session_hits": 0,
            "session_start": datetime.now().isoformat()
        }
        add_log(state, "🔄 Statistics reset (tested MACs preserved)", "info")
    
    save_state(state)
    
    return jsonify({"success": True})

@app.route('/api/proxy_stats', methods=['GET'])
@login_required
def get_proxy_stats():
    """Get proxy statistics."""
    return jsonify(proxy_scorer.get_stats())

@app.route('/api/mac_stats', methods=['GET'])
@login_required
def get_mac_stats():
    """Get MAC space statistics."""
    mac_prefix = config.get("mac_prefix", "00:1A:79")
    total_possible = estimate_mac_space(mac_prefix)
    tested_count = len(state.get("tested_macs", set()))
    coverage = (tested_count / total_possible * 100) if total_possible > 0 else 0
    
    return jsonify({
        "mac_prefix": mac_prefix,
        "total_possible": total_possible,
        "tested_count": tested_count,
        "coverage_percent": round(coverage, 2),
        "remaining": total_possible - tested_count
    })

@app.route('/api/export_hits', methods=['GET'])
@login_required
def export_hits():
    """Export found MACs."""
    format_type = request.args.get('format', 'json')
    
    if format_type == 'txt':
        # Simple text format
        lines = []
        for hit in config["found_macs"]:
            de_flag = " [DE]" if hit.get("has_de") else ""
            lines.append(f"{hit['mac']} | {hit['expiry']} | {hit['channels']}ch{de_flag}")
        
        return '\n'.join(lines), 200, {'Content-Type': 'text/plain'}
    
    else:
        # JSON format
        return jsonify(config["found_macs"])

# ============== WEBSOCKET EVENTS ==============

@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    print(f"Client connected: {request.sid}")
    
    # Send initial state
    scanner_manager._emit_update()

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    print(f"Client disconnected: {request.sid}")

@socketio.on('request_update')
def handle_request_update():
    """Handle manual update request."""
    scanner_manager._emit_update()

# ============== MAIN ==============

if __name__ == '__main__':
    print("MacAttack-Web v3.0 - Async Web Interface")
    print("=========================================")
    print(f"Portal: {config['portal_url']}")
    print(f"MAC Prefix: {config['mac_prefix']}")
    print(f"Proxies: {len(config['proxies'])}")
    print(f"Max Workers: {config['settings']['max_workers']}")
    print(f"Chunk Size: {config['settings']['chunk_size']}")
    
    if mac_list:
        print(f"MAC List: {len(mac_list)} MACs loaded")
    
    print("\n🌐 Starting web server on http://localhost:5000")
    print("📊 Dashboard: http://localhost:5000")
    print("🔧 API: http://localhost:5000/api/")
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)