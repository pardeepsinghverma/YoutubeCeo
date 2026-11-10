import sys
import os
import re
import subprocess
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
from config import FFMPEG_PATH, OUTPUT_AUDIO_DIR

def inspect_video(video_path: Path):
    """
    Uses the bundled FFmpeg binary to extract video dimensions and duration.
    Returns: dict(duration_seconds, width, height, is_short, file_size_mb)
    """
    video_path = Path(video_path)
    file_size_mb = round(video_path.stat().st_size / (1024 * 1024), 2)
    
    # Run ffmpeg -i <path> to capture stderr info
    cmd = [FFMPEG_PATH, "-i", str(video_path)]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    output = result.stderr

    # Parse duration: Duration: 00:01:23.45,
    duration = 0.0
    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if dur_match:
        h, m, s = dur_match.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)

    # Parse resolution: Stream ... Video: ... 1920x1080 [SAR ...
    width, height = 0, 0
    res_match = re.search(r",\s*(\d{2,5})x(\d{2,5})(?:\s*\[|\s*,|\s+SAR)", output)
    if res_match:
        width = int(res_match.group(1))
        height = int(res_match.group(2))

    # Check for audio stream
    has_audio = bool(re.search(r"Stream\s*#\d+:\d+.*?: Audio:", output, re.IGNORECASE))

    # Determine if it's a Short
    # YouTube Short: Duration <= 60 seconds and vertical/square (height >= width)
    is_short = False
    if duration <= 60.5 and height >= width and width > 0:
        is_short = True

    return {
        "duration_seconds": round(duration, 2),
        "width": width,
        "height": height,
        "is_short": is_short,
        "has_audio": has_audio,
        "file_size_mb": file_size_mb
    }

import hashlib

def extract_audio(video_path: Path) -> Path:
    """
    Extracts audio to a compact mono mp3 for fast AI processing.
    Uses MD5 hash filename to ensure safe ASCII headers during API uploads.
    """
    video_path = Path(video_path)
    file_hash = hashlib.md5(video_path.name.encode("utf-8", errors="ignore")).hexdigest()[:12]
    audio_path = OUTPUT_AUDIO_DIR / f"audio_{file_hash}.mp3"
    
    if audio_path.exists():
        return audio_path

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "64k",
        str(audio_path)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return audio_path

def extract_thumbnail_frame(video_path: Path, timestamp_sec: float = 2.0) -> Path:
    """
    Extracts a high-quality keyframe image snapshot from the video for thumbnail analysis.
    Uses MD5 hash filename to ensure safe ASCII headers during API uploads.
    """
    video_path = Path(video_path)
    file_hash = hashlib.md5(video_path.name.encode("utf-8", errors="ignore")).hexdigest()[:12]
    thumb_path = OUTPUT_AUDIO_DIR / f"thumb_{file_hash}.jpg"
    
    if thumb_path.exists():
        return thumb_path

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-ss", str(timestamp_sec),
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(thumb_path)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return thumb_path

if __name__ == "__main__":
    print("Media inspector initialized. Ready to inspect video files.")
