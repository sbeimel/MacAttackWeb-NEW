# Scannen ohne Proxy - Vollständige Lösung

## Problem
1. Scanner funktionierte nicht ohne Proxy (return None bei no_proxy)
2. Konfiguration wurde nicht in `/app/data/macattack.json` gespeichert

## Lösung ✅

### 1. Konfigurationspfade geändert
```python
# Neue Pfade:
DATA_DIR = Path("/app/data")
CONFIG_FILE = DATA_DIR / "macattack.json"
STATE_FILE = DATA_DIR / "state.json" 
SECURITY_FILE = DATA_DIR / "security.json"
```

**Vorteile:**
- ✅ Persistente Speicherung in Docker-Container
- ✅ Zentrale Datenverwaltung in `/app/data/`
- ✅ Automatische Verzeichniserstellung

### 2. Proxy-Logik repariert
```python
# VORHER (fehlerhaft):
if proxies and len(proxies) > 0:
    proxy = stb._smart_rotator.get_best_proxy(proxies, state.get("current_proxy"))
    if not proxy:
        retry_queue.add_retry(mac, 0, None, "no_proxy")
        return None  # ❌ MAC wird nicht getestet!

# NACHHER (korrekt):
if proxies and len(proxies) > 0:
    proxy = stb._smart_rotator.get_best_proxy(proxies, state.get("current_proxy"))
    if not proxy:
        # No suitable proxy available, but we have proxies configured
        # This means all proxies are blocked/failed - skip this MAC for now
        retry_queue.add_retry(mac, 0, None, "no_proxy")
        return None
else:
    # No proxies configured - use direct connection (this is OK!)
    state["current_proxy"] = None
    # ✅ MAC wird trotzdem getestet!
```

### 3. Unterscheidung zwischen "Keine Proxies" vs "Alle Proxies blockiert"

**Keine Proxies konfiguriert:**
- ✅ Scanner verwendet direkte Verbindung
- ✅ MACs werden normal getestet
- ✅ Keine Proxy-Fehlermeldungen

**Proxies konfiguriert aber alle blockiert:**
- ✅ MAC wird übersprungen und für Retry vorgemerkt
- ✅ Wartet auf bessere Proxy-Verfügbarkeit

## Dateispeicherung

### Konfigurationsdateien:
- **`/app/data/macattack.json`** - Hauptkonfiguration (Portale, Settings, Found MACs)
- **`/app/data/state.json`** - Scanner-Status (Tested MACs, Statistiken)
- **`/app/data/security.json`** - Passwort und Sicherheitseinstellungen

### Automatische Verzeichniserstellung:
```python
DATA_DIR = Path("/app/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
```

## Test-Szenarien

### ✅ Scannen ohne Proxy:
1. Keine Proxies in Konfiguration
2. Scanner startet normal
3. MACs werden direkt getestet
4. Erfolgreiche Hits werden gefunden
5. Fehlermeldungen zeigen "Request timeout" statt "Proxy slow: None"

### ✅ Scannen mit Proxy:
1. Proxies konfiguriert
2. Scanner verwendet Proxy-Rotation
3. Proxy-Statistiken werden aktualisiert
4. Fehlermeldungen zeigen Proxy-IP

### ✅ Persistente Konfiguration:
1. Settings werden in `/app/data/macattack.json` gespeichert
2. Portale werden persistent gespeichert
3. Found MACs bleiben nach Neustart erhalten
4. Scanner-Status wird wiederhergestellt

## Erwartetes Verhalten

**Ohne Proxy:**
- ✅ Scanner funktioniert normal
- ✅ Direkte Verbindung zum Portal
- ✅ Normale MAC-Tests
- ✅ Korrekte Fehlermeldungen

**Mit Proxy:**
- ✅ Proxy-Rotation funktioniert
- ✅ Proxy-Statistiken werden aktualisiert
- ✅ Intelligente Retry-Logik

**Konfiguration:**
- ✅ Alle Einstellungen werden in `/app/data/` gespeichert
- ✅ Persistenz über Container-Neustarts
- ✅ Zentrale Datenverwaltung

Das Scannen sollte jetzt sowohl mit als auch ohne Proxy einwandfrei funktionieren!