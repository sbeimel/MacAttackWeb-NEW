# MacAttack-Web v3.0 - Simple Docker Image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY stb.py .
COPY app.py .
COPY web.py .
COPY templates/ templates/

# Create directories
RUN mkdir -p /app/data

# Create startup script
COPY <<EOF /app/start.sh
#!/bin/bash

# Copy default config if needed
if [ ! -f /app/data/config.json ]; then
    cat > /app/data/config.json << 'CONFIG'
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
    "quickscan_only": false,
    "debug_mode": false,
    "connections_per_host": 5,
    "requests_per_minute_per_proxy": 30,
    "min_delay_between_requests": 0.5
  }
}
CONFIG
    echo "✅ Created default config.json"
fi

# Create symlinks
ln -sf /app/data/config.json /app/config.json 2>/dev/null || true
ln -sf /app/data/state.json /app/state.json 2>/dev/null || true

# Copy MAC list if provided
if [ -f /app/data/macs.txt ]; then
    ln -sf /app/data/macs.txt /app/macs.txt
    echo "✅ MAC list found: \$(wc -l < /app/data/macs.txt) MACs"
fi

echo "🚀 Starting MacAttack-Web v3.0"
echo "📊 Dashboard: http://localhost:5005"
echo "🔧 API: http://localhost:5005/api/"

exec python web.py
EOF

RUN chmod +x /app/start.sh

# Expose port
EXPOSE 5005

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5005/api/state || exit 1

# Set environment
ENV PYTHONUNBUFFERED=1

# Start
ENTRYPOINT ["/app/start.sh"]