# Use Ubuntu as base image
FROM ubuntu:22.04

# Install dependencies
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    vlc \
    xvfb \
    x11vnc \
    xdotool \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip3 install -r requirements.txt

# Create app directory
WORKDIR /app

# Copy application files
COPY . .

# Set environment variables
ENV DISPLAY=:1
ENV VLC_PLUGIN_PATH=/usr/lib/x86_64-linux-gnu/vlc/plugins

# Create VLC config directory and set permissions
RUN mkdir -p /root/.config/vlc && \
    chmod -R 777 /root/.config

# Expose VNC port
EXPOSE 5900

# Create startup script
RUN echo '#!/bin/bash' > /startup.sh && \
    echo 'Xvfb :1 -screen 0 1024x768x16 &' >> /startup.sh && \
    echo 'x11vnc -create -forever -nopw -display :1 &' >> /startup.sh && \
    echo 'python3 /app/web_interface.py &' >> /startup.sh && \
    echo 'tail -f /dev/null' >> /startup.sh && \
    chmod +x /startup.sh

# Set the entrypoint
ENTRYPOINT ["/startup.sh"]
