FROM bluerobotics/blueos-base:latest

# Set working directory
WORKDIR /app

# Create necessary directories
RUN mkdir -p static views

# Download Vue.js into static directory (using a specific version to ensure stability)
RUN curl -s https://unpkg.com/vue@3.3.4/dist/vue.global.prod.js -o static/vue.js

# Install essential GStreamer packages for ARM with H.265 support
RUN apt update && apt install -y \
    gcc \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    gstreamer1.0-plugins-rs \
    libgstreamer1.0-0 \
    libgstreamer-plugins-base1.0-0 \
    libgraphene-1.0-0 \
    libegl1-mesa \
    libegl1-mesa-dev \
    libgles2-mesa \
    libgles2-mesa-dev \
    libgl1-mesa-dev \
    libglu1-mesa \
    libglu1-mesa-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install websockets aiohttp

# Remove problematic GStreamer plugins that cause version conflicts
RUN rm -rf /usr/local/lib/aarch64-linux-gnu/gstreamer-1.0/libgstnvcodec.so \
           /usr/local/lib/aarch64-linux-gnu/gstreamer-1.0/libgstopengl.so \
           /usr/local/lib/aarch64-linux-gnu/gstreamer-1.0/libgstqsv.so \
           /usr/local/lib/aarch64-linux-gnu/gstreamer-1.0/libgstvalidatetracer.so \
           /usr/local/lib/aarch64-linux-gnu/gstreamer-1.0/libgstrtspclientsink.so \
           /usr/local/lib/aarch64-linux-gnu/gstreamer-1.0/libgstunixfd.so \
           /usr/local/lib/aarch64-linux-gnu/gstreamer-1.0/libgstdsd.so \
           /usr/local/lib/aarch64-linux-gnu/gstreamer-1.0/libgstaja.so \
           /usr/local/lib/aarch64-linux-gnu/gstreamer-1.0/libgsttensordecoders.so \
           /usr/local/lib/aarch64-linux-gnu/gstreamer-1.0/libgstuvcgadget.so \
           /usr/local/lib/aarch64-linux-gnu/gstreamer-1.0/libgstanalyticsoverlay.so \
           /usr/local/lib/aarch64-linux-gnu/gstreamer-1.0/validate/ \
           /usr/local/lib/aarch64-linux-gnu/libgstrtspserver-1.0.so.0 \
           /usr/local/lib/aarch64-linux-gnu/libgstanalytics-1.0.so.0 \
           /usr/local/lib/aarch64-linux-gnu/libgstvalidate-1.0.so.0 || true

# Copy the rest of the application
COPY . .

# Set default environment variables
ENV LOG_FOLDER=/home/blueos/logs
ENV VIDEO_FOLDER=/home/blueos/videos
ENV SETTINGS_FOLDER=/home/blueos/settings
ENV BLUEOS_ADDRESS=blueos.internal
ENV PYTHONUNBUFFERED=1

# GStreamer environment variables for ARM
ENV GST_PLUGIN_PATH=/usr/lib/aarch64-linux-gnu/gstreamer-1.0:/usr/lib/arm-linux-gnueabihf/gstreamer-1.0
ENV GST_REGISTRY_FORK=no
ENV GST_DEBUG=1
ENV LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu:/usr/lib/arm-linux-gnueabihf
# Disable problematic plugins that cause version conflicts
ENV GST_PLUGIN_SYSTEM_PATH=/usr/lib/aarch64-linux-gnu/gstreamer-1.0:/usr/lib/arm-linux-gnueabihf/gstreamer-1.0

# Create necessary directories for storage
RUN mkdir -p $LOG_FOLDER $VIDEO_FOLDER

# Expose the web interface port
EXPOSE 8080

LABEL version="1.0.7"
LABEL permissions='\
{"HostConfig":{"Binds":["/usr/blueos/extensions/dashcam/videos/:/home/blueos/videos/","/root/.config/blueos/ardupilot-manager/firmware/logs/:/home/blueos/logs/","/usr/blueos/extensions/dashcam/settings/:/home/blueos/settings/"],"CpuQuota":400000,"CpuPeriod":100000,"ExtraHosts":["blueos.internal:host-gateway"],"PortBindings":{"8080/tcp":[{"HostPort":""}]}}}'

LABEL authors='[\
    {\
        "name": "Willian Galvani",\
        "email": "willian@bluerobotics.com"\
    }\
]'
LABEL company='{\
        "about": "",\
        "name": "Blue Robotics",\
        "email": "support@bluerobotics.com"\
    }'
LABEL type="other"
LABEL tags='[\
        "Media"\
    ]'
LABEL readme='https://raw.githubusercontent.com/Williangalvani/BlueOS-Dashcam/{tag}/Readme.md'
LABEL links='{\
        "website": "https://github.com/Williangalvani/BlueOS-Dashcam/",\
        "support": "https://github.com/Williangalvani/BlueOS-Dashcam/issues"\
    }'
LABEL requirements="core >= 1.4"

# Run the application
CMD ["sh", "-c", "python video_recorder.py --log-folder $LOG_FOLDER --video-folder $VIDEO_FOLDER --blueos-address $BLUEOS_ADDRESS"] 