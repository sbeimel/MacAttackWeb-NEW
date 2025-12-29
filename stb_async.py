import aiohttp, asyncio, hashlib, json, logging

class AsyncStbClient:
    def __init__(self, timeout=10):
        self.timeout = aiohttp.ClientTimeout(total=20, connect=5)
        self.connector = aiohttp.TCPConnector(ssl=False, limit=0)
        self.session = None

    async def init_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(
                connector=self.connector, timeout=self.timeout,
                headers={'User-Agent': 'Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3'}
            )

    async def _request(self, url, params, headers, proxy):
        try:
            p_url = proxy if "://" in proxy else f"http://{proxy}"
            async with self.session.get(url, params=params, headers=headers, proxy=p_url) as resp:
                if resp.status != 200: return None
                text = await resp.text()
                if not text.strip().startswith('{'): return None
                return json.loads(text)
        except: return None

    async def quick_scan(self, portal_url, mac, proxy):
        """AUTO-DETECT: Probiert verschiedene Endpunkte automatisch."""
        await self.init_session()
        mac = mac.upper()
        sn = hashlib.md5(mac.encode()).hexdigest().upper()[:13]
        dev_id2 = hashlib.sha256(sn.encode()).hexdigest().upper()
        
        base = portal_url.rstrip('/')
        # Liste der gängigen Stalker-Pfade
        endpoints = [f"{base}/server/load.php", f"{base}/portal.php", f"{base}/stalker_portal/server/load.php", f"{base}/c/"]

        for url in endpoints:
            headers = {'Cookie': f'mac={mac}; stid={dev_id2}'}
            params = {'type': 'stb', 'action': 'handshake', 'token': '', 'mac': mac, 'stid': dev_id2}
            data = await self._request(url, params, headers, proxy)
            
            if data and data.get('js', {}).get('token'):
                token = data['js']['token']
                headers['Authorization'] = f"Bearer {token}"
                params = {'type': 'stb', 'action': 'get_profile', 'mac': mac, 'stid': dev_id2}
                profile = await self._request(url, params, headers, proxy)
                
                if profile and profile.get('js'):
                    return True, {
                        'mac': mac, 'portal': portal_url, 'endpoint': url,
                        'expiry': profile['js'].get('phone', 'N/A'),
                        'token': token, 'stid': dev_id2
                    }
        return False, None

    async def fetch_details(self, res, proxy):
        headers = {'Cookie': f"mac={res['mac']}; stid={res['stid']}", 'Authorization': f"Bearer {res['token']}"}
        tasks = [
            self._request(res['endpoint'], {'type': 'itv', 'action': 'get_genres'}, headers, proxy),
            self._request(res['endpoint'], {'type': 'itv', 'action': 'get_all_channels'}, headers, proxy)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        res['genres'] = [g['title'] for g in results[0].get('js', [])] if (not isinstance(results[0], Exception) and results[0]) else []
        res['channels'] = len(results[1].get('js', {}).get('data', [])) if (not isinstance(results[1], Exception) and results[1]) else 0
        return res

    async def close(self):
        if self.session: await self.session.close()
