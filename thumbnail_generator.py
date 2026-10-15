import os
import sys
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import json
import time
import random
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

from config import BASE_DIR, GEMINI_API_KEY, FFMPEG_PATH
from channel_settings import get_channel_settings
from db import get_connection, update_video_thumbnail

THUMBNAILS_DIR = BASE_DIR / "thumbnails"
THUMBNAILS_DIR.mkdir(exist_ok=True)

# Recommended font options on Windows
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\impact.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf"
]

def get_thumbnail_font(size: int):
    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except Exception:
                pass
    return ImageFont.load_default()

def generate_visual_prompt_with_gemini(title: str, description: str = "", is_short: bool = True, part_number: int = None) -> str:
    """
    Analyzes story title, description, and part number to create a high-CTR,
    premium 3D animated visual prompt matching the user's detailed art direction.
    """
    cfg = get_channel_settings()
    channel_name = cfg.get("channel_name", "Lil Kido Stories").strip() or "Lil Kido Stories"
    part_str = f"Part {part_number} of " if part_number else ""
    format_label = "9:16 vertical mobile Reel/Shorts poster (720x1280)" if is_short else "16:9 widescreen YouTube video thumbnail (1280x720)"
    aspect_desc = "9:16 vertical poster orientation, 720x1280 pixels, characters large and clearly visible in middle-lower frame, readable on small phone screens" if is_short else "16:9 widescreen orientation, 1280x720 pixels, main characters centered, balanced colorful background, readable at small sizes"
    
    art_style = cfg.get("thumbnail_art_style", "3D Pixar Animation")
    mood = cfg.get("thumbnail_mood", "Joyful, safe, playful, magical, full of adventure")

    api_key = os.getenv("GEMINI_API_KEY", "") or GEMINI_API_KEY

    if api_key:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            prompt_request = f"""
            You are a master YouTube thumbnail art director for a premier kids & family 3D animation channel.
            
            CHANNEL: “{channel_name}”
            VIDEO TITLE: {title}
            DESCRIPTION: {description}
            PART: {part_str}
            FORMAT: {format_label}

            Write a vivid, highly detailed, master-grade image generation prompt (approx 75-100 words) tailored to this specific story following this exact structure:

            1. Scene & Character Subject: Show cute, adorable cartoon characters from this story (with big expressive eyes, cheerful or dramatic comedic facial expressions, colorful outfits, festive Indian storybook village setting or fairytale world).
            2. Art Style & Aesthetic: Premium {art_style} aesthetic, soft rounded character designs, bright candy colors, cinematic rim lighting, rich details, and a dreamy fairytale atmosphere. Make characters large and clearly visible, with expressive faces that instantly grab attention. Colorful, uncluttered composition with strong contrast, soft depth of field, and a polished professional YouTube thumbnail look.
            3. Composition: {aspect_desc}.
            4. Mood & Target Audience: Target audience: children aged 3–8 and family. Overall mood: {mood}. No scary characters, no dark murky colors, no photorealism, no clutter, no watermark.

            Do NOT include the words 'Prompt:' or any markdown asterisks. Output ONLY the raw prompt text.
            """

            for model_name in ["gemini-3.1-flash-lite", "gemini-3.8-flash"]:
                try:
                    res = client.models.generate_content(model=model_name, contents=prompt_request)
                    text = (res.text or "").strip().replace('"', '').replace('\n', ' ')
                    if len(text) > 30:
                        return text
                except Exception as m_err:
                    if "503" in str(m_err) or "UNAVAILABLE" in str(m_err):
                        time.sleep(1.5)
                        continue
                    break
        except Exception as e:
            print(f"[-] Gemini prompt generation error: {e}")

    # Fallback prompt matching user's detailed structure
    clean_title = title.replace("#shorts", "").replace("(Part:", "").strip()
    orientation_tag = "9:16 vertical poster orientation, 720x1280 pixels" if is_short else "16:9 widescreen orientation, 1280x720 pixels"
    return (
        f"Create a vibrant, adorable, high-quality 3D animated YouTube thumbnail for a kids storytelling channel called “{channel_name}”. "
        f"Show cute cartoon characters from the story '{clean_title}' with big expressive eyes, cheerful dramatic smiles, and colorful festive outfits, "
        f"surrounded by a magical Indian storybook village with glowing magical particles, fluffy clouds, and lush greenery. "
        f"Premium Pixar-inspired 3D animation aesthetic, soft rounded character designs, bright candy colors, cinematic lighting, rich details, dreamy fairytale atmosphere. "
        f"Characters large and clearly visible with strong contrast. Composition: {orientation_tag}, main characters centered, readable at small sizes. "
        f"Target audience: children aged 3–8. Joyful, safe, playful, magical. No scary characters, no dark atmosphere, no photorealism, no clutter, no watermark."
    )

def extract_keyframe_from_video(video_path: Path, output_path: Path, timestamp_sec: float = 8.0) -> bool:
    """
    Extracts a high quality frame from the video using FFmpeg as a reliable fallback.
    """
    try:
        ts_str = f"{int(timestamp_sec // 60):02d}:{int(timestamp_sec % 60):02d}"
        cmd = [
            FFMPEG_PATH, "-y",
            "-ss", f"00:{ts_str}",
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(output_path)
        ]
        res = subprocess.run(cmd, capture_output=True)
        return res.returncode == 0 and output_path.exists()
    except Exception as e:
        print(f"[-] Keyframe extraction failed: {e}")
        return False

def composite_branding_and_save(
    video_id: int,
    base_img: Image.Image,
    is_short: bool = True,
    part_number: int = None,
    output_path: Path = None,
    visual_prompt: str = None
) -> Path:
    """
    Composites high-contrast 'PART X' badge, Channel Logo, and top/bottom shadows
    onto any base image (AI-generated or keyframe) and saves to disk & DB.
    """
    target_w = 720 if is_short else 1280
    target_h = 1280 if is_short else 720

    if not output_path:
        tag = f"part_{part_number}" if part_number else "full"
        output_path = THUMBNAILS_DIR / f"thumb_{video_id}_{tag}.jpg"

    if base_img.size != (target_w, target_h):
        base_img = base_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
    if base_img.mode != "RGBA":
        base_img = base_img.convert("RGBA")

    overlay = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Top gradient shadow so badge and logo are ultra-crisp
    for y in range(160):
        alpha = int(140 * (1.0 - y / 160.0))
        draw.line([(0, y), (target_w, y)], fill=(0, 0, 0, alpha))

    # Bottom gradient shadow
    for y in range(target_h - 180, target_h):
        alpha = int(160 * ((y - (target_h - 180)) / 180.0))
        draw.line([(0, y), (target_w, y)], fill=(0, 0, 0, alpha))

    # 1. Overlay Channel Logo
    settings = get_channel_settings()
    logo_path = settings.get("logo_path")
    if settings.get("show_logo", True) and logo_path and Path(logo_path).exists():
        try:
            logo_img = Image.open(logo_path).convert("RGBA")
            logo_w = 110 if is_short else 140
            aspect = logo_img.height / max(1, logo_img.width)
            logo_h = int(logo_w * aspect)
            logo_img = logo_img.resize((logo_w, logo_h), Image.Resampling.LANCZOS)

            # Circular antialiased mask
            mask = Image.new("L", (logo_w, logo_h), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, logo_w, logo_h), fill=255)

            # Place logo at top-right
            logo_x = target_w - logo_w - 24
            logo_y = 28

            # Soft dark drop shadow under logo
            halo = Image.new("RGBA", (logo_w + 14, logo_h + 14), (0, 0, 0, 0))
            h_draw = ImageDraw.Draw(halo)
            h_draw.ellipse((0, 0, logo_w + 14, logo_h + 14), fill=(0, 0, 0, 190))
            overlay.paste(halo, (logo_x - 7, logo_y - 7), halo)

            overlay.paste(logo_img, (logo_x, logo_y), mask)
            print("[+] Added channel logo to thumbnail.")
        except Exception as l_err:
            print(f"[-] Logo overlay note: {l_err}")

    # 2. Overlay Part Badge
    if part_number is not None:
        font_size = 46 if is_short else 56
        badge_font = get_thumbnail_font(font_size)

        part_text = f"PART {part_number}"
        bbox = badge_font.getbbox(part_text)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        # Badge pill placement (top-left)
        bx = 28
        by = 32
        bw = tw + 40
        bh = th + 24

        badge_bg = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        b_draw = ImageDraw.Draw(badge_bg)

        # Rounded rectangle with vibrant red background and electric yellow border
        b_draw.rounded_rectangle(
            (0, 0, bw - 1, bh - 1),
            radius=14,
            fill=(220, 38, 38, 245),       # Rich Red
            outline=(255, 245, 5, 255),    # Electric Yellow border
            width=3
        )
        overlay.paste(badge_bg, (bx, by), badge_bg)

        # Draw Part Text with drop shadow
        tx = bx + 20 - bbox[0]
        ty = by + 12 - bbox[1]
        draw.text((tx + 2, ty + 2), part_text, font=badge_font, fill=(0, 0, 0, 230))
        draw.text((tx, ty), part_text, font=badge_font, fill=(255, 255, 255, 255))
        print(f"[+] Added 'PART {part_number}' badge to thumbnail.")

    # Composite and Save
    final_img = Image.alpha_composite(base_img, overlay).convert("RGB")
    final_img.save(output_path, "JPEG", quality=95)
    print(f"[OK] Thumbnail created: {output_path.name} ({target_w}x{target_h})")

    # Map with Video in SQLite DB (including rich prompt)
    update_video_thumbnail(video_id, str(output_path), visual_prompt)
    return output_path

def generate_thumbnail_image(
    video_id: int,
    video_path: Path,
    title: str,
    description: str = "",
    is_short: bool = True,
    part_number: int = None,
    force_ai: bool = True,
    output_path: Path = None,
    custom_prompt: str = None
) -> Path:
    """
    Middleware: Generates AI thumbnail using rich prompt, overlays 'Part' badge and Channel Logo,
    maps to video in SQLite, and returns the saved file path.
    """
    video_path = Path(video_path).resolve()
    target_w = 720 if is_short else 1280
    target_h = 1280 if is_short else 720

    if not output_path:
        tag = f"part_{part_number}" if part_number else "full"
        output_path = THUMBNAILS_DIR / f"thumb_{video_id}_{tag}.jpg"

    visual_prompt = custom_prompt or generate_visual_prompt_with_gemini(
        title=title, description=description, is_short=is_short, part_number=part_number
    )
    print(f"[*] Visual Prompt for #{video_id}: {visual_prompt[:80]}...")

    base_img = None

    # Step 1: Generate AI Artwork via Pollinations
    if force_ai:
        try:
            print(f"[*] Crafting AI thumbnail for #{video_id}: {title[:35]}...")
            encoded_prompt = urllib.parse.quote(visual_prompt)
            seed = random.randint(1000, 999999)
            url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={target_w}&height={target_h}&nologo=true&seed={seed}"
            
            print(f"[*] Fetching AI artwork from generator ({target_w}x{target_h})...")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=35) as resp:
                base_img = Image.open(resp).convert("RGBA")
        except Exception as ai_err:
            print(f"[-] AI image generation note: {ai_err}. Using enhanced video keyframe.")
            base_img = None

    # Fallback: Extract video keyframe if AI fetch was not available
    if base_img is None:
        temp_frame = THUMBNAILS_DIR / f"temp_frame_{video_id}.jpg"
        extracted = extract_keyframe_from_video(video_path, temp_frame, timestamp_sec=7.0)
        if extracted and temp_frame.exists():
            base_img = Image.open(temp_frame).convert("RGBA")
            temp_frame.unlink(missing_ok=True)
            # Enhance contrast and color vibrancy for YouTube thumbnail pop
            enhancer_color = ImageEnhance.Color(base_img)
            base_img = enhancer_color.enhance(1.25)
            enhancer_contrast = ImageEnhance.Contrast(base_img)
            base_img = enhancer_contrast.enhance(1.15)
        else:
            # Solid gradient fallback
            base_img = Image.new("RGBA", (target_w, target_h), (30, 20, 50, 255))

    return composite_branding_and_save(
        video_id=video_id,
        base_img=base_img,
        is_short=is_short,
        part_number=part_number,
        output_path=output_path,
        visual_prompt=visual_prompt
    )

def get_or_create_thumbnail_prompt(video_id: int) -> dict:
    """
    Looks up or synthesizes the rich prompt for a given video ID.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        if not row:
            return {}
        v = dict(row)
    finally:
        conn.close()

    prompt = v.get("thumbnail_prompt")
    if not prompt or len(prompt) < 25:
        prompt = generate_visual_prompt_with_gemini(
            title=v.get("title") or v.get("file_name", ""),
            description=v.get("description", ""),
            is_short=bool(v.get("is_short", 1)),
            part_number=v.get("part_number")
        )
        update_video_thumbnail(video_id, v.get("thumbnail_path"), prompt)

    return {
        "video_id": video_id,
        "title": v.get("title") or v.get("file_name", ""),
        "part_number": v.get("part_number"),
        "is_short": bool(v.get("is_short", 1)),
        "prompt": prompt,
        "thumbnail_path": v.get("thumbnail_path")
    }

def generate_thumbnail_for_video_id(video_id: int, force: bool = False) -> Optional[Path]:
    """
    Helper to look up video details in DB and generate its custom thumbnail.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        if not row:
            return None
        v = dict(row)
    finally:
        conn.close()

    existing_thumb = v.get("thumbnail_path")
    if not force and existing_thumb and Path(existing_thumb).exists():
        return Path(existing_thumb)

    return generate_thumbnail_image(
        video_id=v["id"],
        video_path=Path(v["file_path"]),
        title=v["title"] or v["file_name"],
        description=v["description"] or "",
        is_short=bool(v["is_short"]),
        part_number=v.get("part_number")
    )

def generate_all_missing_thumbnails(target_type: str = "SHORTS") -> int:
    """
    Batch helper: Generates custom thumbnails for all videos/shorts missing one.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        where = "WHERE (thumbnail_path IS NULL OR thumbnail_path = '')"
        if target_type == "SHORTS":
            where += " AND is_short = 1"
        elif target_type == "LONG":
            where += " AND is_short = 0"
        
        cursor.execute(f"SELECT id FROM videos {where} ORDER BY part_number ASC, id ASC")
        rows = cursor.fetchall()
        v_ids = [r["id"] for r in rows]
    finally:
        conn.close()

    generated_count = 0
    for vid in v_ids:
        try:
            generate_thumbnail_for_video_id(vid, force=True)
            generated_count += 1
            time.sleep(1)
        except Exception as e:
            print(f"[-] Thumbnail generation error on #{vid}: {e}")

    return generated_count
