# Proxy-Logik Fix - Kein Proxy Konfiguriert

## Problem
```
🐌 Proxy slow: None
```

Obwohl keine Proxies konfiguriert waren, zeigte der Scanner trotzdem Proxy-Fehlermeldungen mit `None` an.

## Ursache
Die Proxy-Fehlerbehandlung wurde immer ausgeführt, auch wenn `proxy = None` war:
```python
# VORHER (fehlerhaft):
if error_type == "slow":
    add_log(state, f"🐌 Proxy slow: {proxy}", "warning")  # proxy = None
```

## Lösung ✅

### 1. Intelligente Proxy-Auswahl
```python
# Get proxy if available
proxy = None
if proxies and len(proxies) > 0:  # Prüft ob Proxies existieren UND nicht leer
    proxy = stb._smart_rotator.get_best_proxy(proxies, state.get("current_proxy"))
    state["current_proxy"] = proxy
else:
    # No proxies configured - use direct connection
    state["current_proxy"] = None
```

### 2. Separate Fehlerbehandlung für Proxy vs. Direkte Verbindung
```python
elif error_type:
    if proxy:
        # Proxy error - retry with different proxy
        proxy_scorer.record_fail(proxy, error_type, portal_url)
        stb._smart_rotator.record_failure(proxy, error_type)
        
        if error_type == "dead":
            add_log(state, f"💀 Proxy dead: {proxy}", "error")
        elif error_type == "slow":
            add_log(state, f"🐌 Proxy slow: {proxy}", "warning")
    else:
        # Direct connection error (no proxy)
        if error_type == "dead":
            add_log(state, f"💀 Connection failed: {mac}", "error")
        elif error_type == "slow":
            add_log(state, f"🐌 Request timeout: {mac}", "warning")
```

### 3. Intelligente Retry-Logik
```python
if proxy:
    # Mit Proxy: Retry mit anderem Proxy
    retry_queue.add_retry(mac, 0, proxy, error_type)
else:
    # Ohne Proxy: Nur bei Timeouts retry, nicht bei dead/blocked
    if error_type == "slow":
        retry_queue.add_retry(mac, 0, None, error_type)
```

### 4. Bedingte Statistik-Updates
```python
# Update proxy statistics only if proxy was used
if proxy:
    proxy_scorer.record_success(proxy, elapsed_ms)
    stb._smart_rotator.record_success(proxy, elapsed_ms)
```

## Ergebnis

**Ohne Proxies konfiguriert:**
- ✅ `🐌 Request timeout: 00:1A:79:XX:XX:XX` statt `🐌 Proxy slow: None`
- ✅ `💀 Connection failed: 00:1A:79:XX:XX:XX` statt `💀 Proxy dead: None`
- ✅ Keine Proxy-Statistiken werden aktualisiert
- ✅ Intelligente Retry-Logik (weniger aggressive Retries ohne Proxy)

**Mit Proxies konfiguriert:**
- ✅ Normale Proxy-Fehlermeldungen mit Proxy-IP
- ✅ Proxy-Statistiken werden korrekt aktualisiert
- ✅ Aggressive Retry-Logik mit verschiedenen Proxies

Die Fehlermeldungen sind jetzt korrekt und zeigen nicht mehr `None` als Proxy an!