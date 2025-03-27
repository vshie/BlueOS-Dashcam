#!/bin/bash

# Default paths and settings
DEFAULT_LOG_FOLDER="/home/blueos/logs"
DEFAULT_VIDEO_FOLDER="/home/blueos/videos"
DEFAULT_MAVLINK_URL="ws://blueos.internal:6040/mavlink/ws/mavlink?filter=HEARTBEAT"

# Parse command line arguments
LOG_FOLDER=${1:-$DEFAULT_LOG_FOLDER}
VIDEO_FOLDER=${2:-$DEFAULT_VIDEO_FOLDER}
MAVLINK_URL=${3:-$DEFAULT_MAVLINK_URL}

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment and install dependencies
echo "Activating virtual environment and installing dependencies..."
source venv/bin/activate
pip install -r requirements.txt

# Create necessary directories
echo "Creating necessary directories..."
mkdir -p "$LOG_FOLDER" "$VIDEO_FOLDER"

echo "Setup complete! To run the application:"
echo "1. Activate the virtual environment: source venv/bin/activate"
echo "2. Run the application: python video_recorder.py --log-folder $LOG_FOLDER --video-folder $VIDEO_FOLDER --mavlink-url $MAVLINK_URL" 