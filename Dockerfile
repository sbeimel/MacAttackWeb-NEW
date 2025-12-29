# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create directories for data persistence
RUN mkdir -p /app/data /app/logs

# Copy Python dependencies file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY stb.py .
COPY templates/ templates/
COPY static/ static/

# Set environment variables
ENV HOST=0.0.0.0:5003
ENV CONFIG=/app/data/macattack.json
ENV PYTHONUNBUFFERED=1

# Expose the application port
EXPOSE 5003

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5003/ || exit 1

# Run the application
CMD ["python", "app.py"]
