# MacAttack-Web v3.0 - Docker Setup Guide

## 🐳 **Quick Start with Docker**

### 1. **Build and Run**
```bash
# Build the async version
docker build -f Dockerfile_async -t macattack-async:3.0 .

# Run with basic setup
docker run -d \
  --name macattack-web \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  macattack-async:3.0
```

### 2. **Using Docker Compose (Recommended)**
```bash
# Create data directory
mkdir -p data

# Start with docker-compose
docker-compose -f docker-compose_async.yml up -d

# View logs
docker-compose -f docker-compose_async.yml logs -f
```

### 3. **Access the Application**
- **Web Interface:** http://localhost:5000
- **API:** http://localhost:5000/api/

## 📁 **Directory Structure**

```
project/
├── Dockerfile_async          # Docker build file
├── docker-compose_async.yml  # Docker Compose config
├── data/                     # Persistent data (auto-created)
│   ├── config.json          # Configuration
│   ├── state.json           # Scanner state
│   └── macs.txt             # MAC list (optional)
├── stb_async.py             # Async STB client
├── app_async.py             # CLI application
├── web_async.py             # Web interface
└── templates/               # HTML templates
    └── index_async.html
```

## ⚙️ **Configuration**

### Default Configuration
The container creates a default `config.json` if none exists:

```json
{
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
    "auto_save": true,
    "quickscan_only": false
  }
}
```

### Custom Configuration
1. **Via Web Interface:** Configure through http://localhost:5000
2. **Via File:** Edit `data/config.json` directly
3. **Via Environment:** Set environment variables

```bash
docker run -d \
  --name macattack-web \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  -e PORTAL_URL="http://your-portal.com/portal.php" \
  -e MAC_PREFIX="00:1A:79" \
  macattack-async:3.0
```

## 📋 **MAC List Setup**

### Option 1: File Upload
```bash
# Copy your MAC list to data directory
cp your_macs.txt data/macs.txt

# Restart container to load MACs
docker restart macattack-web
```

### Option 2: Volume Mount
```bash
docker run -d \
  --name macattack-web \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/your_macs.txt:/app/data/macs.txt:ro \
  macattack-async:3.0
```

## 🌐 **Proxy Configuration**

### Via Web Interface
1. Open http://localhost:5000
2. Scroll to "Proxies" textarea
3. Add one proxy per line:
```
proxy1.com:8080
proxy2.com:3128
socks5://proxy3.com:1080
http://user:pass@proxy4.com:8080
```

### Via Configuration File
Edit `data/config.json`:
```json
{
  "proxies": [
    "proxy1.com:8080",
    "proxy2.com:3128",
    "socks5://proxy3.com:1080"
  ]
}
```

## 🔧 **Advanced Docker Setup**

### Production with Nginx
```bash
# Start with nginx reverse proxy
docker-compose -f docker-compose_async.yml --profile production up -d
```

### Custom Resource Limits
```bash
docker run -d \
  --name macattack-web \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  --memory=2g \
  --cpus=2 \
  macattack-async:3.0
```

### Health Monitoring
```bash
# Check container health
docker ps
docker logs macattack-web

# Health check endpoint
curl http://localhost:5000/api/state
```

## 📊 **Monitoring & Logs**

### View Logs
```bash
# Real-time logs
docker logs -f macattack-web

# Last 100 lines
docker logs --tail 100 macattack-web

# Docker Compose logs
docker-compose -f docker-compose_async.yml logs -f
```

### Performance Monitoring
```bash
# Container stats
docker stats macattack-web

# Resource usage
docker exec macattack-web ps aux
docker exec macattack-web free -h
```

## 🔄 **Updates & Maintenance**

### Update to New Version
```bash
# Pull latest code
git pull

# Rebuild image
docker build -f Dockerfile_async -t macattack-async:3.0 .

# Stop old container
docker stop macattack-web
docker rm macattack-web

# Start new container (data persists)
docker run -d \
  --name macattack-web \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  macattack-async:3.0
```

### Backup Data
```bash
# Backup persistent data
tar -czf macattack-backup-$(date +%Y%m%d).tar.gz data/

# Restore from backup
tar -xzf macattack-backup-20240101.tar.gz
```

### Reset Everything
```bash
# Stop and remove container
docker stop macattack-web
docker rm macattack-web

# Clear data (WARNING: Loses all progress!)
rm -rf data/

# Start fresh
docker run -d \
  --name macattack-web \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  macattack-async:3.0
```

## 🐛 **Troubleshooting**

### Container Won't Start
```bash
# Check logs for errors
docker logs macattack-web

# Check if port is in use
netstat -tulpn | grep 5000

# Try different port
docker run -d -p 5001:5000 ... macattack-async:3.0
```

### Permission Issues
```bash
# Fix data directory permissions
sudo chown -R $USER:$USER data/
chmod -R 755 data/
```

### Memory Issues
```bash
# Reduce chunk size in config.json
{
  "settings": {
    "chunk_size": 100,
    "max_workers": 20
  }
}

# Or set memory limit
docker run --memory=1g ... macattack-async:3.0
```

### Network Issues
```bash
# Test container network
docker exec macattack-web ping google.com

# Check proxy connectivity
docker exec macattack-web curl -x proxy1.com:8080 http://google.com
```

## 🔒 **Security Considerations**

### Production Deployment
1. **Use HTTPS:** Configure nginx with SSL certificates
2. **Firewall:** Restrict access to port 5000
3. **Authentication:** Add basic auth to nginx
4. **Updates:** Keep Docker images updated

### Example Nginx Config
```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

## 📈 **Performance Tuning**

### High-Performance Setup
```bash
docker run -d \
  --name macattack-web \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  --memory=4g \
  --cpus=4 \
  -e MAX_WORKERS=100 \
  -e CHUNK_SIZE=2000 \
  macattack-async:3.0
```

### Resource Recommendations
| MACs/Hour | Memory | CPU | Workers | Chunk Size |
|-----------|--------|-----|---------|------------|
| < 1,000   | 512MB  | 1   | 20      | 500        |
| 1,000-5,000 | 1GB  | 2   | 50      | 1,000      |
| 5,000-20,000 | 2GB | 4   | 100     | 2,000      |
| > 20,000  | 4GB    | 8   | 200     | 5,000      |

---

**🎯 Ready to scan! Your async MAC scanner is now running in Docker!**