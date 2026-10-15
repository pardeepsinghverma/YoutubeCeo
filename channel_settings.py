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

def get_channel_settings() -> Dict[str, Any]:
    """
    Reads channel branding and outro settings from JSON, returning defaults if not created.
    """
    if not SETTINGS_FILE.exists():
        save_channel_settings(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = DEFAULT_SETTINGS.copy()
        merged.update(data)
        return merged
    except Exception as e:
        print(f"[!] Error reading channel_settings.json: {e}")
        return DEFAULT_SETTINGS.copy()

def save_channel_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """
    Persists updated channel branding settings to JSON file.
    """
    current = get_channel_settings() if SETTINGS_FILE.exists() else DEFAULT_SETTINGS.copy()
    current.update(settings)
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[!] Error saving channel_settings.json: {e}")
    return current

def get_formatted_outro_text(channel_name: str = "") -> str:
    """
    Returns the goodbye/outro text using the template and channel name.
    """
    cfg = get_channel_settings()
    c_name = channel_name.strip() or cfg.get("channel_name", "").strip() or "Our Channel"
    template = cfg.get("outro_text_template", "Watch next part on {channel_name}")
    if "{channel_name}" in template:
        return template.replace("{channel_name}", c_name)
    return f"{template} {c_name}".strip()
