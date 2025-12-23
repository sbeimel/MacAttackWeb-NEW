"""
STB (Set-Top Box) API Client for Stalker Portals
Handles authentication, token management, and API requests
"""
import requests
from requests.adapters import HTTPAdapter, Retry
from urllib.parse import urlparse, quote
import re
import logging
import time
import hashlib
import json

logger = logging.getLogger("MacAttack.stb")
logger.setLevel(logging.DEBUG)

# Session management
_session = None
_session_created = 0
_SESSION_MAX_AGE = 300


def _get_session():
    """Get or create a requests session with automatic refresh."""
    global _session, _session_created
    current_time = time.time()
    
    if _session is None or (current_time - _session_created) > _SESSION_MAX_AGE:
        if _session is not None:
            try:
                _session.close()
            except:
                pass
        
        _session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
        _session.mount("http://", HTTPAdapter(max_retries=retries))
        _session.mount("https://", HTTPAdapter(max_retries=retries))
        _session_created = current_time
    
    return _session


def _get_proxy_dict(proxy):
    """Convert proxy string to requests proxy dict."""
    if not proxy:
        return None
    return {"http": f"http://{proxy}", "https": f"http://{proxy}"}


def _generate_device_ids(mac):
    """Generate device IDs based on MAC address."""
    serialnumber = hashlib.md5(mac.encode()).hexdigest().upper()
    sn = serialnumber[0:13]
    device_id = hashlib.sha256(sn.encode()).hexdigest().upper()
    device_id2 = hashlib.sha256(mac.encode()).hexdigest().upper()
    hw_version_2 = hashlib.sha1(mac.encode()).hexdigest()
    return sn, device_id, device_id2, hw_version_2


def _get_cookies(mac):
    """Generate cookies for STB emulation."""
    sn, device_id, device_id2, hw_version_2 = _generate_device_ids(mac)
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


def _get_headers(token=None):
    """Generate headers for STB emulation."""
    headers = {
        "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
        "Accept-Encoding": "identity",
        "Accept": "*/*",
        "Connection": "keep-alive",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def detect_portal_type(url, proxy=None):
    """Detect the portal type (portal.php or stalker_portal)."""
    parsed_url = urlparse(url)
    parsed_path = parsed_url.path
    
    if parsed_path.endswith("c"):
        parsed_path = parsed_path[:-1]
    if parsed_path.endswith("c/"):
        parsed_path = parsed_path[:-2]
    
    host = parsed_url.hostname
    port = parsed_url.port or 80
    base_url = f"http://{host}:{port}"
    
    headers = _get_headers()
    proxies = _get_proxy_dict(proxy)
    session = _get_session()
    
    # Check for type portal
    try:
        version_url = f"{base_url}/c/version.js"
        response = session.get(version_url, headers=headers, proxies=proxies, timeout=10)
        if response.status_code == 200:
            match = re.search(r"var ver = ['\"](.*?)['\"];", response.text)
            if match:
                return "portal.php", match.group(1)
    except:
        pass
    
    # Check for stalker_portal
    try:
        version_url = f"{base_url}/stalker_portal/c/version.js"
        response = session.get(version_url, headers=headers, proxies=proxies, timeout=10)
        if response.status_code == 200:
            match = re.search(r"var ver = ['\"](.*?)['\"];", response.text)
            if match:
                return "stalker_portal/server/load.php", match.group(1)
    except:
        pass
    
    # Default to portal.php
    return "portal.php", "5.3.1"


def get_token(url, mac, proxy=None, timeout=30):
    """Get authentication token from portal."""
    parsed_url = urlparse(url)
    parsed_path = parsed_url.path
    
    if parsed_path.endswith("c"):
        parsed_path = parsed_path[:-1]
    if parsed_path.endswith("c/"):
        parsed_path = parsed_path[:-2]
    
    host = parsed_url.hostname
    port = parsed_url.port or 80
    base_url = f"http://{host}:{port}{parsed_path}"
    
    portal_type, portal_version = detect_portal_type(url, proxy)
    
    # Fix double stalker_portal
    if "stalker_portal/" in base_url and "stalker_portal/" in portal_type:
        base_url = base_url.replace("stalker_portal/", "")
    
    handshake_url = f"{url}/{portal_type}?action=handshake&type=stb&token=&JsHttpRequest=1-xml"
    
    try:
        session = _get_session()
        cookies = _get_cookies(mac)
        headers = _get_headers()
        proxies = _get_proxy_dict(proxy)
        
        response = session.get(handshake_url, cookies=cookies, headers=headers, proxies=proxies, timeout=timeout)
        response.raise_for_status()
        
        data = response.json()
        token = data.get("js", {}).get("token")
        token_random = data.get("js", {}).get("random")
        
        if token:
            logger.info(f"Token retrieved for MAC {mac}")
            return token, token_random, portal_type, portal_version
        
        logger.error("Token not found in handshake response")
        return None, None, None, None
        
    except Exception as e:
        logger.error(f"Error getting token: {e}")
        return None, None, None, None


def get_profile(url, mac, token, portal_type, proxy=None):
    """Get account profile information."""
    try:
        session = _get_session()
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token)
        proxies = _get_proxy_dict(proxy)
        
        profile_url = f"{url}/{portal_type}?type=stb&action=get_profile&JsHttpRequest=1-xml"
        response = session.get(profile_url, cookies=cookies, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        return response.json().get("js", {})
    except Exception as e:
        logger.error(f"Error getting profile: {e}")
        return {}


def get_account_info(url, mac, token, portal_type, proxy=None):
    """Get account expiration info."""
    try:
        session = _get_session()
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token)
        proxies = _get_proxy_dict(proxy)
        
        info_url = f"{url}/{portal_type}?type=account_info&action=get_main_info&JsHttpRequest=1-xml"
        response = session.get(info_url, cookies=cookies, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        return response.json().get("js", {})
    except Exception as e:
        logger.error(f"Error getting account info: {e}")
        return {}


def get_genres(url, mac, token, portal_type, proxy=None):
    """Get live TV genres/categories."""
    try:
        session = _get_session()
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token)
        proxies = _get_proxy_dict(proxy)
        
        genres_url = f"{url}/{portal_type}?type=itv&action=get_genres&JsHttpRequest=1-xml"
        response = session.get(genres_url, cookies=cookies, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        return response.json().get("js", [])
    except Exception as e:
        logger.error(f"Error getting genres: {e}")
        return []


def get_vod_categories(url, mac, token, portal_type, proxy=None):
    """Get VOD categories."""
    try:
        session = _get_session()
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token)
        proxies = _get_proxy_dict(proxy)
        
        vod_url = f"{url}/{portal_type}?type=vod&action=get_categories&JsHttpRequest=1-xml"
        response = session.get(vod_url, cookies=cookies, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        return response.json().get("js", [])
    except Exception as e:
        logger.error(f"Error getting VOD categories: {e}")
        return []


def get_series_categories(url, mac, token, portal_type, proxy=None):
    """Get series categories."""
    try:
        session = _get_session()
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token)
        proxies = _get_proxy_dict(proxy)
        
        series_url = f"{url}/{portal_type}?type=series&action=get_categories&JsHttpRequest=1-xml"
        response = session.get(series_url, cookies=cookies, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        return response.json().get("js", [])
    except Exception as e:
        logger.error(f"Error getting series categories: {e}")
        return []


def get_channels(url, mac, token, portal_type, category_type, category_id, proxy=None, page=0):
    """Get channels/items from a category."""
    try:
        session = _get_session()
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token)
        proxies = _get_proxy_dict(proxy)
        
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


def get_stream_url(url, mac, token, portal_type, cmd, proxy=None):
    """Get stream URL for a channel."""
    try:
        session = _get_session()
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token)
        proxies = _get_proxy_dict(proxy)
        
        stream_url = f"{url}/{portal_type}?type=itv&action=create_link&cmd={quote(cmd)}&JsHttpRequest=1-xml"
        response = session.get(stream_url, cookies=cookies, headers=headers, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        data = response.json().get("js", {})
        cmd_result = data.get("cmd", "")
        
        # Extract URL from cmd
        if cmd_result:
            parts = cmd_result.split(" ")
            if len(parts) > 1:
                return parts[-1]
            return cmd_result
        
        return None
    except Exception as e:
        logger.error(f"Error getting stream URL: {e}")
        return None


def get_vod_stream_url(url, mac, token, portal_type, cmd, proxy=None):
    """Get stream URL for VOD content."""
    try:
        session = _get_session()
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token)
        proxies = _get_proxy_dict(proxy)
        
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
    """Test if a MAC address is valid on a portal."""
    try:
        token, token_random, portal_type, portal_version = get_token(url, mac, proxy, timeout)
        
        if not token:
            return False, None, "No token"
        
        # Try to get profile to verify
        profile = get_profile(url, mac, token, portal_type, proxy)
        
        if profile:
            account_info = get_account_info(url, mac, token, portal_type, proxy)
            expiry = account_info.get("phone", "Unknown")
            return True, expiry, "Valid"
        
        return False, None, "Invalid profile"
        
    except Exception as e:
        return False, None, str(e)
