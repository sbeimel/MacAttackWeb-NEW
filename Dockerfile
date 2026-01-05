# MacAttack-Web v3.0 - Async Edition Docker Image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY stb.py .
COPY app.py .
COPY web.py .
COPY templates/ templates/

# Create directories for persistent data
RUN mkdir -p /app/data

# Copy default configuration if it doesn't exist
COPY <<EOF /app/config_default.json
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
EOF

# Create startup script
COPY <<EOF /app/start.sh
#!/bin/bash

# Copy default config if config.json doesn't exist
if [ ! -f /app/data/config.json ]; then
    cp /app/config_default.json /app/data/config.json
    echo "Created default config.json"
fi

# Create symlinks to data directory for persistence
ln -sf /app/data/config.json /app/config.json
ln -sf /app/data/state.json /app/state.json

# Copy MAC list if provided
if [ -f /app/data/macs.txt ]; then
    ln -sf /app/data/macs.txt /app/macs.txt
    echo "MAC list found: $(wc -l < /app/data/macs.txt) MACs"
fi

# Start the web interface
echo "Starting MacAttack-Web v3.0 - Async Edition"
echo "Web Interface: http://localhost:5000"
echo "API: http://localhost:5000/api/"

exec python web.py
EOF

# Make startup script executable
RUN chmod +x /app/start.sh

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/api/state || exit 1

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV FLASK_ENV=production

# Use startup script as entrypoint
ENTRYPOINT ["/app/start.sh"]