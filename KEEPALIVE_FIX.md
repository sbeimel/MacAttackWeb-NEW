# Keepalive Timeout Fix Applied ✅

## Issue
```
Scanner thread error: keepalive_timeout cannot be set if force_close is True
```

## Root Cause
The `keepalive_timeout` and `force_close` parameters are mutually exclusive in aiohttp TCPConnector. You cannot have both enabled at the same time.

## Fix Applied

### Changed Connection Strategy
```python
# OLD (conflicting):
force_close=True,           # Force close connections
keepalive_timeout=30,       # Keep connections alive (CONFLICT!)

# NEW (compatible):
force_close=False,          # Allow connection reuse for better performance
keepalive_timeout=30,       # Connection keepalive (30s)
enable_cleanup_closed=True, # Clean up closed connections
```

### Benefits of New Approach
- ✅ **Better Performance**: Connection reuse reduces overhead
- ✅ **Smart Cleanup**: Closed connections are properly cleaned up
- ✅ **Configurable Timeouts**: 30-second keepalive timeout
- ✅ **DNS Caching**: 5-minute TTL for DNS lookups

## Running Attacks Display Fix

### Added Current Scanner Integration
- ✅ **Shows current portal** in Running Attacks section
- ✅ **Real-time stats** for active scanner
- ✅ **Status updates** (Running/Paused/Stopped)
- ✅ **Portal URL display** from attack configuration

### Features
```javascript
// Automatically adds current scanner to running attacks
runningAttacks.set('current_scanner', {
    portal: { name: 'Current Portal', url: portal_url },
    status: 'running',
    stats: { tested: X, hits: Y, errors: 0 }
});
```

## Current TCPConnector Configuration

```python
self.connector = aiohttp.TCPConnector(
    # Connection pooling
    limit=min(self.max_workers, 500),
    limit_per_host=self.connections_per_host,
    
    # Connection management
    force_close=False,              # Allow reuse
    keepalive_timeout=30,           # 30s keepalive
    enable_cleanup_closed=True,     # Clean up
    
    # Performance features
    use_dns_cache=True,             # DNS caching
    resolver=resolver,              # aiodns resolver
    ttl_dns_cache=300,             # 5min DNS TTL
)
```

## Status
✅ **FIXED** - Scanner should now start without keepalive errors
✅ **ENHANCED** - Running Attacks now shows current portal
✅ **OPTIMIZED** - Better connection reuse for improved performance

## Next Steps
1. Restart the server
2. Start a scan
3. Check "Running Attacks" section - should show current portal
4. Verify no more TCPConnector errors