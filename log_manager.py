import sys
import json
from datetime import datetime
from pathlib import Path
from collections import deque

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from config import LOGS_JSON_PATH, BASE_DIR

EXECUTION_LOG_PATH = BASE_DIR / "upload_execution.log"

# Keep the latest 300 log events in memory for live UI polling
LIVE_LOG_BUFFER = deque(maxlen=300)

def step_log(msg: str, level: str = "INFO", video_id: int = None):
    """
    Logs an individual step in the upload & scheduling pipeline.
    Writes to stdout, appends to upload_execution.log, and caches in memory.
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = f"[#{video_id}] " if video_id else ""
    line = f"[{ts}] [{level.upper()}] {prefix}{msg}"
    print(line, flush=True)

    LIVE_LOG_BUFFER.append(line)
    try:
        with open(EXECUTION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def get_live_step_logs(lines: int = 100) -> list:
    """Returns the most recent lines from the live execution log."""
    if LIVE_LOG_BUFFER:
        return list(LIVE_LOG_BUFFER)[-lines:]
    if EXECUTION_LOG_PATH.exists():
        try:
            with open(EXECUTION_LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
                return [l.strip() for l in all_lines[-lines:]]
        except Exception:
            return []
    return []

def init_logs_file():
    if not LOGS_JSON_PATH.exists():
        with open(LOGS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)

def get_logs() -> list:
    init_logs_file()
    try:
        with open(LOGS_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def log_upload(
    video_id: int,
    file_name: str,
    file_path: str,
    is_short: bool,
    title: str,
    description: str,
    tags: list,
    hashtags: list,
    scheduled_datetime: str,
    youtube_url: str = "",
    status: str = "SUCCESS",
    error_message: str = None,
    screenshot_path: str = None
):
    """
    Appends a detailed log record of an upload attempt to upload_logs.json.
    """
    init_logs_file()
    logs = get_logs()

    record = {
        "log_id": len(logs) + 1,
        "logged_at": datetime.now().isoformat(),
        "video_id": video_id,
        "file_name": file_name,
        "file_path": str(file_path),
        "video_type": "SHORT" if is_short else "LONG_FORM",
        "title": title,
        "description": description,
        "tags": tags if isinstance(tags, list) else [],
        "hashtags": hashtags if isinstance(hashtags, list) else [],
        "scheduled_for": scheduled_datetime,
        "youtube_url": youtube_url or "",
        "status": status,
        "error": error_message,
        "error_screenshot": screenshot_path
    }

    logs.append(record)

    with open(LOGS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2, ensure_ascii=False)

    step_log(f"Upload record saved to {LOGS_JSON_PATH.name} (Log #{record['log_id']}, Status: {status})", level=status, video_id=video_id)
    return record

if __name__ == "__main__":
    init_logs_file()
    print(f"Logs initialized at: {LOGS_JSON_PATH}")
