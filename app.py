import os, json, logging, threading, asyncio, hashlib, time, secrets
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from collections import deque
from functools import wraps
import stb_async

# --- CONFIG & LOGGING ---
CONFIG_FILE = os.getenv("CONFIG", "./data/macattack.json")
os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)

app = Flask(__name__)
app.secret_key = secrets.token_urlsafe(32)

SCAN_STATE = {
    "running": False, "total": 0, "checked": 0, "hits": 0, "errors": 0,
    "active_proxies": 0, "queue_size": 0, "start_time": None
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f: return json.load(f)
    return {"portals": [], "proxies": [], "found_macs": [], "settings": {}}

def save_config(config_data):
    with open(CONFIG_FILE, 'w') as f: json.dump(config_data, f, indent=4)

# --- AUTH DECORATOR ---
def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        conf = load_config()
        if conf.get("settings", {}).get("auth_enabled") and not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# --- SCANNER CORE ---
class AsyncScanner:
    def __init__(self, macs, portal, proxies, threads, skip_unlimited):
        self.macs = macs
        self.portal = portal
        self.proxies = deque([{'url': p, 'score': 10} for p in proxies])
        self.threads = threads
        self.skip_unlimited = skip_unlimited
        self.queue = asyncio.Queue()
        self.retry_counts = {}
        self.client = stb_async.AsyncStbClient()
        self.running = True

    async def worker(self):
        while self.running and SCAN_STATE['running']:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except: continue

            if not self.proxies: break
            
            p_obj = self.proxies.popleft()
            try:
                success, data = await self.client.quick_scan(self.portal, item['mac'], p_obj['url'])
                SCAN_STATE['checked'] += 1
                
                if success:
                    p_obj['score'] = min(p_obj['score'] + 1, 20)
                    self.proxies.append(p_obj)
                    
                    is_unl = 'unlimited' in str(data['expiry']).lower()
                    if not (is_unl and self.skip_unlimited):
                        data = await self.client.fetch_details(data, p_obj['url'])
                    
                    data['found_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    SCAN_STATE['hits'] += 1
                    
                    # Persistent Save
                    conf = load_config()
                    conf['found_macs'].append(data)
                    save_config(conf)
                else:
                    self.proxies.append(p_obj)
            except (stb_async.ProxyError):
                p_obj['score'] -= 1
                if p_obj['score'] > 0: self.proxies.append(p_obj)
                
                # RE-QUEUE LOGIK (Der Kern deiner Anfrage)
                retries = self.retry_counts.get(item['mac'], 0)
                if retries < 100:
                    self.retry_counts[item['mac']] = retries + 1
                    await self.queue.put(item)
                else:
                    SCAN_STATE['errors'] += 1
            finally:
                self.queue.task_done()
                SCAN_STATE['active_proxies'] = len(self.proxies)
                SCAN_STATE['queue_size'] = self.queue.qsize()

    async def run(self):
        for m in self.macs: await self.queue.put({'mac': m})
        workers = [asyncio.create_task(self.worker()) for _ in range(self.threads)]
        await self.queue.join()
        await self.client.close()
        SCAN_STATE['running'] = False

def start_scan_thread(macs, portal, proxies, threads, skip_unl):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    scanner = AsyncScanner(macs, portal, proxies, threads, skip_unl)
    loop.run_until_complete(scanner.run())

# --- ROUTES ---
@app.route('/')
def index():
    conf = load_config()
    if conf.get("settings", {}).get("auth_enabled") and not session.get("logged_in"):
        return render_template('setup.html') # Oder Login
    return render_template('index.html', version="3.1-Async")

@app.route('/api/attack/start', methods=['POST'])
@requires_auth
def api_start():
    if SCAN_STATE['running']: return jsonify({"error": "Scan already running"})
    data = request.json
    SCAN_STATE.update({"running": True, "total": len(data['macs']), "checked": 0, "hits": 0, "errors": 0})
    
    t = threading.Thread(target=start_scan_thread, args=(
        data['macs'], data['portal'], data['proxies'], 
        int(data.get('threads', 100)), data.get('skip_unlimited', False)
    ))
    t.start()
    return jsonify({"success": True})

@app.route('/api/attack/status')
def api_status():
    return jsonify(SCAN_STATE)

@app.route('/api/attack/stop', methods=['POST'])
@requires_auth
def api_stop():
    SCAN_STATE['running'] = False
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
