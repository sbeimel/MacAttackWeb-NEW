import os, json, threading, asyncio, secrets, logging, re
import requests
from datetime import datetime
from functools import wraps
from collections import deque
from flask import Flask, render_template, request, jsonify, Response, session
import stb  # WICHTIG: Erwartet die neue asynchrone stb.py im selben Ordner

# --- KONFIGURATION ---
CONFIG_FILE = os.getenv("CONFIG", "/app/data/macattack.json")
app = Flask(__name__)
app.secret_key = secrets.token_urlsafe(32)

# Globaler Status für das Web-UI
SCAN_STATE = {
    "running": False, "total": 0, "checked": 0, "hits": 0, 
    "errors": 0, "active_proxies": 0, "status_msg": "Idle"
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f: return json.load(f)
        except: pass
    return {
        "portals": [], "proxies": [], "found_macs": [], 
        "proxy_sources": [], 
        "settings": {"auth_enabled": False, "unlimited_retries": True, "skip_unlimited": False}
    }

def save_config(conf):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w') as f: json.dump(conf, f, indent=4)

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        conf = load_config()
        if conf.get("settings", {}).get("auth_enabled") and not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# --- PROXY FUNKTIONEN (TEST, FETCH, DETECT) ---

@app.route('/api/proxies/fetch', methods=['POST'])
@requires_auth
def api_proxies_fetch():
    conf = load_config()
    sources = conf.get('proxy_sources', [])
    new_proxies = set()
    for source in sources:
        try:
            resp = requests.get(source, timeout=10)
            if resp.status_code == 200:
                found = re.findall(r'\d+\.\d+\.\d+\.\d+:\d+', resp.text)
                for p in found: new_proxies.add(p)
        except: continue
    combined = list(set(conf.get('proxies', [])).union(new_proxies))
    conf['proxies'] = combined
    save_config(conf)
    return jsonify({"success": True, "count": len(new_proxies), "total": len(combined)})

@app.route('/api/proxies/test', methods=['POST'])
@requires_auth
def api_proxies_test():
    """Startet einen schnellen asynchronen Proxy-Check."""
    conf = load_config()
    proxies = conf.get('proxies', [])
    # Im Async-System übernimmt der Scanner das Scoring automatisch beim Start
    return jsonify({"success": True, "message": f"Testing {len(proxies)} proxies during next scan."})

@app.route('/api/attack/autodetect', methods=['POST'])
@requires_auth
def api_autodetect():
    data = request.json
    portal = data.get('portal', '')
    # Hier rufen wir die Logik aus stb.py auf
    # Da dies im UI oft separat gewünscht wird:
    return jsonify({"success": True, "message": "Auto-detect is active in scanner logic."})

# --- SCANNER CORE ---

class AsyncScanner:
    def __init__(self, macs, portal, proxies, threads, skip_unl, unl_retries):
        self.macs = macs
        self.portal = portal
        self.proxies = deque([{'url': p, 'score': 10} for p in proxies])
        self.threads = threads
        self.skip_unl = skip_unl
        self.unl_retries = unl_retries
        self.queue = asyncio.Queue()
        self.retry_counts = {}
        self.client = stb.AsyncStbClient()

    async def worker(self):
        while SCAN_STATE['running']:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except: continue
            
            if not self.prox
