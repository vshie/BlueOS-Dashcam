FROM bluerobotics/blueos-base:latest

# Set working directory
WORKDIR /app

# Create necessary directories
RUN mkdir -p static views

# Download Vue.js into static directory (using a specific version to ensure stability)
RUN curl -s https://unpkg.com/vue@3.3.4/dist/vue.global.prod.js -o static/vue.js

RUN apt update && apt install -y gcc
RUN python -m pip install websockets aiohttp

# Copy the rest of the application
COPY . .

# Set default environment variables
ENV LOG_FOLDER=/home/blueos/logs
ENV VIDEO_FOLDER=/home/blueos/videos
ENV SETTINGS_FOLDER=/home/blueos/settings
ENV BLUEOS_ADDRESS=blueos.internal
ENV PYTHONUNBUFFERED=1

# Create necessary directories for storage
RUN mkdir -p $LOG_FOLDER $VIDEO_FOLDER

# Expose the web interface port
EXPOSE 8080

LABEL version="1.0.7"
LABEL permissions='\
{"HostConfig":{"Binds":["/usr/blueos/extensions/dashcam/videos/:/home/blueos/videos/","/root/.config/blueos/ardupilot-manager/firmware/logs/:/home/blueos/logs/","/usr/blueos/extensions/dashcam/settings/:/home/blueos/settings/"],"CpuQuota":100000,"CpuPeriod":100000,"ExtraHosts":["blueos.internal:host-gateway"],"PortBindings":{"8080/tcp":[{"HostPort":""}]}}}'

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