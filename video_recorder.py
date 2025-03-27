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

class VideoRecorder:
    SETTINGS_PATH = "/home/blueos/settings/dashcam.json"
    
    def __init__(self, log_folder: str, video_folder: str, mavlink_url: str):
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            force=True,
            stream=sys.stdout  # Ensure logs go to stdout for Docker
        )
        self.logger = logging.getLogger("dashcam")
        
        self.settings = self.load_settings()
        self.settings["settings"]["log_folder"] = log_folder
        self.settings["settings"]["video_folder"] = video_folder
        self.logger.info(f"Settings path: {self.SETTINGS_PATH}")
        self.logger.info(f"Settings: {self.settings}")
        self.mavlink_url = mavlink_url
        self.recording_processes: Dict[str, subprocess.Popen] = {}
        self.is_armed = False
        self.ws = None
        self.app = web.Application()
        self.setup_routes()

    def load_settings(self) -> dict:
        settings_path = Path(self.SETTINGS_PATH)
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
                "out_of_space_action": "stop"
            }
        }

    def save_settings(self):
        settings_path = Path(self.SETTINGS_PATH)
        with open(settings_path, 'w') as f:
            json.dump(self.settings, f, indent=4)
        self.logger.info("Settings saved.")

    def setup_routes(self):
        self.app.router.add_get('/', self.handle_index)
        self.app.router.add_post('/update_settings', self.handle_update_settings)
        self.app.router.add_post('/add_stream', self.handle_add_stream)
        self.app.router.add_post('/delete_stream', self.handle_delete_stream)
        self.app.router.add_post('/delete_oldest', self.handle_delete_oldest)
        self.app.router.add_get('/disk_space', self.handle_disk_space)
        self.app.router.add_get('/stream_status', self.handle_stream_status)
        self.app.router.add_get('/register_service', self.handle_register_service)
        
        # Create static directory if it doesn't exist
        static_dir = Path('static')
        if not static_dir.exists():
            static_dir.mkdir(exist_ok=True)
            self.logger.info(f"Created static directory at {static_dir.absolute()}")
            
        self.app.router.add_static('/static', str(static_dir))

    async def handle_update_settings(self, request):
        data = await request.post()
        
        # Update settings
        self.settings["settings"]["minimum_free_space_mb"] = int(data.get("minimum_free_space_mb", 1024))
        self.settings["settings"]["out_of_space_action"] = data.get("out_of_space_action", "stop")
        
        # Save settings to file
        self.save_settings()
        
        # Redirect back to main page
        return web.Response(status=302, headers={'Location': '/'})

    async def handle_add_stream(self, request):
        data = await request.post()
        stream_name = data.get("stream_name")
        stream_url = data.get("stream_url")
        
        if stream_name and stream_url:
            # Check if stream with this name already exists
            for stream in self.settings["streams"]:
                if stream["name"] == stream_name:
                    # Update existing stream
                    stream["url"] = stream_url
                    break
            else:
                # Add new stream
                self.settings["streams"].append({
                    "name": stream_name,
                    "url": stream_url
                })
            
            # Save settings to file
            self.save_settings()
        
        # Redirect back to main page
        return web.Response(status=302, headers={'Location': '/'})

    async def handle_delete_stream(self, request):
        data = await request.post()
        stream_name = data.get("stream_name")
        
        if stream_name:
            # Stop recording if active
            if stream_name in self.recording_processes:
                self.stop_recording(stream_name)
            
            # Remove stream from settings
            self.settings["streams"] = [s for s in self.settings["streams"] if s["name"] != stream_name]
            
            # Save settings to file
            self.save_settings()
        
        # Redirect back to main page
        return web.Response(status=302, headers={'Location': '/'})

    async def handle_delete_oldest(self, request):
        """Manually trigger deletion of oldest video file"""
        deleted = self.delete_oldest_video()
        
        if deleted:
            message = f"Deleted oldest video: {deleted}"
        else:
            message = "No videos to delete"
        
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
            'description': 'Video recording service for BlueOS',
            'icon': 'mdi-video',
            'company': 'Blue Robotics',
            'version': '1.0.0',
            'webpage': 'https://github.com/bluerobotics/BlueOS-Dashcam',
            'api': '/v1.0/docs'
        })

    async def handle_index(self, request):
        # Create a simple HTML response if template file doesn't exist
        template_path = Path("views/index.html")
        if template_path.exists():
            try:
                with open(template_path, "r") as file:
                    template_content = file.read()
                
                # Prepare initial data for frontend
                initial_data = {
                    "is_armed": self.is_armed,
                    "active_recordings": list(self.recording_processes.keys()),
                    "streams": self.settings["streams"],
                    "settings": self.settings["settings"]
                }
                
                # Convert to JSON and pass to template
                import json
                template_content = template_content.replace('{{ initial_data|safe }}', json.dumps(initial_data))
                
                return web.Response(
                    text=template_content,
                    content_type="text/html"
                )
            except Exception as e:
                self.logger.error(f"Error rendering template: {e}")
                # Fall back to simplified response if template can't be rendered
                return self.create_simple_response()
        else:
            self.logger.warning(f"Template file not found at {template_path.absolute()}")
            return self.create_simple_response()

    def create_simple_response(self):
        """Create a simple HTML response if template file doesn't exist"""
        html = """
        <!DOCTYPE html>
        <html>
            <head>
                <title>Video Recorder</title>
                <style>
                    body { font-family: Arial, sans-serif; margin: 20px; }
                    h1 { color: #333; }
                    .status { padding: 10px; border-radius: 5px; margin: 10px 0; }
                    .armed { background-color: #e6ffe6; border: 1px solid #4CAF50; }
                    .disarmed { background-color: #ffe6e6; border: 1px solid #F44336; }
                    .section { background-color: #f2f2f2; padding: 15px; margin: 15px 0; border-radius: 5px; }
                    .item { background-color: white; padding: 10px; margin: 5px 0; border-radius: 3px; }
                    button { background-color: #4CAF50; color: white; border: none; padding: 10px 15px; cursor: pointer; border-radius: 3px; }
                    button.delete { background-color: #F44336; }
                </style>
            </head>
            <body>
                <h1>Video Recorder</h1>
                <div class="status {0}">
                    Vehicle Status: <strong>{1}</strong>
                </div>
                <div class="section">
                    <h2>Active Recordings</h2>
                    {2}
                </div>
                <div class="section">
                    <h2>Streams Configuration</h2>
                    {3}
                    <form action="/add_stream" method="post">
                        <h3>Add Stream</h3>
                        <p>Stream Name: <input type="text" name="stream_name" required></p>
                        <p>Stream URL: <input type="text" name="stream_url" required></p>
                        <button type="submit">Save Stream</button>
                    </form>
                </div>
                <div class="section">
                    <h2>Settings</h2>
                    <p>Log Folder: {4}</p>
                    <p>Video Folder: {5}</p>
                    <p>Minimum Free Space: {6} MB</p>
                    <form action="/update_settings" method="post">
                        <h3>Update Settings</h3>
                        <p>Minimum Free Space (MB): <input type="number" name="minimum_free_space_mb" value="{6}" required></p>
                        <p>
                            Out of Space Action: 
                            <select name="out_of_space_action">
                                <option value="stop" {7}>Stop Recording</option>
                                <option value="delete_oldest_video" {8}>Delete Oldest Video</option>
                            </select>
                        </p>
                        <button type="submit">Save Settings</button>
                    </form>
                    <form action="/delete_oldest" method="post" style="margin-top: 20px;">
                        <button type="submit" class="delete">Delete Oldest Video Now</button>
                    </form>
                </div>
            </body>
        </html>
        """
        
        armed_status = "armed" if self.is_armed else "disarmed"
        armed_text = "ARMED" if self.is_armed else "DISARMED"
        
        # Build active recordings HTML
        active_recordings_html = ""
        if self.recording_processes:
            for stream in self.recording_processes.keys():
                active_recordings_html += f'<div class="item">{stream}</div>'
        else:
            active_recordings_html = '<div class="item">No active recordings</div>'
        
        # Build streams HTML
        streams_html = ""
        if self.settings["streams"]:
            for stream in self.settings["streams"]:
                streams_html += f'<div class="item"><strong>{stream["name"]}</strong><br>URL: {stream["url"]}'
                streams_html += f'<form action="/delete_stream" method="post" style="display:inline; float:right">'
                streams_html += f'<input type="hidden" name="stream_name" value="{stream["name"]}">'
                streams_html += f'<button type="submit" class="delete">Delete</button></form></div>'
        else:
            streams_html = '<div class="item">No streams configured</div>'
        
        # Selected state for dropdown
        stop_selected = 'selected' if self.settings["settings"]["out_of_space_action"] == "stop" else ''
        delete_selected = 'selected' if self.settings["settings"]["out_of_space_action"] == "delete_oldest_video" else ''
        
        return web.Response(
            text=html.format(
                armed_status,
                armed_text,
                active_recordings_html,
                streams_html,
                self.settings["settings"]["log_folder"],
                self.settings["settings"]["video_folder"],
                self.settings["settings"]["minimum_free_space_mb"],
                stop_selected,
                delete_selected
            ),
            content_type='text/html'
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

    def start_recording(self, stream: dict, base_filename: str):
        """Start recording a single stream"""
        # Use %03d as a placeholder for segment numbers (000, 001, etc.)
        output_file = f"{base_filename}_{stream['name']}_%03d.mp4"
        output_path = Path(self.settings["settings"]["video_folder"]) / output_file

        # FFmpeg command with 1GB chunk size
        cmd = [
            "ffmpeg", "-i", stream["url"],
            "-c:v", "copy",  # Only copy video stream
            "-an",  # Disable audio
            "-f", "segment",
            "-segment_time", "3600",  # 1 hour segments
            "-segment_format", "mp4",
            "-reset_timestamps", "1",
            str(output_path)
        ]

        self.logger.info(f"Starting recording for {stream['name']} to {output_path}")
        self.logger.info(f"FFmpeg command: {' '.join(cmd)}")  # Print the command for debugging
        process = subprocess.Popen(cmd)
        self.recording_processes[stream["name"]] = process

    def stop_recording(self, stream_name: str):
        """Stop recording a single stream"""
        if stream_name in self.recording_processes:
            self.logger.info(f"Stopping recording for {stream_name}")
            process = self.recording_processes[stream_name]
            process.terminate()
            process.wait()
            del self.recording_processes[stream_name]

    def handle_space_issue(self):
        """Handle out of space situation"""
        action = self.settings["settings"]["out_of_space_action"]
        if action == "stop":
            for stream_name in list(self.recording_processes.keys()):
                self.stop_recording(stream_name)
        elif action == "delete_oldest_video":
            self.delete_oldest_video()

    def delete_oldest_video(self) -> Optional[str]:
        """Delete the oldest video file and return its name or None if no videos exist"""
        video_folder = Path(self.settings["settings"]["video_folder"])
        video_files = list(video_folder.glob("*.mp4"))
        if video_files:
            oldest_video = min(video_files, key=lambda x: x.stat().st_mtime)
            self.logger.info(f"Deleting oldest video: {oldest_video}")
            oldest_video.unlink()
            return str(oldest_video.name)
        return None

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
            if base_filename:
                self.logger.info(f"Found latest bin file: {base_filename}")
                for stream in self.settings["streams"]:
                    if self.get_free_space_mb() < self.settings["settings"]["minimum_free_space_mb"]:
                        self.handle_space_issue()
                    self.start_recording(stream, base_filename)
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
        self.logger.info(f"Settings path: {self.SETTINGS_PATH}")
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

async def main():
    parser = argparse.ArgumentParser(description='Video Recorder for BlueOS')
    parser.add_argument('--log-folder', required=True, help='Path to the log folder containing .bin files')
    parser.add_argument('--video-folder', required=True, help='Path to store video recordings')
    parser.add_argument('--mavlink-url', default='ws://blueos.internal:6040/mavlink/ws/mavlink?filter=HEARTBEAT',
                       help='WebSocket URL for MAVLink2Rest connection')
    args = parser.parse_args()

    # Setup logging at the start of main
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        force=True,
        stream=sys.stdout
    )
    logger = logging.getLogger("dashcam")

    # Ensure directories exist
    for directory in [args.log_folder, args.video_folder]:
        if not os.path.exists(directory):
            logger.info(f"Creating directory: {directory}")
            os.makedirs(directory, exist_ok=True)

    recorder = VideoRecorder(args.log_folder, args.video_folder, args.mavlink_url)
    try:
        await recorder.run()
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main())) 