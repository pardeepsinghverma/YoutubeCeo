import os
from pathlib import Path
from dotenv import load_dotenv
import imageio_ffmpeg

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "videos.db"
CHROME_PROFILE_DIR = BASE_DIR / "chrome_profile"
PROFILES_BASE_DIR = BASE_DIR / "chrome_profiles"
OUTPUT_AUDIO_DIR = BASE_DIR / "temp_audio"
OUTPUT_AUDIO_DIR.mkdir(exist_ok=True)
CHROME_PROFILE_DIR.mkdir(exist_ok=True)
PROFILES_BASE_DIR.mkdir(exist_ok=True)

# Gemini API Key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Scheduling Defaults
SHORTS_DAILY_LIMIT = int(os.getenv("SHORTS_DAILY_LIMIT", "2"))
LONG_DAILY_LIMIT = int(os.getenv("LONG_DAILY_LIMIT", "1"))

# Publishing Times (24h format, local timezone)
SHORTS_SCHEDULE_TIMES = ["11:00", "17:00"]
LONG_SCHEDULE_TIMES = ["19:30"]

# FFmpeg Executable from imageio-ffmpeg
FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

# JSON Upload Logs
LOGS_JSON_PATH = BASE_DIR / "upload_logs.json"
