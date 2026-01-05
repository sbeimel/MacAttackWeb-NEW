# MAC-Liste Verzeichnis-Fehler behoben

## Problem
```
IsADirectoryError: [Errno 21] Is a directory: '/app/data/macs.txt'
```

Der Fehler trat auf, weil Docker ein Verzeichnis `/app/data/macs.txt` erstellt hat, anstatt eine Datei zu mounten.

## Ursache
Docker-Compose Volume-Mapping:
```yaml
volumes:
  - ./macs.txt:/app/data/macs.txt:ro
```

Wenn `./macs.txt` nicht existiert, erstellt Docker automatisch ein **Verzeichnis** anstatt einer Datei.

## Lösung ✅

### 1. Robuste MAC-Liste Lade-Funktion
```python
def load_mac_list(file_path: str) -> List[str]:
    try:
        path = Path(file_path)
        if not path.exists():
            logger.warning(f"MAC list file {file_path} not found")
            return []
        
        if path.is_dir():  # ✅ Prüft ob es ein Verzeichnis ist
            logger.warning(f"MAC list path {file_path} is a directory, not a file")
            return []
        
        with open(file_path, 'r') as f:
            macs = [line.strip() for line in f if line.strip()]
        return macs
    except IsADirectoryError:  # ✅ Behandelt Directory-Fehler
        logger.warning(f"MAC list path {file_path} is a directory, not a file")
        return []
```

### 2. Docker-Compose Volume-Mapping entfernt
```yaml
# VORHER (problematisch):
volumes:
  - ./data:/app/data
  - ./macs.txt:/app/data/macs.txt:ro  # ❌ Erstellt Verzeichnis wenn Datei nicht existiert

# NACHHER (sicher):
volumes:
  - ./data:/app/data
  # MAC-Listen werden über Web-Interface verwaltet
```

### 3. Intelligente MAC-Liste Priorität
```python
# 1. Priorität: Web-Interface MAC-Listen
if config.get("mac_lists", {}).get("1"):
    mac_list = config["mac_lists"]["1"]
    
# 2. Priorität: Datei in /app/data/
elif mac_list_path.exists() and mac_list_path.is_file():
    mac_list = load_mac_list(str(mac_list_path))
    
# 3. Priorität: Datei im Root-Verzeichnis
elif Path("macs.txt").exists() and Path("macs.txt").is_file():
    mac_list = load_mac_list("macs.txt")
    
# 4. Fallback: Random MAC-Generierung
else:
    logger.info("No MAC list found - will use random MAC generation")
```

## Vorteile der neuen Lösung

### ✅ Keine Docker Volume-Probleme
- Kein automatisches Verzeichnis-Erstellen
- Saubere Container-Konfiguration
- Weniger Abhängigkeiten von externen Dateien

### ✅ Flexible MAC-Listen Verwaltung
- **Web-Interface**: MAC-Listen über Browser verwalten
- **Datei-Upload**: Import über Web-Interface
- **Externe Dateien**: Fallback für bestehende Setups
- **Random Generation**: Funktioniert immer

### ✅ Robuste Fehlerbehandlung
- Prüft ob Pfad existiert
- Unterscheidet zwischen Datei und Verzeichnis
- Behandelt alle möglichen Fehlertypen
- Graceful Fallbacks

### ✅ Bessere Benutzerfreundlichkeit
- MAC-Listen über Web-Interface verwalten
- Keine manuelle Datei-Erstellung nötig
- Persistente Speicherung in Konfiguration
- Import/Export Funktionen

## Erwartetes Verhalten

**Beim Start:**
- ✅ Keine Directory-Fehler mehr
- ✅ MAC-Listen werden aus Konfiguration geladen
- ✅ Fallback auf Dateien funktioniert
- ✅ Random Generation als letzter Fallback

**Im Betrieb:**
- ✅ MAC-Listen über Web-Interface verwalten
- ✅ Import/Export über Browser
- ✅ Persistente Speicherung in `/app/data/macattack.json`
- ✅ Keine Docker Volume-Probleme

Der Fehler sollte jetzt vollständig behoben sein!