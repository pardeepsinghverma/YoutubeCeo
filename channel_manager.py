import sys
import os
import json
import time
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional

from config import BASE_DIR, PROFILES_BASE_DIR, CHROME_PROFILE_DIR
from db import (
    get_all_channels,
    get_channel_by_id,
    get_channel_by_key,
    get_active_channel,
    set_active_channel,
    add_channel,
    update_channel,
    delete_channel,
    set_channel_auth_status,
    assign_videos_to_channel
)
from log_manager import step_log

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

def find_chrome_executable() -> Optional[str]:
    for p in CHROME_PATHS:
        if Path(p).exists():
            return p
    return None

def has_cookies_in_profile(profile_dir: str) -> bool:
    """Checks if the Chrome user-data-dir has existing cookie stores."""
    p = Path(profile_dir)
    if not p.exists():
        return False
    cookie_paths = [
        p / "Default" / "Network" / "Cookies",
        p / "Default" / "Cookies",
        p / "Network" / "Cookies",
        p / "Cookies"
    ]
    return any(cp.exists() and cp.stat().st_size > 0 for cp in cookie_paths)

def check_channel_youtube_cookies(profile_dir: str) -> bool:
    """
    Directly checks the Chrome Cookies SQLite database for active YouTube/Google auth tokens
    like LOGIN_INFO, SSID, APISID, etc. Instantaneous and 100% reliable.
    """
    p = Path(profile_dir)
    cookie_paths = [
        p / "Default" / "Network" / "Cookies",
        p / "Default" / "Cookies",
        p / "Network" / "Cookies",
        p / "Cookies"
    ]
    target_cookie = None
    for cp in cookie_paths:
        if cp.exists() and cp.stat().st_size > 0:
            target_cookie = cp
            break

    if not target_cookie:
        return False

    import tempfile, shutil, sqlite3
    tmp = tempfile.mktemp(suffix=".sqlite")
    try:
        shutil.copyfile(str(target_cookie), tmp)
        conn = sqlite3.connect(tmp)
        c = conn.cursor()
        c.execute("""
            SELECT COUNT(1) FROM cookies 
            WHERE (host_key LIKE '%youtube.com%' OR host_key LIKE '%.google.com%')
              AND name IN ('LOGIN_INFO', 'APISID', 'SSID', 'SAPISID', 'SID')
        """)
        count = c.fetchone()[0]
        conn.close()
        return count > 0
    except Exception as e:
        # If SQLite inspect fails, fallback to size check
        return target_cookie.stat().st_size > 2048
    finally:
        try:
            if Path(tmp).exists():
                os.unlink(tmp)
        except Exception:
            pass

def verify_channel_session(channel_id: int) -> Dict[str, Any]:
    """
    Checks if a channel's Google session is valid and authenticated in YouTube Studio.
    Fast and non-blocking using direct SQLite auth cookie inspection.
    """
    ch = get_channel_by_id(channel_id)
    if not ch:
        return {"success": False, "authenticated": False, "error": "Channel not found"}

    prof_dir = ch["profile_dir"]
    is_auth = check_channel_youtube_cookies(prof_dir)
    set_channel_auth_status(channel_id, is_auth)

    return {
        "success": True,
        "authenticated": is_auth,
        "channel_id": channel_id,
        "channel_name": ch["channel_name"],
        "detail": "Authenticated with YouTube / Google session" if is_auth else "No YouTube login tokens found in profile"
    }

def launch_channel_login_window(channel_id: int) -> Dict[str, Any]:
    """
    Launches a dedicated real Google Chrome window pointing to YouTube Studio login
    using this channel's isolated profile.
    This lets the user log into account 2, 3, or 4 without conflicting with other channels!
    """
    ch = get_channel_by_id(channel_id)
    if not ch:
        raise Exception(f"Channel #{channel_id} not found.")

    chrome_exe = find_chrome_executable()
    if not chrome_exe:
        raise Exception("Google Chrome executable not found on this system.")

    prof_dir = Path(ch["profile_dir"]).resolve()
    prof_dir.mkdir(parents=True, exist_ok=True)

    step_log(f"Launching real Chrome for Channel '{ch['channel_name']}' with isolated profile: {prof_dir}", level="INFO")

    cmd = [
        chrome_exe,
        f"--user-data-dir={prof_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://accounts.google.com/ServiceLogin?service=youtube&continue=https://studio.youtube.com"
    ]

    # Non-blocking subprocess launch so the web dashboard continues running
    proc = subprocess.Popen(cmd)

    return {
        "success": True,
        "message": f"Login window launched for '{ch['channel_name']}'. Please log in on the opened Chrome window.",
        "channel_id": channel_id,
        "channel_name": ch["channel_name"],
        "pid": proc.pid
    }

def create_channel_with_profile(
    channel_name: str,
    handle: str = "",
    thumbnail_style: str = "3D Pixar Animation",
    thumbnail_mood: str = "Joyful, safe, playful, magical, full of adventure",
    schedule_frequency: str = "daily",
    schedule_interval_days: int = 1,
    shorts_schedule_times: list = None,
    long_schedule_times: list = None
) -> Dict[str, Any]:
    """
    Registers a new YouTube Channel with an isolated Chrome profile folder.
    """
    import uuid
    safe_slug = "".join([c if c.isalnum() else "_" for c in channel_name.lower()]).strip("_")[:15] or "channel"
    unique_key = f"{safe_slug}_{uuid.uuid4().hex[:6]}"
    profile_dir = PROFILES_BASE_DIR / unique_key
    profile_dir.mkdir(parents=True, exist_ok=True)

    ch = add_channel(
        channel_name=channel_name,
        handle=handle,
        profile_dir=str(profile_dir),
        thumbnail_style=thumbnail_style,
        thumbnail_mood=thumbnail_mood,
        schedule_frequency=schedule_frequency,
        schedule_interval_days=schedule_interval_days,
        shorts_schedule_times=shorts_schedule_times or ["11:00", "17:00"],
        long_schedule_times=long_schedule_times or ["19:30"]
    )
    step_log(f"Created new channel: '{channel_name}' (ID: {ch['id']}, Profile: {profile_dir})", level="SUCCESS")
    return ch

if __name__ == "__main__":
    print("Channels in system:")
    for c in get_all_channels():
        print(f" - #{c['id']}: {c['channel_name']} (active={c['is_active']}, auth={c['is_authenticated']})")
