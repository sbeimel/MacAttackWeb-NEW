# MacAttack-Web

Web-basierte Linux/Docker-Version von MacAttack für das Testen von IPTV Stalker Portalen.

## Features

- **MAC Attack**: Brute-Force-Testing von MAC-Adressen auf Stalker Portalen
- **MAC Player**: Verbindung zu Portalen und Abruf von Playlists (Live, VOD, Series)
- **Proxy Management**: Automatisches Fetchen und Testen von Proxies
- **Export**: Gefundene MACs als TXT oder JSON exportieren
- **Docker Support**: Einfaches Deployment mit Docker/Docker Compose

## Quick Start

### Docker (Empfohlen)

```bash
# Repository klonen
git clone <repository-url>
cd MacAttack-Web

# Container starten
docker-compose up -d

# Logs anzeigen
docker-compose logs -f
```

### Docker Compose (Standalone)

```yaml
services:
  macattack-web:
    build: .
    container_name: MacAttack-Web
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    environment:
      - HOST=0.0.0.0:8080
    restart: unless-stopped
```

### Manuell

```bash
# Dependencies installieren
pip install -r requirements.txt

# Anwendung starten
python app.py
```

Die Anwendung ist erreichbar unter: `http://localhost:8080`

## Verwendung

### MAC Attack Tab

1. Portal URL eingeben (z.B. `http://example.com/c/`)
2. Optional: Proxies im Proxies-Tab konfigurieren
3. Speed und andere Einstellungen im Settings-Tab anpassen
4. "Start" klicken

### MAC Player Tab

1. Portal URL eingeben
2. MAC-Adresse eingeben (z.B. `00:1A:79:XX:XX:XX`)
3. Optional: Proxy eingeben
4. "Connect" klicken
5. Kategorien und Kanäle durchsuchen
6. Stream-URL kopieren und in einem Player öffnen

### Proxies Tab

- **Fetch Proxies**: Lädt Proxies von öffentlichen Quellen
- **Test Proxies**: Testet alle Proxies auf Funktionalität
- Proxies können auch manuell eingegeben werden (eine pro Zeile)

## Konfiguration

### Umgebungsvariablen

| Variable | Standard | Beschreibung |
|----------|----------|--------------|
| `HOST` | `0.0.0.0:8080` | Host und Port |
| `CONFIG` | `/app/data/macattack.json` | Pfad zur Konfigurationsdatei |

### Verzeichnisse

| Pfad | Beschreibung |
|------|--------------|
| `data/` | Konfiguration und gefundene MACs |
| `logs/` | Log-Dateien |

## API Endpoints

| Endpoint | Methode | Beschreibung |
|----------|---------|--------------|
| `/api/settings` | GET/POST | Einstellungen abrufen/speichern |
| `/api/attack/start` | POST | Attack starten |
| `/api/attack/stop` | POST | Attack stoppen |
| `/api/attack/status` | GET | Attack-Status abrufen |
| `/api/proxies` | GET/POST/DELETE | Proxies verwalten |
| `/api/proxies/fetch` | POST | Proxies von Quellen laden |
| `/api/proxies/test` | POST | Proxies testen |
| `/api/player/connect` | POST | Mit Portal verbinden |
| `/api/player/channels` | POST | Kanäle abrufen |
| `/api/player/stream` | POST | Stream-URL abrufen |
| `/api/found` | GET/DELETE | Gefundene MACs |
| `/api/found/export` | GET | MACs exportieren |

## Disclaimer

MacAttack-Web ist ausschließlich ein Test-Tool. Die unbefugte Nutzung auf fremden Portalen kann gegen Gesetze oder Nutzungsbedingungen verstoßen. Stellen Sie sicher, dass Sie die Berechtigung haben, ein Portal zu testen.

## Lizenz

MIT License
