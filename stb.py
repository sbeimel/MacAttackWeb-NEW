"""
STB (Set-Top Box) API Client for Stalker Portals
Optimized version with quick scan and proper session cleanup
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


def parse_proxy(proxy_str):
    """
    Parse proxy string and return requests-compatible proxy dict.
    Supports: HTTP, SOCKS4, SOCKS5
    """
    if not proxy_str:
        return None
    
    proxy_str = proxy_str.strip()
    
    if proxy_str.startswith("socks5://"):
        return {"http": proxy_str, "https": proxy_str}
    elif proxy_str.startswith("socks4://"):
        return {"http": proxy_str, "https": proxy_str}
    elif proxy_str.startswith("http://"):
        return {"http": proxy_str, "https": proxy_str}
    else:
        return {"http": f"http://{proxy_str}", "https": f"http://{proxy_str}"}


def _create_session():
    """Create a new session with retries."""
    session = requests.Session()
    retries = Retry(total=2, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _generate_device_ids(mac):
    """Generate device IDs based on MAC address."""
    serialnumber = hashlib.md5(mac.encode()).hexdigest().upper()
    sn = serialnumber[0:13]
    device_id = hashlib.sha256(sn.encode()).hexdigest().upper()
    device_id2 = hashlib.sha256(mac.encode()).hexdigest().upper()
    hw_version_2 = hashlib.sha1(mac.encode()).hexdigest()
    snmac = f"{sn}{mac}"
    sig = hashlib.sha256(snmac.encode()).hexdigest().upper()
    return sn, device_id, device_id2, hw_version_2, sig


def _normalize_url(url):
    """Normalize URL - remove trailing slashes."""
    return url.rstrip('/')


def auto_detect_portal_url(base_url, proxy=None, timeout=10):
    """Auto-detect portal endpoint from base URL."""
    base_url = _normalize_url(base_url)
    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or 80
    scheme = parsed.scheme or "http"
    
    if '/c' in parsed.path:
        portal_type, version = detect_portal_type(base_url, proxy)
        return base_url, portal_type, version
    
    clean_base = f"{scheme}://{host}:{port}"
    
    headers = _get_headers()
    proxies = parse_proxy(proxy)
    session = _create_session()
    
    endpoints = [
        ("/c/", "portal.php"),
        ("/stalker_portal/c/", "stalker_portal/server/load.php"),
    ]
    
    try:
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
    finally:
        session.close()
    
    logger.warning(f"No portal endpoint auto-detected for {base_url}, defaulting to /c/")
    return f"{clean_base}/c", "portal.php", "5.3.1"


def _get_cookies(mac):
    """Generate cookies for STB emulation."""
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
    """Generate headers for STB emulation."""
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
    """Detect the portal type (portal.php or stalker_portal)."""
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
    session = _create_session()
    
    try:
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
    finally:
        session.close()
    
    return "portal.php", "5.3.1"


def get_token(url, mac, proxy=None, timeout=30):
    """
    Get authentication token from portal.
    Returns: (token, token_random, portal_type, portal_version, session)
    NOTE: Caller must close session!
    """
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
    
    base_url = f"{scheme}://{host}:{port}{parsed_path}"
    
    if "stalker_portal/" in base_url and "stalker_portal/" in portal_type:
        base_url = base_url.replace("stalker_portal/", "")
    
    handshake_url = f"{url}/{portal_type}?action=handshake&type=stb&token=&JsHttpRequest=1-xml"
    
    try:
        session = _create_session()
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
                
                session.headers.update({
                    "Connection": "keep-alive",
                    "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
                    "Accept-Encoding": "identity",
                    "Accept": "*/*",
                    "Authorization": f"Bearer {token}",
                    "X-Random": str(token_random),
                })
                
                session.cookies.update(cookies)
                
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
            return token, token_random, portal_type, portal_version, session
        
        logger.error("Token not found in handshake response")
        session.close()
        return None, None, None, None, None
        
    except Exception as e:
        logger.error(f"Error getting token: {e}")
        if 'session' in locals():
            session.close()
        return None, None, None, None, None


def close_session(url, mac, token, portal_type, session, token_random=None, proxy=None):
    """
    Close session on portal (logout).
    Actions: watchdog + logout + del_session
    """
    url = _normalize_url(url)
    if not session or not token:
        return
    
    try:
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token, token_random)
        proxies = parse_proxy(proxy)
        
        # 1. Watchdog
        try:
            watchdog_url = f"{url}/{portal_type}?type=watchdog&JsHttpRequest=1-xml"
            session.get(watchdog_url, cookies=cookies, headers=headers, proxies=proxies, timeout=5)
        except:
            pass
        
        # 2. Logout
        try:
            logout_url = f"{url}/{portal_type}?type=account_info&action=logout&JsHttpRequest=1-xml"
            session.get(logout_url, cookies=cookies, headers=headers, proxies=proxies, timeout=5)
        except:
            pass
        
        # 3. Delete session
        try:
            del_session_url = f"{url}/{portal_type}?type=stb&action=del_session&JsHttpRequest=1-xml"
            session.get(del_session_url, cookies=cookies, headers=headers, proxies=proxies, timeout=5)
        except:
            pass
        
        logger.debug(f"Session closed for MAC {mac}")
    except Exception as e:
        logger.debug(f"Error closing session: {e}")
    finally:
        try:
            session.close()
        except:
            pass


def get_all_channels(url, mac, token, portal_type, session, token_random=None, proxy=None, timeout=15):
    """Get all channels count - quick check."""
    url = _normalize_url(url)
    try:
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


def quick_scan(url, mac, proxy=None, timeout=15):
    """
    Quick scan: Token + Channel Check
    Returns: (valid, channels_count, session, token, token_random, portal_type)
    
    If valid and channels > 0: session is kept open for full_scan
    If invalid or channels == 0: session is closed
    """
    url = _normalize_url(url)
    
    try:
        # Step 1: Get token
        token, token_random, portal_type, portal_version, session = get_token(url, mac, proxy, timeout)
        
        if not token or not session:
            return False, 0, None, None, None, None
        
        # Step 2: Quick channel check
        channels_count = get_all_channels(url, mac, token, portal_type, session, token_random, proxy, timeout)
        
        if channels_count == 0:
            # No channels - close session and exit
            close_session(url, mac, token, portal_type, session, token_random, proxy)
            logger.info(f"Quick scan: MAC {mac} has 0 channels - SKIP")
            return False, 0, None, None, None, None
        
        # Valid portal with channels - keep session open for full scan
        logger.info(f"Quick scan: MAC {mac} has {channels_count} channels - VALID")
        return True, channels_count, session, token, token_random, portal_type
        
    except Exception as e:
        logger.error(f"Error in quick scan for {mac}: {e}")
        if 'session' in locals() and session:
            try:
                session.close()
            except:
                pass
        return False, 0, None, None, None, None


def full_scan(url, mac, channels_count, session, token, token_random, portal_type, proxy=None, timeout=15):
    """
    Full scan: Get all details (expiry, genres, VOD, backend, etc.)
    NOTE: Uses existing session from quick_scan
    NOTE: Closes session at end!
    
    Returns: (success, result_dict)
    """
    url = _normalize_url(url)
    result = {
        "mac": mac,
        "portal": url,
        "expiry": None,
        "channels": channels_count,
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
        cookies = _get_cookies(mac)
        cookies["token"] = token
        headers = _get_headers(token, token_random)
        proxies = parse_proxy(proxy)
        
        # Step 1: Get profile
        try:
            profile_url = f"{url}/{portal_type}?type=stb&action=get_profile&JsHttpRequest=1-xml"
            response = session.get(profile_url, cookies=cookies, headers=headers, proxies=proxies, timeout=timeout)
            if response.status_code == 200:
                profile = response.json().get("js", {})
                exp_billing = profile.get("expire_billing_date")
                result["client_ip"] = profile.get("ip")
                
                if exp_billing:
                    try:
                        from datetime import datetime as dt
                        dt_object = dt.strptime(exp_billing, "%Y-%m-%d %H:%M:%S")
                        exp_billing = dt_object.strftime("%B %d, %Y, %I:%M %p")
                    except (ValueError, TypeError):
                        pass
        except:
            exp_billing = None
        
        # Step 2: Get account info (expiry)
        try:
            info_url = f"{url}/{portal_type}?type=account_info&action=get_main_info&JsHttpRequest=1-xml"
            response = session.get(info_url, cookies=cookies, headers=headers, proxies=proxies, timeout=timeout)
            if response.status_code == 200:
                account_info = response.json().get("js", {})
                phone = account_info.get("phone")
                
                if phone is not None:
                    expiry = phone if phone else "Unknown"
                    if expiry == "Unknown" and exp_billing:
                        expiry = exp_billing
                    
                    try:
                        timestamp = int(expiry)
                        from datetime import datetime as dt
                        expiry = dt.utcfromtimestamp(timestamp).strftime("%B %d, %Y, %I:%M %p")
                    except (ValueError, TypeError):
                        pass
                    
                    result["expiry"] = expiry
        except:
            pass
        
        # Step 3: Get stream info (backend/credentials)
        try:
            link_url = f"{url}/{portal_type}?type=itv&action=create_link&cmd=http://localhost/ch/10000_&series=&forced_storage=undefined&disable_ad=0&download=0&JsHttpRequest=1-xml"
            response = session.get(link_url, cookies=cookies, headers=headers, proxies=proxies, timeout=timeout)
            
            if response.status_code == 200:
                data = response.json()
                js_data = data.get("js", {})
                cmd_value = js_data.get("cmd")
                
                if cmd_value:
                    cmd_value = cmd_value.replace("ffmpeg ", "", 1).replace("'ffmpeg' ", "")
                    parsed = urlparse(cmd_value)
                    backend_url = f"{parsed.scheme}://{parsed.hostname}"
                    if parsed.port:
                        backend_url += f":{parsed.port}"
                    
                    path_parts = parsed.path.strip("/").split("/")
                    if len(path_parts) >= 2:
                        result["backend_url"] = backend_url
                        result["username"] = path_parts[0]
                        result["password"] = path_parts[1]
                        
                        # Step 4: Get Xtream info
                        try:
                            xtream_url = f"{backend_url}/player_api.php?username={path_parts[0]}&password={path_parts[1]}"
                            xtream_response = session.get(xtream_url, timeout=timeout)
                            
                            if xtream_response.status_code == 200:
                                xtream_data = xtream_response.json()
                                user_info = xtream_data.get("user_info", {})
                                
                                if "max_connections" in user_info:
                                    try:
                                        result["max_connections"] = int(user_info["max_connections"])
                                    except:
                                        pass
                                
                                if "active_cons" in user_info:
                                    try:
                                        result["active_cons"] = int(user_info["active_cons"])
                                    except:
                                        pass
                                
                                if "created_at" in user_info:
                                    try:
                                        from datetime import datetime as dt, timezone as tz
                                        timestamp = int(user_info["created_at"])
                                        result["created_at"] = dt.fromtimestamp(timestamp, tz.utc).strftime("%B %d, %Y, %I:%M %p")
                                    except:
                                        pass
                        except:
                            pass
        except:
            pass
        
        # Step 5: Get genres
        try:
            genres_url = f"{url}/{portal_type}?type=itv&action=get_genres&JsHttpRequest=1-xml"
            response = session.get(genres_url, cookies=cookies, headers=headers, proxies=proxies, timeout=timeout)
            if response.status_code == 200:
                genres = response.json().get("js", [])
                genres = [g for g in genres if g.get("id") != "*"]
                result["genres"] = [g.get("title", "") for g in genres]
        except:
            pass
        
        # Step 6: Get VOD categories
        try:
            vod_url = f"{url}/{portal_type}?type=vod&action=get_categories&JsHttpRequest=1-xml"
            response = session.get(vod_url, cookies=cookies, headers=headers, proxies=proxies, timeout=timeout)
            if response.status_code == 200:
                vod_cats = response.json().get("js", [])
                result["vod_categories"] = [v.get("title", "") for v in vod_cats if isinstance(v, dict)]
        except:
            pass
        
        # Step 7: Get Series categories
        try:
            series_url = f"{url}/{portal_type}?type=series&action=get_categories&JsHttpRequest=1-xml"
            response = session.get(series_url, cookies=cookies, headers=headers, proxies=proxies, timeout=timeout)
            if response.status_code == 200:
                series_cats = response.json().get("js", [])
                result["series_categories"] = [s.get("title", "") for s in series_cats if isinstance(s, dict)]
        except:
            pass
        
        logger.info(f"Full scan complete: MAC {mac} - Expiry: {result['expiry']} - Channels: {channels_count}")
        return True, result
        
    except Exception as e:
        logger.error(f"Error in full scan for {mac}: {e}")
        return False, result
    finally:
        # Always close session after full scan
        close_session(url, mac, token, portal_type, session, token_random, proxy)


def test_mac_full(url, mac, proxy=None, timeout=15):
    """
    Combined quick + full scan with early exit.
    
    Flow:
    1. Quick scan (token + channels)
    2. If channels == 0 → Early exit (session closed)
    3. If channels > 0 → Full scan (uses same session)
    
    Returns: (success, result_dict)
    """
    # Quick scan first
    valid, channels_count, session, token, token_random, portal_type = quick_scan(url, mac, proxy, timeout)
    
    if not valid or channels_count == 0:
        # Early exit - invalid or no channels
        return False, {
            "mac": mac,
            "portal": url,
            "channels": 0,
            "expiry": None,
            "genres": [],
            "vod_categories": [],
            "series_categories": [],
        }
    
    # Channels found - do full scan (reuses session)
    return full_scan(url, mac, channels_count, session, token, token_random, portal_type, proxy, timeout)


# Keep old functions for backward compatibility (player, etc.)
def get_profile(url, mac, token, portal_type, token_random=None, proxy=None):
    """Get account profile - creates own session."""
    url = _normalize_url(url)
    session = _create_session()
    try:
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
    finally:
        session.close()


def get_genres(url, mac, token, portal_type, token_random=None, proxy=None):
    """Get genres - creates own session."""
    url = _normalize_url(url)
    session = _create_session()
    try:
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
    finally:
        session.close()


def get_vod_categories(url, mac, token, portal_type, token_random=None, proxy=None):
    """Get VOD categories - creates own session."""
    url = _normalize_url(url)
    session = _create_session()
    try:
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
    finally:
        session.close()


def get_series_categories(url, mac, token, portal_type, token_random=None, proxy=None):
    """Get series categories - creates own session."""
    url = _normalize_url(url)
    session = _create_session()
    try:
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
    finally:
        session.close()


def get_channels(url, mac, token, portal_type, category_type, category_id, token_random=None, proxy=None, page=0):
    """Get channels - creates own session."""
    url = _normalize_url(url)
    session = _create_session()
    try:
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
        return data.get("data", []), int(data.get("total_items", 0))
    except Exception as e:
        logger.error(f"Error getting channels: {e}")
        return [], 0
    finally:
        session.close()


def get_stream_url(url, mac, token, portal_type, cmd, token_random=None, proxy=None):
    """Get stream URL - creates own session."""
    url = _normalize_url(url)
    session = _create_session()
    try:
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
            return parts[-1] if len(parts) > 1 else cmd_result
        return None
    except Exception as e:
        logger.error(f"Error getting stream URL: {e}")
        return None
    finally:
        session.close()


def get_vod_stream_url(url, mac, token, portal_type, cmd, token_random=None, proxy=None):
    """Get VOD stream URL - creates own session."""
    url = _normalize_url(url)
    session = _create_session()
    try:
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
            return parts[-1] if len(parts) > 1 else cmd_result
        return None
    except Exception as e:
        logger.error(f"Error getting VOD stream URL: {e}")
        return None
    finally:
        session.close()

# Alias für falsche Schreibweise (Kompatibilität)
test_mac_Full = test_mac_full
