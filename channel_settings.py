import json
from pathlib import Path
from typing import Dict, Any
from config import BASE_DIR

SETTINGS_FILE = BASE_DIR / "channel_settings.json"
BRANDING_DIR = BASE_DIR / "branding"
BRANDING_DIR.mkdir(exist_ok=True)

DEFAULT_SETTINGS: Dict[str, Any] = {
    "channel_name": "Kahani Junction",
    "logo_path": "",
    "show_logo": True,
    "logo_position": "top_right",   # options: 'top_right', 'top_left', 'bottom_right'
    "logo_size": 110,               # width in pixels for 1080x1920 layout
    "logo_opacity": 0.9,
    "show_outro": True,
    "outro_text_template": "Watch next part on {channel_name}",
    "outro_duration_sec": 3.5,
    # Scheduling & Frequency
    "schedule_frequency": "daily",         # 'daily', 'alternate_days', 'weekly', 'every_x_days'
    "schedule_interval_days": 1,          # 1=daily, 2=alternate days, 7=weekly, or custom N
    "shorts_schedule_times": ["11:00", "17:00"],
    "long_schedule_times": ["19:30"],
    "schedule_start_days_ahead": 1,
    # AI Thumbnail Art Direction & Prompt Settings
    "thumbnail_art_style": "3D Pixar Animation",
    "thumbnail_mood": "Joyful, safe, playful, magical, full of adventure",
    "thumbnail_prompt_template": (
        "Create a vibrant, adorable, high-quality 3D animated YouTube thumbnail for a kids' storytelling channel called “{channel_name}”. "
        "Show cute cartoon characters with big expressive eyes, cheerful smiles, and playful outfits, "
        "surrounded by a magical fairytale storybook world matching the story scene. "
        "Use a premium Pixar-inspired 3D animation aesthetic, soft rounded character designs, bright candy colors, cinematic rim lighting, rich details, and a dreamy fairytale atmosphere. "
        "Make the characters large and clearly visible, with expressive faces that instantly grab children's attention. "
        "Use a colorful, uncluttered composition with strong contrast, soft depth of field, and a polished professional YouTube thumbnail look. "
        "Target audience: children aged 3–8 and family. The overall mood should feel joyful, safe, playful, magical, and full of adventure. "
        "No scary characters, no dark atmosphere, no photorealism, no clutter, no watermark."
    )
}

def get_channel_settings(channel_id: int = None) -> Dict[str, Any]:
    """
    Reads channel branding, outro, thumbnail, and scheduling settings.
    Single Source of Truth: Reads directly from the channels table in SQLite
    for the specified channel_id or the currently active channel.
    """
    try:
        from db import get_channel_by_id, get_active_channel
        ch = get_channel_by_id(channel_id) if channel_id else get_active_channel()
        if ch:
            shorts_times = json.loads(ch["shorts_schedule_times"]) if isinstance(ch.get("shorts_schedule_times"), str) else (ch.get("shorts_schedule_times") or ["11:00", "17:00"])
            long_times = json.loads(ch["long_schedule_times"]) if isinstance(ch.get("long_schedule_times"), str) else (ch.get("long_schedule_times") or ["19:30"])
            
            cfg = DEFAULT_SETTINGS.copy()
            cfg.update({
                "channel_id": ch["id"],
                "channel_name": ch.get("channel_name") or DEFAULT_SETTINGS["channel_name"],
                "handle": ch.get("handle", ""),
                "profile_dir": ch.get("profile_dir", ""),
                "logo_path": ch.get("logo_path", ""),
                "show_logo": bool(ch.get("show_logo", True if ch.get("logo_path") else False)),
                "logo_position": ch.get("logo_position", "top_right"),
                "logo_size": ch.get("logo_size", 110),
                "logo_opacity": ch.get("logo_opacity", 0.9),
                "show_outro": bool(ch.get("show_outro", True)),
                "watermark_text": ch.get("watermark_text") or f"Watch next part on {ch.get('channel_name', 'our channel')}",
                "outro_text_template": ch.get("watermark_text") or "Watch next part on {channel_name}",
                "outro_duration_sec": ch.get("outro_duration_sec", 3.5),
                "thumbnail_art_style": ch.get("thumbnail_style") or DEFAULT_SETTINGS["thumbnail_art_style"],
                "thumbnail_mood": ch.get("thumbnail_mood") or DEFAULT_SETTINGS["thumbnail_mood"],
                "thumbnail_prompt_template": ch.get("thumbnail_prompt_template") or DEFAULT_SETTINGS["thumbnail_prompt_template"],
                "schedule_frequency": ch.get("schedule_frequency", "daily"),
                "schedule_interval_days": ch.get("schedule_interval_days", 1),
                "shorts_schedule_times": shorts_times,
                "long_schedule_times": long_times,
                "is_active": bool(ch.get("is_active", 0)),
                "is_authenticated": bool(ch.get("is_authenticated", 0))
            })
            return cfg
    except Exception as e:
        print(f"[!] Warning reading channel settings from SQLite: {e}")

    # Fallback to file/defaults if DB not available
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = DEFAULT_SETTINGS.copy()
            merged.update(data)
            return merged
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()

def save_channel_settings(settings: Dict[str, Any], channel_id: int = None) -> Dict[str, Any]:
    """
    Persists updated channel branding, scheduling, and outro settings directly
    to the active channel (or specified channel_id) in SQLite.
    """
    try:
        from db import get_channel_by_id, get_active_channel, update_channel
        target_ch = get_channel_by_id(channel_id) if channel_id else get_active_channel()
        if target_ch:
            cid = target_ch["id"]
            db_updates = {}
            mapping = {
                "channel_name": "channel_name",
                "handle": "handle",
                "logo_path": "logo_path",
                "watermark_text": "watermark_text",
                "outro_text_template": "watermark_text",
                "thumbnail_art_style": "thumbnail_style",
                "thumbnail_style": "thumbnail_style",
                "thumbnail_mood": "thumbnail_mood",
                "thumbnail_prompt_template": "thumbnail_prompt_template",
                "schedule_frequency": "schedule_frequency",
                "schedule_interval_days": "schedule_interval_days",
                "shorts_schedule_times": "shorts_schedule_times",
                "long_schedule_times": "long_schedule_times",
                "show_logo": "show_logo",
                "logo_position": "logo_position",
                "logo_size": "logo_size",
                "logo_opacity": "logo_opacity",
                "show_outro": "show_outro",
                "outro_duration_sec": "outro_duration_sec"
            }
            for k, col in mapping.items():
                if k in settings:
                    db_updates[col] = settings[k]

            if db_updates:
                update_channel(cid, **db_updates)

            # Also sync to JSON file as fallback
            try:
                current_cfg = get_channel_settings(cid)
                with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                    json.dump(current_cfg, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

            return get_channel_settings(cid)
    except Exception as e:
        print(f"[!] Error saving channel settings to SQLite: {e}")

    # Fallback to local file
    current = DEFAULT_SETTINGS.copy()
    current.update(settings)
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
    return current

def get_formatted_outro_text(channel_name: str = "", channel_id: int = None) -> str:
    """
    Returns the goodbye/outro text using the template and channel name.
    """
    cfg = get_channel_settings(channel_id=channel_id)
    c_name = channel_name.strip() or cfg.get("channel_name", "").strip() or "Our Channel"
    template = cfg.get("outro_text_template", "Watch next part on {channel_name}")
    if "{channel_name}" in template:
        return template.replace("{channel_name}", c_name)
    return f"{template} {c_name}".strip()
