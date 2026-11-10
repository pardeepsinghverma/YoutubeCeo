import sys
import os
import re
import subprocess
import json
import hashlib
from pathlib import Path
from dotenv import load_dotenv

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from typing import Optional
from config import FFMPEG_PATH, BASE_DIR
from db import add_video, update_video_metadata, get_connection
from media_inspector import inspect_video
from channel_settings import get_channel_settings
from media_evasion import detect_hardware_encoder, build_anti_fingerprint_audio_filter

load_dotenv()

SHORTS_DIR = BASE_DIR / "shorts_output"
SHORTS_DIR.mkdir(exist_ok=True)


def sanitize_folder_name(name: str) -> str:
    """
    Sanitizes the original video title into a safe Windows directory name.
    e.g. '3 बहनों की लालची भाभी'
    """
    clean = re.sub(r'[\/:*?"<>|]', "", name).strip().rstrip(". ")
    clean = re.sub(r'\s*\(\d+P\)\s*$', '', clean, flags=re.IGNORECASE)
    return clean[:80] if clean else "Untitled_Story"

def generate_hinglish_subtitles_and_hook(audio_path: Path, part_num: int, video_title_hint: str, add_parts_label: bool = True) -> dict:
    """
    Prompts Gemini to convert audio speech into synchronized Hinglish subtitles and a top hook.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        part_tag = f"Part: {part_num}" if add_parts_label else ""
        return {
            "top_hook_text": f"Wait for the Twist! {part_tag}".strip(),
            "short_title": f"{video_title_hint[:25]} {part_tag} #shorts".strip(),
            "srt_content": "1\n00:00:01,000 --> 00:00:05,000\nDekhiye aage kya hua!\n\n2\n00:00:06,000 --> 00:00:10,000\nLike aur share karein!"
        }

    from google import genai
    client = genai.Client(api_key=api_key)

    uploaded_file = client.files.upload(file=str(audio_path))

    part_req = (
        f"CRUCIAL: This video is Part {part_num} of a multi-part series. "
        f"You MUST include 'Part: {part_num}' in the short_title (e.g. 'Kahani Ka Naam (Part: {part_num}) #shorts') "
        f"and include 'Part: {part_num}' in the top_hook_text so viewers immediately recognize this part."
        if add_parts_label else "Do not force part labels in title or hook."
    )

    prompt = f"""
    This is an audio segment from Part {part_num} of the Hindi story video '{video_title_hint}'.
    {part_req}
    1. Transcribe the spoken Hindi dialogue into clean, conversational HINGLISH (Hindi written in Roman English alphabet).
    2. Format as a standard SRT subtitle string. Keep subtitle lines short (3 to 5 words each) so they appear punchy, colorful, and fast-paced in mobile captions.
    3. Generate a punchy 1-line top hook text (in English or Hinglish) for the header banner of the Short.
    4. Generate a viral title ending with #shorts.

    Return JSON:
    {{
        "top_hook_text": "string",
        "short_title": "string #shorts",
        "srt_content": "string",
        "hashtags": ["#shorts", "#trending", "#viral"],
        "tags": ["shorts", "hindi kahaniya", "moral stories"]
    }}
    """

    res_data = None
    for model_name in ["gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.5-flash", "gemini-3.8-flash"]:
        try:
            res = client.models.generate_content(
                model=model_name,
                contents=[uploaded_file, prompt],
                config={"response_mime_type": "application/json"}
            )
            raw = res.text.strip()
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            res_data = json.loads(raw)
            break
        except Exception as e:
            print(f"[!] Model {model_name} error: {e}")

    if not res_data:
        # Fallback if AI models busy
        part_tag = f"Part: {part_num}" if add_parts_label else ""
        res_data = {
            "top_hook_text": f"Wait for the Twist! {part_tag}".strip(),
            "short_title": f"{video_title_hint[:25]} {part_tag} #shorts".strip(),
            "srt_content": (
                "1\n00:00:00,200 --> 00:00:07,000\nDekhiye aage kya hua!\n\n"
                "2\n00:00:07,100 --> 00:00:14,000\nKahani mein aaya bada twist!\n\n"
                "3\n00:00:14,100 --> 00:00:21,000\nSabhi log hairan reh gaye!\n\n"
                "4\n00:00:21,100 --> 00:00:28,000\nSach janne ke liye dekhiye aage!\n\n"
                "5\n00:00:28,100 --> 00:00:35,000\nSubscribe for next part!"
            ),
            "hashtags": ["#shorts", "#trending", "#viral"],
            "tags": ["shorts", "hindi kahaniya", "moral stories"]
        }

    # Ensure Part label is present in title if requested
    if add_parts_label:
        st = res_data.get("short_title", "")
        if f"part" not in st.lower():
            clean_t = st.replace("#shorts", "").strip()
            res_data["short_title"] = f"{clean_t} (Part: {part_num}) #shorts"
        th = res_data.get("top_hook_text", "")
        if f"part" not in th.lower():
            res_data["top_hook_text"] = f"Part: {part_num} | {th}"

    return res_data

def parse_time_to_seconds(time_str: str) -> float:
    """
    Converts any timestamp string (SRT, Gemini variants, mm:ss:mmm, hh:mm:ss.sss) to seconds as float.
    """
    time_str = time_str.strip().replace(',', '.')
    if ':' in time_str:
        parts = time_str.split(':')
        if len(parts) == 3:
            if '.' not in parts[2] and len(parts[2]) == 3:
                try:
                    return int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 1000.0
                except ValueError:
                    return 0.0
            else:
                try:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                except ValueError:
                    return 0.0
        elif len(parts) == 2:
            try:
                return int(parts[0]) * 60 + float(parts[1])
            except ValueError:
                return 0.0
    try:
        return float(time_str)
    except ValueError:
        return 0.0

def format_ass_time(total_seconds: float) -> str:
    """
    Formats total seconds into strict ASS format: H:MM:SS.cc
    """
    total_seconds = max(0.0, total_seconds)
    h = int(total_seconds // 3600)
    m = int((total_seconds % 3600) // 60)
    s = int(total_seconds % 60)
    cs = int(round((total_seconds - int(total_seconds)) * 100))
    if cs >= 100:
        cs = 0
        s += 1
        if s >= 60:
            s = 0
            m += 1
            if m >= 60:
                m = 0
                h += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

# Curated palette of vibrant viral colors for subtitles: (Name, Text_BGR, Glow_BGR)
VIRAL_COLORS = [
    ("Electric Yellow", "&H0005F5FF&", "&H0000D0FF&"),
    ("Neon Cyan",       "&H00FFFF00&", "&H00D0D000&"),
    ("Vibrant Lime",    "&H0033FF33&", "&H0015D515&"),
    ("Flame Orange",    "&H000099FF&", "&H000070D0&"),
    ("Hot Pink",        "&H009933FF&", "&H007515D0&"),
    ("Neon Lavender",   "&H00FF55B0&", "&H00D02580&"),
]

def format_colorful_dialogue(text: str, color_idx: int = 0) -> tuple[str, str]:
    """
    Takes dialogue text, converts to clean uppercase, and formats into
    punchy two-tone viral captions with a rotating vibrant color palette:
    - Line 1: Rotating Neon Color with tighter, crisper glow
    - Line 2: Crisp Pure White with soft shadow
    Returns (glow_ass_text, front_ass_text) for dual-layer rendering.
    """
    clean = text.strip().upper().replace("{", "").replace("}", "").replace("(", "").replace(")", "")
    words = clean.split()
    if not words:
        return ("", "")

    if len(words) == 1:
        l1, l2 = words[0], ""
    elif len(words) in (2, 3):
        l1 = " ".join(words[:2]) if len(words) == 3 else words[0]
        l2 = words[2] if len(words) == 3 else words[1]
    elif len(words) in (4, 5):
        mid = (len(words) + 1) // 2
        l1 = " ".join(words[:mid])
        l2 = " ".join(words[mid:])
    else:
        mid = len(words) // 2
        l1 = " ".join(words[:mid])
        l2 = " ".join(words[mid:])

    _, text_bgr, glow_bgr = VIRAL_COLORS[color_idx % len(VIRAL_COLORS)]

    # Tighter, crisper glow (blur=5.5, bord=4.5, shad=2.0) to eliminate cloudy background haze
    if l2:
        glow_text = (
            f"{{\\blur5.5\\bord4.5\\c{glow_bgr}\\3c{glow_bgr}\\shad2.0\\4c&H60000000&}}{l1}\\N"
            f"{{\\blur3.5\\bord3.0\\c&H00B0B0B0&\\3c&H00B0B0B0&\\shad2.0\\4c&H60000000&}}{l2}"
        )
        front_text = (
            f"{{\\blur0.4\\bord1.2\\c{text_bgr}\\3c{glow_bgr}}}{l1}\\N"
            f"{{\\blur0.4\\bord1.2\\c&H00FFFFFF&\\3c&H00C0C0C0&}}{l2}"
        )
    else:
        glow_text = f"{{\\blur5.5\\bord4.5\\c{glow_bgr}\\3c{glow_bgr}\\shad2.0\\4c&H60000000&}}{l1}"
        front_text = f"{{\\blur0.4\\bord1.2\\c{text_bgr}\\3c{glow_bgr}}}{l1}"

    return glow_text, front_text

def format_outro_dialogue(channel_name: str = "") -> tuple[str, str]:
    """
    Formats the high-impact outro call to action in viral neon colors:
    Line 1: WATCH NEXT PART ON (Electric Yellow)
    Line 2: 👉 {CHANNEL_NAME} 👈 (Neon Cyan)
    """
    cfg = get_channel_settings()
    c_name = channel_name.strip() or cfg.get("channel_name", "").strip() or "Our Channel"
    template = cfg.get("outro_text_template", "Watch next part on {channel_name}").strip()

    if "{channel_name}" in template:
        prefix = template.replace("{channel_name}", "").strip().upper()
        l1 = prefix if prefix else "WATCH NEXT PART ON"
        l2 = f"👉 {c_name.upper()} 👈"
    else:
        formatted = template.replace("{channel_name}", c_name).upper()
        words = formatted.split()
        if len(words) <= 3:
            l1 = formatted
            l2 = ""
        else:
            mid = (len(words) + 1) // 2
            l1 = " ".join(words[:mid])
            l2 = " ".join(words[mid:])

    if l2:
        glow_text = (
            f"{{\\blur5.5\\bord4.5\\c&H0000D0FF&\\3c&H0000D0FF&\\shad2.0\\4c&H60000000&}}{l1}\\N"
            f"{{\\blur5.5\\bord4.5\\c&H00D0D000&\\3c&H00D0D000&\\shad2.0\\4c&H60000000&}}{l2}"
        )
        front_text = (
            f"{{\\blur0.4\\bord1.2\\c&H0005F5FF&\\3c&H0000C8FF&}}{l1}\\N"
            f"{{\\blur0.4\\bord1.2\\c&H00FFFF00&\\3c&H00D0D000&}}{l2}"
        )
    else:
        glow_text = f"{{\\blur5.5\\bord4.5\\c&H0000D0FF&\\3c&H0000D0FF&\\shad2.0\\4c&H60000000&}}{l1}"
        front_text = f"{{\\blur0.4\\bord1.2\\c&H0005F5FF&\\3c&H0000C8FF&}}{l1}"

    return glow_text, front_text

def srt_to_ass(srt_text: str, fallback_hook: str = "", duration_sec: float = 35.0) -> str:
    ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: SubGlow,Arial Black,64,&H0000F0FF,&H000000FF,&H0000D0FF,&H00000000,-1,0,0,0,100,108,1.5,0,1,5.0,0,2,40,40,360,1
Style: SubFront,Arial Black,64,&H00FFFFFF,&H000000FF,&H0000E0FF,&H80000000,-1,0,0,0,100,108,1.5,0,1,1.2,2.0,2,40,40,360,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    time_pattern = re.compile(r'(\d[\d:.,]+)\s*-->\s*(\d[\d:.,]+)')
    lines = [l.strip() for l in srt_text.strip().replace("\r\n", "\n").split("\n")]
    
    parsed_items = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = time_pattern.search(line)
        if match:
            start_sec = parse_time_to_seconds(match.group(1))
            end_sec = parse_time_to_seconds(match.group(2))
            sub_lines = []
            i += 1
            while i < len(lines):
                next_l = lines[i]
                if not next_l:
                    break
                if time_pattern.search(next_l):
                    i -= 1
                    break
                if next_l.isdigit() and i + 1 < len(lines) and time_pattern.search(lines[i+1]):
                    i -= 1
                    break
                sub_lines.append(next_l)
                i += 1
            text = " ".join(sub_lines).strip().replace("{", "(").replace("}", ")")
            if text and end_sec > start_sec:
                parsed_items.append([start_sec, end_sec, text])
        i += 1

    if not parsed_items:
        clean_fb = fallback_hook[:40] if fallback_hook else "Watch Till The End!"
        parsed_items.append([0.0, max(5.0, duration_sec - 4.0), clean_fb])

    # Check channel settings for Goodbye Outro
    cfg = get_channel_settings()
    show_outro = cfg.get("show_outro", True)
    outro_dur = min(4.5, max(2.5, float(cfg.get("outro_duration_sec", 3.5))))
    outro_start = max(1.0, duration_sec - outro_dur) if (show_outro and duration_sec >= 10.0) else None

    # 1. Seamless Intro: Ensure subtitles appear immediately at 0.2s
    if parsed_items and parsed_items[0][0] > 0.4:
        parsed_items[0][0] = 0.2

    # 2. Smooth Continuity: Bridge small pause gaps (< 1.5s) between spoken phrases
    for idx in range(len(parsed_items) - 1):
        curr_end = parsed_items[idx][1]
        next_start = parsed_items[idx + 1][0]
        if 0 < (next_start - curr_end) <= 1.5:
            parsed_items[idx][1] = next_start - 0.05

    # 3. If Outro Goodbye Text is enabled, trim spoken subtitles so they don't collide
    if outro_start is not None:
        trimmed_items = []
        for s_sec, e_sec, text in parsed_items:
            if s_sec >= outro_start - 0.4:
                continue
            if e_sec > outro_start:
                e_sec = outro_start - 0.05
            if e_sec > s_sec:
                trimmed_items.append([s_sec, e_sec, text])
        parsed_items = trimmed_items
    else:
        # Complete Coverage: Ensure last subtitle remains visible until duration_sec
        if parsed_items and parsed_items[-1][1] < (duration_sec - 1.0):
            parsed_items[-1][1] = duration_sec

    events = []
    for idx, (s_sec, e_sec, text) in enumerate(parsed_items):
        s_str = format_ass_time(s_sec)
        e_str = format_ass_time(e_sec)
        g_txt, f_txt = format_colorful_dialogue(text, color_idx=idx)
        if g_txt and f_txt:
            events.append(f"Dialogue: 0,{s_str},{e_str},SubGlow,,0,0,0,,{g_txt}\n")
            events.append(f"Dialogue: 1,{s_str},{e_str},SubFront,,0,0,0,,{f_txt}\n")

    # 4. Append Outro Goodbye Dialogue event in the final seconds
    if outro_start is not None:
        o_start_str = format_ass_time(outro_start)
        o_end_str = format_ass_time(duration_sec)
        og_txt, of_txt = format_outro_dialogue(cfg.get("channel_name", ""))
        if og_txt and of_txt:
            events.append(f"Dialogue: 0,{o_start_str},{o_end_str},SubGlow,,0,0,0,,{og_txt}\n")
            events.append(f"Dialogue: 1,{o_start_str},{o_end_str},SubFront,,0,0,0,,{of_txt}\n")

    return ass_header + "".join(events)

def render_vertical_short(
    video_path: Path,
    start_sec: int,
    duration_sec: int,
    top_hook: str,
    srt_content: str,
    output_path: Path,
    part_label: Optional[str] = None
) -> bool:
    """
    Renders 1080x1920 vertical Short:
    - Scales video to 100% width (1080px) centered
    - Optional Channel Logo watermark overlay
    - Top area: Optional red PART badge + top hook text in quotes
    - Bottom area: Vibrant rotating Hinglish subtitles + Goodbye Outro
    """
    file_id = hashlib.md5(f"{video_path.name}_{start_sec}".encode()).hexdigest()[:8]
    temp_ass = BASE_DIR / f"temp_{file_id}.ass"
    
    clean_hook = top_hook.replace("'", "").replace(":", "-").replace('"', '')[:45]
    try:
        ass_data = srt_to_ass(srt_content, fallback_hook=clean_hook, duration_sec=float(duration_sec))
        with open(temp_ass, "w", encoding="utf-8") as f:
            f.write(ass_data)
        safe_ass_path = str(temp_ass.resolve()).replace("\\", "/").replace(":", "\\:")

        # Check for Channel Logo Watermark
        cfg = get_channel_settings()
        show_logo = cfg.get("show_logo", True)
        logo_path = cfg.get("logo_path", "")
        valid_logo = None
        if show_logo:
            if logo_path and Path(logo_path).is_file():
                valid_logo = Path(logo_path)
            elif (BASE_DIR / "branding" / "channel_logo.png").is_file():
                valid_logo = BASE_DIR / "branding" / "channel_logo.png"
            elif (BASE_DIR / "branding" / "default_logo.png").is_file():
                valid_logo = BASE_DIR / "branding" / "default_logo.png"

        logo_size = int(cfg.get("logo_size", 110))
        logo_opacity = float(cfg.get("logo_opacity", 0.95))
        logo_pos = cfg.get("logo_position", "top_right")
        if logo_pos == "top_left":
            overlay_xy = "50:80"
        elif logo_pos == "bottom_right":
            overlay_xy = "W-w-50:H-h-120"
        else:  # top_right
            overlay_xy = "W-w-50:80"

        inputs = [
            FFMPEG_PATH, "-y",
            "-ss", str(start_sec),
            "-t", str(duration_sec),
            "-i", str(video_path),
        ]

        if valid_logo:
            inputs.extend(["-i", str(valid_logo.resolve())])
            logo_prep = f"[1:v]scale={logo_size}:-1,format=rgba,colorchannelmixer=aa={logo_opacity}[vlogo];"
            bg_prep = f"[0:v]scale=1080:-2,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black[vbg];[vbg][vlogo]overlay={overlay_xy}[vbrand];"
            base_node = "[vbrand]"
        else:
            logo_prep = ""
            bg_prep = "[0:v]scale=1080:-2,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black[vbg];"
            base_node = "[vbg]"

        if part_label:
            clean_part = part_label.replace(":", "\\:").replace("'", "").replace('"', '')
            filter_complex = (
                f"{logo_prep}{bg_prep}"
                f"{base_node}drawtext=text='{clean_part}':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=190:box=1:boxcolor=red@0.85:boxborderw=14[vpart];"
                f"[vpart]drawtext=text='\"{clean_hook}\"':fontcolor=white:fontsize=40:x=(w-text_w)/2:y=300:box=1:boxcolor=black@0.6:boxborderw=10[vtxt];"
                f"[vtxt]subtitles='{safe_ass_path}'[vout]"
            )
        else:
            filter_complex = (
                f"{logo_prep}{bg_prep}"
                f"{base_node}drawtext=text='\"{clean_hook}\"':fontcolor=white:fontsize=46:x=(w-text_w)/2:y=240:box=1:boxcolor=black@0.6:boxborderw=12[vtxt];"
                f"[vtxt]subtitles='{safe_ass_path}'[vout]"
            )

        encoder_name, encoder_flags = detect_hardware_encoder()
        audio_evasion = build_anti_fingerprint_audio_filter(speed_factor=1.035)

        # Check if video has audio stream to safely apply acoustic evasion
        has_audio = True
        try:
            meta = inspect_video(video_path)
            has_audio = bool(meta.get("has_audio", True))
        except Exception:
            has_audio = True

        if has_audio:
            filter_complex += f";[0:a]{audio_evasion}[aout]"
            audio_map = ["-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]
        else:
            audio_map = []

        cmd = inputs + [
            "-filter_complex", filter_complex,
            "-map", "[vout]"
        ] + audio_map + [
            "-c:v", encoder_name
        ] + encoder_flags + [
            "-pix_fmt", "yuv420p",
            str(output_path)
        ]

        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        if res.returncode != 0:
            print(f"[!] FFmpeg render error: {res.stderr[-400:]}")
            return False
        return True
    finally:
        temp_ass.unlink(missing_ok=True)

def create_shorts_from_video(
    video_path: Path, 
    num_parts: int = 0, 
    add_parts_label: bool = True,
    parent_video_id: Optional[int] = None,
    progress_callback = None
) -> list:
    """
    Creates vertical Shorts from a video with 5-second overlap:
    Part 1: 0-35s
    Part 2: 30-65s
    Part 3: 60-95s...
    - num_parts: 0 or <= 0 converts the ENTIRE video (max 15 mins = 30 parts).
    - add_parts_label: Embeds 'PART: X' banner and adds '(Part: X)' to title.
    - progress_callback: Optional callback for UI worker progress updates.
    """
    video_path = Path(video_path)
    created_shorts = []

    # Dedicated subfolder per video: shorts_output/{org video title}/
    clean_folder_name = sanitize_folder_name(video_path.stem)
    video_shorts_dir = SHORTS_DIR / clean_folder_name
    video_shorts_dir.mkdir(parents=True, exist_ok=True)

    # 1. Determine actual video duration
    info_orig = inspect_video(video_path)
    total_dur = info_orig.get("duration_seconds", 900.0)
    # Cap whole video slicing at 15 minutes (900 seconds) -> max 30 parts
    effective_dur = min(total_dur, 900.0)
    max_calc_parts = min(30, max(1, int((effective_dur - 5) // 30) + 1))

    if not num_parts or num_parts <= 0:
        target_parts = max_calc_parts
    else:
        target_parts = min(num_parts, max_calc_parts, 30)

    # 2. Auto-resolve parent video ID from DB if not provided
    if parent_video_id is None:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute("SELECT id FROM videos WHERE file_path = ? OR file_name = ?", (str(video_path), video_path.name))
            row = c.fetchone()
            conn.close()
            if row:
                parent_video_id = row["id"]
        except Exception:
            pass

    label_status = f"ENABLED (Part: 1, Part: 2...)" if add_parts_label else "DISABLED"
    print(f"\n[*] Slicing {target_parts} Parts from: {video_path.name}")
    print(f"[*] Total length: {total_dur:.1f}s (Capped at 15m/900s, max 30 parts)")
    print(f"[*] 'Add Parts Label:' {label_status}")

    for part_idx in range(1, target_parts + 1):
        # 5-second overlap formula: Part 1=0, Part 2=30, Part 3=60...
        start_sec = (part_idx - 1) * 30
        if start_sec >= total_dur - 5:
            print(f"[*] Reached end of video at {total_dur:.1f}s.")
            break

        duration_sec = min(35, int(total_dur - start_sec))
        if duration_sec < 10 and part_idx > 1:
            print(f"[*] Skipping remaining {duration_sec}s tail chunk.")
            break

        output_file = video_shorts_dir / f"Part_{part_idx}.mp4"
        audio_temp = video_shorts_dir / f"temp_audio_p{part_idx}.mp3"

        print(f"\n--- Part {part_idx}/{target_parts}: {start_sec}s to {start_sec + duration_sec}s ---")

        if progress_callback:
            progress_callback(part_idx, target_parts, f"Slicing Part {part_idx}/{target_parts} ({start_sec}s-{start_sec + duration_sec}s)...")

        # 1. Extract audio chunk
        subprocess.run([
            FFMPEG_PATH, "-y",
            "-ss", str(start_sec), "-t", str(duration_sec),
            "-i", str(video_path),
            "-vn", "-ac", "1", "-b:a", "64k",
            str(audio_temp)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # 2. Get Hinglish subtitles & Hook
        print("[*] Generating Hinglish subtitles & viral hook...")
        ai_data = generate_hinglish_subtitles_and_hook(audio_temp, part_idx, video_path.stem, add_parts_label=add_parts_label)
        top_hook = ai_data.get("top_hook_text", f"Part: {part_idx} - Watch Till End!")
        srt_text = ai_data.get("srt_content", "")
        short_title = ai_data.get("short_title", f"{video_path.stem[:25]} Part: {part_idx} #shorts")
        tags = ai_data.get("tags", ["shorts", "hindi kahaniya"])
        hashtags = ai_data.get("hashtags", ["#shorts", "#trending", "#viral"])

        # 3. Render 1080x1920 Short
        part_banner = f"PART: {part_idx}" if add_parts_label else None
        print(f"[*] Rendering vertical 9:16 video (Banner: {part_banner or 'None'})...")
        success = render_vertical_short(video_path, start_sec, duration_sec, top_hook, srt_text, output_file, part_label=part_banner)
        
        if success:
            # 4. Inspect rendered Short
            info = inspect_video(output_file)

            # 5. Add to SQLite database
            cfg = get_channel_settings()
            c_name = cfg.get("channel_name", "").strip() or "our channel"
            desc = f"{top_hook}\n\nWatch full story on {c_name}!\n\n#shorts #trending #viral"
            row_id = add_video(
                file_path=output_file,
                file_name=output_file.name,
                file_size_mb=info["file_size_mb"],
                duration_seconds=info["duration_seconds"],
                width=info["width"],
                height=info["height"],
                is_short=True,
                parent_video_id=parent_video_id,
                part_number=part_idx
            )
            update_video_metadata(row_id, short_title, [short_title], desc, tags, hashtags)

            # Auto-generate AI Thumbnail with Part Badge & Logo
            try:
                from thumbnail_generator import generate_thumbnail_for_video_id
                print(f"[*] Generating AI thumbnail for Part {part_idx}...")
                generate_thumbnail_for_video_id(row_id)
            except Exception as th_err:
                print(f"[-] Note: Thumbnail generation note for Part {part_idx}: {th_err}")

            # Dispatch Telegram HITL review card
            try:
                import asyncio
                from telegram_bot import send_video_review_card
                asyncio.run(send_video_review_card(row_id))
            except Exception:
                pass

            print(f"[OK] Created Short Part {part_idx}: {output_file.name} (DB ID #{row_id})")
            audio_temp.unlink(missing_ok=True)
            created_shorts.append({"id": row_id, "path": str(output_file), "title": short_title, "part": part_idx})

            if progress_callback:
                progress_callback(part_idx, target_parts, f"Rendered Part {part_idx}/{target_parts} ({output_file.name})")

    print(f"\n[OK] Successfully generated {len(created_shorts)} vertical Shorts with Hinglish subtitles!")
    return created_shorts

if __name__ == "__main__":
    folder = Path(r"D:\Videos\Animations\500+2D Cartoon Bundle-20260920T075834Z-1-005\500+2D Cartoon Bundle")
    sample_vid = list(folder.glob("*.mp4"))[0]
    create_shorts_from_video(sample_vid, num_parts=0)
