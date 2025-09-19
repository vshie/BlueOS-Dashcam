import asyncio
import json
import os
import shutil
import websockets
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import subprocess
from aiohttp import web
import argparse
import sys
import logging
import time
import signal
import aiohttp
import re

class VideoRecorder:
    def __init__(self, log_folder: str, video_folder: str, mavlink_url: str, settings_path: str = "/home/blueos/settings/dashcam.json"):
        # Setup logging
        # Create logs directory if it doesn't exist
        log_dir = Path(video_folder).parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "dashcam.log"
        
        # Create file handler for logging to file
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        
        # Create console handler for stdout
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        
        # Configure root logger
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            force=True,
            handlers=[file_handler, console_handler]
        )
        self.logger = logging.getLogger("dashcam")
        
        self.settings_path = settings_path
        self.settings = self.load_settings()
        self.settings["settings"]["log_folder"] = log_folder
        self.settings["settings"]["video_folder"] = video_folder
        self.logger.info(f"Settings path: {self.settings_path}")
        self.logger.info(f"Settings: {self.settings}")
        self.mavlink_url = mavlink_url
        self.recording_processes: Dict[str, subprocess.Popen] = {}
        self.is_armed = False
        self.ws = None
        self.app = web.Application()
        self.setup_routes()

    def load_settings(self) -> dict:
        settings_path = Path(self.settings_path)
        if settings_path.exists():
            with open(settings_path) as f:
                return json.load(f)
        # Create settings directory if it doesn't exist
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        return {
            "streams": [],
            "settings": {
                "log_folder": "/home/blueos/logs",
                "video_folder": "/home/blueos/videos",
                "minimum_free_space_mb": 1024,
                "out_of_space_action": "delete_oldest_video",
                "segment_size": 500  # Size in MB for video segments
            }
        }

    def save_settings(self):
        settings_path = Path(self.settings_path)
        with open(settings_path, 'w') as f:
            json.dump(self.settings, f, indent=4)
        self.logger.info("Settings saved.")

    def setup_routes(self):
        self.app.router.add_get('/', self.handle_index)
        self.app.router.add_post('/update_settings', self.handle_update_settings)
        self.app.router.add_post('/delete_oldest', self.handle_delete_oldest)
        self.app.router.add_get('/api/settings', self.handle_settings_api)
        self.app.router.add_post('/api/settings', self.handle_settings_update)
        self.app.router.add_get('/api/status', self.handle_status_api)
        self.app.router.add_get('/register_service', self.handle_register_service)
        
        # Create static directory if it doesn't exist
        static_dir = Path('static')
        self.app.router.add_static('/static', str(static_dir))

    async def fetch_camera_streams(self) -> List[dict]:
        """Fetch available streams from MAVLink camera manager"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://{self.mavlink_url.split('://')[1].split('/')[0]}/mavlink-camera-manager/streams") as response:
                    if response.status == 200:
                        streams = await response.json()
                        new_streams = []
                        for stream in streams:
                            url = stream["video_and_stream"]["stream_information"]["endpoints"][0]
                            if "rtsp" in url.lower():
                                new_streams.append({
                                    "name": stream["video_and_stream"]["name"],
                                    "url": url.replace("rtspu://", "rtsp://").replace("rtspt://", "rtsp://").replace("rtsph://", "rtsp://").replace("0.0.0.0", "blueos.internal"),
                                    "enabled": True
                                })
                        return new_streams
  
                    else:
                        self.logger.error(f"Failed to fetch streams: {response.status}")
                        return []
        except Exception as e:
            self.logger.error(f"Error fetching streams: {e}")
            return []

    async def update_streams_from_camera_manager(self):
        """Update settings with streams from camera manager"""
        camera_streams = await self.fetch_camera_streams()
        
        # Update existing streams and add new ones
        existing_names = {stream["name"] for stream in self.settings["streams"]}
        
        for stream in camera_streams:
            if stream["name"] not in existing_names:
                self.settings["streams"].append(stream)
                self.logger.info(f"Added new stream: {stream['name']}")
            else:
                # Update URL of existing stream but preserve enabled state
                for existing_stream in self.settings["streams"]:
                    if existing_stream["name"] == stream["name"]:
                        existing_stream["url"] = stream["url"]
                        break
        
        self.save_settings()

    async def handle_update_settings(self, request):
        data = await request.post()
        # data should always be the full settings object
        self.settings = data
        self.save_settings()
        
        # Redirect back to main page
        return web.Response(status=302, headers={'Location': '/'})

    async def handle_delete_oldest(self, request=None):
        """Manually trigger deletion of oldest video file"""
        video_folder = Path(self.settings["settings"]["video_folder"])
        video_files = list(video_folder.glob("*.mp4"))
        message = ""
        if video_files:
            oldest_video = min(video_files, key=lambda x: x.stat().st_mtime)
            self.logger.info(f"Deleting oldest video: {oldest_video}")
            oldest_video.unlink()
            message = f"deleted oldest video: {oldest_video}"
        else:
            message = "no videos to delete"
        # Redirect back to main page
        return web.Response(status=302, headers={'Location': f'/?message={message}'})

    async def handle_disk_space(self, request):
        """Return disk space information as JSON"""
        try:
            video_folder = Path(self.settings["settings"]["video_folder"])
            if not video_folder.exists():
                self.logger.warning(f"Warning: Video folder {video_folder} doesn't exist. Creating it.")
                video_folder.mkdir(parents=True, exist_ok=True)
                
            usage = shutil.disk_usage(video_folder)
            
            # Calculate free space and total space in bytes
            free_bytes = usage.free
            total_bytes = usage.total
            free_mb = free_bytes // (1024 * 1024)
            
            response_data = {
                'freeBytes': free_bytes,
                'totalBytes': total_bytes,
                'freeMb': free_mb,
                'minimumFreeMb': self.settings["settings"]["minimum_free_space_mb"]
            }
            
            return web.json_response(response_data)
            
        except Exception as e:
            self.logger.error(f"Error getting disk space: {e}")
            return web.json_response({
                'freeBytes': 0,
                'totalBytes': 0,
                'freeMb': 0,
                'minimumFreeMb': self.settings["settings"]["minimum_free_space_mb"],
                'error': str(e)
            }, status=500)

    async def handle_stream_status(self, request):
        """Return stream status information as JSON"""
        return web.json_response({
            'is_armed': self.is_armed,
            'active_recordings': list(self.recording_processes.keys()),
            'streams_configured': len(self.settings["streams"]),
            'timestamp': datetime.now().isoformat()
        })

    async def handle_register_service(self, request):
        """Handle BlueOS service registration"""
        return web.json_response({
            'name': 'Dashcam',
            'description': 'Video recording extension for BlueOS',
            'icon': 'mdi-video',
            'company': 'Blue Robotics',
            'version': '1.0.0',
            'webpage': 'https://github.com/bluerobotics/BlueOS-Dashcam',
            'api': '/v1.0/docs'
        })

    async def handle_dashcam_data(self, request):
        """Return all dashcam data as JSON"""
        # Check disk space
        try:
            video_folder = Path(self.settings["settings"]["video_folder"])
            if not video_folder.exists():
                video_folder.mkdir(parents=True, exist_ok=True)
                
            usage = shutil.disk_usage(video_folder)
            free_bytes = usage.free
            total_bytes = usage.total
            free_mb = free_bytes // (1024 * 1024)
            
            disk_space = {
                'freeBytes': free_bytes,
                'totalBytes': total_bytes,
                'freeMb': free_mb,
                'minimumFreeMb': self.settings["settings"]["minimum_free_space_mb"]
            }
        except Exception as e:
            self.logger.error(f"Error getting disk space: {e}")
            disk_space = {
                'freeBytes': 0,
                'totalBytes': 0,
                'freeMb': 0,
                'minimumFreeMb': self.settings["settings"]["minimum_free_space_mb"],
                'error': str(e)
            }
        
        # Compile all data
        response_data = {
            'is_armed': self.is_armed,
            'active_recordings': list(self.recording_processes.keys()),
            'streams': self.settings["streams"],
            'settings': self.settings["settings"],
            'disk_space': disk_space,
            'timestamp': datetime.now().isoformat()
        }
        
        return web.json_response(response_data)

    async def handle_index(self, request):
        # Update streams from camera manager before serving the page
        await self.update_streams_from_camera_manager()
        
        # Simply serve the HTML template without embedded data
        template_path = Path("views/index.html")
        with open(template_path, "r") as file:
            template_content = file.read()
        
        return web.Response(
            text=template_content,
            content_type="text/html"
        )

    def get_free_space_mb(self) -> int:
        """Get free space in MB for the video folder"""
        path = Path(self.settings["settings"]["video_folder"])
        return shutil.disk_usage(path).free // (1024 * 1024)

    def get_latest_bin_file(self) -> Optional[str]:
        """Get the latest .bin file from the log folder"""
        log_folder = Path(self.settings["settings"]["log_folder"])
        bin_files = list(log_folder.glob("*.BIN"))
        if not bin_files:
            return None
        return max(bin_files, key=lambda x: x.stat().st_mtime).stem

    def get_next_video_file(self) -> Optional[str]:
        """Get the highest numbered .mp4 file from the video folder"""
        video_folder = Path(self.settings["settings"]["video_folder"])
        video_files = list(video_folder.glob("*.mp4"))
        max_number = 0
        for video_file in video_files:
            self.logger.debug(f"Checking file {video_file}")
            filename = video_file.stem
            self.logger.debug(f"Filename: {filename}")
            # Extract number before first underscore
            if '_' in filename:
                number_part = filename.split('_')[0]
                try:
                    number = int(number_part)
                    max_number = max(max_number, number)
                except ValueError as e:
                    self.logger.debug(f"Skipping file {filename} because {e}")
                    continue
        return str(max_number + 1) if max_number is not None else "0"

    def sanitize_filename(self, name: str) -> str:
        """Sanitize a string to be safe for use in filenames"""
        # Replace problematic characters with underscores
        # Unsafe characters: / \ : * ? " < > | and whitespace
        unsafe_chars = r'[\\/*?:"<>|\s]'
        sanitized = re.sub(unsafe_chars, '_', name)

        # Remove leading/trailing whitespace and periods
        sanitized = sanitized.strip('. ')

        # Ensure we return something if the name is empty after sanitization
        if not sanitized:
            sanitized = "unnamed_stream"

        self.logger.debug(f"Sanitized stream name '{name}' to '{sanitized}'")
        return sanitized

    def start_recording(self, stream: dict, base_filename: str):
        """Start recording a single stream using GStreamer"""
        # Create a base filename for splitmuxsink
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

        # Sanitize the stream name for safe use in filenames
        sanitized_stream_name = self.sanitize_filename(stream['name'])

        base_output = f"{base_filename}_{sanitized_stream_name}_{timestamp}"
        output_dir = Path(self.settings["settings"]["video_folder"])
        output_pattern = str(output_dir / f"{base_output}_%02d.mp4")

        # Get segment size from settings, with fallback to 500 MB if not set
        segment_size_mb = self.settings["settings"].get("segment_size", 500)
        segment_size_bytes = segment_size_mb * 1024 * 1024

        # Build the GStreamer pipeline command - handle both H.264 and H.265 streams
        # Use decodebin to automatically handle different codecs
        cmd = [
            "gst-launch-1.0",
            "-e",  # Handle EOS gracefully
            "rtspsrc",
            f"location={stream['url']}",
            "latency=0",  # Small latency for better sync
            "!",
            "queue",
            "max-size-buffers=100",  # Reasonable buffer size
            "max-size-time=1000000000",  # 1 second buffer
            "!",
            "decodebin",  # Auto-detect and decode H.264/H.265 streams
            "!",
            "videoconvert",  # Ensure proper color space conversion
            "!",
            "videoscale",  # Handle resolution changes
            "!",
            "x264enc",  # Use software encoder for ARM compatibility
            "bitrate=2000",  # Set reasonable bitrate for ARM
            "speed-preset=ultrafast",  # Optimize for ARM performance
            "!",
            "h264parse",  # Parse encoded H.264
            "!",
            "mp4mux",  # Direct mp4 muxing
            "faststart=true",
            "!",
            "filesink",
            f"location={output_pattern.replace('_%02d', '')}"  # Single file for now
        ]

        self.logger.info(f"Starting recording for {stream['name']} to {output_pattern}")
        self.logger.info(f"Stream URL: {stream['url']}")
        self.logger.info(f"Segment size: {segment_size_mb} MB ({segment_size_bytes} bytes)")
        self.logger.info(f"GStreamer command: {' '.join(cmd)}")  # Print the command for debugging
        
        # Test RTSP connection first
        self.logger.info(f"Testing RTSP connection to {stream['url']}...")
        test_cmd = [
            "gst-discoverer-1.0",
            "--timeout=10",
            stream['url']
        ]
        try:
            test_result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=15)
            if test_result.returncode == 0:
                self.logger.info(f"RTSP stream discovery successful: {test_result.stdout.strip()}")
            else:
                self.logger.warning(f"RTSP stream discovery failed: {test_result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            self.logger.warning("RTSP stream discovery timed out")
        except Exception as e:
            self.logger.warning(f"RTSP stream discovery error: {e}")

        # Start subprocess with stdout and stderr capture
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout
            universal_newlines=True,
            bufsize=1  # Line buffered
        )
        
        # Store process and start output monitoring task
        self.recording_processes[stream["name"]] = process
        
        # Start monitoring subprocess output asynchronously
        asyncio.create_task(self._monitor_subprocess_output(stream["name"], process))
        
        # Start a task to check if the file is actually being written
        asyncio.create_task(self._verify_recording_start(stream["name"], output_pattern.replace('_%02d', '')))

    async def _verify_recording_start(self, stream_name: str, output_file: str):
        """Verify that recording has actually started and is writing data"""
        try:
            # Wait a bit for the file to be created
            await asyncio.sleep(3)
            
            output_path = Path(output_file)
            max_checks = 10  # Check for up to 30 seconds
            
            for i in range(max_checks):
                if output_path.exists():
                    file_size = output_path.stat().st_size
                    if file_size > 0:
                        self.logger.info(f"[{stream_name}] Recording verified: {output_file} ({file_size} bytes)")
                        return
                    else:
                        self.logger.warning(f"[{stream_name}] File exists but is empty: {output_file}")
                else:
                    self.logger.warning(f"[{stream_name}] Output file not created yet: {output_file}")
                
                await asyncio.sleep(3)
            
            # If we get here, recording didn't start properly
            self.logger.error(f"[{stream_name}] Recording failed to start properly after 30 seconds")
            if stream_name in self.recording_processes:
                self.stop_recording(stream_name)
                
        except Exception as e:
            self.logger.error(f"Error verifying recording start for {stream_name}: {e}")

    async def _monitor_subprocess_output(self, stream_name: str, process: subprocess.Popen):
        """Monitor subprocess output and log it"""
        try:
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                
                # Log the GStreamer output with stream name prefix
                line = line.strip()
                if line:  # Only log non-empty lines
                    self.logger.info(f"[{stream_name}] {line}")
                    
        except Exception as e:
            self.logger.error(f"Error monitoring output for {stream_name}: {e}")
        finally:
            # Ensure process is cleaned up
            if stream_name in self.recording_processes:
                del self.recording_processes[stream_name]

    def stop_recording(self, stream_name: str):
        """Stop recording a single stream"""
        if stream_name in self.recording_processes:
            self.logger.info(f"Stopping recording for {stream_name}")
            process = self.recording_processes[stream_name]
            
            # Send SIGINT instead of SIGTERM for a more graceful shutdown
            # SIGINT allows GStreamer to handle EOS and finalize the file properly
            process.send_signal(signal.SIGINT)
            
            # Give GStreamer some time to properly finalize the file
            try:
                process.wait(timeout=5)  # Wait up to 5 seconds for proper shutdown
            except subprocess.TimeoutExpired:
                self.logger.warning(f"GStreamer process for {stream_name} did not exit gracefully, forcing termination")
                process.terminate()
                process.wait()
            
            # Close stdout to signal the monitoring task to stop
            if process.stdout:
                process.stdout.close()
                
            # The monitoring task will clean up the process from recording_processes

    async def handle_space_issue(self):
        """Handle out of space situation"""
        action = self.settings["settings"]["out_of_space_action"]
        if action == "stop":
            for stream_name in list(self.recording_processes.keys()):
                self.stop_recording(stream_name)
        elif action == "delete_oldest_video":
            await self.handle_delete_oldest()

    async def process_heartbeat(self, message: dict):
        """Process MAVLink heartbeat message"""
        # Skip messages that aren't HEARTBEAT
        if message.get("message", {}).get("type") != "HEARTBEAT":
            return
        
        # Skip messages from non-autopilot components (e.g. onboard controllers, cameras)
        # Valid autopilots have non-zero values different from MAV_AUTOPILOT_INVALID
        autopilot_type = message.get("message", {}).get("autopilot", {}).get("type")
        valid_autopilots = [
            "MAV_AUTOPILOT_GENERIC", 
            "MAV_AUTOPILOT_ARDUPILOTMEGA",
            "MAV_AUTOPILOT_PX4"
        ]
        if autopilot_type not in valid_autopilots:
            self.logger.debug(f"Ignoring message from non-autopilot component: {autopilot_type}")
            return
        
        # Skip messages from non-vehicle types (like cameras, gimbals, etc.)
        mavtype = message.get("message", {}).get("mavtype", {}).get("type")
        vehicle_types = [
            "MAV_TYPE_FIXED_WING", 
            "MAV_TYPE_QUADROTOR", 
            "MAV_TYPE_HELICOPTER", 
            "MAV_TYPE_GROUND_ROVER", 
            "MAV_TYPE_SUBMARINE",
            "MAV_TYPE_SURFACE_BOAT",
            "MAV_TYPE_VTOL"
        ]
        if mavtype not in vehicle_types:
            self.logger.warning(f"Ignoring message from non-vehicle component: {mavtype}")
            return

        # Extract base_mode from the message
        base_mode = message.get("message", {}).get("base_mode", {}).get("bits", 0)
        self.logger.debug(f"Base mode bits: {base_mode}")
        
        # Check if the vehicle is armed (bit 7 is set)
        is_armed = bool(base_mode & 0x80)
        self.logger.debug(f"Vehicle armed: {is_armed}")
        
        if is_armed and not self.is_armed:
            # Vehicle just armed
            self.logger.info("Vehicle just armed, starting recordings...")
            self.is_armed = True
            base_filename = self.get_latest_bin_file()
            if not base_filename:
                self.logger.info("No latest bin file found, using next video file")
                base_filename = self.get_next_video_file()
            self.logger.info(f"base filename: {base_filename}")
            if base_filename:
                for stream in self.settings["streams"]:
                    # Only record enabled streams
                    if stream.get("enabled", False):
                        count = 0
                        while self.get_free_space_mb() < self.settings["settings"]["minimum_free_space_mb"]:
                            count += 1
                            if count > 10:
                                self.logger.error("Failed to handle space issue!")
                                break
                            try:
                                await self.handle_space_issue()
                            except Exception as e:
                                self.logger.error(f"Error handling space issue: {e}")
                                break
                        if self.get_free_space_mb() >= self.settings["settings"]["minimum_free_space_mb"]:
                            self.logger.info(f"Starting recording for {stream['name']} with base filename: {base_filename}")
                            self.start_recording(stream, base_filename)
                    else:
                        self.logger.info(f"Skipping disabled stream: {stream['name']}")
            else:
                self.logger.info("No .bin files found in log folder")
        
        elif not is_armed and self.is_armed:
            # Vehicle just disarmed
            self.logger.info("Vehicle just disarmed, stopping recordings...")
            self.is_armed = False
            for stream_name in list(self.recording_processes.keys()):
                self.stop_recording(stream_name)

    async def connect_websocket(self):
        """Connect to MAVLink2Rest websocket"""
        while True:
            try:
                self.logger.info(f"Connecting to WebSocket at {self.mavlink_url}")
                async with websockets.connect(self.mavlink_url) as websocket:
                    self.ws = websocket
                    self.logger.info("WebSocket connected successfully")
                    async for message in websocket:
                        await self.process_heartbeat(json.loads(message))
            except Exception as e:
                self.logger.error(f"WebSocket error: {e}")
                await asyncio.sleep(1)  # Wait before reconnecting

    async def run(self):
        """Main run loop"""
        self.logger.info(f"Starting Dashcam service...")
        self.logger.info(f"Settings path: {self.settings_path}")
        # Create necessary directories
        os.makedirs(self.settings["settings"]["log_folder"], exist_ok=True)
        os.makedirs(self.settings["settings"]["video_folder"], exist_ok=True)

        # Start the web server and WebSocket connection concurrently
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        
        try:
            await asyncio.gather(
                site.start(),
                self.connect_websocket()
            )
        except KeyboardInterrupt:
            self.logger.info("Shutting down gracefully...")
            # Stop all active recordings
            for stream_name in list(self.recording_processes.keys()):
                self.stop_recording(stream_name)
            await runner.cleanup()

    async def handle_status_api(self, request):
        """Return current system status as JSON"""
        # Get disk space info
        try:
            video_folder = Path(self.settings["settings"]["video_folder"])
            if not video_folder.exists():
                video_folder.mkdir(parents=True, exist_ok=True)
                
            usage = shutil.disk_usage(video_folder)
            free_bytes = usage.free
            total_bytes = usage.total
            free_mb = free_bytes // (1024 * 1024)
            
            disk_space = {
                'freeBytes': free_bytes,
                'totalBytes': total_bytes,
                'freeMb': free_mb
            }
        except Exception as e:
            self.logger.error(f"Error getting disk space: {e}")
            disk_space = {
                'freeBytes': 0,
                'totalBytes': 0,
                'freeMb': 0,
                'error': str(e)
            }
        
        # Compile and return status information
        response_data = {
            # System paths (read-only)
            'paths': {
                'log_folder': self.settings["settings"]["log_folder"],
                'video_folder': self.settings["settings"]["video_folder"]
            },
            # Vehicle and recording status
            'vehicle': {
                'is_armed': self.is_armed
            },
            'recordings': {
                'active': list(self.recording_processes.keys())
            },
            # Current disk space
            'disk_space': disk_space,
            'timestamp': datetime.now().isoformat()
        }

        return web.json_response(response_data)

    async def handle_settings_api(self, request):
        """Return current settings as JSON"""
        # Update streams from camera manager before responding
        await self.update_streams_from_camera_manager()

        # Format settings to match the expected structure
        response_data = {
            'general': {
                'minimum_free_space_mb': self.settings["settings"]["minimum_free_space_mb"],
                'out_of_space_action': self.settings["settings"]["out_of_space_action"],
                'segment_size': self.settings["settings"].get("segment_size", 500)
            },
            'streams': self.settings["streams"]
        }

        return web.json_response(response_data)

    async def handle_settings_update(self, request):
        """Update settings from API request"""
        try:
            # Get JSON data from request
            data = await request.json()
            
            # Basic validation
            if not isinstance(data, dict):
                return web.json_response({
                    "success": False,
                    "message": "Invalid request format: body must be a JSON object"
                }, status=400)
            
            # Update general settings
            if "general" in data and isinstance(data["general"], dict):
                for key, value in data["general"].items():
                    # Skip read-only settings
                    if key not in ["log_folder", "video_folder"]:
                        self.settings["settings"][key] = value
            
            # Update streams
            if "streams" in data and isinstance(data["streams"], list):
                # Get current active recordings to check if we need to stop any
                current_stream_names = {stream["name"] for stream in self.settings["streams"]}
                new_stream_names = {stream["name"] for stream in data["streams"] if "name" in stream}
                
                # Stop recordings for streams that are being removed
                for stream_name in current_stream_names - new_stream_names:
                    if stream_name in self.recording_processes:
                        self.stop_recording(stream_name)
                
                # Replace the entire streams array
                self.settings["streams"] = data["streams"]
                
                # Ensure all streams have an enabled field
                for stream in self.settings["streams"]:
                    if "enabled" not in stream:
                        stream["enabled"] = True
            
            # Save settings to file
            self.save_settings()
            
            return web.json_response({
                "success": True,
                "message": "Settings updated successfully"
            })
            
        except json.JSONDecodeError:
            return web.json_response({
                "success": False,
                "message": "Invalid JSON data"
            }, status=400)
        except Exception as e:
            self.logger.error(f"Error updating settings: {e}")
            return web.json_response({
                "success": False,
                "message": f"Error updating settings: {str(e)}"
            }, status=500)

async def main():
    parser = argparse.ArgumentParser(description='Video Recorder for BlueOS')
    parser.add_argument('--log-folder', required=True, help='Path to the log folder containing .bin files')
    parser.add_argument('--video-folder', required=True, help='Path to store video recordings')
    parser.add_argument('--blueos-address', default='blueos.internal',
                       help='Address of the BlueOS system')
    parser.add_argument('--settings-path', default='/home/blueos/settings/dashcam.json',
                       help='Path to the settings JSON file')
    args = parser.parse_args()

    # Setup logging at the start of main
    # Create logs directory if it doesn't exist
    log_dir = Path(args.video_folder).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "dashcam.log"
    
    # Create file handler for logging to file
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    
    # Create console handler for stdout
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    
    # Configure root logger
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        force=True,
        handlers=[file_handler, console_handler]
    )
    logger = logging.getLogger("dashcam")

    # Ensure directories exist
    for directory in [args.log_folder, args.video_folder]:
        if not os.path.exists(directory):
            logger.info(f"Creating directory: {directory}")
            os.makedirs(directory, exist_ok=True)

    # Construct MAVLink URL from blueos address
    mavlink_url = f"ws://{args.blueos_address}/mavlink2rest/ws/mavlink?filter=HEARTBEAT"

    recorder = VideoRecorder(args.log_folder, args.video_folder, mavlink_url, args.settings_path)
    try:
        await recorder.run()
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main())) 