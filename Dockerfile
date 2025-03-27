FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies - temporarily install gcc for building dependencies
RUN apt-get update && apt-get install -y gcc && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get purge -y gcc && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Create necessary directories
RUN mkdir -p static views

# Download Vue.js into static directory (using a specific version to ensure stability)
RUN curl -s https://unpkg.com/vue@3.3.4/dist/vue.global.prod.js -o static/vue.js

# Copy the rest of the application
COPY . .

# Set default environment variables
ENV LOG_FOLDER=/home/blueos/logs
ENV VIDEO_FOLDER=/home/blueos/videos
ENV SETTINGS_FOLDER=/home/blueos/settings
ENV MAVLINK_URL=ws://blueos.internal/mavlink2rest/ws/mavlink?filter=HEARTBEAT
ENV PYTHONUNBUFFERED=1

# Create necessary directories for storage
RUN mkdir -p $LOG_FOLDER $VIDEO_FOLDER

# Expose the web interface port
EXPOSE 8080

# Run the application
CMD ["sh", "-c", "python video_recorder.py --log-folder $LOG_FOLDER --video-folder $VIDEO_FOLDER --mavlink-url $MAVLINK_URL"] 