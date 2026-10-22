import sys
import os
import json
import base64
import webbrowser
import subprocess
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta
from fastapi import FastAPI, BackgroundTasks, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from channel_settings import get_channel_settings, save_channel_settings, BRANDING_DIR
import uvicorn

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from config import CHROME_PROFILE_DIR, GEMINI_API_KEY, LOGS_JSON_PATH, BASE_DIR
from db import init_db, get_stats, get_videos_by_status, update_video_metadata, add_video
from media_inspector import inspect_video
from ai_seo import generate_video_seo
from scheduler import assign_schedule_slots, process_upload_queue
from log_manager import get_logs

app = FastAPI(title="YouTube AI Automation Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Background worker status
WORKER_STATE = {
    "is_busy": False,
    "current_task": "Idle",
    "progress": 0,
    "last_message": "System ready"
}

@app.get("/api/system-status")
def get_system_status(channel_id: Optional[int] = None):
    from db import get_active_channel, get_all_channels
    from channel_manager import check_channel_youtube_cookies
    active_ch = get_active_channel()
    channels = get_all_channels()
    target_ch = None
    if channel_id and channel_id != 0:
        target_ch = next((c for c in channels if c["id"] == channel_id), None)
    if not target_ch:
        target_ch = active_ch
    
    prof_dir = target_ch["profile_dir"] if target_ch else str(CHROME_PROFILE_DIR)
    is_yt_auth = check_channel_youtube_cookies(prof_dir)
    gemini_connected = bool(os.getenv("GEMINI_API_KEY", "") or GEMINI_API_KEY)
    
    stats_ch_id = channel_id if channel_id is not None else (active_ch["id"] if active_ch else None)
    
    return {
        "youtube_authenticated": is_yt_auth,
        "gemini_connected": gemini_connected,
        "worker": WORKER_STATE,
        "stats": get_stats(channel_id=stats_ch_id if stats_ch_id != 0 else None),
        "active_channel": active_ch,
        "channels_count": len(channels)
    }

@app.get("/api/videos")
def get_videos(filter: Optional[str] = None, video_type: Optional[str] = None, parent_id: Optional[int] = None, channel_id: Optional[int] = None):
    videos = get_videos_by_status(status=filter if filter != "ALL" else None, video_type=video_type, parent_id=parent_id, channel_id=channel_id if channel_id != 0 else None)
    return videos

@app.get("/api/logs")
def get_upload_logs():
    return get_logs()

@app.get("/api/live-upload-logs")
def get_live_upload_logs():
    from log_manager import get_live_step_logs
    return {"logs": get_live_step_logs(150)}

@app.get("/api/video-file/{video_id}")
def get_video_file(video_id: int):
    from db import get_connection
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT file_path FROM videos WHERE id = ?", (video_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Video not found in DB")
    p = Path(row["file_path"])
    if not p.exists():
        raise HTTPException(status_code=404, detail="File does not exist on disk")
    return FileResponse(str(p), media_type="video/mp4")

@app.get("/api/thumbnail/{video_id}")
def get_thumbnail(video_id: int):
    from db import get_connection
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT thumbnail_path FROM videos WHERE id = ?", (video_id,))
    row = c.fetchone()
    conn.close()
    if not row or not row["thumbnail_path"]:
        raise HTTPException(status_code=404, detail="Thumbnail not generated yet")
    p = Path(row["thumbnail_path"])
    if not p.exists():
        raise HTTPException(status_code=404, detail="Thumbnail file not found on disk")
    return FileResponse(str(p), media_type="image/jpeg")

def bg_generate_all_thumbs(target_type: str = "SHORTS"):
    global WORKER_STATE
    WORKER_STATE["is_busy"] = True
    WORKER_STATE["current_task"] = f"Generating AI Thumbnails ({target_type})..."
    try:
        from thumbnail_generator import generate_all_missing_thumbnails
        count = generate_all_missing_thumbnails(target_type=target_type)
        WORKER_STATE["last_message"] = f"AI Thumbnails generated successfully for {count} video(s)!"
    except Exception as e:
        WORKER_STATE["last_message"] = f"Thumbnail generation error: {e}"
    finally:
        WORKER_STATE["is_busy"] = False
        WORKER_STATE["current_task"] = "Idle"

@app.get("/api/thumbnail-prompt/{video_id}")
def api_get_thumbnail_prompt(video_id: int):
    from thumbnail_generator import get_or_create_thumbnail_prompt
    data = get_or_create_thumbnail_prompt(video_id)
    if not data:
        raise HTTPException(status_code=404, detail="Video not found")
    return data

@app.post("/api/save-thumbnail-image")
def api_save_thumbnail_image(payload: dict):
    from PIL import Image
    import io
    from thumbnail_generator import composite_branding_and_save
    from db import get_connection

    vid_id = payload.get("video_id")
    b64_data = payload.get("image_base64", "")
    prompt = payload.get("prompt", "")

    if not vid_id or not b64_data:
        raise HTTPException(status_code=400, detail="Missing video_id or image_base64")

    if "," in b64_data:
        b64_data = b64_data.split(",")[1]

    raw_bytes = base64.b64decode(b64_data)
    base_img = Image.open(io.BytesIO(raw_bytes))

    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT is_short, part_number FROM videos WHERE id = ?", (vid_id,))
    row = c.fetchone()
    conn.close()

    is_short = bool(row["is_short"]) if row else True
    part_num = row["part_number"] if row else None

    saved_path = composite_branding_and_save(
        video_id=int(vid_id),
        base_img=base_img,
        is_short=is_short,
        part_number=part_num,
        visual_prompt=prompt
    )

    return {
        "message": "Custom AI thumbnail saved successfully!",
        "thumbnail_path": str(saved_path),
        "thumbnail_url": f"/api/thumbnail/{vid_id}"
    }

@app.post("/api/generate-thumbnail")
def api_generate_thumbnail(payload: dict):
    from thumbnail_generator import generate_thumbnail_image
    from db import get_connection

    vid_id = payload.get("video_id")
    custom_prompt = payload.get("custom_prompt")
    force_ai = payload.get("force_ai", True)

    if not vid_id:
        raise HTTPException(status_code=400, detail="Missing video_id")

    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM videos WHERE id = ?", (vid_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Video not found")

    v = dict(row)
    req_ch_id = payload.get("channel_id") or v.get("channel_id")
    res = generate_thumbnail_image(
        video_id=v["id"],
        video_path=Path(v["file_path"]),
        title=v["title"] or v["file_name"],
        description=v["description"] or "",
        is_short=bool(v["is_short"]),
        part_number=v.get("part_number"),
        force_ai=force_ai,
        custom_prompt=custom_prompt,
        channel_id=req_ch_id
    )

    if not res or not res.exists():
        raise HTTPException(status_code=500, detail="Failed to generate thumbnail")

    return {
        "message": "Thumbnail generated successfully!",
        "thumbnail_path": str(res),
        "thumbnail_url": f"/api/thumbnail/{vid_id}",
        "prompt": v.get("thumbnail_prompt")
    }

@app.post("/api/generate-all-thumbnails")
def api_generate_all_thumbnails(payload: dict, background_tasks: BackgroundTasks):
    target_type = payload.get("target_type", "SHORTS")
    background_tasks.add_task(bg_generate_all_thumbs, target_type)
    return {"message": f"AI Thumbnail generation started for {target_type}"}

@app.get("/api/channel-settings")
def api_get_channel_settings(channel_id: Optional[int] = None):
    cfg = get_channel_settings(channel_id=channel_id)
    custom_logo_exists = bool(cfg.get("logo_path") and Path(cfg["logo_path"]).is_file())
    default_logo_exists = (BASE_DIR / "branding" / "default_logo.png").is_file()
    return {
        "settings": cfg,
        "has_logo": custom_logo_exists or default_logo_exists,
        "is_custom_logo": custom_logo_exists,
        "channel_id": cfg.get("channel_id")
    }

@app.post("/api/channel-settings")
def api_save_channel_settings(payload: dict):
    channel_id = payload.get("channel_id")
    updated = save_channel_settings(payload, channel_id=channel_id)
    return {"message": f"Settings saved for channel '{updated.get('channel_name')}'!", "settings": updated}

@app.post("/api/upload-logo")
async def api_upload_logo(file: UploadFile = File(...), channel_id: Optional[int] = None):
    ext = Path(file.filename).suffix.lower()
    if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        raise HTTPException(status_code=400, detail="Invalid image format. Please upload a PNG, JPG, or WEBP image.")
    
    from db import get_channel_by_id, get_active_channel
    target_ch = get_channel_by_id(channel_id) if channel_id else get_active_channel()
    cid = target_ch["id"] if target_ch else 1

    target_path = BRANDING_DIR / f"channel_logo_{cid}{ext}"
    content = await file.read()
    with open(target_path, "wb") as f:
        f.write(content)
    
    updated = save_channel_settings({
        "logo_path": str(target_path),
        "show_logo": True
    }, channel_id=cid)
    return {
        "message": f"Channel logo uploaded successfully for {target_ch.get('channel_name', 'channel')}!",
        "logo_path": str(target_path),
        "settings": updated
    }

@app.post("/api/upload-logo-base64")
def api_upload_logo_base64(payload: dict):
    b64_data = payload.get("image_base64", "")
    filename = payload.get("filename", "channel_logo.png")
    channel_id = payload.get("channel_id")
    ext = Path(filename).suffix.lower() or ".png"
    if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        ext = ".png"
    if "," in b64_data:
        b64_data = b64_data.split(",")[1]
    raw_bytes = base64.b64decode(b64_data)

    from db import get_channel_by_id, get_active_channel
    target_ch = get_channel_by_id(channel_id) if channel_id else get_active_channel()
    cid = target_ch["id"] if target_ch else 1

    target_path = BRANDING_DIR / f"channel_logo_{cid}{ext}"
    with open(target_path, "wb") as f:
        f.write(raw_bytes)
    updated = save_channel_settings({
        "logo_path": str(target_path),
        "show_logo": True
    }, channel_id=cid)
    return {"message": "Channel logo uploaded successfully!", "logo_path": str(target_path), "settings": updated}

@app.get("/api/logo-image")
def api_get_logo_image(channel_id: Optional[int] = None):
    cfg = get_channel_settings(channel_id=channel_id)
    lp = cfg.get("logo_path", "")
    if lp and Path(lp).is_file():
        return FileResponse(str(Path(lp)))
    def_p = BASE_DIR / "branding" / "default_logo.png"
    if def_p.is_file():
        return FileResponse(str(def_p))
    raise HTTPException(status_code=404, detail="No logo image found")

# -------------------------------------------------------------
# MULTI-CHANNEL & MULTI-ACCOUNT MANAGEMENT ENDPOINTS
# -------------------------------------------------------------

@app.get("/api/channels")
def api_get_channels():
    from db import get_all_channels, get_active_channel
    channels = get_all_channels()
    active = get_active_channel()
    return {"channels": channels, "active_channel": active}

@app.post("/api/channels/create")
def api_create_channel(payload: dict):
    from channel_manager import create_channel_with_profile
    channel_name = payload.get("channel_name", "").strip()
    if not channel_name:
        raise HTTPException(status_code=400, detail="Channel name cannot be empty")
    handle = payload.get("handle", "").strip()
    thumbnail_style = payload.get("thumbnail_style", "3D Pixar Animation")
    thumbnail_mood = payload.get("thumbnail_mood", "Joyful, safe, playful, magical, full of adventure")
    schedule_freq = payload.get("schedule_frequency", "daily")
    interval_days = int(payload.get("schedule_interval_days", 1))
    shorts_times = payload.get("shorts_schedule_times", ["11:00", "17:00"])
    long_times = payload.get("long_schedule_times", ["19:30"])
    
    new_ch = create_channel_with_profile(
        channel_name=channel_name,
        handle=handle,
        thumbnail_style=thumbnail_style,
        thumbnail_mood=thumbnail_mood,
        schedule_frequency=schedule_freq,
        schedule_interval_days=interval_days,
        shorts_schedule_times=shorts_times,
        long_schedule_times=long_times
    )
    return {"message": f"Channel '{channel_name}' created successfully!", "channel": new_ch}

@app.post("/api/channels/{channel_id}/login")
def api_channel_login(channel_id: int):
    from channel_manager import launch_channel_login_window
    try:
        res = launch_channel_login_window(channel_id)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/channels/{channel_id}/verify")
def api_channel_verify(channel_id: int):
    from channel_manager import verify_channel_session
    try:
        res = verify_channel_session(channel_id)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/channels/{channel_id}/set-active")
def api_set_active_channel(channel_id: int):
    from db import set_active_channel
    act = set_active_channel(channel_id)
    return {"message": f"Active channel switched to '{act['channel_name']}'", "channel": act}

@app.post("/api/channels/{channel_id}/update")
def api_update_channel(channel_id: int, payload: dict):
    from db import update_channel
    updated = update_channel(channel_id, **payload)
    return {"message": "Channel updated successfully", "channel": updated}

@app.delete("/api/channels/{channel_id}")
def api_delete_channel(channel_id: int):
    from db import delete_channel
    try:
        delete_channel(channel_id)
        return {"message": f"Channel #{channel_id} removed"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/assign-videos-channel")
def api_assign_videos_channel(payload: dict):
    from db import assign_videos_to_channel
    video_ids = payload.get("video_ids", [])
    channel_id = payload.get("channel_id")
    if not video_ids or not channel_id:
        raise HTTPException(status_code=400, detail="Missing video_ids or channel_id")
    count = assign_videos_to_channel(video_ids, int(channel_id))
    return {"message": f"Assigned {count} video(s) to Channel #{channel_id}"}

def bg_scan_folder(folder_path: str):
    global WORKER_STATE
    WORKER_STATE["is_busy"] = True
    WORKER_STATE["current_task"] = f"Scanning {folder_path}..."
    try:
        folder = Path(folder_path).resolve()
        supported = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
        files = [f for f in folder.rglob("*") if f.suffix.lower() in supported]
        for idx, f in enumerate(files):
            try:
                info = inspect_video(f)
                add_video(
                    file_path=f,
                    file_name=f.name,
                    file_size_mb=info["file_size_mb"],
                    duration_seconds=info["duration_seconds"],
                    width=info["width"],
                    height=info["height"],
                    is_short=info["is_short"]
                )
            except Exception as e:
                print(f"Error inspecting {f.name}: {e}")
        WORKER_STATE["last_message"] = f"Scanned {len(files)} videos successfully."
    except Exception as e:
        WORKER_STATE["last_message"] = f"Scan error: {e}"
    finally:
        WORKER_STATE["is_busy"] = False
        WORKER_STATE["current_task"] = "Idle"

@app.post("/api/scan")
def api_scan(payload: dict, background_tasks: BackgroundTasks):
    folder = payload.get("folder", "").strip()
    if not folder or not Path(folder).exists():
        raise HTTPException(status_code=400, detail="Invalid folder path")
    background_tasks.add_task(bg_scan_folder, folder)
    return {"message": "Folder scan started in background"}

def bg_generate_seo(limit: int = 10):
    global WORKER_STATE
    WORKER_STATE["is_busy"] = True
    WORKER_STATE["current_task"] = f"Generating AI SEO (batch of {limit})..."
    try:
        pending = get_videos_by_status("SCANNED")[:limit]
        for idx, item in enumerate(pending, 1):
            WORKER_STATE["current_task"] = f"Generating SEO ({idx}/{len(pending)}): {item['file_name']}"
            res = generate_video_seo(Path(item["file_path"]), is_short=bool(item["is_short"]))
            update_video_metadata(
                video_id=item["id"],
                title=res.get("selected_title", item["file_name"]),
                title_alts=res.get("title_alternatives", []),
                description=res.get("description", ""),
                tags=res.get("tags", []),
                hashtags=res.get("hashtags", [])
            )
        WORKER_STATE["last_message"] = f"AI SEO generated for {len(pending)} videos."
    except Exception as e:
        WORKER_STATE["last_message"] = f"AI error: {e}"
    finally:
        WORKER_STATE["is_busy"] = False
        WORKER_STATE["current_task"] = "Idle"

@app.post("/api/generate-seo")
def api_generate_seo(payload: dict, background_tasks: BackgroundTasks):
    limit = payload.get("limit", 10)
    background_tasks.add_task(bg_generate_seo, limit)
    return {"message": f"AI SEO generation started for {limit} videos"}

@app.post("/api/schedule-all")
def api_schedule_all():
    from scheduler import assign_schedule_slots
    assigned = assign_schedule_slots()
    return {"message": f"Schedules assigned to {len(assigned)} video(s) successfully", "assigned": assigned}

@app.post("/api/schedule-plan")
def api_schedule_plan(payload: dict):
    from scheduler import assign_schedule_slots
    from channel_settings import save_channel_settings

    freq = payload.get("frequency", "daily")
    interval = int(payload.get("interval_days", 1))
    time_slots = payload.get("time_slots", ["11:00", "17:00"])
    start_date = payload.get("start_date")
    target_type = payload.get("target_type", "SHORTS")
    overwrite = payload.get("overwrite_existing", True)
    parent_id = payload.get("parent_id")
    video_ids = payload.get("video_ids")
    channel_id = payload.get("channel_id")

    # Persist frequency preferences for this channel
    save_channel_settings({
        "schedule_frequency": freq,
        "schedule_interval_days": interval,
        "shorts_schedule_times": time_slots
    }, channel_id=channel_id)

    assigned = assign_schedule_slots(
        start_date=start_date,
        frequency=freq,
        interval_days=interval,
        custom_times=time_slots,
        target_type=target_type,
        parent_video_id=parent_id,
        overwrite_existing=overwrite,
        video_ids=video_ids,
        channel_id=channel_id
    )
    return {
        "message": f"Successfully assigned schedules to {len(assigned)} video(s)!",
        "assigned_count": len(assigned),
        "assigned": assigned
    }

@app.post("/api/schedule-preview")
def api_schedule_preview(payload: dict):
    from scheduler import calculate_slots_plan
    from db import get_connection

    freq = payload.get("frequency", "daily")
    interval = int(payload.get("interval_days", 1))
    time_slots = payload.get("time_slots", ["11:00", "17:00"])
    start_date_str = payload.get("start_date")
    target_type = payload.get("target_type", "SHORTS")
    parent_id = payload.get("parent_id")
    video_ids = payload.get("video_ids")
    channel_id = payload.get("channel_id")

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        except Exception:
            start_date = datetime.now() + timedelta(days=1)
    else:
        start_date = datetime.now() + timedelta(days=1)
    start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        where_clauses = ["status != 'PUBLISHED' AND status != 'SCHEDULED'"]
        params = []
        if channel_id:
            where_clauses.append("(channel_id = ? OR channel_id IS NULL OR channel_id = 1)")
            params.append(channel_id)

        if video_ids:
            placeholders = ",".join(["?"] * len(video_ids))
            where_clauses.append(f"id IN ({placeholders})")
            params.extend(video_ids)
        else:
            if target_type == "SHORTS":
                where_clauses.append("is_short = 1")
            elif target_type == "LONG":
                where_clauses.append("is_short = 0")
            if parent_id:
                where_clauses.append("parent_video_id = ?")
                params.append(parent_id)

        query = f"SELECT id, title, file_name, part_number, is_short FROM videos WHERE {' AND '.join(where_clauses)} ORDER BY part_number ASC, id ASC"
        cursor.execute(query, params)
        rows = [dict(r) for r in cursor.fetchall()]

        plan = calculate_slots_plan(
            items=rows,
            start_date=start_date,
            frequency=freq,
            interval_days=interval,
            time_slots=time_slots
        )
        return {"count": len(plan), "plan": plan[:12]}
    finally:
        conn.close()

@app.post("/api/update-video-schedule")
def api_update_video_schedule(payload: dict):
    from scheduler import update_video_schedule
    vid_id = payload.get("video_id")
    dt_str = payload.get("scheduled_datetime", "").strip()
    if not vid_id or not dt_str:
        raise HTTPException(status_code=400, detail="Missing video_id or scheduled_datetime")
    update_video_schedule(int(vid_id), dt_str)
    return {"message": f"Updated schedule for video #{vid_id} to {dt_str}"}

def bg_upload_single(video_id: int, headless: bool = False, channel_id: int = None):
    global WORKER_STATE
    WORKER_STATE["is_busy"] = True
    
    from db import get_channel_by_id, get_active_channel
    target_ch = get_channel_by_id(channel_id) if channel_id else get_active_channel()
    final_channel_id = target_ch["id"] if target_ch else 1
    channel_name = target_ch["channel_name"] if target_ch else "Default Channel"

    WORKER_STATE["current_task"] = f"Uploading Video #{video_id} to {channel_name}..."
    try:
        from log_manager import step_log
        step_log(f"Worker initiated single video upload for #{video_id} on Channel '{channel_name}' (#{final_channel_id}, headless={headless})", level="INFO", video_id=video_id)

        cmd = [sys.executable, str(BASE_DIR / "scheduler.py"), "upload", str(video_id), "--channel", str(final_channel_id)]
        if headless:
            cmd.append("--headless")

        sub_env = os.environ.copy()
        sub_env["PYTHONIOENCODING"] = "utf-8"
        sub_env["PYTHONUTF8"] = "1"

        proc = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace", env=sub_env)
        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or proc.stdout.strip() or f"Process exited with code {proc.returncode}"
            WORKER_STATE["last_message"] = f"Upload error on #{video_id}: {err_msg[:120]}"
            print(f"[!] Worker bg_upload_single failed for #{video_id}:\n{err_msg}", flush=True)
            step_log(f"Worker upload error on #{video_id}: {err_msg}", level="ERROR", video_id=video_id)
        else:
            from db import get_connection
            v_status = "SCHEDULED"
            try:
                c = get_connection()
                row = c.cursor().execute("SELECT status FROM videos WHERE id = ?", (video_id,)).fetchone()
                if row and row["status"]:
                    v_status = row["status"]
                c.close()
            except Exception:
                pass
            if v_status == "PUBLISHED":
                WORKER_STATE["last_message"] = f"Video #{video_id} published LIVE to '{channel_name}' successfully!"
            else:
                WORKER_STATE["last_message"] = f"Video #{video_id} uploaded & scheduled to '{channel_name}' successfully!"
            step_log(f"Worker completed upload for #{video_id} to '{channel_name}' successfully ({v_status})!", level="SUCCESS", video_id=video_id)
    except Exception as e:
        err_msg = str(e).strip() or repr(e)
        WORKER_STATE["last_message"] = f"Upload error on #{video_id}: {err_msg}"
        from log_manager import step_log
        step_log(f"Worker upload exception on #{video_id}: {err_msg}", level="ERROR", video_id=video_id)
    finally:
        WORKER_STATE["is_busy"] = False
        WORKER_STATE["current_task"] = "Idle"

@app.post("/api/upload-single-video")
def api_upload_single_video(payload: dict, background_tasks: BackgroundTasks):
    vid_id = payload.get("video_id")
    headless = payload.get("headless", False)
    req_ch_id = payload.get("channel_id")
    if not vid_id:
        raise HTTPException(status_code=400, detail="Missing video_id")
    
    from db import get_channel_by_id, get_active_channel, get_connection
    target_ch = get_channel_by_id(req_ch_id) if req_ch_id else get_active_channel()
    final_channel_id = target_ch["id"] if target_ch else 1
    channel_name = target_ch["channel_name"] if target_ch else "Default Channel"

    # Single Source of Truth: update video's channel_id in DB to match
    try:
        conn = get_connection()
        conn.cursor().execute("UPDATE videos SET channel_id = ? WHERE id = ?", (final_channel_id, int(vid_id)))
        conn.commit()
        conn.close()
    except Exception:
        pass

    background_tasks.add_task(bg_upload_single, int(vid_id), headless, final_channel_id)
    return {"message": f"Upload started for video #{vid_id} on {channel_name}", "channel_name": channel_name, "channel_id": final_channel_id}

def bg_upload_next(limit: int = 1, headless: bool = False, channel_id: int = None):
    global WORKER_STATE
    WORKER_STATE["is_busy"] = True
    from db import get_channel_by_id, get_active_channel
    target_ch = get_channel_by_id(channel_id) if channel_id else get_active_channel()
    final_channel_id = target_ch["id"] if target_ch else 1
    channel_name = target_ch["channel_name"] if target_ch else "Default Channel"

    WORKER_STATE["current_task"] = f"Uploading & scheduling next {limit} video(s) to {channel_name}..."
    try:
        from log_manager import step_log
        step_log(f"Worker initiating batch upload of {limit} video(s) for Channel '{channel_name}' (#{final_channel_id})...", level="INFO")

        cmd = [sys.executable, str(BASE_DIR / "scheduler.py"), "queue", str(limit), "--channel", str(final_channel_id)]
        if headless:
            cmd.append("--headless")

        sub_env = os.environ.copy()
        sub_env["PYTHONIOENCODING"] = "utf-8"
        sub_env["PYTHONUTF8"] = "1"

        proc = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace", env=sub_env)
        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or proc.stdout.strip() or f"Process exited with code {proc.returncode}"
            WORKER_STATE["last_message"] = f"Batch upload failed: {err_msg[:120]}"
            print(f"[!] Worker bg_upload_next failed:\n{err_msg}", flush=True)
            step_log(f"Worker batch upload error: {err_msg}", level="ERROR")
        else:
            WORKER_STATE["last_message"] = f"Batch upload of {limit} video(s) to '{channel_name}' completed!"
            step_log(f"Worker completed batch upload of {limit} video(s) to '{channel_name}'!", level="SUCCESS")
    except Exception as e:
        err_msg = str(e).strip() or repr(e)
        WORKER_STATE["last_message"] = f"Upload error: {err_msg}"
        from log_manager import step_log
        step_log(f"Worker batch upload exception: {err_msg}", level="ERROR")
    finally:
        WORKER_STATE["is_busy"] = False
        WORKER_STATE["current_task"] = "Idle"

@app.post("/api/upload-next")
def api_upload_next(payload: dict, background_tasks: BackgroundTasks):
    limit = payload.get("limit", 1)
    headless = payload.get("headless", False)
    req_ch_id = payload.get("channel_id")
    from db import get_channel_by_id, get_active_channel
    target_ch = get_channel_by_id(req_ch_id) if req_ch_id else get_active_channel()
    final_channel_id = target_ch["id"] if target_ch else 1
    channel_name = target_ch["channel_name"] if target_ch else "Default Channel"

    background_tasks.add_task(bg_upload_next, limit, headless, final_channel_id)
    return {"message": f"Batch upload started for next {limit} video(s) on {channel_name}", "channel_name": channel_name}

def bg_create_shorts(video_id: int, num_parts: int = 0, add_parts_label: bool = True):
    global WORKER_STATE
    WORKER_STATE["is_busy"] = True
    WORKER_STATE["current_task"] = "Preparing whole-video Shorts slicing..."
    try:
        import importlib
        import shorts_generator
        importlib.reload(shorts_generator)
        from shorts_generator import create_shorts_from_video
        from db import get_connection
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT file_path, file_name, duration_seconds FROM videos WHERE id = ?", (video_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            raise Exception("Video not found")

        v_path = Path(row["file_path"])
        v_name = row["file_name"]

        def on_progress(p_idx, total_p, msg):
            pct = int((p_idx / max(1, total_p)) * 100)
            label_flag = " (Part labels ON)" if add_parts_label else ""
            WORKER_STATE["current_task"] = f"Slicing Shorts [{p_idx}/{total_p}]{label_flag}: {v_name[:20]}..."
            WORKER_STATE["progress"] = pct
            WORKER_STATE["last_message"] = msg

        created = create_shorts_from_video(
            v_path, 
            num_parts=num_parts, 
            add_parts_label=add_parts_label, 
            parent_video_id=video_id,
            progress_callback=on_progress
        )
        assign_schedule_slots()
        label_txt = " (with Parts labels)" if add_parts_label else ""
        WORKER_STATE["last_message"] = f"Generated {len(created)} vertical Shorts{label_txt} for {v_name[:25]}."
    except Exception as e:
        WORKER_STATE["last_message"] = f"Shorts creation error: {e}"
    finally:
        WORKER_STATE["is_busy"] = False
        WORKER_STATE["current_task"] = "Idle"

@app.post("/api/create-shorts")
def api_create_shorts(payload: dict, background_tasks: BackgroundTasks):
    video_id = payload.get("video_id")
    num_parts = payload.get("num_parts", 0)
    add_parts_label = payload.get("add_parts_label", True)
    background_tasks.add_task(bg_create_shorts, video_id, num_parts, add_parts_label)
    return {"message": "Shorts generation started in background"}

@app.post("/api/upload-next")
def api_upload_next(payload: dict, background_tasks: BackgroundTasks):
    limit = payload.get("limit", 1)
    headless = payload.get("headless", False)
    background_tasks.add_task(bg_upload_next, limit, headless)
    return {"message": f"Upload started for next {limit} video(s)"}

# -------------------------------------------------------------
# COMMAND RUNNER & TERMINAL CONSOLE LOGGING
# -------------------------------------------------------------
CONSOLE_LOGS = []
CONSOLE_STATE = {
    "is_running": False,
    "current_command": "",
    "returncode": None
}
CONSOLE_PROCESS = None

def run_cmd_worker(cmd: str):
    global CONSOLE_LOGS, CONSOLE_STATE, CONSOLE_PROCESS
    CONSOLE_STATE["is_running"] = True
    CONSOLE_STATE["current_command"] = cmd
    CONSOLE_STATE["returncode"] = None
    CONSOLE_LOGS.append(f"\n$ {cmd}\n")
    try:
        py_exec = sys.executable
        final_cmd = cmd
        if final_cmd.startswith("python "):
            final_cmd = f'"{py_exec}" ' + final_cmd[7:]
        elif final_cmd.startswith("python.exe "):
            final_cmd = f'"{py_exec}" ' + final_cmd[11:]

        sub_env = os.environ.copy()
        sub_env["PYTHONIOENCODING"] = "utf-8"
        sub_env["PYTHONUTF8"] = "1"

        final_cmd = f"chcp 65001 >nul & {final_cmd}"

        CONSOLE_PROCESS = subprocess.Popen(
            final_cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(BASE_DIR),
            env=sub_env
        )
        for line in iter(CONSOLE_PROCESS.stdout.readline, ""):
            CONSOLE_LOGS.append(line)
            if len(CONSOLE_LOGS) > 3000:
                CONSOLE_LOGS.pop(0)
        CONSOLE_PROCESS.stdout.close()
        ret = CONSOLE_PROCESS.wait()
        CONSOLE_STATE["returncode"] = ret
        status_msg = f"[Process finished with exit code {ret}]" if ret == 0 else f"[Process failed with exit code {ret}]"
        CONSOLE_LOGS.append(f"\n{status_msg}\n")
    except Exception as e:
        CONSOLE_STATE["returncode"] = -1
        CONSOLE_LOGS.append(f"\n[Execution error: {e}]\n")
    finally:
        CONSOLE_STATE["is_running"] = False
        CONSOLE_PROCESS = None

@app.post("/api/run-command")
def api_run_command(payload: dict, background_tasks: BackgroundTasks):
    global CONSOLE_STATE
    if CONSOLE_STATE["is_running"]:
        return JSONResponse(status_code=409, content={"message": "A command is already running in the console."})
    
    cmd = payload.get("command", "").strip()
    if not cmd:
        action = payload.get("action", "")
        if action == "scan":
            folder = payload.get("folder", "").strip()
            cmd = f'python main.py scan "{folder}"'
        elif action == "make-shorts":
            vid_id = payload.get("video_id")
            parts = payload.get("parts", 0)
            label = "" if payload.get("add_parts_label", True) else " --no-parts-label"
            cmd = f'python main.py make-shorts {vid_id} --parts {parts}{label}'
        elif action == "generate-seo":
            limit = payload.get("limit", 10)
            cmd = f'python main.py generate-seo --limit {limit}'
        elif action == "schedule-all":
            cmd = 'python main.py schedule-all'
        elif action == "upload":
            limit = payload.get("limit", 1)
            headless = " --headless" if payload.get("headless", False) else ""
            cmd = f'python main.py upload --limit {limit}{headless}'
        elif action == "login":
            cmd = 'python main.py login'
        elif action == "status":
            cmd = 'python main.py status'
        elif action == "list":
            flt = payload.get("filter", "")
            cmd = f'python main.py list' + (f' --filter {flt}' if flt else '')
        elif action == "logs":
            limit = payload.get("limit", 15)
            cmd = f'python main.py logs --limit {limit}'
        elif action == "generate-thumbnails":
            ttype = payload.get("type", "SHORTS")
            cmd = f'python main.py generate-thumbnails --type {ttype}'

    if not cmd:
        raise HTTPException(status_code=400, detail="No command or action specified")

    background_tasks.add_task(run_cmd_worker, cmd)
    return {"message": f"Command started: {cmd}", "command": cmd}

@app.get("/api/terminal-logs")
def api_terminal_logs(since_index: int = 0):
    total = len(CONSOLE_LOGS)
    lines = CONSOLE_LOGS[since_index:] if since_index < total else []
    return {
        "is_running": CONSOLE_STATE["is_running"],
        "current_command": CONSOLE_STATE["current_command"],
        "returncode": CONSOLE_STATE["returncode"],
        "lines": lines,
        "next_index": total
    }

@app.post("/api/terminal-clear")
def api_terminal_clear():
    global CONSOLE_LOGS
    CONSOLE_LOGS.clear()
    return {"message": "Terminal logs cleared"}

@app.post("/api/terminal-kill")
def api_terminal_kill():
    global CONSOLE_PROCESS
    if CONSOLE_PROCESS and CONSOLE_PROCESS.poll() is None:
        try:
            CONSOLE_PROCESS.terminate()
            return {"message": "Command process termination signal sent"}
        except Exception as e:
            return {"message": f"Kill error: {e}"}
    return {"message": "No running command to terminate"}

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>YouTube AI Auto-Publisher Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://js.puter.com/v2/"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    body { background-color: #0b0f19; color: #e2e8f0; font-family: ui-sans-serif, system-ui, sans-serif; }
    .card { background-color: #131b2e; border: 1px solid #1e293b; border-radius: 0.75rem; }
    .table-row:hover { background-color: #1a233a; }
  </style>
</head>
<body class="min-h-screen p-6">
  <div class="max-w-7xl mx-auto space-y-6">
    
    <!-- Top Bar -->
    <header class="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4 pb-4 border-b border-slate-800">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-red-600 flex items-center justify-center text-white text-xl shadow-lg shadow-red-500/30">
          <i class="fa-brands fa-youtube"></i>
        </div>
        <div>
          <h1 class="text-2xl font-bold text-white tracking-tight">YouTube AI Auto-Publisher</h1>
          <p class="text-xs text-slate-400">Automated 500-Video Viral Scheduling & Multi-Part Shorts Platform</p>
        </div>
      </div>

      <!-- Controls & Health Badges -->
      <div class="flex items-center gap-3 text-xs flex-wrap">
        <!-- Master "Add Parts Label:" Toggle -->
        <div class="flex items-center gap-2 px-3 py-1.5 rounded-full card border border-slate-700/80 bg-slate-900/90 shadow-sm" title="Controls whether Part numbers like 'Part: 1' are embedded on video and added to title">
          <i class="fa-solid fa-tag text-amber-400"></i>
          <span class="text-xs text-slate-300 font-medium">Add Parts Label:</span>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" id="global-parts-label-toggle" checked onchange="toggleGlobalPartsLabel()" class="sr-only peer">
            <div class="w-8 h-4 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-amber-500"></div>
          </label>
          <span id="global-parts-label-text" class="text-[11px] text-amber-400 font-bold">ON</span>
        </div>

        <div id="badge-yt" class="flex items-center gap-2 px-3 py-1.5 rounded-full card">
          <span class="w-2.5 h-2.5 rounded-full bg-slate-500 animate-pulse"></span>
          <span>YouTube Studio: Checking...</span>
        </div>
        <div id="badge-ai" class="flex items-center gap-2 px-3 py-1.5 rounded-full card">
          <span class="w-2.5 h-2.5 rounded-full bg-slate-500 animate-pulse"></span>
          <span>Gemini 3.8 Flash: Checking...</span>
        </div>
        <div id="badge-worker" class="flex items-center gap-2 px-3 py-1.5 rounded-full card">
          <span class="w-2.5 h-2.5 rounded-full bg-blue-500"></span>
          <span id="worker-task-text">Idle</span>
        </div>

        <!-- Channel Switcher Dropdown in Top Bar -->
        <div class="relative inline-block text-left" id="channel-dropdown-container">
          <button onclick="toggleChannelDropdown(event)" id="current-channel-btn" class="flex items-center gap-2 px-3 py-1.5 rounded-full card border border-slate-700 bg-slate-900/90 hover:bg-slate-800 text-white font-medium text-xs shadow-sm transition">
            <span id="active-channel-indicator" class="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse"></span>
            <span class="text-slate-400 font-normal">Channel:</span>
            <span id="active-channel-name" class="font-bold text-white">Lil Kido Stories</span>
            <span id="active-channel-badge" class="px-1.5 py-0.2 text-[9px] rounded bg-emerald-950 text-emerald-400 border border-emerald-800/80">Connected</span>
            <i class="fa-solid fa-chevron-down text-slate-400 text-[10px] ml-0.5"></i>
          </button>
          
          <div id="channel-dropdown-menu" class="hidden absolute right-0 mt-2 w-72 rounded-xl card bg-slate-900 border border-slate-700 shadow-2xl z-50 p-2 space-y-1">
            <div class="px-3 py-1.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider flex items-center justify-between border-b border-slate-800">
              <span>Connected Channels</span>
              <span id="dropdown-channel-count" class="text-indigo-400 font-mono">1</span>
            </div>
            <div id="channel-dropdown-list" class="space-y-1 max-h-56 overflow-y-auto py-1">
              <!-- Dynamically rendered -->
            </div>
            <div class="pt-2 border-t border-slate-800 flex items-center gap-1.5 text-[11px]">
              <button onclick="openCreateChannelModal(); closeChannelDropdown();" class="flex-1 py-1.5 px-2 bg-indigo-600/30 hover:bg-indigo-600/40 text-indigo-300 rounded-lg text-center font-semibold transition flex items-center justify-center gap-1">
                <i class="fa-solid fa-plus text-[10px]"></i> Connect Account
              </button>
              <button onclick="switchTab('channels'); closeChannelDropdown();" class="py-1.5 px-2.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg font-medium transition" title="Channels Panel">
                <i class="fa-solid fa-users-gear text-xs"></i>
              </button>
            </div>
          </div>
        </div>
      </div>
    </header>

    <!-- Metrics Cards (6 KPIs) -->
    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
      <div class="card p-4">
        <div class="text-xs text-slate-400 font-medium">Total Media</div>
        <div id="kpi-total" class="text-2xl font-bold text-white mt-1">0</div>
        <div class="text-[11px] text-slate-500 mt-1"><span id="kpi-long">0</span> Long-Form</div>
      </div>
      <div class="card p-4 border-amber-900/50 bg-amber-950/20">
        <div class="text-xs text-amber-400 font-medium flex items-center gap-1">
          <i class="fa-solid fa-bolt"></i> Generated Parts
        </div>
        <div id="kpi-parts" class="text-2xl font-bold text-amber-300 mt-1">0</div>
        <div class="text-[11px] text-amber-400/80 mt-1">⚡ 35s Hinglish Shorts</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-slate-400 font-medium">Pending AI SEO</div>
        <div id="kpi-pending" class="text-2xl font-bold text-amber-400 mt-1">0</div>
        <div class="text-[11px] text-slate-500 mt-1">Awaiting metadata</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-slate-400 font-medium">Ready to Schedule</div>
        <div id="kpi-ready" class="text-2xl font-bold text-blue-400 mt-1">0</div>
        <div class="text-[11px] text-slate-500 mt-1">Metadata ready</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-slate-400 font-medium">Scheduled & Live</div>
        <div class="flex items-baseline gap-2 mt-1">
          <span id="kpi-scheduled" class="text-2xl font-bold text-emerald-400">0</span>
          <span class="text-[10px] text-slate-400 font-mono">sched</span>
          <span id="kpi-published" class="text-2xl font-bold text-green-300 ml-1">0</span>
          <span class="text-[10px] text-green-400 font-mono">live</span>
        </div>
        <div class="text-[11px] text-slate-500 mt-1">Uploaded to YouTube</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-slate-400 font-medium">Failed</div>
        <div id="kpi-failed" class="text-2xl font-bold text-rose-400 mt-1">0</div>
        <div class="text-[11px] text-slate-500 mt-1">Requires review</div>
      </div>
    </div>

    <!-- Actions Control Bar -->
    <div class="card p-4 space-y-4">
      <div class="flex flex-col lg:flex-row items-center justify-between gap-4">
        <!-- Scan Input -->
        <div class="flex items-center gap-2 w-full lg:w-1/2">
          <input type="text" id="scan-folder-input" placeholder="Paste full path to your 500 videos folder..." 
                 class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-red-500">
          <button onclick="scanFolder()" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white rounded-lg text-sm font-medium whitespace-nowrap transition">
            <i class="fa-solid fa-folder-open mr-1"></i> Scan Folder
          </button>
        </div>

        <!-- Quick Action Buttons -->
        <div class="flex items-center gap-2 w-full lg:w-auto justify-end flex-wrap">
          <button onclick="generateSeoBatch()" class="px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white rounded-lg text-sm font-medium transition shadow-lg shadow-purple-600/20">
            <i class="fa-solid fa-wand-magic-sparkles mr-1"></i> Generate AI SEO
          </button>
          <button onclick="openScheduleModal('ALL')" class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-medium transition shadow-lg shadow-blue-600/20">
            <i class="fa-solid fa-calendar-days mr-1"></i> 🗓️ Schedule & Frequency
          </button>
          <button onclick="openUploadModal('ALL')" class="px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded-lg text-sm font-medium transition shadow-lg shadow-red-600/20">
            <i class="fa-solid fa-cloud-arrow-up mr-1"></i> ☁️ Upload to YouTube
          </button>
        </div>
      </div>

      <!-- Live status message banner -->
      <div id="status-banner" class="text-xs text-slate-400 bg-slate-900/60 p-2.5 rounded-lg border border-slate-800 flex items-center justify-between">
        <div><i class="fa-solid fa-circle-info text-blue-400 mr-1.5"></i> <span id="banner-text">Ready. Enter folder path or start uploading.</span></div>
        <div class="text-[11px] text-slate-500">Auto-refreshing every 3s</div>
      </div>
    </div>

    <!-- Tabs Navigation -->
    <div class="flex items-center gap-4 border-b border-slate-800 pb-2 text-sm font-medium overflow-x-auto">
      <button onclick="switchTab('videos')" id="tab-btn-videos" class="text-white border-b-2 border-red-500 pb-2 transition flex items-center gap-1.5 whitespace-nowrap">
        <i class="fa-solid fa-list-check"></i> Video Queue
      </button>
      <button onclick="switchTab('parts')" id="tab-btn-parts" class="text-slate-400 hover:text-white pb-2 transition flex items-center gap-1.5 whitespace-nowrap">
        <i class="fa-solid fa-bolt text-amber-400"></i> Generated Parts (<span id="parts-badge-count">0</span>)
      </button>
      <button onclick="switchTab('channels')" id="tab-btn-channels" class="text-slate-400 hover:text-white pb-2 transition flex items-center gap-1.5 whitespace-nowrap">
        <i class="fa-solid fa-users-gear text-indigo-400"></i> Channels & Accounts (<span id="channels-badge-count">1</span>)
      </button>
      <button onclick="switchTab('logs')" id="tab-btn-logs" class="text-slate-400 hover:text-white pb-2 transition flex items-center gap-1.5 whitespace-nowrap">
        <i class="fa-solid fa-clock-rotate-left"></i> Upload Logs History
      </button>
      <button onclick="switchTab('commands')" id="tab-btn-commands" class="text-slate-400 hover:text-white pb-2 transition flex items-center gap-1.5 whitespace-nowrap">
        <i class="fa-solid fa-terminal text-emerald-400"></i> Command Panel & Runner
      </button>
      <button onclick="switchTab('branding')" id="tab-btn-branding" class="text-slate-400 hover:text-white pb-2 transition flex items-center gap-1.5 whitespace-nowrap">
        <i class="fa-solid fa-palette text-pink-400"></i> Channel & Branding
      </button>
    </div>

    <!-- Tab 1: Video Queue Table -->
    <div id="tab-videos" class="card overflow-hidden">
      <div class="p-3 border-b border-slate-800 flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
        <div class="flex items-center gap-2">
          <span class="text-sm font-semibold text-slate-300">Video Queue</span>
          <!-- Parent filter active indicator -->
          <div id="parent-filter-chip" class="hidden items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-amber-950/80 text-amber-300 border border-amber-800 text-[11px]">
            <span>Filtered: <strong id="parent-filter-title">...</strong></span>
            <button onclick="clearParentFilter()" class="ml-1 text-slate-400 hover:text-white">&times;</button>
          </div>
        </div>

        <div class="flex items-center gap-2 flex-wrap w-full md:w-auto">
          <!-- Live Search Input -->
          <div class="relative">
            <input type="text" id="video-search-input" oninput="loadVideos()" placeholder="Search title or file..." 
                   class="bg-slate-900 border border-slate-700 rounded-lg pl-7 pr-2.5 py-1 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-red-500">
            <i class="fa-solid fa-magnifying-glass absolute left-2.5 top-2 text-[10px] text-slate-500"></i>
          </div>

          <!-- Channel Filter -->
          <select id="channel-filter" onchange="onChannelFilterChange()" class="bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1 text-xs text-indigo-300 font-medium">
            <option value="ALL">🌐 All Channels</option>
          </select>

          <!-- Video Type Filter -->
          <select id="type-filter" onchange="loadVideos()" class="bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1 text-xs text-white">
            <option value="ALL">All Media Types</option>
            <option value="LONG">🎬 Long-Form Only</option>
            <option value="PARTS">⚡ Generated Parts & Shorts</option>
          </select>

          <!-- Status Filter -->
          <select id="status-filter" onchange="loadVideos()" class="bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1 text-xs text-white">
            <option value="ALL">All Statuses</option>
            <option value="SCANNED">Pending AI SEO (SCANNED)</option>
            <option value="METADATA_READY">Ready to Schedule</option>
            <option value="SCHEDULED">Scheduled</option>
            <option value="PUBLISHED">Published (Live)</option>
            <option value="FAILED">Failed</option>
          </select>
        </div>
      </div>

      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs">
          <thead class="bg-slate-900/80 text-slate-400 border-b border-slate-800 uppercase tracking-wider text-[10px]">
            <tr>
              <th class="p-3">ID</th>
              <th class="p-3">Channel</th>
              <th class="p-3">Type / Part #</th>
              <th class="p-3">File / Title</th>
              <th class="p-3">Duration</th>
              <th class="p-3">Scheduled For</th>
              <th class="p-3">Status</th>
              <th class="p-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="videos-tbody" class="divide-y divide-slate-800/60 text-slate-300">
            <tr><td colspan="7" class="p-4 text-center text-slate-500">Loading videos...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Tab 2: Generated Parts Dedicated View -->
    <div id="tab-parts" class="card overflow-hidden hidden">
      <div class="p-3 border-b border-slate-800 flex flex-col md:flex-row items-start md:items-center justify-between gap-3 bg-slate-900/40">
        <div>
          <div class="text-sm font-semibold text-white flex items-center gap-2">
            <i class="fa-solid fa-bolt text-amber-400"></i>
            <span>All Generated Parts & Multi-Part Shorts</span>
            <span id="tab-parts-count-pill" class="text-[11px] px-2 py-0.5 rounded-full bg-amber-950 text-amber-300 border border-amber-800">19 Shorts</span>
          </div>
          <p class="text-[11px] text-slate-400 mt-0.5">Vertical 9:16 Shorts sliced in 35-second segments (5s overlap) with Hinglish subtitles & outro</p>
        </div>
        
        <div class="flex items-center gap-2 flex-wrap">
          <button onclick="generateAllThumbnails('SHORTS')" class="px-3 py-1.5 bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white rounded-lg text-xs font-semibold shadow-md shadow-purple-600/20 transition flex items-center gap-1.5">
            <i class="fa-solid fa-wand-magic-sparkles text-[11px]"></i>
            <span>🎨 Generate All 19 AI Thumbnails</span>
          </button>

          <button onclick="openScheduleModal('SHORTS')" class="px-3 py-1.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white rounded-lg text-xs font-semibold shadow-md shadow-blue-600/20 transition flex items-center gap-1.5">
            <i class="fa-solid fa-calendar-days text-[11px]"></i>
            <span>🗓️ Smart Schedule All 19 Shorts</span>
          </button>

          <button onclick="openUploadModal('SHORTS')" class="px-3 py-1.5 bg-gradient-to-r from-red-600 to-rose-600 hover:from-red-500 hover:to-rose-500 text-white rounded-lg text-xs font-semibold shadow-md shadow-red-600/20 transition flex items-center gap-1.5">
            <i class="fa-solid fa-cloud-arrow-up text-[11px]"></i>
            <span>☁️ Batch Upload to YouTube</span>
          </button>

          <button onclick="loadParts()" class="px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium transition flex items-center gap-1">
            <i class="fa-solid fa-arrows-rotate text-[11px]"></i> Refresh
          </button>
        </div>
      </div>

      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs">
          <thead class="bg-slate-900/80 text-slate-400 border-b border-slate-800 uppercase tracking-wider text-[10px]">
            <tr>
              <th class="p-3">Short ID</th>
              <th class="p-3">Channel</th>
              <th class="p-3">Thumbnail</th>
              <th class="p-3">Part #</th>
              <th class="p-3">Short Title</th>
              <th class="p-3">Parent Story</th>
              <th class="p-3">Duration</th>
              <th class="p-3">Scheduled Slot</th>
              <th class="p-3">Status</th>
              <th class="p-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="parts-tbody" class="divide-y divide-slate-800/60 text-slate-300">
            <tr><td colspan="9" class="p-6 text-center text-slate-500">Loading parts...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Tab 3: Logs Table -->
    <div id="tab-logs" class="card overflow-hidden hidden">
      <div class="p-3 border-b border-slate-800 flex items-center justify-between">
        <div class="text-sm font-semibold text-slate-300">Upload Logs (upload_logs.json)</div>
        <button onclick="loadLogs()" class="text-xs text-slate-400 hover:text-white"><i class="fa-solid fa-arrows-rotate mr-1"></i> Refresh</button>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs">
          <thead class="bg-slate-900/80 text-slate-400 border-b border-slate-800 uppercase tracking-wider text-[10px]">
            <tr>
              <th class="p-3">#</th>
              <th class="p-3">Timestamp</th>
              <th class="p-3">Type</th>
              <th class="p-3">Title</th>
              <th class="p-3">Scheduled For</th>
              <th class="p-3">Status</th>
              <th class="p-3">YouTube Link</th>
            </tr>
          </thead>
          <tbody id="logs-tbody" class="divide-y divide-slate-800/60 text-slate-300">
            <tr><td colspan="7" class="p-4 text-center text-slate-500">No logs found yet.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Tab 4: Command Center & Interactive Terminal Console -->
    <div id="tab-commands" class="space-y-6 hidden">
      
      <!-- Top Section: Command Cards (Grid of 6) -->
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        
        <!-- Command 1: Scan Videos Folder -->
        <div class="card p-4 space-y-3 border-slate-700/80 bg-slate-900/60 flex flex-col justify-between">
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2 text-white font-semibold text-sm">
                <div class="w-7 h-7 rounded-lg bg-blue-600/20 text-blue-400 flex items-center justify-center text-xs">
                  <i class="fa-solid fa-folder-open"></i>
                </div>
                <span>1. Scan Videos Folder</span>
              </div>
              <span class="text-[10px] font-mono text-slate-500">scan</span>
            </div>
            <p class="text-xs text-slate-400">Scans directory, analyzes durations & aspect ratios, and registers new files into SQLite.</p>
            <div class="space-y-1">
              <label class="block text-[11px] text-slate-400">Folder Path</label>
              <input type="text" id="cmd-scan-path" value="D:\Videos\Animations\500+2D Cartoon Bundle-20260920T075834Z-1-005\500+2D Cartoon Bundle" 
                     class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500 font-mono text-[11px]">
              <button onclick="document.getElementById('cmd-scan-path').value='D:\\Videos\\Animations\\500+2D Cartoon Bundle-20260920T075834Z-1-005\\500+2D Cartoon Bundle'" 
                      class="text-[10px] text-blue-400 hover:text-blue-300">
                ↺ Reset to 500+ Cartoon Path
              </button>
            </div>
          </div>
          <button onclick="runCommandAction('scan')" class="w-full mt-2 py-2 px-3 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-semibold shadow-md shadow-blue-600/20 transition flex items-center justify-center gap-1.5">
            <i class="fa-solid fa-play text-[10px]"></i> Run Scan (<span class="font-mono text-[11px]">scan</span>)
          </button>
        </div>

        <!-- Command 2: Make Multi-Part Shorts -->
        <div class="card p-4 space-y-3 border-amber-900/60 bg-amber-950/10 flex flex-col justify-between">
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2 text-white font-semibold text-sm">
                <div class="w-7 h-7 rounded-lg bg-amber-500/20 text-amber-400 flex items-center justify-center text-xs">
                  <i class="fa-solid fa-scissors"></i>
                </div>
                <span>2. Cut Video to Shorts</span>
              </div>
              <span class="text-[10px] font-mono text-amber-400">make-shorts</span>
            </div>
            <p class="text-xs text-slate-400">Converts video into 35s vertical Shorts with Hinglish dialogue subtitles & 5s overlap.</p>
            <div class="grid grid-cols-2 gap-2">
              <div>
                <label class="block text-[11px] text-slate-400 mb-0.5">Select Video</label>
                <select id="cmd-shorts-video-id" class="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-amber-500">
                  <option value="5">Loading videos...</option>
                </select>
              </div>
              <div>
                <label class="block text-[11px] text-slate-400 mb-0.5">Parts Count</label>
                <select id="cmd-shorts-parts" class="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-amber-500">
                  <option value="0" selected>Whole Video (Max 30)</option>
                  <option value="1">1 Part (0-35s)</option>
                  <option value="3">3 Parts (~95s)</option>
                  <option value="5">5 Parts (~155s)</option>
                  <option value="10">10 Parts (~305s)</option>
                </select>
              </div>
            </div>
            <div class="flex items-center gap-2 pt-1">
              <input type="checkbox" id="cmd-shorts-label" checked class="rounded bg-slate-900 border-slate-700 text-amber-500 focus:ring-0">
              <label for="cmd-shorts-label" class="text-xs text-slate-300 font-medium">Embed "Part: 1" Label Badge</label>
            </div>
          </div>
          <button onclick="runCommandAction('make-shorts')" class="w-full mt-2 py-2 px-3 bg-gradient-to-r from-amber-600 to-red-600 hover:from-amber-500 hover:to-red-500 text-white rounded-lg text-xs font-semibold shadow-md shadow-amber-600/30 transition flex items-center justify-center gap-1.5">
            <i class="fa-solid fa-bolt text-[10px]"></i> Slices into Shorts (<span class="font-mono text-[11px]">make-shorts</span>)
          </button>
        </div>

        <!-- Command 3: Generate AI SEO -->
        <div class="card p-4 space-y-3 border-slate-700/80 bg-slate-900/60 flex flex-col justify-between">
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2 text-white font-semibold text-sm">
                <div class="w-7 h-7 rounded-lg bg-purple-600/20 text-purple-400 flex items-center justify-center text-xs">
                  <i class="fa-solid fa-wand-magic-sparkles"></i>
                </div>
                <span>3. Generate AI SEO</span>
              </div>
              <span class="text-[10px] font-mono text-purple-400">generate-seo</span>
            </div>
            <p class="text-xs text-slate-400">Calls Gemini AI to generate click-worthy viral titles, full descriptions, hashtags, and category tags.</p>
            <div>
              <label class="block text-[11px] text-slate-400 mb-0.5">Batch Processing Limit</label>
              <select id="cmd-seo-limit" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-purple-500">
                <option value="5">Batch of 5 videos</option>
                <option value="10" selected>Batch of 10 videos</option>
                <option value="25">Batch of 25 videos</option>
                <option value="50">Batch of 50 videos</option>
              </select>
            </div>
          </div>
          <button onclick="runCommandAction('generate-seo')" class="w-full mt-2 py-2 px-3 bg-purple-600 hover:bg-purple-500 text-white rounded-lg text-xs font-semibold shadow-md shadow-purple-600/20 transition flex items-center justify-center gap-1.5">
            <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i> Generate SEO (<span class="font-mono text-[11px]">generate-seo</span>)
          </button>
        </div>

        <!-- Command 4: Schedule Queue -->
        <div class="card p-4 space-y-3 border-slate-700/80 bg-slate-900/60 flex flex-col justify-between">
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2 text-white font-semibold text-sm">
                <div class="w-7 h-7 rounded-lg bg-cyan-600/20 text-cyan-400 flex items-center justify-center text-xs">
                  <i class="fa-solid fa-calendar-days"></i>
                </div>
                <span>4. Auto-Schedule Queue</span>
              </div>
              <span class="text-[10px] font-mono text-cyan-400">schedule-all</span>
            </div>
            <p class="text-xs text-slate-400">Assigns YouTube release time slots: Shorts publish at 11:00 & 17:00, Long-form at 19:30.</p>
            <div class="p-2 rounded bg-slate-950 border border-slate-800 text-[11px] text-slate-400 space-y-0.5">
              <div class="flex justify-between"><span>Shorts Slots:</span> <span class="text-cyan-300 font-mono">11:00, 17:00</span></div>
              <div class="flex justify-between"><span>Long Slots:</span> <span class="text-blue-300 font-mono">19:30</span></div>
            </div>
          </div>
          <button onclick="runCommandAction('schedule-all')" class="w-full mt-2 py-2 px-3 bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg text-xs font-semibold shadow-md shadow-cyan-600/20 transition flex items-center justify-center gap-1.5">
            <i class="fa-solid fa-calendar-check text-[10px]"></i> Schedule Slots (<span class="font-mono text-[11px]">schedule-all</span>)
          </button>
        </div>

        <!-- Command 5: Upload to YouTube -->
        <div class="card p-4 space-y-3 border-slate-700/80 bg-slate-900/60 flex flex-col justify-between">
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2 text-white font-semibold text-sm">
                <div class="w-7 h-7 rounded-lg bg-red-600/20 text-red-400 flex items-center justify-center text-xs">
                  <i class="fa-solid fa-cloud-arrow-up"></i>
                </div>
                <span>5. Upload to YouTube</span>
              </div>
              <span class="text-[10px] font-mono text-red-400">upload</span>
            </div>
            <p class="text-xs text-slate-400">Uses Playwright persistent browser session to upload scheduled videos to YouTube Studio.</p>
            <div class="grid grid-cols-2 gap-2">
              <div>
                <label class="block text-[11px] text-slate-400 mb-0.5">Upload Limit</label>
                <select id="cmd-upload-limit" class="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-red-500">
                  <option value="1" selected>1 Video</option>
                  <option value="2">2 Videos</option>
                  <option value="5">5 Videos</option>
                </select>
              </div>
              <div class="flex flex-col justify-end">
                <label class="flex items-center gap-1.5 text-xs text-slate-300 pb-2 cursor-pointer">
                  <input type="checkbox" id="cmd-upload-headless" class="rounded bg-slate-900 border-slate-700 text-red-500 focus:ring-0">
                  <span>Headless</span>
                </label>
              </div>
            </div>
          </div>
          <button onclick="runCommandAction('upload')" class="w-full mt-2 py-2 px-3 bg-red-600 hover:bg-red-500 text-white rounded-lg text-xs font-semibold shadow-md shadow-red-600/20 transition flex items-center justify-center gap-1.5">
            <i class="fa-solid fa-cloud-arrow-up text-[10px]"></i> Launch Uploader (<span class="font-mono text-[11px]">upload</span>)
          </button>
        </div>

        <!-- Command 6: YouTube Login -->
        <div class="card p-4 space-y-3 border-slate-700/80 bg-slate-900/60 flex flex-col justify-between">
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2 text-white font-semibold text-sm">
                <div class="w-7 h-7 rounded-lg bg-emerald-600/20 text-emerald-400 flex items-center justify-center text-xs">
                  <i class="fa-solid fa-key"></i>
                </div>
                <span>6. YouTube Studio Login</span>
              </div>
              <span class="text-[10px] font-mono text-emerald-400">login</span>
            </div>
            <p class="text-xs text-slate-400">Launches Chromium session. Log into YouTube Studio once and your session is saved locally for $0.</p>
            <div class="p-2 rounded bg-slate-950 border border-slate-800 text-[11px] text-slate-400">
              <span>Profile Dir: </span> <span class="text-emerald-400 font-mono text-[10px]">chrome_profile/</span>
            </div>
          </div>
          <button onclick="runCommandAction('login')" class="w-full mt-2 py-2 px-3 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-semibold shadow-md shadow-emerald-600/20 transition flex items-center justify-center gap-1.5">
            <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i> Open Login Browser (<span class="font-mono text-[11px]">login</span>)
          </button>
        </div>

      </div>

      <!-- Quick Action Badges Bar -->
      <div class="card p-3 flex items-center justify-between gap-3 flex-wrap bg-slate-900/90 border-slate-800">
        <div class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
          <i class="fa-solid fa-bolt-lightning text-amber-400"></i>
          <span>Instant Diagnostics:</span>
        </div>
        <div class="flex items-center gap-2 flex-wrap">
          <button onclick="runRawCommand('python main.py status')" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-mono transition">
            <i class="fa-solid fa-chart-pie mr-1 text-blue-400"></i> status
          </button>
          <button onclick="runRawCommand('python main.py list')" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-mono transition">
            <i class="fa-solid fa-list mr-1 text-slate-400"></i> list (all)
          </button>
          <button onclick="runRawCommand('python main.py list --filter SCANNED')" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-mono transition">
            <i class="fa-solid fa-filter mr-1 text-amber-400"></i> list --filter SCANNED
          </button>
          <button onclick="runRawCommand('python main.py list --filter METADATA_READY')" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-mono transition">
            <i class="fa-solid fa-filter mr-1 text-purple-400"></i> list --filter READY
          </button>
          <button onclick="runRawCommand('python main.py logs --limit 15')" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-mono transition">
            <i class="fa-solid fa-clock-rotate-left mr-1 text-emerald-400"></i> logs --limit 15
          </button>
        </div>
      </div>

      <!-- Bottom Section: Interactive Live Terminal Window -->
      <div class="card overflow-hidden border-slate-700 shadow-2xl bg-[#070b14]">
        
        <!-- Terminal Header -->
        <div class="p-3 bg-slate-900/90 border-b border-slate-800 flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
          <div class="flex items-center gap-3">
            <div class="flex items-center gap-1.5">
              <span class="w-3 h-3 rounded-full bg-rose-500 inline-block"></span>
              <span class="w-3 h-3 rounded-full bg-amber-500 inline-block"></span>
              <span class="w-3 h-3 rounded-full bg-emerald-500 inline-block"></span>
            </div>
            <div class="text-xs font-mono font-medium text-slate-300 flex items-center gap-2">
              <i class="fa-solid fa-terminal text-emerald-400"></i>
              <span>Terminal Console</span>
              <span id="term-status-badge" class="px-2 py-0.5 rounded-full text-[10px] bg-slate-800 text-slate-400 font-bold">IDLE</span>
            </div>
          </div>

          <div class="flex items-center gap-2 flex-wrap">
            <label class="text-[11px] text-slate-400 flex items-center gap-1 mr-2 cursor-pointer">
              <input type="checkbox" id="term-autoscroll" checked class="rounded bg-slate-950 border-slate-700 text-emerald-500 focus:ring-0">
              <span>Auto-scroll</span>
            </label>
            <button onclick="copyTerminalOutput()" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs transition" title="Copy output">
              <i class="fa-regular fa-copy mr-1"></i> Copy
            </button>
            <button onclick="clearTerminalOutput()" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs transition" title="Clear console">
              <i class="fa-solid fa-trash-can mr-1"></i> Clear
            </button>
            <button id="btn-term-kill" onclick="killRunningProcess()" class="hidden px-2.5 py-1 bg-rose-900/80 hover:bg-rose-800 text-rose-200 border border-rose-700 rounded text-xs transition font-semibold">
              <i class="fa-solid fa-stop mr-1"></i> Terminate
            </button>
          </div>
        </div>

        <!-- Custom Command Prompt Input Bar -->
        <div class="p-2.5 bg-slate-950/70 border-b border-slate-800/80 flex items-center gap-2">
          <span class="font-mono text-emerald-400 text-xs font-bold pl-2">$</span>
          <input type="text" id="term-cmd-input" placeholder="Type command... e.g. python main.py status" 
                 onkeydown="if(event.key === 'Enter') submitCustomCommand()"
                 class="w-full bg-transparent border-0 text-xs font-mono text-emerald-300 placeholder-slate-600 focus:outline-none">
          <button onclick="submitCustomCommand()" class="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-xs font-semibold transition whitespace-nowrap">
            <i class="fa-solid fa-play text-[9px] mr-1"></i> Run
          </button>
        </div>

        <!-- Terminal Output Screen -->
        <div class="relative">
          <pre id="terminal-screen" class="p-4 font-mono text-xs text-emerald-400/90 overflow-y-auto max-h-[480px] min-h-[280px] leading-relaxed whitespace-pre-wrap select-text selection:bg-emerald-900 selection:text-white bg-[#070b14]">Ready. Click any action card above or run a custom command.</pre>
        </div>

      </div>

    </div>

    <!-- Tab 5: Channel & Branding Customization -->
    <div id="tab-branding" class="space-y-6 hidden">
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
        
        <!-- Left Column: Channel Settings Form (8 cols) -->
        <div class="lg:col-span-8 space-y-6">
          
          <!-- Card 1: Channel Identity & Goodbye Text -->
          <div class="card p-5 space-y-5 border-slate-700/80 bg-slate-900/70">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3">
              <div class="flex items-center gap-2.5">
                <div class="w-8 h-8 rounded-lg bg-pink-600/20 text-pink-400 flex items-center justify-center">
                  <i class="fa-solid fa-signature"></i>
                </div>
                <div>
                  <h3 class="text-sm font-bold text-white">Channel Identity & Outro Call-to-Action</h3>
                  <p class="text-xs text-slate-400">Configure channel name and the goodbye message displayed at the end of Shorts</p>
                </div>
              </div>
              <span class="text-xs px-2.5 py-1 rounded-full bg-pink-950/60 text-pink-300 border border-pink-800/80 font-medium">Viral CTR Hook</span>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <!-- Channel Name -->
              <div class="space-y-1.5 md:col-span-2">
                <label class="block text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                  <i class="fa-brands fa-youtube text-red-500"></i> YouTube Channel Name
                </label>
                <input type="text" id="brand-channel-name" placeholder="e.g. Kahani Junction" oninput="updateLiveOutroPreview()"
                       class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-pink-500 font-medium">
                <p class="text-[11px] text-slate-400">This channel name will be inserted into outro captions and video descriptions.</p>
              </div>

              <!-- Goodbye Outro Toggle -->
              <div class="p-3 rounded-lg bg-slate-950 border border-slate-800 flex items-center justify-between">
                <div>
                  <div class="text-xs font-semibold text-white flex items-center gap-1.5">
                    <i class="fa-solid fa-bullhorn text-amber-400"></i> Show Goodbye Text in Shorts
                  </div>
                  <div class="text-[11px] text-slate-400 mt-0.5">Displays outro call-to-action in the final seconds</div>
                </div>
                <label class="relative inline-flex items-center cursor-pointer">
                  <input type="checkbox" id="brand-show-outro" checked onchange="updateLiveOutroPreview()" class="sr-only peer">
                  <div class="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-pink-600"></div>
                </label>
              </div>

              <!-- Outro Duration -->
              <div class="p-3 rounded-lg bg-slate-950 border border-slate-800 space-y-1.5">
                <label class="block text-xs font-semibold text-slate-300 flex items-center justify-between">
                  <span>Outro Display Duration</span>
                  <span id="brand-outro-duration-val" class="text-pink-400 font-bold text-xs">3.5s</span>
                </label>
                <input type="range" id="brand-outro-duration" min="2.5" max="5.0" step="0.5" value="3.5" oninput="document.getElementById('brand-outro-duration-val').innerText = this.value + 's'"
                       class="w-full accent-pink-500 cursor-pointer">
                <p class="text-[10px] text-slate-500">Duration in the last seconds of the 35s Short (2.5s - 5.0s)</p>
              </div>

              <!-- Outro Text Template -->
              <div class="space-y-1.5 md:col-span-2">
                <label class="block text-xs font-semibold text-slate-300">
                  Goodbye Text Template
                </label>
                <div class="relative">
                  <input type="text" id="brand-outro-template" value="Watch next part on {channel_name}" oninput="updateLiveOutroPreview()"
                         class="w-full bg-slate-950 border border-slate-700 rounded-lg pl-3 pr-24 py-2 text-xs text-white focus:outline-none focus:border-pink-500 font-mono text-[11px]">
                  <button onclick="document.getElementById('brand-outro-template').value='Watch next part on {channel_name}'; updateLiveOutroPreview();"
                          class="absolute right-2 top-1.5 px-2 py-0.5 rounded text-[10px] bg-slate-800 hover:bg-slate-700 text-slate-300">
                    ↺ Default
                  </button>
                </div>
                <p class="text-[11px] text-slate-400">Use <code class="text-pink-300 font-mono">{channel_name}</code> as placeholder. Generates 2-line high-CTR viral neon subtitle.</p>
              </div>
            </div>
          </div>

          <!-- Card 2: Channel Logo & Watermark Settings -->
          <div class="card p-5 space-y-5 border-slate-700/80 bg-slate-900/70">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3">
              <div class="flex items-center gap-2.5">
                <div class="w-8 h-8 rounded-lg bg-emerald-600/20 text-emerald-400 flex items-center justify-center">
                  <i class="fa-solid fa-stamp"></i>
                </div>
                <div>
                  <h3 class="text-sm font-bold text-white">Channel Logo / Watermark Overlay</h3>
                  <p class="text-xs text-slate-400">Burn a professional logo watermark into the top corner of every vertical Short</p>
                </div>
              </div>
              <span class="text-xs px-2.5 py-1 rounded-full bg-emerald-950/60 text-emerald-300 border border-emerald-800/80 font-medium">Brand Recognition</span>
            </div>

            <!-- Upload Area & Current Logo -->
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4 items-center">
              <!-- Current Logo Badge -->
              <div class="p-4 rounded-xl bg-slate-950 border border-slate-800 flex flex-col items-center justify-center text-center space-y-2">
                <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Active Logo</div>
                <div class="w-20 h-20 rounded-full border-2 border-slate-700 bg-slate-900 overflow-hidden flex items-center justify-center p-1 shadow-lg relative group">
                  <img id="brand-logo-preview-img" src="/api/logo-image" alt="Channel Logo" 
                       onerror="this.src='https://ui-avatars.com/api/?name=KJ&background=e11d48&color=fff&size=128'" 
                       class="w-full h-full object-contain rounded-full">
                </div>
                <span id="brand-logo-status" class="text-[10px] text-emerald-400 font-medium">Ready</span>
              </div>

              <!-- Upload Drag & Drop / File Input -->
              <div class="md:col-span-2 p-5 rounded-xl border-2 border-dashed border-slate-700 hover:border-pink-500/80 bg-slate-950/60 flex flex-col items-center justify-center text-center space-y-2 cursor-pointer transition"
                   onclick="document.getElementById('brand-logo-file-input').click()">
                <input type="file" id="brand-logo-file-input" accept="image/png,image/jpeg,image/webp" onchange="handleLogoUpload(this.files[0])" class="hidden">
                <div class="w-10 h-10 rounded-full bg-pink-500/10 text-pink-400 flex items-center justify-center text-lg">
                  <i class="fa-solid fa-cloud-arrow-up"></i>
                </div>
                <div>
                  <span class="text-xs font-semibold text-white hover:text-pink-400">Click to upload channel logo</span>
                  <span class="text-xs text-slate-400"> or drag and drop</span>
                </div>
                <p class="text-[10px] text-slate-500">Supports PNG (transparent recommended), JPG, WEBP • Max 5MB</p>
              </div>
            </div>

            <!-- Logo Placement Controls -->
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2">
              
              <!-- Show Logo Toggle -->
              <div class="p-3 rounded-lg bg-slate-950 border border-slate-800 flex items-center justify-between">
                <div>
                  <div class="text-xs font-semibold text-white">Show Logo Watermark</div>
                  <div class="text-[11px] text-slate-400">Embed in Shorts</div>
                </div>
                <label class="relative inline-flex items-center cursor-pointer">
                  <input type="checkbox" id="brand-show-logo" checked onchange="updateLiveOutroPreview()" class="sr-only peer">
                  <div class="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-emerald-600"></div>
                </label>
              </div>

              <!-- Position Selector -->
              <div class="p-3 rounded-lg bg-slate-950 border border-slate-800 space-y-1">
                <label class="block text-xs font-semibold text-slate-300">Logo Position</label>
                <select id="brand-logo-position" onchange="updateLiveOutroPreview()" class="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1 text-xs text-white focus:outline-none focus:border-pink-500">
                  <option value="top_right" selected>Top-Right Corner (Recommended)</option>
                  <option value="top_left">Top-Left Corner</option>
                  <option value="bottom_right">Bottom-Right Corner</option>
                </select>
              </div>

              <!-- Logo Size Slider -->
              <div class="p-3 rounded-lg bg-slate-950 border border-slate-800 space-y-1">
                <div class="flex items-center justify-between text-xs font-semibold text-slate-300">
                  <span>Logo Width</span>
                  <span id="brand-logo-size-val" class="text-emerald-400 font-bold">110px</span>
                </div>
                <input type="range" id="brand-logo-size" min="80" max="160" step="5" value="110" oninput="document.getElementById('brand-logo-size-val').innerText = this.value + 'px'; updateLiveOutroPreview();"
                       class="w-full accent-emerald-500 cursor-pointer">
              </div>

            </div>

            <!-- Save Action Button -->
            <div class="pt-3 border-t border-slate-800 flex items-center justify-between">
              <span id="brand-save-status" class="text-xs text-slate-400">All modifications auto-apply to future Shorts renders</span>
              <button onclick="saveChannelSettingsForm()" class="px-5 py-2.5 bg-gradient-to-r from-pink-600 to-purple-600 hover:from-pink-500 hover:to-purple-500 text-white rounded-lg text-xs font-bold shadow-lg shadow-pink-600/25 transition flex items-center gap-2">
                <i class="fa-solid fa-floppy-disk"></i> Save Channel Branding Settings
              </button>
            </div>
          </div>

          <!-- Card 3: AI Thumbnail Prompt & Master Art Direction -->
          <div class="card p-5 space-y-5 border-slate-700/80 bg-slate-900/70">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3">
              <div class="flex items-center gap-2.5">
                <div class="w-8 h-8 rounded-lg bg-purple-600/20 text-purple-400 flex items-center justify-center">
                  <i class="fa-solid fa-palette"></i>
                </div>
                <div>
                  <h3 class="text-sm font-bold text-white">AI Thumbnail Prompt & Master Art Direction</h3>
                  <p class="text-xs text-slate-400">Customize the 3D aesthetic, character expressions, and story prompt template</p>
                </div>
              </div>
              <span class="text-xs px-2.5 py-1 rounded-full bg-purple-950/60 text-purple-300 border border-purple-800/80 font-medium">Pixar/Disney 3D</span>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <!-- Aesthetic Style Selector -->
              <div class="space-y-1.5">
                <label class="block text-xs font-semibold text-slate-300">Visual Art Style</label>
                <select id="brand-thumb-style" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-purple-500">
                  <option value="3D Pixar Animation" selected>✨ 3D Pixar / Disney Animation (High CTR)</option>
                  <option value="3D Fairytale Storybook">📖 3D Fairytale Storybook & Magical Particles</option>
                  <option value="3D Dreamy Claymation">🧸 3D Stylized Cute Character Art</option>
                  <option value="Vibrant 3D Cartoon">🎨 Vibrant Expressive Cartoon 3D</option>
                </select>
              </div>

              <!-- Target Audience & Mood -->
              <div class="space-y-1.5">
                <label class="block text-xs font-semibold text-slate-300">Audience & Mood</label>
                <input type="text" id="brand-thumb-mood" value="Joyful, safe, playful, magical, full of adventure"
                       class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-purple-500">
              </div>

              <!-- Master Prompt Blueprint Template -->
              <div class="space-y-1.5 md:col-span-2">
                <div class="flex items-center justify-between">
                  <label class="block text-xs font-semibold text-slate-300">
                    Master Art Direction Prompt Blueprint
                  </label>
                  <button type="button" onclick="resetDefaultPromptTemplate()" class="text-[10px] text-purple-400 hover:text-purple-300 underline">
                    ↺ Reset to Recommended Blueprint
                  </button>
                </div>
                <textarea id="brand-thumb-template" rows="5"
                          class="w-full bg-slate-950 border border-slate-700 rounded-lg p-3 text-xs text-slate-200 focus:outline-none focus:border-purple-500 font-mono text-[11px] leading-relaxed"></textarea>
                <p class="text-[11px] text-slate-400">
                  Gemini analyzes each video's title, captions, and Part number, then injects specific characters and drama into this blueprint for maximum CTR.
                </p>
              </div>
            </div>

            <!-- Save Action Button -->
            <div class="pt-3 border-t border-slate-800 flex items-center justify-between">
              <span id="brand-prompt-save-status" class="text-xs text-slate-400">Applies to all new AI thumbnail generations</span>
              <button onclick="saveChannelSettingsForm()" class="px-5 py-2.5 bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white rounded-lg text-xs font-bold shadow-lg shadow-purple-600/25 transition flex items-center gap-2">
                <i class="fa-solid fa-floppy-disk"></i> Save All Branding & Prompt Settings
              </button>
            </div>
          </div>

        </div>

        <!-- Right Column: Live Mobile Preview Device (4 cols) -->
        <div class="lg:col-span-4 space-y-4">
          <div class="card p-4 space-y-3 border-slate-700/80 bg-slate-900/90 sticky top-6">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2 text-white font-bold text-xs">
                <i class="fa-solid fa-mobile-screen-button text-pink-400"></i>
                <span>Live Shorts 9:16 Preview</span>
              </div>
              <span class="text-[10px] text-slate-400 uppercase tracking-widest font-mono">1080x1920</span>
            </div>

            <!-- Simulated Phone Screen (aspect 9/16) -->
            <div class="relative w-full aspect-[9/16] bg-black rounded-2xl overflow-hidden border-2 border-slate-700 shadow-2xl flex flex-col justify-between p-3 select-none">
              
              <!-- Top Area -->
              <div class="relative z-10 space-y-2">
                <!-- Part Badge & Watermark Row -->
                <div class="flex items-start justify-between">
                  <div class="w-6"></div> <!-- spacer -->
                  <div class="px-2.5 py-0.5 rounded bg-red-600 text-white font-black text-[11px] shadow tracking-wider uppercase">
                    PART: 1
                  </div>
                  <!-- Mini Logo in corner -->
                  <div id="sim-logo-container" class="w-7 h-7 rounded-full overflow-hidden border border-white/20 bg-slate-900 shadow">
                    <img id="sim-logo-img" src="/api/logo-image" alt="logo" class="w-full h-full object-contain">
                  </div>
                </div>

                <!-- Top Hook -->
                <div class="text-center">
                  <span class="inline-block px-2 py-0.5 rounded bg-black/60 text-white font-semibold text-[10px] border border-white/10">
                    "Wait For The Twist!"
                  </span>
                </div>
              </div>

              <!-- Center Video Artwork Area (Simulated 16:9 box) -->
              <div class="w-full aspect-video bg-slate-800/80 rounded-lg border border-slate-700/60 flex items-center justify-center relative overflow-hidden">
                <i class="fa-solid fa-film text-slate-600 text-2xl"></i>
                <span class="absolute bottom-1 right-1 text-[8px] font-mono text-slate-500">16:9 Video Canvas</span>
              </div>

              <!-- Bottom Area: Live Subtitles / Outro Preview -->
              <div class="relative z-10 pb-4 text-center space-y-1">
                <div class="text-[9px] uppercase tracking-wider text-slate-500 font-mono">Simulated Goodbye Outro (Last 3.5s)</div>
                <div id="sim-outro-line1" class="font-black text-xs text-[#FFF505] drop-shadow-[0_2px_4px_rgba(0,0,0,0.9)] tracking-wide">
                  WATCH NEXT PART ON
                </div>
                <div id="sim-outro-line2" class="font-black text-sm text-[#00FFFF] drop-shadow-[0_2px_4px_rgba(0,0,0,0.9)] tracking-wider">
                  👉 KAHANI JUNCTION 👈
                </div>
              </div>

            </div>

            <p class="text-[11px] text-slate-400 text-center">
              <i class="fa-solid fa-sparkles text-amber-400 mr-1"></i> Subtitles rotate vibrant viral colors during dialogue, and switch to this Goodbye Call-to-Action in the final 3.5 seconds!
            </p>
          </div>
        </div>

      </div>
    </div>

    <!-- Tab 6: Multi-Channel & Multi-Account Management Panel -->
    <div id="tab-channels" class="card p-6 space-y-6 hidden">
      <!-- Section Header -->
      <div class="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <div class="flex items-center gap-2.5">
            <div class="w-9 h-9 rounded-xl bg-indigo-600/20 text-indigo-400 flex items-center justify-center text-lg shadow">
              <i class="fa-solid fa-users-gear"></i>
            </div>
            <div>
              <h2 class="text-lg font-bold text-white tracking-tight">Connected YouTube Accounts & Channels</h2>
              <p class="text-xs text-slate-400">Manage multiple channels side-by-side with 100% isolated Chrome browser profiles, distinct cookies, and independent schedules.</p>
            </div>
          </div>
        </div>

        <div class="flex items-center gap-2.5">
          <button onclick="verifyAllChannelSessions()" class="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium transition flex items-center gap-1.5 shadow">
            <i class="fa-solid fa-arrows-rotate text-[11px]"></i> Verify All Sessions
          </button>
          <button onclick="openCreateChannelModal()" class="px-4 py-2 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white rounded-lg text-xs font-bold shadow-lg shadow-indigo-600/25 transition flex items-center gap-1.5">
            <i class="fa-solid fa-plus text-[11px]"></i> Connect New YouTube Account
          </button>
        </div>
      </div>

      <!-- Channel Cards Grid -->
      <div id="channels-cards-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        <!-- Rendered dynamically via loadChannels() -->
        <div class="card p-5 text-center text-slate-500 col-span-3">Loading connected channels...</div>
      </div>

      <!-- Multi-Channel Isolation & Safe Growth Guide -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2">
        <div class="p-4 rounded-xl bg-slate-950/70 border border-slate-800 space-y-2">
          <div class="flex items-center gap-2 text-indigo-400 font-bold text-xs">
            <i class="fa-solid fa-shield-halved"></i>
            <span>100% Isolated Profiles</span>
          </div>
          <p class="text-[11px] text-slate-400 leading-relaxed">
            Each channel gets a dedicated Chrome profile directory (<code class="text-slate-300 font-mono">chrome_profiles/...</code>). You can log in to 3–4 accounts simultaneously without session conflicts, cookie overwrites, or Google sign-out issues.
          </p>
        </div>

        <div class="p-4 rounded-xl bg-slate-950/70 border border-slate-800 space-y-2">
          <div class="flex items-center gap-2 text-amber-400 font-bold text-xs">
            <i class="fa-solid fa-calendar-check"></i>
            <span>Independent Schedules</span>
          </div>
          <p class="text-[11px] text-slate-400 leading-relaxed">
            Assign custom daily posting frequencies and times per channel (e.g. Channel 1 @ 11:00 & 17:00, Channel 2 @ 12:00 & 18:00). Uploads use the assigned channel's authenticated profile.
          </p>
        </div>

        <div class="p-4 rounded-xl bg-slate-950/70 border border-slate-800 space-y-2">
          <div class="flex items-center gap-2 text-pink-400 font-bold text-xs">
            <i class="fa-solid fa-palette"></i>
            <span>Distinct Visual Branding</span>
          </div>
          <p class="text-[11px] text-slate-400 leading-relaxed">
            Tailor AI thumbnails, outro watermark text, and prompt templates to each channel's niche (e.g. 3D Pixar for kids, cinematic anime for stories, neon realistic for tech).
          </p>
        </div>
      </div>
    </div>

  <!-- Modal: Connect New YouTube Channel -->
  <div id="modal-create-channel" class="fixed inset-0 bg-black/80 backdrop-blur-md hidden items-center justify-center p-4 z-50">
    <div class="card max-w-lg w-full p-6 space-y-5 border-slate-700 shadow-2xl bg-slate-900">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-lg bg-indigo-600/20 text-indigo-400 flex items-center justify-center text-sm">
            <i class="fa-solid fa-plus"></i>
          </div>
          <div>
            <h3 class="text-sm font-bold text-white">Connect New YouTube Account</h3>
            <p class="text-xs text-slate-400">Creates an isolated profile to run another channel side-by-side</p>
          </div>
        </div>
        <button onclick="closeCreateChannelModal()" class="text-slate-400 hover:text-white text-lg">&times;</button>
      </div>

      <div class="space-y-4 text-xs">
        <div>
          <label class="block font-semibold text-slate-300 mb-1">Channel Name *</label>
          <input type="text" id="create-ch-name" placeholder="e.g. Bedtime Tales 4 Kids" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500">
        </div>

        <div>
          <label class="block font-semibold text-slate-300 mb-1">YouTube Handle (Optional)</label>
          <input type="text" id="create-ch-handle" placeholder="@BedtimeTales4Kids" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500">
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block font-semibold text-slate-300 mb-1">AI Thumbnail Aesthetic</label>
            <select id="create-ch-thumb-style" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-2.5 py-2 text-white focus:outline-none focus:border-indigo-500">
              <option value="3D Pixar Animation" selected>3D Pixar Animation</option>
              <option value="Anime / Manga 2D">Anime / Manga 2D</option>
              <option value="Cinematic Photorealism">Cinematic Photorealism</option>
              <option value="Watercolor Storybook">Watercolor Storybook</option>
              <option value="Cyberpunk Neon 3D">Cyberpunk Neon 3D</option>
            </select>
          </div>
          <div>
            <label class="block font-semibold text-slate-300 mb-1">Daily Shorts Slots</label>
            <input type="text" id="create-ch-times" value="11:00, 17:00" placeholder="11:00, 17:00" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500">
          </div>
        </div>

        <div>
          <label class="block font-semibold text-slate-300 mb-1">Thumbnail Mood / Art Direction</label>
          <input type="text" id="create-ch-mood" value="Joyful, safe, playful, magical, full of adventure" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500">
        </div>

        <div class="p-3 rounded-lg bg-indigo-950/30 border border-indigo-900/50 flex items-center justify-between">
          <div>
            <div class="font-semibold text-indigo-300">Open Login Browser Immediately</div>
            <div class="text-[11px] text-slate-400">Launch Chrome with this isolated profile right after creating</div>
          </div>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" id="create-ch-open-browser" checked class="sr-only peer">
            <div class="w-8 h-4 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-indigo-600"></div>
          </label>
        </div>
      </div>

      <div class="flex items-center justify-end gap-2 pt-3 border-t border-slate-800">
        <button type="button" onclick="closeCreateChannelModal()" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium">Cancel</button>
        <button type="button" onclick="submitCreateChannel()" class="px-5 py-2 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white rounded-lg text-xs font-bold shadow-lg shadow-indigo-600/30">Create & Launch</button>
      </div>
    </div>
  </div>

  <!-- Modal: Edit Channel Settings -->
  <div id="modal-edit-channel" class="fixed inset-0 bg-black/80 backdrop-blur-md hidden items-center justify-center p-4 z-50">
    <div class="card max-w-lg w-full p-6 space-y-4 border-slate-700 shadow-2xl bg-slate-900">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 rounded-lg bg-indigo-600/20 text-indigo-400 flex items-center justify-center">
            <i class="fa-solid fa-gear"></i>
          </div>
          <div>
            <h3 class="text-sm font-bold text-white">Channel Settings & Branding</h3>
            <p id="edit-ch-subtitle" class="text-xs text-slate-400">Configure channel details</p>
          </div>
        </div>
        <button onclick="closeEditChannelModal()" class="text-slate-400 hover:text-white text-lg">&times;</button>
      </div>

      <input type="hidden" id="edit-ch-id">
      <div class="space-y-3 text-xs">
        <div>
          <label class="block font-semibold text-slate-300 mb-1">Channel Name</label>
          <input type="text" id="edit-ch-name" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white">
        </div>
        <div>
          <label class="block font-semibold text-slate-300 mb-1">YouTube Handle</label>
          <input type="text" id="edit-ch-handle" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white">
        </div>
        <div>
          <label class="block font-semibold text-slate-300 mb-1">Outro Call-To-Action Template</label>
          <input type="text" id="edit-ch-watermark" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white">
        </div>
        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="block font-semibold text-slate-300 mb-1">Thumbnail Style</label>
            <input type="text" id="edit-ch-thumb-style" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white">
          </div>
          <div>
            <label class="block font-semibold text-slate-300 mb-1">Shorts Schedule Times</label>
            <input type="text" id="edit-ch-times" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white">
          </div>
        </div>
        <div>
          <label class="block font-semibold text-slate-300 mb-1">Thumbnail Mood</label>
          <input type="text" id="edit-ch-thumb-mood" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white">
        </div>
      </div>

      <div class="flex items-center justify-end gap-2 pt-3 border-t border-slate-800">
        <button type="button" onclick="closeEditChannelModal()" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium">Cancel</button>
        <button type="button" onclick="submitEditChannel()" class="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-bold shadow transition">Save Settings</button>
      </div>
    </div>
  </div>

  <!-- Interactive Cut Shorts Modal with "Add Parts Label:" toggle -->
  <div id="modal-cut-shorts" class="fixed inset-0 bg-black/70 backdrop-blur-sm hidden items-center justify-center p-4 z-50">
    <div class="card max-w-lg w-full p-6 space-y-5 border-slate-700 shadow-2xl bg-slate-900">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 rounded-lg bg-amber-500/20 text-amber-400 flex items-center justify-center">
            <i class="fa-solid fa-scissors"></i>
          </div>
          <div>
            <h3 class="text-base font-bold text-white">Generate Vertical Shorts</h3>
            <p class="text-xs text-slate-400">Creates 1080x1920 mobile Shorts with Hinglish dialogue</p>
          </div>
        </div>
        <button onclick="closeCutShortsModal()" class="text-slate-400 hover:text-white text-lg">&times;</button>
      </div>

      <div class="space-y-4">
        <div>
          <label class="block text-xs font-medium text-slate-400 mb-1">Source Video</label>
          <div id="modal-video-title" class="p-2.5 rounded-lg bg-slate-950 border border-slate-800 text-xs text-slate-200 font-medium truncate">Video Name</div>
          <input type="hidden" id="modal-video-id" value="">
        </div>

        <div>
          <label class="block text-xs font-medium text-slate-400 mb-1">Segments to Generate (Entire Video up to 15 mins)</label>
          <select id="modal-parts-count" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-amber-500">
            <option value="0" selected>🔥 Convert Whole Video (All Parts)</option>
          </select>
          <div id="modal-parts-hint" class="text-[11px] text-amber-400/90 mt-1.5 flex items-center gap-1.5">
            <i class="fa-solid fa-circle-info text-[10px]"></i>
            <span>Calculates full parts for video (max 15 mins = 30 parts).</span>
          </div>
        </div>

        <!-- The "Add Parts Label:" Toggle in Modal -->
        <div class="p-3.5 rounded-lg bg-slate-950 border border-slate-800 space-y-2">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <i class="fa-solid fa-tag text-amber-400 text-sm"></i>
              <span class="text-xs font-semibold text-white">Add Parts Label:</span>
            </div>
            <label class="relative inline-flex items-center cursor-pointer">
              <input type="checkbox" id="modal-parts-label-toggle" checked onchange="updateModalToggleText()" class="sr-only peer">
              <div class="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-amber-500"></div>
            </label>
          </div>
          <p class="text-[11px] text-slate-400 leading-relaxed">
            When enabled, renders a prominent <strong class="text-amber-300">PART: 1</strong> banner on top of the video and adds <strong class="text-amber-300">(Part: 1)</strong> into the YouTube title so viewers can discover subsequent parts easily.
          </p>
        </div>
      </div>

      <div class="flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
        <button onclick="closeCutShortsModal()" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium transition">
          Cancel
        </button>
        <button onclick="submitCutShorts()" class="px-4 py-2 bg-gradient-to-r from-amber-600 to-red-600 hover:from-amber-500 hover:to-red-500 text-white rounded-lg text-xs font-semibold shadow-lg shadow-amber-600/30 transition flex items-center gap-1.5">
          <i class="fa-solid fa-bolt"></i> Generate Shorts Now
        </button>
      </div>
    </div>
  </div>

  <!-- Video Player Preview Modal -->
  <div id="modal-video-preview" class="fixed inset-0 bg-black/80 backdrop-blur-md hidden items-center justify-center p-4 z-50">
    <div class="card max-w-lg w-full p-5 space-y-4 border-slate-700 shadow-2xl bg-slate-900">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 rounded-lg bg-amber-500/20 text-amber-400 flex items-center justify-center">
            <i class="fa-solid fa-play"></i>
          </div>
          <div>
            <h3 id="preview-modal-title" class="text-sm font-bold text-white truncate max-w-xs">Video Preview</h3>
            <p class="text-xs text-slate-400">Preview 9:16 vertical render with live burned-in subtitles</p>
          </div>
        </div>
        <button onclick="closeVideoPreviewModal()" class="text-slate-400 hover:text-white text-xl">&times;</button>
      </div>
      <div class="flex justify-center bg-black rounded-lg overflow-hidden border border-slate-800">
        <video id="preview-video-element" controls autoplay playsinline class="max-h-[60vh] w-auto rounded bg-black"></video>
      </div>
      <div class="flex justify-end pt-1">
        <button onclick="closeVideoPreviewModal()" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium">Close</button>
      </div>
    </div>
  <!-- Thumbnail Preview & Prompt Studio Modal -->
  <div id="modal-thumbnail-preview" class="fixed inset-0 bg-black/80 backdrop-blur-md hidden items-center justify-center p-4 z-50 overflow-y-auto">
    <div class="card max-w-2xl w-full p-6 space-y-4 border-slate-700 shadow-2xl bg-slate-900 my-6">
      
      <!-- Modal Header -->
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-lg bg-purple-600/20 text-purple-400 flex items-center justify-center">
            <i class="fa-solid fa-palette text-sm"></i>
          </div>
          <div>
            <h3 id="thumb-modal-title" class="text-sm font-bold text-white truncate max-w-md">Thumbnail Studio</h3>
            <p class="text-xs text-slate-400">Preview custom thumbnail and customize AI visual prompt</p>
          </div>
        </div>
        <button onclick="closeThumbnailModal()" class="text-slate-400 hover:text-white text-xl p-1">&times;</button>
      </div>

      <!-- Main Content Grid -->
      <div class="grid grid-cols-1 md:grid-cols-12 gap-5 items-start">
        
        <!-- Left: Image Preview (5 cols) -->
        <div class="md:col-span-5 flex flex-col items-center">
          <div class="relative w-full max-w-[240px] aspect-[9/16] bg-black/80 rounded-xl overflow-hidden border-2 border-slate-700 shadow-xl flex items-center justify-center">
            <img id="thumb-modal-img" src="" class="w-full h-full object-cover" />
            <div id="thumb-modal-loader" class="hidden absolute inset-0 bg-black/75 backdrop-blur-sm flex flex-col items-center justify-center gap-2 p-3 text-center">
              <i class="fa-solid fa-circle-notch fa-spin text-purple-400 text-2xl"></i>
              <span id="thumb-modal-loader-text" class="text-xs text-purple-200 font-medium">Generating AI artwork...</span>
            </div>
          </div>
          <div class="mt-2 text-center text-[10px] text-slate-500 font-mono">
            <span>Auto-overlay: PART Badge + Logo</span>
          </div>
        </div>

        <!-- Right: Prompt Studio & Generation Actions (7 cols) -->
        <div class="md:col-span-7 space-y-3.5">
          
          <div class="space-y-1.5">
            <div class="flex items-center justify-between">
              <label class="block text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                <i class="fa-solid fa-wand-magic-sparkles text-purple-400"></i>
                <span>Story AI Visual Prompt</span>
              </label>
              <button onclick="fetchFreshPromptForCurrentVideo()" class="text-[10px] text-purple-400 hover:text-purple-300 underline flex items-center gap-1">
                <i class="fa-solid fa-arrows-rotate text-[9px]"></i> Re-synthesize
              </button>
            </div>
            <textarea id="thumb-modal-prompt-input" rows="6" placeholder="Loading prompt..."
                      class="w-full bg-slate-950 border border-slate-700 rounded-lg p-2.5 text-xs text-slate-200 focus:outline-none focus:border-purple-500 font-mono text-[11px] leading-relaxed"></textarea>
            <p class="text-[10px] text-slate-400">
              Detailed 3D Pixar aesthetic prompt synthesized by Gemini from video dialogue and title. You can edit this prompt directly before generating!
            </p>
          </div>

          <!-- Status Message -->
          <div id="thumb-modal-status-box" class="p-2 rounded bg-slate-950 border border-slate-800 text-[11px] text-slate-400 flex items-center justify-between">
            <span id="thumb-modal-status" class="truncate">Select generation method:</span>
            <span id="thumb-modal-badge" class="text-[10px] px-2 py-0.5 rounded bg-purple-950 text-purple-300 border border-purple-800/80 font-mono">Ready</span>
          </div>

          <!-- Action Buttons Grid -->
          <div class="space-y-2 pt-1">
            <button id="thumb-modal-puter-btn" onclick="generateWithPuterAI()" 
                    class="w-full py-2 px-3 bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white rounded-lg text-xs font-semibold shadow-lg shadow-purple-600/25 transition flex items-center justify-center gap-2">
              <i class="fa-solid fa-bolt text-yellow-300"></i>
              <span>Generate with Browser AI (Flux / Puter)</span>
            </button>

            <div class="grid grid-cols-2 gap-2">
              <button id="thumb-modal-backend-btn" onclick="generateFromModalPrompt(true)" 
                      class="py-2 px-2.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium border border-slate-700 transition flex items-center justify-center gap-1.5 truncate">
                <i class="fa-solid fa-robot text-purple-400"></i>
                <span>Backend Generator</span>
              </button>
              
              <button id="thumb-modal-frame-btn" onclick="generateFromModalPrompt(false)" 
                      class="py-2 px-2.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium border border-slate-700 transition flex items-center justify-center gap-1.5 truncate">
                <i class="fa-solid fa-film text-blue-400"></i>
                <span>Best Video Frame</span>
              </button>
            </div>
          </div>

        </div>

      </div>

      <!-- Modal Footer -->
      <div class="flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
        <button onclick="closeThumbnailModal()" class="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs font-medium">Close</button>
      </div>

    </div>
  </div>

  <!-- Smart Scheduling & Frequency Planner Modal -->
  <div id="modal-smart-schedule" class="fixed inset-0 bg-black/80 backdrop-blur-md hidden items-center justify-center p-4 z-50 overflow-y-auto">
    <div class="card max-w-2xl w-full p-6 space-y-5 border-slate-700 shadow-2xl bg-slate-900 my-8">
      
      <!-- Modal Header -->
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div class="flex items-center gap-2.5">
          <div class="w-9 h-9 rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 text-white flex items-center justify-center shadow-md">
            <i class="fa-solid fa-calendar-days text-sm"></i>
          </div>
          <div>
            <h3 class="text-base font-bold text-white flex items-center gap-2">
              <span>Smart Scheduling & Frequency Planner</span>
              <span class="text-[10px] px-2 py-0.5 rounded bg-blue-950 text-blue-300 border border-blue-800/80 uppercase font-mono">Autopilot</span>
            </h3>
            <p class="text-xs text-slate-400">Set publishing cadence, daily time slots, and preview release calendar</p>
          </div>
        </div>
        <button onclick="closeScheduleModal()" class="text-slate-400 hover:text-white text-xl p-1">&times;</button>
      </div>

      <!-- Settings Grid -->
      <div class="space-y-4">
        
        <!-- Target Videos Selection -->
        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-slate-300">Target Videos to Schedule</label>
          <div class="grid grid-cols-3 gap-2">
            <label class="flex items-center gap-2 p-2.5 rounded-lg border border-slate-800 bg-slate-950/60 cursor-pointer hover:border-slate-700 transition">
              <input type="radio" name="sched-target" value="SHORTS" checked onchange="fetchSchedulePreview()" class="text-blue-600 focus:ring-0">
              <span class="text-xs text-slate-200 font-medium">⚡ 19 Multi-Part Shorts</span>
            </label>
            <label class="flex items-center gap-2 p-2.5 rounded-lg border border-slate-800 bg-slate-950/60 cursor-pointer hover:border-slate-700 transition">
              <input type="radio" name="sched-target" value="ALL" onchange="fetchSchedulePreview()" class="text-blue-600 focus:ring-0">
              <span class="text-xs text-slate-200 font-medium">🌐 All Videos</span>
            </label>
            <label class="flex items-center gap-2 p-2.5 rounded-lg border border-slate-800 bg-slate-950/60 cursor-pointer hover:border-slate-700 transition">
              <input type="radio" name="sched-target" value="LONG" onchange="fetchSchedulePreview()" class="text-blue-600 focus:ring-0">
              <span class="text-xs text-slate-200 font-medium">🎬 Long-Form Only</span>
            </label>
          </div>
        </div>

        <!-- Publishing Frequency Selection (Cards) -->
        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-slate-300">Publishing Frequency Cadence</label>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
            
            <button type="button" onclick="setScheduleFrequency('daily')" id="freq-card-daily" 
                    class="p-2.5 rounded-lg border border-blue-500 bg-blue-950/40 text-left transition flex flex-col justify-between">
              <div>
                <div class="text-xs font-bold text-white flex items-center gap-1.5">
                  <i class="fa-solid fa-sun text-amber-400 text-[11px]"></i>
                  <span>Daily</span>
                </div>
                <div class="text-[10px] text-slate-400 mt-0.5">Every day</div>
              </div>
              <span class="text-[9px] font-mono text-blue-400 mt-2 font-semibold">+1 Day step</span>
            </button>

            <button type="button" onclick="setScheduleFrequency('alternate')" id="freq-card-alternate" 
                    class="p-2.5 rounded-lg border border-slate-800 bg-slate-950/60 hover:border-slate-700 text-left transition flex flex-col justify-between">
              <div>
                <div class="text-xs font-bold text-slate-200 flex items-center gap-1.5">
                  <i class="fa-solid fa-repeat text-indigo-400 text-[11px]"></i>
                  <span>Alternate</span>
                </div>
                <div class="text-[10px] text-slate-400 mt-0.5">Every 2 days</div>
              </div>
              <span class="text-[9px] font-mono text-slate-500 mt-2 font-semibold">Skip a day</span>
            </button>

            <button type="button" onclick="setScheduleFrequency('weekly')" id="freq-card-weekly" 
                    class="p-2.5 rounded-lg border border-slate-800 bg-slate-950/60 hover:border-slate-700 text-left transition flex flex-col justify-between">
              <div>
                <div class="text-xs font-bold text-slate-200 flex items-center gap-1.5">
                  <i class="fa-solid fa-calendar-week text-purple-400 text-[11px]"></i>
                  <span>Weekly</span>
                </div>
                <div class="text-[10px] text-slate-400 mt-0.5">Once a week</div>
              </div>
              <span class="text-[9px] font-mono text-slate-500 mt-2 font-semibold">+7 Days step</span>
            </button>

            <button type="button" onclick="setScheduleFrequency('custom')" id="freq-card-custom" 
                    class="p-2.5 rounded-lg border border-slate-800 bg-slate-950/60 hover:border-slate-700 text-left transition flex flex-col justify-between">
              <div>
                <div class="text-xs font-bold text-slate-200 flex items-center gap-1.5">
                  <i class="fa-solid fa-sliders text-emerald-400 text-[11px]"></i>
                  <span>Custom</span>
                </div>
                <div class="text-[10px] text-slate-400 mt-0.5">Every N days</div>
              </div>
              <span class="text-[9px] font-mono text-slate-500 mt-2 font-semibold">User interval</span>
            </button>

          </div>

          <!-- Custom Interval Input (shown when custom is selected) -->
          <div id="sched-custom-interval-row" class="hidden items-center gap-2 p-2 rounded-lg bg-slate-950 border border-slate-800 mt-2">
            <span class="text-xs text-slate-300">Publish every:</span>
            <input type="number" id="sched-interval-days" min="1" max="30" value="3" onchange="fetchSchedulePreview()" 
                   class="w-16 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs text-white text-center font-bold">
            <span class="text-xs text-slate-400">days</span>
          </div>
        </div>

        <!-- Daily Time Slots Configuration -->
        <div class="space-y-2 p-3.5 rounded-lg bg-slate-950 border border-slate-800">
          <div class="flex items-center justify-between">
            <label class="text-xs font-semibold text-slate-200 flex items-center gap-1.5">
              <i class="fa-solid fa-clock text-amber-400"></i>
              <span>Publishing Time Slots (Daily Times)</span>
            </label>
            <span class="text-[10px] text-slate-400 font-medium">Multiple slots = multiple uploads per active day</span>
          </div>

          <!-- Active Time Slot Pills -->
          <div id="sched-time-slots-container" class="flex flex-wrap items-center gap-2 pt-1">
            <!-- Dynamically populated -->
          </div>

          <!-- Add Time Slot Input & Presets -->
          <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 pt-2 border-t border-slate-800/80">
            <div class="flex items-center gap-1.5">
              <input type="time" id="sched-new-time-input" value="20:00" 
                     class="bg-slate-900 border border-slate-700 rounded px-2.5 py-1 text-xs text-white focus:outline-none focus:border-blue-500">
              <button type="button" onclick="addScheduleTimeSlot()" 
                      class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded text-xs font-medium transition">
                <i class="fa-solid fa-plus text-[10px]"></i> Add Time
              </button>
            </div>

            <!-- Quick Presets -->
            <div class="flex items-center gap-1 text-[10px]">
              <span class="text-slate-500">Presets:</span>
              <button type="button" onclick="applyPresetTimes(1)" class="px-2 py-0.5 rounded bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800">1/day (11 AM)</button>
              <button type="button" onclick="applyPresetTimes(2)" class="px-2 py-0.5 rounded bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800">2/day (11 AM, 5 PM)</button>
              <button type="button" onclick="applyPresetTimes(3)" class="px-2 py-0.5 rounded bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800">3/day (11 AM, 4 PM, 8 PM)</button>
            </div>
          </div>
        </div>

        <!-- Start Date & Overwrite Option -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div class="space-y-1">
            <label class="block text-xs font-semibold text-slate-300">Schedule Start Date</label>
            <div class="flex items-center gap-1.5">
              <input type="date" id="sched-start-date" onchange="fetchSchedulePreview()" 
                     class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500">
              <button type="button" onclick="setStartDateQuick(1)" class="px-2 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs whitespace-nowrap">Tomorrow</button>
              <button type="button" onclick="setStartDateQuick(0)" class="px-2 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs whitespace-nowrap">Today</button>
            </div>
          </div>

          <div class="flex items-center justify-between p-3 rounded-lg bg-slate-950/60 border border-slate-800">
            <div>
              <div class="text-xs font-semibold text-slate-200">Overwrite Existing Dates</div>
              <div class="text-[10px] text-slate-500">Reassign slots for all matching videos</div>
            </div>
            <label class="relative inline-flex items-center cursor-pointer">
              <input type="checkbox" id="sched-overwrite-toggle" checked onchange="fetchSchedulePreview()" class="sr-only peer">
              <div class="w-8 h-4 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-blue-600"></div>
            </label>
          </div>
        </div>

        <!-- Live Calendar Preview Box -->
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <span class="text-xs font-bold text-slate-300 flex items-center gap-1.5">
              <i class="fa-solid fa-eye text-blue-400"></i>
              <span>Live Schedule Preview</span>
            </span>
            <span id="sched-preview-summary" class="text-[11px] font-mono text-emerald-400">Loading preview...</span>
          </div>

          <div class="p-2.5 rounded-lg bg-slate-950 border border-slate-800 max-h-44 overflow-y-auto">
            <table class="w-full text-left text-xs">
              <tbody id="sched-preview-tbody" class="divide-y divide-slate-900 text-slate-300">
                <tr><td class="p-2 text-center text-slate-500">Calculating schedule timeline...</td></tr>
              </tbody>
            </table>
          </div>
        </div>

      </div>

      <!-- Modal Footer -->
      <div class="flex items-center justify-between pt-3 border-t border-slate-800">
        <span class="text-[11px] text-slate-500">
          <i class="fa-solid fa-circle-check text-blue-400 mr-1"></i> Saves your cadence settings automatically
        </span>
        <div class="flex items-center gap-2">
          <button type="button" onclick="closeScheduleModal()" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium transition">
            Cancel
          </button>
          <button type="button" id="btn-apply-schedule" onclick="applySchedulePlan()" class="px-5 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white rounded-lg text-xs font-bold shadow-lg shadow-blue-600/30 transition flex items-center gap-1.5">
            <i class="fa-solid fa-calendar-check"></i>
            <span>Apply Schedule Now</span>
          </button>
        </div>
      </div>

    </div>
  </div>

  <!-- Batch Upload to YouTube Modal -->
  <div id="modal-upload-shorts" class="fixed inset-0 bg-black/80 backdrop-blur-md hidden items-center justify-center p-4 z-50">
    <div class="card max-w-lg w-full p-6 space-y-5 border-slate-700 shadow-2xl bg-slate-900">
      
      <!-- Modal Header -->
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div class="flex items-center gap-2.5">
          <div class="w-9 h-9 rounded-xl bg-gradient-to-br from-red-600 to-rose-600 text-white flex items-center justify-center shadow-md">
            <i class="fa-solid fa-cloud-arrow-up text-sm"></i>
          </div>
          <div>
            <h3 class="text-base font-bold text-white flex items-center gap-2">
              <span>Upload to YouTube Studio</span>
              <span class="text-[10px] px-2 py-0.5 rounded bg-red-950 text-red-300 border border-red-800/80 font-mono">Playwright</span>
            </h3>
            <p class="text-xs text-slate-400">Automates video upload, metadata, and visibility schedule</p>
          </div>
        </div>
        <button onclick="closeUploadModal()" class="text-slate-400 hover:text-white text-xl p-1">&times;</button>
      </div>

      <div class="space-y-4">
        
        <!-- Status indicator -->
        <div id="upload-modal-auth-banner" class="p-3 rounded-lg bg-emerald-950/40 border border-emerald-800/60 flex items-center gap-2 text-xs text-emerald-300">
          <i class="fa-solid fa-circle-check text-emerald-400"></i>
          <span>YouTube Studio Session Ready (Logged In)</span>
        </div>

        <!-- Quantity Selector -->
        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-slate-300">How many videos to upload?</label>
          <div class="grid grid-cols-2 gap-2">
            <label class="flex items-center gap-2 p-2.5 rounded-lg border border-slate-800 bg-slate-950/60 cursor-pointer hover:border-slate-700 transition">
              <input type="radio" name="upload-limit" value="1" checked class="text-red-600 focus:ring-0">
              <div>
                <div class="text-xs font-semibold text-white">Next 1 Video</div>
                <div class="text-[10px] text-slate-500">Quick Test Run</div>
              </div>
            </label>
            <label class="flex items-center gap-2 p-2.5 rounded-lg border border-slate-800 bg-slate-950/60 cursor-pointer hover:border-slate-700 transition">
              <input type="radio" name="upload-limit" value="3" class="text-red-600 focus:ring-0">
              <div>
                <div class="text-xs font-semibold text-white">Next 3 Videos</div>
                <div class="text-[10px] text-slate-500">Short batch</div>
              </div>
            </label>
            <label class="flex items-center gap-2 p-2.5 rounded-lg border border-slate-800 bg-slate-950/60 cursor-pointer hover:border-slate-700 transition">
              <input type="radio" name="upload-limit" value="5" class="text-red-600 focus:ring-0">
              <div>
                <div class="text-xs font-semibold text-white">Next 5 Videos</div>
                <div class="text-[10px] text-slate-500">Weekly queue</div>
              </div>
            </label>
            <label class="flex items-center gap-2 p-2.5 rounded-lg border border-slate-800 bg-slate-950/60 cursor-pointer hover:border-slate-700 transition">
              <input type="radio" name="upload-limit" value="19" class="text-red-600 focus:ring-0">
              <div>
                <div class="text-xs font-semibold text-amber-300">🔥 All 19 Shorts</div>
                <div class="text-[10px] text-slate-500">Full series upload</div>
              </div>
            </label>
          </div>
        </div>

        <!-- Browser Mode -->
        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-slate-300">Browser Visibility</label>
          <div class="grid grid-cols-2 gap-2">
            <label class="flex items-center gap-2 p-2.5 rounded-lg border border-slate-800 bg-slate-950/60 cursor-pointer hover:border-slate-700 transition">
              <input type="radio" name="upload-headless" value="false" checked class="text-red-600 focus:ring-0">
              <div>
                <div class="text-xs font-semibold text-white">Visible Browser</div>
                <div class="text-[10px] text-slate-400">See live Playwright actions (Recommended)</div>
              </div>
            </label>
            <label class="flex items-center gap-2 p-2.5 rounded-lg border border-slate-800 bg-slate-950/60 cursor-pointer hover:border-slate-700 transition">
              <input type="radio" name="upload-headless" value="true" class="text-red-600 focus:ring-0">
              <div>
                <div class="text-xs font-semibold text-white">Headless Mode</div>
                <div class="text-[10px] text-slate-400">Silent background upload</div>
              </div>
            </label>
          </div>
        </div>

        <!-- Informational box -->
        <div class="p-3 rounded-lg bg-slate-950 border border-slate-800 text-[11px] text-slate-400 space-y-1 leading-relaxed">
          <div class="font-semibold text-slate-300 flex items-center gap-1.5">
            <i class="fa-solid fa-wand-magic-sparkles text-red-400"></i>
            <span>How automated scheduling works:</span>
          </div>
          <p>
            Playwright opens YouTube Studio, uploads the video file, applies AI Title, description, tags, selects "Schedule", and enters the exact scheduled date/time slot!
          </p>
        </div>

      </div>

      <!-- Modal Footer -->
      <div class="flex items-center justify-end gap-2 pt-3 border-t border-slate-800">
        <button type="button" onclick="closeUploadModal()" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium transition">
          Cancel
        </button>
        <button type="button" onclick="submitBatchUpload()" class="px-5 py-2 bg-gradient-to-r from-red-600 to-rose-600 hover:from-red-500 hover:to-rose-500 text-white rounded-lg text-xs font-bold shadow-lg shadow-red-600/30 transition flex items-center gap-1.5">
          <i class="fa-solid fa-play text-[10px]"></i>
          <span>Start Upload Queue</span>
        </button>
      </div>

    </div>
  </div>

  <!-- Single Video Slot Editor Modal -->
  <div id="modal-edit-single-schedule" class="fixed inset-0 bg-black/80 backdrop-blur-md hidden items-center justify-center p-4 z-50">
    <div class="card max-w-md w-full p-5 space-y-4 border-slate-700 shadow-2xl bg-slate-900">
      
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 rounded-lg bg-amber-500/20 text-amber-400 flex items-center justify-center">
            <i class="fa-solid fa-clock"></i>
          </div>
          <div>
            <h3 class="text-sm font-bold text-white">Edit Scheduled Slot</h3>
            <p class="text-xs text-slate-400">Change publishing date/time for this Short</p>
          </div>
        </div>
        <button onclick="closeSingleScheduleModal()" class="text-slate-400 hover:text-white text-lg">&times;</button>
      </div>

      <div class="space-y-3">
        <div>
          <label class="block text-[11px] text-slate-400 mb-1">Target Video</label>
          <div id="single-sched-title" class="p-2 rounded bg-slate-950 border border-slate-800 text-xs text-slate-200 font-medium truncate">...</div>
          <input type="hidden" id="single-sched-video-id" value="">
        </div>

        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="block text-[11px] text-slate-400 mb-1">Scheduled Date</label>
            <input type="date" id="single-sched-date" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-white">
          </div>
          <div>
            <label class="block text-[11px] text-slate-400 mb-1">Scheduled Time</label>
            <input type="time" id="single-sched-time" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-white">
          </div>
        </div>
      </div>

      <div class="flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
        <button type="button" onclick="closeSingleScheduleModal()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs font-medium">Cancel</button>
        <button type="button" onclick="saveSingleSchedule()" class="px-4 py-1.5 bg-amber-600 hover:bg-amber-500 text-white rounded text-xs font-semibold shadow transition">Save Schedule</button>
      </div>

    </div>
  </div>

  <script>
    let currentTab = 'videos';
    let currentParentFilter = null;
    let globalAddPartsLabel = true;
    let ALL_VIDEOS = [];
    let ALL_PARTS = [];
    let ALL_CHANNELS = [];
    let activeChannelId = 1;
    let activeChannelObj = null;
    let channelFilterId = 'ALL';

    function previewVideo(id, title) {
      const modal = document.getElementById('modal-video-preview');
      const video = document.getElementById('preview-video-element');
      const titleEl = document.getElementById('preview-modal-title');
      titleEl.innerText = title || ('Video #' + id);
      video.src = '/api/video-file/' + id;
      modal.classList.remove('hidden');
      modal.classList.add('flex');
      video.load();
      video.play().catch(e => console.log('Autoplay:', e));
    }

    function closeVideoPreviewModal() {
      const modal = document.getElementById('modal-video-preview');
      const video = document.getElementById('preview-video-element');
      video.pause();
      video.src = '';
      modal.classList.add('hidden');
      modal.classList.remove('flex');
    }

    function toggleGlobalPartsLabel() {
      const toggle = document.getElementById('global-parts-label-toggle');
      globalAddPartsLabel = toggle.checked;
      const label = document.getElementById('global-parts-label-text');
      label.innerText = globalAddPartsLabel ? 'ON' : 'OFF';
      label.className = globalAddPartsLabel ? 'text-[11px] text-amber-400 font-bold' : 'text-[11px] text-slate-500 font-bold';
    }

    function updateModalToggleText() {
      // sync with modal
    }

    function openCutShortsModal(id, title, durationSeconds) {
      document.getElementById('modal-video-id').value = id;
      document.getElementById('modal-video-title').innerText = title;
      document.getElementById('modal-parts-label-toggle').checked = globalAddPartsLabel;

      const dur = durationSeconds || 900;
      // 15 minutes max = 900s -> 30 parts max (35s chunks with 5s overlap)
      const effDur = Math.min(dur, 900);
      const wholeParts = Math.min(30, Math.max(1, Math.floor((effDur - 5) / 30) + 1));
      const mins = (dur / 60).toFixed(1);
      const coveredMins = (effDur / 60).toFixed(1);

      const select = document.getElementById('modal-parts-count');
      let opts = `
        <option value="${wholeParts}" selected>🔥 Convert Whole Video (${wholeParts} Parts • ${coveredMins} mins)</option>
      `;
      if (wholeParts >= 30) {
        opts += `<option value="30">⚡ Full 30 Parts (15 mins max)</option>`;
      }
      if (wholeParts > 20) opts += `<option value="20">⚡ First 20 Parts (~10 mins)</option>`;
      if (wholeParts > 15) opts += `<option value="15">⚡ First 15 Parts (~7.5 mins)</option>`;
      if (wholeParts > 10) opts += `<option value="10">⚡ First 10 Parts (~5 mins)</option>`;
      if (wholeParts > 5)  opts += `<option value="5">⚡ First 5 Parts (~2.5 mins)</option>`;
      if (wholeParts > 3)  opts += `<option value="3">⚡ First 3 Parts (0-95s)</option>`;
      opts += `<option value="1">⚡ First 1 Part (0-35s)</option>`;

      select.innerHTML = opts;

      document.getElementById('modal-parts-hint').innerHTML = `
        <i class="fa-solid fa-circle-info text-[10px]"></i>
        <span>Video length: <strong>${mins}m (${Math.round(dur)}s)</strong>. Max length capped at 15 mins (up to 30 parts). Will generate <strong>${wholeParts} vertical Shorts</strong> (35s each with 5s overlap) covering the whole story!</span>
      `;

      const modal = document.getElementById('modal-cut-shorts');
      modal.classList.remove('hidden');
      modal.classList.add('flex');
    }

    function closeCutShortsModal() {
      const modal = document.getElementById('modal-cut-shorts');
      modal.classList.add('hidden');
      modal.classList.remove('flex');
    }

    async function submitCutShorts() {
      const id = parseInt(document.getElementById('modal-video-id').value);
      const numParts = parseInt(document.getElementById('modal-parts-count').value);
      const addPartsLabel = document.getElementById('modal-parts-label-toggle').checked;
      closeCutShortsModal();

      await fetch('/api/create-shorts', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({video_id: id, num_parts: numParts, add_parts_label: addPartsLabel})
      });
      fetchStatus();
      loadVideos();
      loadParts();
    }

    function switchTab(tab) {
      currentTab = tab;
      document.getElementById('tab-videos').classList.toggle('hidden', tab !== 'videos');
      document.getElementById('tab-parts').classList.toggle('hidden', tab !== 'parts');
      const chTab = document.getElementById('tab-channels');
      if (chTab) chTab.classList.toggle('hidden', tab !== 'channels');
      document.getElementById('tab-logs').classList.toggle('hidden', tab !== 'logs');
      document.getElementById('tab-commands').classList.toggle('hidden', tab !== 'commands');
      document.getElementById('tab-branding').classList.toggle('hidden', tab !== 'branding');
      
      const vBtn = document.getElementById('tab-btn-videos');
      const pBtn = document.getElementById('tab-btn-parts');
      const chBtn = document.getElementById('tab-btn-channels');
      const lBtn = document.getElementById('tab-btn-logs');
      const cBtn = document.getElementById('tab-btn-commands');
      const bBtn = document.getElementById('tab-btn-branding');

      const activeClass = "text-white border-b-2 border-red-500 pb-2 transition flex items-center gap-1.5 whitespace-nowrap";
      const inactiveClass = "text-slate-400 hover:text-white pb-2 transition flex items-center gap-1.5 whitespace-nowrap";

      if (vBtn) vBtn.className = tab === 'videos' ? activeClass : inactiveClass;
      if (pBtn) pBtn.className = tab === 'parts' ? activeClass : inactiveClass;
      if (chBtn) chBtn.className = tab === 'channels' ? activeClass : inactiveClass;
      if (lBtn) lBtn.className = tab === 'logs' ? activeClass : inactiveClass;
      if (cBtn) cBtn.className = tab === 'commands' ? activeClass : inactiveClass;
      if (bBtn) bBtn.className = tab === 'branding' ? activeClass : inactiveClass;

      if (tab === 'videos') loadVideos();
      if (tab === 'parts') loadParts();
      if (tab === 'channels') loadChannels();
      if (tab === 'logs') loadLogs();
      if (tab === 'commands') {
        populateCommandVideoSelect();
        pollTerminal();
      }
      if (tab === 'branding') {
        loadChannelSettings();
      }
    }

    // -------------------------------------------------------------
    // MULTI-ACCOUNT & MULTI-CHANNEL CLIENT FUNCTIONS
    // -------------------------------------------------------------

    function toggleChannelDropdown(e) {
      if (e) e.stopPropagation();
      const menu = document.getElementById('channel-dropdown-menu');
      if (menu) menu.classList.toggle('hidden');
    }

    function closeChannelDropdown() {
      const menu = document.getElementById('channel-dropdown-menu');
      if (menu) menu.classList.add('hidden');
    }

    document.addEventListener('click', (e) => {
      const container = document.getElementById('channel-dropdown-container');
      if (container && !container.contains(e.target)) {
        closeChannelDropdown();
      }
    });

    async function loadChannels() {
      try {
        const res = await fetch('/api/channels');
        if (!res.ok) return;
        const data = await res.json();
        ALL_CHANNELS = data.channels || [];
        activeChannelObj = data.active_channel || (ALL_CHANNELS[0] || null);
        activeChannelId = activeChannelObj ? activeChannelObj.id : 1;

        // 1. Update Header Switcher
        const activeNameEl = document.getElementById('active-channel-name');
        const activeIndicatorEl = document.getElementById('active-channel-indicator');
        const activeBadgeEl = document.getElementById('active-channel-badge');
        const countBadgeEl = document.getElementById('channels-badge-count');
        const dropdownCountEl = document.getElementById('dropdown-channel-count');

        if (activeNameEl && activeChannelObj) {
          activeNameEl.innerText = activeChannelObj.channel_name || 'My Channel';
        }
        if (countBadgeEl) countBadgeEl.innerText = ALL_CHANNELS.length;
        if (dropdownCountEl) dropdownCountEl.innerText = `${ALL_CHANNELS.length} Account${ALL_CHANNELS.length === 1 ? '' : 's'}`;

        const isAuth = activeChannelObj && activeChannelObj.is_authenticated == 1;
        if (activeIndicatorEl) {
          activeIndicatorEl.className = isAuth 
            ? "w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" 
            : "w-2.5 h-2.5 rounded-full bg-amber-500";
        }
        if (activeBadgeEl) {
          activeBadgeEl.innerText = isAuth ? "Connected" : "Login Needed";
          activeBadgeEl.className = isAuth 
            ? "px-1.5 py-0.2 text-[9px] rounded bg-emerald-950 text-emerald-400 border border-emerald-800/80 font-semibold"
            : "px-1.5 py-0.2 text-[9px] rounded bg-amber-950 text-amber-300 border border-amber-800/80 font-semibold";
        }

        // 2. Render Dropdown Menu Items
        const listEl = document.getElementById('channel-dropdown-list');
        if (listEl) {
          listEl.innerHTML = ALL_CHANNELS.map(ch => {
            const isActive = ch.id === activeChannelId;
            const chAuth = ch.is_authenticated == 1;
            return `
              <div onclick="switchActiveChannel(${ch.id}); closeChannelDropdown();" 
                   class="p-2 rounded-lg cursor-pointer transition flex items-center justify-between ${isActive ? 'bg-indigo-950/70 border border-indigo-800/80' : 'hover:bg-slate-800/70 border border-transparent'}">
                <div class="flex items-center gap-2 overflow-hidden">
                  <span class="w-2 h-2 rounded-full ${chAuth ? 'bg-emerald-400' : 'bg-amber-400'}"></span>
                  <div class="truncate">
                    <div class="text-xs font-bold text-white truncate flex items-center gap-1.5">
                      <span>${ch.channel_name}</span>
                      ${isActive ? '<span class="text-[9px] text-indigo-400 font-mono">(Active)</span>' : ''}
                    </div>
                    <div class="text-[10px] text-slate-400 font-mono truncate">${ch.handle || '@channel'} • ${ch.video_count || 0} vids</div>
                  </div>
                </div>
                ${isActive ? '<i class="fa-solid fa-check text-indigo-400 text-xs"></i>' : ''}
              </div>
            `;
          }).join('');
        }

        // 3. Update Channel Filter Dropdown
        const chFilterEl = document.getElementById('channel-filter');
        if (chFilterEl) {
          const prevVal = chFilterEl.value;
          chFilterEl.innerHTML = `
            <option value="ALL">🌐 All Channels (${ALL_CHANNELS.length})</option>
            ${ALL_CHANNELS.map(c => `<option value="${c.id}">${c.is_authenticated == 1 ? '🟢' : '🟡'} ${c.channel_name} (${c.video_count || 0})</option>`).join('')}
          `;
          if (prevVal && (prevVal === 'ALL' || ALL_CHANNELS.some(c => String(c.id) === String(prevVal)))) {
            chFilterEl.value = prevVal;
          }
        }

        // 4. Render Channels Tab Grid Cards
        const gridEl = document.getElementById('channels-cards-grid');
        if (gridEl) {
          if (ALL_CHANNELS.length === 0) {
            gridEl.innerHTML = '<div class="card p-6 text-center text-slate-500 col-span-3">No channels found. Click "Connect New YouTube Account" to get started!</div>';
          } else {
            gridEl.innerHTML = ALL_CHANNELS.map(ch => {
              const isActive = ch.id === activeChannelId;
              const isAuth = ch.is_authenticated == 1;
              const chNameSafe = ch.channel_name.replace(/'/g, "\\'");
              const profDirName = ch.profile_dir ? ch.profile_dir.split(/[/\\\\]/).pop() : 'chrome_profile';

              return `
                <div class="card p-5 space-y-4 border ${isActive ? 'border-indigo-500/80 bg-slate-900/90 shadow-lg shadow-indigo-950/40' : 'border-slate-800 bg-slate-900/50 hover:border-slate-700'} flex flex-col justify-between">
                  <div class="space-y-3">
                    <!-- Top row: Avatar & Identity -->
                    <div class="flex items-start justify-between gap-3">
                      <div class="flex items-center gap-3">
                        <div class="w-12 h-12 rounded-xl bg-gradient-to-tr from-indigo-600 to-purple-600 flex items-center justify-center text-white font-bold text-lg shadow-md border border-indigo-400/20">
                          ${ch.channel_name ? ch.channel_name.substring(0, 2).toUpperCase() : 'YT'}
                        </div>
                        <div>
                          <div class="flex items-center gap-2">
                            <h3 class="text-sm font-bold text-white tracking-tight">${ch.channel_name}</h3>
                            <span class="text-[10px] font-mono px-1.5 py-0.2 rounded bg-slate-800 text-slate-400">#${ch.id}</span>
                          </div>
                          <p class="text-xs text-indigo-300 font-mono">${ch.handle || '@' + ch.channel_key}</p>
                        </div>
                      </div>

                      <!-- Active Pill -->
                      ${isActive ? `
                        <span class="px-2.5 py-1 rounded-full bg-indigo-950 border border-indigo-700/80 text-indigo-300 font-bold text-[10px] flex items-center gap-1 shadow">
                          <i class="fa-solid fa-star text-amber-400 text-[9px]"></i> Active
                        </span>
                      ` : `
                        <button onclick="switchActiveChannel(${ch.id})" class="px-2 py-0.5 rounded text-[10px] bg-slate-800 hover:bg-slate-700 text-slate-300 transition">
                          Set Active
                        </button>
                      `}
                    </div>

                    <!-- Connection Status Pill -->
                    <div class="p-2.5 rounded-lg ${isAuth ? 'bg-emerald-950/40 border border-emerald-800/60 text-emerald-300' : 'bg-amber-950/40 border border-amber-800/60 text-amber-300'} flex items-center justify-between text-xs">
                      <div class="flex items-center gap-2">
                        <span class="w-2.5 h-2.5 rounded-full ${isAuth ? 'bg-emerald-500 animate-pulse' : 'bg-amber-500'}"></span>
                        <span class="font-semibold">${isAuth ? 'Studio Session Connected' : 'Google Login Required'}</span>
                      </div>
                      <button onclick="verifyChannelSession(${ch.id})" class="text-[10px] underline hover:text-white" title="Test session">
                        <i class="fa-solid fa-arrows-rotate mr-0.5"></i> Test
                      </button>
                    </div>

                    <!-- Metrics Grid (4 mini KPIs) -->
                    <div class="grid grid-cols-4 gap-2 text-center pt-1">
                      <div class="p-2 rounded-lg bg-slate-950/80 border border-slate-800/80">
                        <div class="text-[10px] text-slate-400 uppercase">Videos</div>
                        <div class="text-base font-bold text-white mt-0.5">${ch.video_count || 0}</div>
                      </div>
                      <div class="p-2 rounded-lg bg-slate-950/80 border border-slate-800/80">
                        <div class="text-[10px] text-amber-400 uppercase">Shorts</div>
                        <div class="text-base font-bold text-amber-300 mt-0.5">${ch.shorts_count || 0}</div>
                      </div>
                      <div class="p-2 rounded-lg bg-slate-950/80 border border-slate-800/80">
                        <div class="text-[10px] text-blue-400 uppercase">Sched</div>
                        <div class="text-base font-bold text-blue-300 mt-0.5">${ch.scheduled_count || 0}</div>
                      </div>
                      <div class="p-2 rounded-lg bg-slate-950/80 border border-slate-800/80">
                        <div class="text-[10px] text-emerald-400 uppercase">Live</div>
                        <div class="text-base font-bold text-emerald-300 mt-0.5">${ch.published_count || 0}</div>
                      </div>
                    </div>

                    <!-- Profile Location & Details -->
                    <div class="text-[11px] text-slate-400 space-y-1 pt-1 font-mono">
                      <div class="flex items-center gap-1.5 truncate" title="${ch.profile_dir}">
                        <i class="fa-regular fa-folder text-slate-500"></i>
                        <span>Profile: <span class="text-slate-300">${profDirName}</span></span>
                      </div>
                      <div class="flex items-center gap-1.5 truncate">
                        <i class="fa-solid fa-palette text-slate-500"></i>
                        <span>Style: <span class="text-slate-300 font-sans">${ch.thumbnail_style || '3D Pixar'}</span></span>
                      </div>
                      <div class="flex items-center gap-1.5 truncate">
                        <i class="fa-solid fa-clock text-slate-500"></i>
                        <span>Shorts Times: <span class="text-amber-400">${ch.shorts_schedule_times || '["11:00", "17:00"]'}</span></span>
                      </div>
                    </div>
                  </div>

                  <!-- Actions Footer -->
                  <div class="pt-3 border-t border-slate-800/80 flex items-center justify-between gap-1.5 flex-wrap">
                    <button onclick="launchChannelLogin(${ch.id}, '${chNameSafe}')" class="flex-1 py-1.5 px-2 bg-gradient-to-r from-red-600 to-rose-600 hover:from-red-500 hover:to-rose-500 text-white rounded-lg text-xs font-bold shadow-md shadow-red-600/20 transition flex items-center justify-center gap-1.5">
                      <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                      <span>${isAuth ? 'Open Studio Chrome' : '🔑 Log In In Chrome'}</span>
                    </button>

                    <button onclick="openEditChannelModal(${ch.id})" class="p-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs transition" title="Channel Settings">
                      <i class="fa-solid fa-gear"></i>
                    </button>

                    ${ch.id !== 1 ? `
                      <button onclick="deleteChannelConfirm(${ch.id}, '${chNameSafe}')" class="p-2 bg-slate-800 hover:bg-rose-900/60 text-slate-400 hover:text-rose-300 rounded-lg text-xs transition" title="Remove Channel">
                        <i class="fa-solid fa-trash-can"></i>
                      </button>
                    ` : ''}
                  </div>
                </div>
              `;
            }).join('');
          }
        }
      } catch (err) {
        console.error("loadChannels error:", err);
      }
    }

    async function switchActiveChannel(channelId) {
      try {
        const res = await fetch(`/api/channels/${channelId}/set-active`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          activeChannelId = channelId;
          const chFilterEl = document.getElementById('channel-filter');
          if (chFilterEl) {
            chFilterEl.value = channelId;
          }
          await loadChannels();
          await loadChannelSettings();
          fetchStatus();
          loadVideos();
          loadParts();
        } else {
          alert("Error: " + (data.detail || data.message));
        }
      } catch (err) {
        alert("Failed to switch channel: " + err);
      }
    }

    async function launchChannelLogin(channelId, channelName) {
      try {
        const res = await fetch(`/api/channels/${channelId}/login`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          alert(`🌐 Chrome Login Window Opened for:\n"${channelName}" (Channel #${channelId})\n\n1. Sign in to your YouTube / Google account in the Chrome window that just opened.\n2. Once logged in, click "Test" or "Verify Session" here on the dashboard.`);
        } else {
          alert("Launch error: " + (data.detail || data.message));
        }
      } catch (err) {
        alert("Failed to launch Chrome: " + err);
      }
    }

    async function verifyChannelSession(channelId) {
      try {
        const res = await fetch(`/api/channels/${channelId}/verify`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          if (data.authenticated) {
            alert(`🎉 Verification Success!\n\nChannel "${data.channel_name}" is ACTIVE & AUTHENTICATED with YouTube Studio!`);
          } else {
            alert(`⚠️ Not Logged In Yet for "${data.channel_name}".\n\nPlease click "🔑 Log In In Chrome", complete sign-in in the opened browser, and test again.`);
          }
          await loadChannels();
          fetchStatus();
        } else {
          alert("Verify error: " + (data.detail || data.message));
        }
      } catch (err) {
        alert("Failed to verify session: " + err);
      }
    }

    async function verifyAllChannelSessions() {
      for (const ch of ALL_CHANNELS) {
        try {
          await fetch(`/api/channels/${ch.id}/verify`, { method: 'POST' });
        } catch(e) {}
      }
      await loadChannels();
      fetchStatus();
      alert("✓ Verification completed for all connected accounts.");
    }

    function openCreateChannelModal() {
      document.getElementById('create-ch-name').value = '';
      document.getElementById('create-ch-handle').value = '';
      document.getElementById('modal-create-channel').classList.remove('hidden');
      document.getElementById('modal-create-channel').classList.add('flex');
    }

    function closeCreateChannelModal() {
      document.getElementById('modal-create-channel').classList.add('hidden');
      document.getElementById('modal-create-channel').classList.remove('flex');
    }

    async function submitCreateChannel() {
      const name = document.getElementById('create-ch-name').value.trim();
      const handle = document.getElementById('create-ch-handle').value.trim();
      const style = document.getElementById('create-ch-thumb-style').value;
      const mood = document.getElementById('create-ch-mood').value.trim();
      const timesRaw = document.getElementById('create-ch-times').value;
      const openBrowser = document.getElementById('create-ch-open-browser').checked;

      if (!name) return alert("Please enter a channel name.");

      const times = timesRaw.split(',').map(t => t.trim()).filter(Boolean);

      try {
        const res = await fetch('/api/channels/create', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            channel_name: name,
            handle: handle,
            thumbnail_style: style,
            thumbnail_mood: mood,
            shorts_schedule_times: times.length ? times : ["11:00", "17:00"]
          })
        });

        const data = await res.json();
        if (res.ok) {
          const newCh = data.channel;
          closeCreateChannelModal();
          await loadChannels();

          if (openBrowser && newCh && newCh.id) {
            launchChannelLogin(newCh.id, newCh.channel_name);
          } else {
            alert(`🎉 Created channel "${name}"! Click "Log In In Chrome" to connect your Google account.`);
          }
        } else {
          alert("Error: " + (data.detail || data.message));
        }
      } catch (err) {
        alert("Failed to create channel: " + err);
      }
    }

    function openEditChannelModal(channelId) {
      const ch = ALL_CHANNELS.find(c => c.id === channelId);
      if (!ch) return;
      document.getElementById('edit-ch-id').value = ch.id;
      document.getElementById('edit-ch-subtitle').innerText = `Configure #${ch.id}: ${ch.channel_name}`;
      document.getElementById('edit-ch-name').value = ch.channel_name || '';
      document.getElementById('edit-ch-handle').value = ch.handle || '';
      document.getElementById('edit-ch-watermark').value = ch.watermark_text || 'Watch next part on {channel_name}';
      document.getElementById('edit-ch-thumb-style').value = ch.thumbnail_style || '3D Pixar Animation';
      document.getElementById('edit-ch-thumb-mood').value = ch.thumbnail_mood || '';
      document.getElementById('edit-ch-times').value = typeof ch.shorts_schedule_times === 'string' ? ch.shorts_schedule_times.replace(/[\[\]"]/g, '') : (ch.shorts_schedule_times || []).join(', ');

      document.getElementById('modal-edit-channel').classList.remove('hidden');
      document.getElementById('modal-edit-channel').classList.add('flex');
    }

    function closeEditChannelModal() {
      document.getElementById('modal-edit-channel').classList.add('hidden');
      document.getElementById('modal-edit-channel').classList.remove('flex');
    }

    async function submitEditChannel() {
      const id = parseInt(document.getElementById('edit-ch-id').value);
      const name = document.getElementById('edit-ch-name').value.trim();
      const handle = document.getElementById('edit-ch-handle').value.trim();
      const watermark = document.getElementById('edit-ch-watermark').value.trim();
      const style = document.getElementById('edit-ch-thumb-style').value.trim();
      const mood = document.getElementById('edit-ch-thumb-mood').value.trim();
      const timesRaw = document.getElementById('edit-ch-times').value;
      const times = timesRaw.split(',').map(t => t.trim()).filter(Boolean);

      try {
        const res = await fetch(`/api/channels/${id}/update`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            channel_name: name,
            handle: handle,
            watermark_text: watermark,
            thumbnail_style: style,
            thumbnail_mood: mood,
            shorts_schedule_times: times
          })
        });

        if (res.ok) {
          closeEditChannelModal();
          await loadChannels();
          fetchStatus();
        } else {
          const err = await res.json();
          alert("Update error: " + (err.detail || err.message));
        }
      } catch (e) {
        alert("Failed to update channel: " + e);
      }
    }

    async function deleteChannelConfirm(channelId, channelName) {
      if (!confirm(`Are you sure you want to remove channel "${channelName}" (ID #${channelId})?\n\nAny videos assigned to this channel will be moved back to Channel 1.`)) {
        return;
      }
      try {
        const res = await fetch(`/api/channels/${channelId}`, { method: 'DELETE' });
        const data = await res.json();
        if (res.ok) {
          await loadChannels();
          fetchStatus();
          loadVideos();
          loadParts();
        } else {
          alert("Error: " + (data.detail || data.message));
        }
      } catch (err) {
        alert("Failed to delete channel: " + err);
      }
    }

    function onChannelFilterChange() {
      channelFilterId = document.getElementById('channel-filter').value;
      loadVideos();
      loadParts();
    }

    // Channel & Branding Settings Functions
    async function loadChannelSettings() {
      try {
        const url = activeChannelId ? `/api/channel-settings?channel_id=${activeChannelId}` : '/api/channel-settings';
        const res = await fetch(url);
        const data = await res.json();
        const s = data.settings || {};
        
        document.getElementById('brand-channel-name').value = s.channel_name || '';
        document.getElementById('brand-show-outro').checked = s.show_outro !== false;
        document.getElementById('brand-outro-duration').value = s.outro_duration_sec || 3.5;
        document.getElementById('brand-outro-duration-val').innerText = (s.outro_duration_sec || 3.5) + 's';
        document.getElementById('brand-outro-template').value = s.outro_text_template || 'Watch next part on {channel_name}';
        
        document.getElementById('brand-show-logo').checked = s.show_logo !== false;
        document.getElementById('brand-logo-position').value = s.logo_position || 'top_right';
        document.getElementById('brand-logo-size').value = s.logo_size || 110;
        document.getElementById('brand-logo-size-val').innerText = (s.logo_size || 110) + 'px';
        
        const timestamp = new Date().getTime();
        const logoUrl = `/api/logo-image?channel_id=${activeChannelId || ''}&t=${timestamp}`;
        document.getElementById('brand-logo-preview-img').src = logoUrl;
        document.getElementById('sim-logo-img').src = logoUrl;
        
        if (s.schedule_frequency) currentScheduleFrequency = s.schedule_frequency;
        if (s.shorts_schedule_times && s.shorts_schedule_times.length > 0) currentScheduleTimeSlots = s.shorts_schedule_times;

        // Thumbnail Prompt & Art Direction Settings
        if (s.thumbnail_art_style && document.getElementById('brand-thumb-style')) {
          document.getElementById('brand-thumb-style').value = s.thumbnail_art_style;
        }
        if (s.thumbnail_mood && document.getElementById('brand-thumb-mood')) {
          document.getElementById('brand-thumb-mood').value = s.thumbnail_mood;
        }
        if (document.getElementById('brand-thumb-template')) {
          if (s.thumbnail_prompt_template) {
            document.getElementById('brand-thumb-template').value = s.thumbnail_prompt_template;
          } else {
            resetDefaultPromptTemplate();
          }
        }

        updateLiveOutroPreview();
      } catch (e) {
        console.error("Error loading channel settings:", e);
      }
    }

    function resetDefaultPromptTemplate() {
      const channelName = (document.getElementById('brand-channel-name').value || '').trim() || 'Lil Kido Stories';
      const el = document.getElementById('brand-thumb-template');
      if (el) {
        el.value = 
`Create a vibrant, adorable, high-quality 3D animated YouTube thumbnail for a kids' storytelling channel called “${channelName}”. Show cute cartoon kids with big expressive eyes, cheerful smiles, and playful outfits, surrounded by a magical storybook world with a friendly baby dragon, a cute bunny, a tiny teddy bear, colorful castles, sparkling stars, rainbows, fluffy clouds, and glowing magical particles.

Use a premium Pixar-inspired 3D animation aesthetic, soft rounded character designs, bright candy colors, cinematic lighting, rich details, and a dreamy fairytale atmosphere. Make the characters large and clearly visible, with expressive faces that instantly grab children's attention. Use a colorful, uncluttered composition with strong contrast, soft depth of field, and a polished professional YouTube thumbnail look.

Target audience: children aged 3–8. The overall mood should feel joyful, safe, playful, magical, and full of adventure. No scary characters, no dark atmosphere, no photorealism, no clutter, no watermark.`;
      }
    }

    function updateLiveOutroPreview() {
      const channelName = (document.getElementById('brand-channel-name').value || '').trim() || 'KAHANI JUNCTION';
      const template = (document.getElementById('brand-outro-template').value || '').trim() || 'Watch next part on {channel_name}';
      const showOutro = document.getElementById('brand-show-outro').checked;
      const showLogo = document.getElementById('brand-show-logo').checked;
      
      const logoEl = document.getElementById('sim-logo-container');
      if (logoEl) {
        logoEl.style.display = showLogo ? 'block' : 'none';
      }

      const l1El = document.getElementById('sim-outro-line1');
      const l2El = document.getElementById('sim-outro-line2');
      
      if (!showOutro) {
        l1El.innerText = '(Outro disabled)';
        l1El.className = 'text-xs text-slate-500 italic';
        l2El.innerText = '';
        return;
      }

      l1El.className = 'font-black text-xs text-[#FFF505] drop-shadow-[0_2px_4px_rgba(0,0,0,0.9)] tracking-wide';
      l2El.className = 'font-black text-sm text-[#00FFFF] drop-shadow-[0_2px_4px_rgba(0,0,0,0.9)] tracking-wider';

      if (template.includes('{channel_name}')) {
        let prefix = template.replace('{channel_name}', '').trim().toUpperCase();
        l1El.innerText = prefix || 'WATCH NEXT PART ON';
        l2El.innerText = '👉 ' + channelName.toUpperCase() + ' 👈';
      } else {
        const words = template.toUpperCase().split(' ');
        if (words.length <= 3) {
          l1El.innerText = template.toUpperCase();
          l2El.innerText = '';
        } else {
          const mid = Math.ceil(words.length / 2);
          l1El.innerText = words.slice(0, mid).join(' ');
          l2El.innerText = words.slice(mid).join(' ');
        }
      }
    }

    async function handleLogoUpload(file) {
      if (!file) return;
      const formData = new FormData();
      formData.append('file', file);
      formData.append('channel_id', activeChannelId);
      
      document.getElementById('brand-logo-status').innerText = 'Uploading...';
      try {
        const res = await fetch(`/api/upload-logo?channel_id=${activeChannelId || ''}`, {
          method: 'POST',
          body: formData
        });
        if (!res.ok) {
          const err = await res.json();
          alert(err.detail || 'Upload failed');
          return;
        }
        const data = await res.json();
        const t = new Date().getTime();
        const logoUrl = `/api/logo-image?channel_id=${activeChannelId || ''}&t=${t}`;
        document.getElementById('brand-logo-preview-img').src = logoUrl;
        document.getElementById('sim-logo-img').src = logoUrl;
        document.getElementById('brand-logo-status').innerText = 'Uploaded & Active';
        document.getElementById('brand-save-status').innerText = '✓ Logo updated and set as active!';
        document.getElementById('brand-save-status').className = 'text-xs text-emerald-400 font-bold';
        await loadChannels();
      } catch (e) {
        alert('Upload error: ' + e);
        document.getElementById('brand-logo-status').innerText = 'Upload failed';
      }
    }

    async function saveChannelSettingsForm() {
      const payload = {
        channel_id: activeChannelId,
        channel_name: document.getElementById('brand-channel-name').value.trim(),
        show_outro: document.getElementById('brand-show-outro').checked,
        outro_duration_sec: parseFloat(document.getElementById('brand-outro-duration').value) || 3.5,
        outro_text_template: document.getElementById('brand-outro-template').value.trim(),
        show_logo: document.getElementById('brand-show-logo').checked,
        logo_position: document.getElementById('brand-logo-position').value,
        logo_size: parseInt(document.getElementById('brand-logo-size').value) || 110,
        logo_opacity: 0.95,
        thumbnail_art_style: document.getElementById('brand-thumb-style') ? document.getElementById('brand-thumb-style').value : '3D Pixar Animation',
        thumbnail_mood: document.getElementById('brand-thumb-mood') ? document.getElementById('brand-thumb-mood').value.trim() : 'Joyful, safe, playful, magical',
        thumbnail_prompt_template: document.getElementById('brand-thumb-template') ? document.getElementById('brand-thumb-template').value.trim() : ''
      };

      try {
        const res = await fetch('/api/channel-settings', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        if (res.ok) {
          await loadChannels();
          const statusEl = document.getElementById('brand-save-status');
          statusEl.innerText = '✓ Settings saved successfully! Will apply to next Shorts cuts.';
          statusEl.className = 'text-xs text-emerald-400 font-bold';
          const pStatusEl = document.getElementById('brand-prompt-save-status');
          if (pStatusEl) {
            pStatusEl.innerText = '✓ Prompt blueprint saved successfully!';
            pStatusEl.className = 'text-xs text-purple-400 font-bold';
          }
          setTimeout(() => {
            statusEl.innerText = 'All modifications auto-apply to future Shorts renders';
            statusEl.className = 'text-xs text-slate-400';
            if (pStatusEl) {
              pStatusEl.innerText = 'Applies to all new AI thumbnail generations';
              pStatusEl.className = 'text-xs text-slate-400';
            }
          }, 4000);
        } else {
          alert('Failed to save settings');
        }
      } catch (e) {
        alert('Error saving settings: ' + e);
      }
    }

    async function populateCommandVideoSelect() {
      const select = document.getElementById('cmd-shorts-video-id');
      if (!select) return;
      let videos = ALL_VIDEOS.filter(v => v.is_short != 1 && !v.part_number);
      if (videos.length === 0) {
        try {
          const res = await fetch('/api/videos?video_type=LONG');
          videos = await res.json();
        } catch(e) {
          console.error(e);
        }
      }
      if (!videos || videos.length === 0) {
        select.innerHTML = '<option value="">(No long videos in queue)</option>';
        return;
      }
      const curVal = select.value;
      select.innerHTML = videos.map(v => {
        const title = (v.title || v.file_name).substring(0, 42);
        return `<option value="${v.id}">#${v.id} - ${title} (${Math.round(v.duration_seconds || 0)}s)</option>`;
      }).join('');
      if (curVal && videos.some(v => String(v.id) === String(curVal))) {
        select.value = curVal;
      }
    }

    function filterByParent(parentId, parentTitle) {
      currentParentFilter = parentId;
      document.getElementById('parent-filter-title').innerText = parentTitle.substring(0, 30) + '...';
      document.getElementById('parent-filter-chip').classList.remove('hidden');
      document.getElementById('parent-filter-chip').classList.add('inline-flex');
      loadVideos();
    }

    function clearParentFilter() {
      currentParentFilter = null;
      document.getElementById('parent-filter-chip').classList.add('hidden');
      document.getElementById('parent-filter-chip').classList.remove('inline-flex');
      loadVideos();
    }

    async function fetchStatus() {
      try {
        const res = await fetch('/api/system-status');
        const data = await res.json();
        
        // Update Health Badges
        const ytBadge = document.getElementById('badge-yt');
        if (data.youtube_authenticated) {
          ytBadge.innerHTML = '<span class="w-2.5 h-2.5 rounded-full bg-emerald-500"></span><span class="text-emerald-400">YouTube: Connected</span>';
        } else {
          ytBadge.innerHTML = '<span class="w-2.5 h-2.5 rounded-full bg-rose-500"></span><span class="text-rose-400">YouTube: Not Logged In</span>';
        }

        const aiBadge = document.getElementById('badge-ai');
        if (data.gemini_connected) {
          aiBadge.innerHTML = '<span class="w-2.5 h-2.5 rounded-full bg-emerald-500"></span><span class="text-emerald-400">Gemini 3.8: Active</span>';
        } else {
          aiBadge.innerHTML = '<span class="w-2.5 h-2.5 rounded-full bg-rose-500"></span><span class="text-rose-400">Gemini: Missing Key</span>';
        }

        // Worker Task
        const wText = document.getElementById('worker-task-text');
        wText.innerText = data.worker.current_task;
        if (data.worker.is_busy) {
          wText.className = "text-amber-400 font-semibold animate-pulse";
        } else {
          wText.className = "text-slate-300";
        }

        document.getElementById('banner-text').innerText = data.worker.last_message;

        // KPI Counts
        const stats = data.stats || {};
        document.getElementById('kpi-total').innerText = stats.total || 0;
        document.getElementById('kpi-long').innerText = stats.long_form || 0;
        document.getElementById('kpi-parts').innerText = stats.parts_count || 0;
        document.getElementById('parts-badge-count').innerText = stats.parts_count || 0;
        document.getElementById('kpi-pending').innerText = stats.pending_ai || 0;
        document.getElementById('kpi-ready').innerText = stats.ready_to_schedule || 0;
        document.getElementById('kpi-scheduled').innerText = stats.scheduled || 0;
        const pubEl = document.getElementById('kpi-published');
        if (pubEl) pubEl.innerText = stats.published || 0;
        document.getElementById('kpi-failed').innerText = stats.failed || 0;

      } catch (e) {
        console.error("Status fetch failed", e);
      }
    }

    function viewSeoDetails(idx, isPartsTab = false) {
      const list = isPartsTab ? ALL_PARTS : ALL_VIDEOS;
      const v = list[idx];
      if (!v) return;
      let msg = "TITLE:\\n" + (v.title || v.file_name);
      if (v.part_number) msg += `\\n(Series Part: ${v.part_number})`;
      msg += "\\n\\nDESCRIPTION:\\n" + (v.description || "Not generated yet");
      if (v.tags) msg += "\\n\\nTAGS:\\n" + v.tags;
      if (v.hashtags) msg += "\\n\\nHASHTAGS:\\n" + v.hashtags;
      alert(msg);
    }

    async function loadVideos() {
      try {
        const filter = document.getElementById('status-filter').value;
        const videoType = document.getElementById('type-filter').value;
        const search = document.getElementById('video-search-input').value.trim().toLowerCase();

        let url = `/api/videos?filter=${filter}`;
        if (videoType !== 'ALL') url += `&video_type=${videoType}`;
        if (currentParentFilter) url += `&parent_id=${currentParentFilter}`;
        const chFilter = document.getElementById('channel-filter')?.value || 'ALL';
        if (chFilter !== 'ALL') url += `&channel_id=${chFilter}`;

        const res = await fetch(url);
        let videos = await res.json();
        
        if (search) {
          videos = videos.filter(v => 
            (v.title && v.title.toLowerCase().includes(search)) || 
            (v.file_name && v.file_name.toLowerCase().includes(search)) ||
            (v.id && String(v.id).includes(search))
          );
        }

        ALL_VIDEOS = videos || [];
        populateCommandVideoSelect();
        const tbody = document.getElementById('videos-tbody');

        if (!videos || videos.length === 0) {
          tbody.innerHTML = '<tr><td colspan="8" class="p-6 text-center text-slate-500">No videos found matching filters.</td></tr>';
          return;
        }

        tbody.innerHTML = videos.map((v, idx) => {
          const isShort = v.is_short == 1;
          const isPart = v.part_number !== null && v.part_number !== undefined;
          
          let typeBadge = '';
          if (isPart) {
            typeBadge = `<span class="px-2 py-0.5 rounded text-[10px] bg-amber-950 text-amber-300 border border-amber-800/80 font-bold whitespace-nowrap"><i class="fa-solid fa-bolt text-[9px] mr-0.5"></i> PART: ${v.part_number}</span>`;
          } else if (isShort) {
            typeBadge = `<span class="px-2 py-0.5 rounded text-[10px] bg-red-950/80 text-red-400 border border-red-800/60 font-semibold whitespace-nowrap">⚡ SHORT</span>`;
          } else {
            typeBadge = `<span class="px-2 py-0.5 rounded text-[10px] bg-blue-950/80 text-blue-400 border border-blue-800/60 font-semibold whitespace-nowrap">🎬 LONG</span>`;
          }

          let statusClass = "bg-slate-800 text-slate-400";
          if (v.status === 'METADATA_READY') statusClass = "bg-blue-950 text-blue-400 border border-blue-800/60";
          if (v.status === 'SCHEDULED') statusClass = "bg-emerald-950 text-emerald-400 border border-emerald-800/60";
          if (v.status === 'PUBLISHED') statusClass = "bg-green-950 text-emerald-300 border border-emerald-600/90 font-bold";
          if (v.status === 'FAILED') statusClass = "bg-rose-950 text-rose-400 border border-rose-800/60";

          const safeTitle = (v.title || v.file_name).replace(/'/g, "\\'").replace(/"/g, '&quot;');
          const chName = v.channel_name || (ALL_CHANNELS.find(c => c.id === v.channel_id)?.channel_name || 'Channel ' + (v.channel_id || 1));

          return `
            <tr class="table-row">
              <td class="p-3 text-slate-500 font-mono">#${v.id}</td>
              <td class="p-3">
                <span class="px-2 py-0.5 rounded text-[10px] bg-indigo-950/80 text-indigo-300 border border-indigo-800/70 font-medium whitespace-nowrap">
                  <i class="fa-brands fa-youtube text-[9px] mr-1"></i>${chName}
                </span>
              </td>
              <td class="p-3">${typeBadge}</td>
              <td class="p-3 max-w-sm">
                <div class="font-medium text-slate-200 truncate">${v.title || v.file_name}</div>
                ${isPart ? `
                  <div class="text-[10px] text-amber-400/90 flex items-center gap-1 mt-0.5 truncate">
                    <i class="fa-solid fa-arrows-split-up-and-left rotate-90 text-[8px]"></i>
                    <span>Part ${v.part_number} of: <strong class="text-slate-300">${v.parent_title || v.parent_file_name || 'Long Video #' + v.parent_video_id}</strong></span>
                  </div>
                ` : `
                  <div class="text-[10px] text-slate-500 truncate">${v.file_name}</div>
                `}
                ${(!isShort && v.generated_parts_count > 0) ? `
                  <button onclick="filterByParent(${v.id}, '${safeTitle}')" class="mt-1 text-[10px] px-2 py-0.5 rounded-full bg-amber-950/60 text-amber-300 border border-amber-800/70 hover:bg-amber-900 transition inline-flex items-center gap-1">
                    <i class="fa-solid fa-film text-[9px]"></i> View ${v.generated_parts_count} Generated Parts <i class="fa-solid fa-arrow-right text-[8px]"></i>
                  </button>
                ` : ''}
              </td>
              <td class="p-3 text-slate-400 whitespace-nowrap">${Math.round(v.duration_seconds)}s</td>
              <td class="p-3 text-slate-300 font-mono text-[11px] whitespace-nowrap">
                <button onclick="openSingleScheduleModal(${v.id}, '${v.scheduled_datetime || ''}', '${safeTitle}')" 
                        title="Click to edit schedule" 
                        class="hover:bg-slate-800 px-1.5 py-0.5 rounded transition inline-flex items-center gap-1 text-slate-300 hover:text-white">
                  <span>${v.scheduled_datetime || '<span class="text-slate-600">Not set</span>'}</span>
                  <i class="fa-solid fa-pen text-[9px] text-slate-500 hover:text-amber-400"></i>
                </button>
              </td>
              <td class="p-3 whitespace-nowrap"><span class="px-2 py-0.5 rounded text-[10px] ${statusClass}">${v.status}</span></td>
              <td class="p-3 text-right whitespace-nowrap space-x-1">
                ${(isShort || isPart) ? `
                  <button onclick="previewVideo(${v.id}, '${safeTitle}')" class="text-xs px-2.5 py-1 rounded bg-amber-950/80 hover:bg-amber-900 text-amber-300 border border-amber-800/80 font-medium inline-flex items-center gap-1">
                    <i class="fa-solid fa-play text-[9px]"></i> Watch
                  </button>
                  <button onclick="uploadSingleVideoConfirm(${v.id}, '${safeTitle}')" class="text-xs px-2.5 py-1 rounded bg-red-950/80 hover:bg-red-900 text-red-300 border border-red-800/80 font-semibold inline-flex items-center gap-1">
                    <i class="fa-solid fa-cloud-arrow-up text-[9px]"></i> Upload
                  </button>
                ` : ''}
                <button onclick="generateSingleThumbnail(${v.id})" class="text-xs text-purple-400 hover:text-purple-300 px-1" title="Generate AI Thumbnail">
                  <i class="fa-solid fa-wand-magic-sparkles"></i> Thumb
                </button>
                <button onclick="viewSeoDetails(${idx}, false)" class="text-xs text-blue-400 hover:text-blue-300 px-1">
                  <i class="fa-regular fa-eye"></i> SEO
                </button>
                ${!isShort ? `
                  <button onclick="openCutShortsModal(${v.id}, '${safeTitle}', ${v.duration_seconds || 0})" class="text-xs text-amber-400 hover:text-amber-300 ml-1 font-medium">
                    <i class="fa-solid fa-scissors"></i> Cut Shorts
                  </button>
                ` : ''}
              </td>
            </tr>
          `;
        }).join('');
      } catch (e) {
        console.error(e);
      }
    }

    async function loadParts() {
      try {
        let url = '/api/videos?video_type=PARTS';
        const chFilter = document.getElementById('channel-filter')?.value || 'ALL';
        if (chFilter !== 'ALL') url += `&channel_id=${chFilter}`;

        const res = await fetch(url);
        const parts = await res.json();
        ALL_PARTS = parts || [];
        const tbody = document.getElementById('parts-tbody');
        const countPill = document.getElementById('tab-parts-count-pill');
        if (countPill) countPill.innerText = `${ALL_PARTS.length} Shorts Ready`;

        if (!parts || parts.length === 0) {
          tbody.innerHTML = '<tr><td colspan="10" class="p-6 text-center text-slate-500">No parts generated yet. Click "Cut Shorts" on any long video in the Video Queue!</td></tr>';
          return;
        }

        tbody.innerHTML = parts.map((p, idx) => {
          let statusClass = "bg-slate-800 text-slate-400";
          if (p.status === 'METADATA_READY') statusClass = "bg-blue-950 text-blue-400 border border-blue-800/60";
          if (p.status === 'SCHEDULED') statusClass = "bg-emerald-950 text-emerald-400 border border-emerald-800/60";
          if (p.status === 'PUBLISHED') statusClass = "bg-green-950 text-emerald-300 border border-emerald-600/90 font-bold";
          if (p.status === 'FAILED') statusClass = "bg-rose-950 text-rose-400 border border-rose-800/60";

          const safePartTitle = (p.title || p.file_name).replace(/'/g, "\\'").replace(/"/g, '&quot;');
          const hasThumb = Boolean(p.thumbnail_path);
          const chName = p.channel_name || (ALL_CHANNELS.find(c => c.id === p.channel_id)?.channel_name || 'Channel ' + (p.channel_id || 1));

          return `
            <tr class="table-row">
              <td class="p-3 text-slate-500 font-mono">#${p.id}</td>
              <td class="p-3">
                <span class="px-2 py-0.5 rounded text-[10px] bg-indigo-950/80 text-indigo-300 border border-indigo-800/70 font-medium whitespace-nowrap">
                  <i class="fa-brands fa-youtube text-[9px] mr-1"></i>${chName}
                </span>
              </td>
              <td class="p-3">
                <div onclick="previewThumbnail(${p.id}, '${safePartTitle}')" class="relative w-12 h-20 rounded-md overflow-hidden border border-slate-700 bg-slate-950 flex-shrink-0 cursor-pointer group shadow hover:border-purple-400 transition" title="Click to view full thumbnail">
                  <img src="/api/thumbnail/${p.id}?t=${hasThumb ? '1' : '0'}" class="w-full h-full object-cover" onerror="this.onerror=null; this.style.display='none'; this.nextElementSibling.style.display='flex';">
                  <div class="hidden absolute inset-0 bg-slate-900 flex flex-col items-center justify-center p-1 text-center">
                    <i class="fa-solid fa-image text-slate-600 text-xs"></i>
                    <span class="text-[8px] text-slate-500 font-mono mt-0.5">No thumb</span>
                  </div>
                  <div class="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 flex items-center justify-center transition">
                    <i class="fa-solid fa-magnifying-glass-plus text-white text-xs"></i>
                  </div>
                </div>
              </td>
              <td class="p-3">
                <span class="px-2 py-0.5 rounded text-[10px] bg-amber-950 text-amber-300 border border-amber-800 font-bold whitespace-nowrap">
                  ⚡ PART: ${p.part_number || 1}
                </span>
              </td>
              <td class="p-3 max-w-xs">
                <div class="font-medium text-slate-200 truncate">${p.title || p.file_name}</div>
                <div class="text-[10px] text-slate-500 font-mono truncate">${p.file_name}</div>
              </td>
              <td class="p-3 max-w-xs">
                <div class="text-xs text-slate-300 truncate">${p.parent_title || p.parent_file_name || 'Long Video #' + p.parent_video_id}</div>
                <div class="text-[10px] text-slate-500">Parent ID #${p.parent_video_id || 'N/A'}</div>
              </td>
              <td class="p-3 text-slate-400 whitespace-nowrap">${Math.round(p.duration_seconds)}s</td>
              <td class="p-3 text-amber-300 font-mono text-[11px] whitespace-nowrap">
                <button onclick="openSingleScheduleModal(${p.id}, '${p.scheduled_datetime || ''}', '${safePartTitle}')" 
                        title="Click to edit schedule" 
                        class="hover:bg-amber-950/60 px-2 py-1 rounded border border-transparent hover:border-amber-700/60 transition inline-flex items-center gap-1.5 text-amber-300 hover:text-amber-200">
                  <i class="fa-regular fa-calendar-check text-[10px] text-amber-400"></i>
                  <span>${p.scheduled_datetime || '<span class="text-slate-500 italic">Pending slot</span>'}</span>
                  <i class="fa-solid fa-pen text-[9px] text-slate-500 hover:text-amber-300"></i>
                </button>
              </td>
              <td class="p-3 whitespace-nowrap"><span class="px-2 py-0.5 rounded text-[10px] ${statusClass}">${p.status}</span></td>
              <td class="p-3 text-right whitespace-nowrap space-x-1.5">
                <button onclick="previewVideo(${p.id}, '${safePartTitle}')" class="text-xs px-2.5 py-1 rounded bg-amber-950/80 hover:bg-amber-900 text-amber-300 border border-amber-800/80 font-medium inline-flex items-center gap-1.5 transition shadow">
                  <i class="fa-solid fa-play text-[9px]"></i> Watch
                </button>
                <button onclick="generateSingleThumbnail(${p.id})" class="text-xs px-2.5 py-1 rounded bg-purple-950/80 hover:bg-purple-900 text-purple-300 border border-purple-800/80 font-medium inline-flex items-center gap-1.5 transition shadow" title="Generate or Regenerate AI Thumbnail">
                  <i class="fa-solid fa-wand-magic-sparkles text-[9px]"></i> AI Thumb
                </button>
                <button onclick="uploadSingleVideoConfirm(${p.id}, '${safePartTitle}')" class="text-xs px-2.5 py-1 rounded bg-red-950/80 hover:bg-red-900 text-red-300 border border-red-800/80 font-semibold inline-flex items-center gap-1.5 transition shadow">
                  <i class="fa-solid fa-cloud-arrow-up text-[9px]"></i> Upload
                </button>
                <button onclick="viewSeoDetails(${idx}, true)" class="text-xs text-blue-400 hover:text-blue-300 px-1 py-1">
                  <i class="fa-regular fa-eye"></i> SEO
                </button>
              </td>
            </tr>
          `;
        }).join('');
      } catch (e) {
        console.error(e);
      }
    }

    let currentPreviewVideoId = null;

    async function previewThumbnail(id, title) {
      currentPreviewVideoId = id;
      const modal = document.getElementById('modal-thumbnail-preview');
      const img = document.getElementById('thumb-modal-img');
      const titleEl = document.getElementById('thumb-modal-title');
      const statusEl = document.getElementById('thumb-modal-status');
      const badge = document.getElementById('thumb-modal-badge');
      const promptInput = document.getElementById('thumb-modal-prompt-input');
      
      titleEl.innerText = title || ('Thumbnail #' + id);
      statusEl.innerText = 'Ready. Edit prompt or select generation method:';
      statusEl.className = 'truncate text-slate-400';
      badge.innerText = 'Video ID #' + id;
      img.src = '/api/thumbnail/' + id + '?t=' + new Date().getTime();
      promptInput.value = "Analyzing video dialogue & story to construct 3D Pixar prompt...";

      modal.classList.remove('hidden');
      modal.classList.add('flex');

      try {
        const res = await fetch('/api/thumbnail-prompt/' + id);
        if (res.ok) {
          const data = await res.json();
          promptInput.value = data.prompt || "";
        }
      } catch (e) {
        console.error("Prompt load error:", e);
      }
    }

    async function fetchFreshPromptForCurrentVideo() {
      if (!currentPreviewVideoId) return;
      const promptInput = document.getElementById('thumb-modal-prompt-input');
      promptInput.value = "Re-synthesizing fresh master 3D prompt with Gemini...";
      try {
        const res = await fetch('/api/thumbnail-prompt/' + currentPreviewVideoId);
        const data = await res.json();
        promptInput.value = data.prompt || "";
      } catch (e) {
        alert("Failed to refresh prompt: " + e);
      }
    }

    function closeThumbnailModal() {
      const modal = document.getElementById('modal-thumbnail-preview');
      const img = document.getElementById('thumb-modal-img');
      img.src = '';
      currentPreviewVideoId = null;
      modal.classList.add('hidden');
      modal.classList.remove('flex');
    }

    async function generateFromModalPrompt(forceAi = true) {
      if (!currentPreviewVideoId) return;
      const vidId = currentPreviewVideoId;
      const promptText = document.getElementById('thumb-modal-prompt-input').value.trim();
      const statusEl = document.getElementById('thumb-modal-status');
      const loader = document.getElementById('thumb-modal-loader');
      const loaderText = document.getElementById('thumb-modal-loader-text');
      
      loader.classList.remove('hidden');
      loader.classList.add('flex');
      loaderText.innerText = forceAi ? "Generating 3D AI artwork..." : "Extracting & enhancing video frame...";
      statusEl.innerText = forceAi ? "Backend AI generation in progress..." : "Extracting high-contrast frame...";

      try {
        const res = await fetch('/api/generate-thumbnail', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            video_id: vidId,
            custom_prompt: promptText,
            force_ai: forceAi
          })
        });
        const data = await res.json();
        if (res.ok) {
          statusEl.innerText = "✓ Thumbnail updated successfully!";
          statusEl.className = "text-emerald-400 font-bold truncate";
          document.getElementById('thumb-modal-img').src = '/api/thumbnail/' + vidId + '?t=' + new Date().getTime();
          loadParts();
          loadVideos();
        } else {
          alert("Error: " + (data.detail || data.message || "Generation failed"));
        }
      } catch (e) {
        alert("Error: " + e);
      } finally {
        loader.classList.add('hidden');
        loader.classList.remove('flex');
      }
    }

    async function generateWithPuterAI() {
      if (!currentPreviewVideoId) return;
      const vidId = currentPreviewVideoId;
      const promptText = document.getElementById('thumb-modal-prompt-input').value.trim();
      const statusEl = document.getElementById('thumb-modal-status');
      const loader = document.getElementById('thumb-modal-loader');
      const loaderText = document.getElementById('thumb-modal-loader-text');
      const btn = document.getElementById('thumb-modal-puter-btn');
      
      if (!promptText) return alert("Please enter a prompt first.");

      btn.disabled = true;
      loader.classList.remove('hidden');
      loader.classList.add('flex');
      loaderText.innerText = "Generating 3D Pixar artwork with Flux (via Puter)...";
      statusEl.innerText = "⏳ Generating in browser (takes ~8-12s)...";

      try {
        if (typeof puter === 'undefined' || !puter.ai || !puter.ai.txt2img) {
          throw new Error("Puter.js library not ready in this browser. Falling back to backend AI.");
        }

        const imgEl = await puter.ai.txt2img(promptText);
        loaderText.innerText = "Applying Part Badge & Lil Kido Stories logo...";
        
        let base64Data = "";
        if (imgEl.src && imgEl.src.startsWith('data:image')) {
          base64Data = imgEl.src;
        } else {
          const canvas = document.createElement('canvas');
          canvas.width = imgEl.naturalWidth || 720;
          canvas.height = imgEl.naturalHeight || 1280;
          const ctx = canvas.getContext('2d');
          ctx.drawImage(imgEl, 0, 0);
          base64Data = canvas.toDataURL('image/jpeg', 0.95);
        }

        const res = await fetch('/api/save-thumbnail-image', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({video_id: vidId, image_base64: base64Data, prompt: promptText})
        });

        if (res.ok) {
          statusEl.innerText = "✓ Custom 3D AI Thumbnail created!";
          statusEl.className = "text-emerald-400 font-bold truncate";
          document.getElementById('thumb-modal-img').src = '/api/thumbnail/' + vidId + '?t=' + new Date().getTime();
          loadParts();
          loadVideos();
        } else {
          const err = await res.json();
          alert("Save error: " + (err.detail || err.message));
        }
      } catch (err) {
        console.warn("Puter browser generation note:", err);
        statusEl.innerText = "Browser note: " + err.message + ". Switching to backend generator...";
        await generateFromModalPrompt(true);
      } finally {
        btn.disabled = false;
        loader.classList.add('hidden');
        loader.classList.remove('flex');
      }
    }

    async function generateSingleThumbnail(id, force = true) {
      try {
        const btn = event?.target?.closest('button');
        if (btn) btn.disabled = true;
        const res = await fetch('/api/generate-thumbnail', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({video_id: id, force: force})
        });
        const data = await res.json();
        if (res.ok) {
          const img = document.getElementById('thumb-modal-img');
          if (img && !document.getElementById('modal-thumbnail-preview').classList.contains('hidden')) {
            img.src = '/api/thumbnail/' + id + '?t=' + new Date().getTime();
          }
          loadParts();
          loadVideos();
        } else {
          alert("Error: " + (data.detail || data.message || "Failed to generate thumbnail"));
        }
      } catch (e) {
        console.error(e);
        alert("Failed to generate thumbnail: " + e);
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function generateAllThumbnails(targetType = 'SHORTS') {
      if (!confirm(`Generate custom AI thumbnails with Part badges & logo for all ${targetType}?`)) return;
      try {
        const res = await fetch('/api/generate-all-thumbnails', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({target_type: targetType})
        });
        const data = await res.json();
        fetchStatus();
      } catch (e) {
        alert("Batch generation error: " + e);
      }
    }

    async function loadLogs() {
      try {
        const res = await fetch('/api/logs');
        const logs = await res.json();
        const tbody = document.getElementById('logs-tbody');

        if (!logs || logs.length === 0) {
          tbody.innerHTML = '<tr><td colspan="7" class="p-6 text-center text-slate-500">No logs found yet.</td></tr>';
          return;
        }

        tbody.innerHTML = logs.slice().reverse().map(l => {
          const ytLink = l.youtube_url 
            ? `<a href="${l.youtube_url}" target="_blank" class="text-blue-400 hover:underline">Link <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i></a>`
            : '<span class="text-slate-600">-</span>';
          
          return `
            <tr class="table-row">
              <td class="p-3 text-slate-500">#${l.log_id}</td>
              <td class="p-3 text-slate-400 font-mono text-[11px]">${l.logged_at.replace('T', ' ').substring(0, 19)}</td>
              <td class="p-3"><span class="px-2 py-0.5 rounded text-[10px] ${l.video_type === 'SHORT' ? 'bg-red-950 text-red-400' : 'bg-blue-950 text-blue-400'}">${l.video_type}</span></td>
              <td class="p-3 text-slate-200 max-w-xs truncate">${l.title || l.file_name}</td>
              <td class="p-3 text-slate-300 font-mono text-[11px]">${l.scheduled_for || 'N/A'}</td>
              <td class="p-3">
                <span class="px-2 py-0.5 rounded text-[10px] ${l.status === 'SUCCESS' ? 'bg-emerald-950 text-emerald-400' : 'bg-rose-950 text-rose-400'}">
                  ${l.status}
                </span>
              </td>
              <td class="p-3">${ytLink}</td>
            </tr>
          `;
        }).join('');
      } catch (e) {
        console.error(e);
      }
    }

    async function scanFolder() {
      const folder = document.getElementById('scan-folder-input').value.trim();
      if (!folder) return alert("Please enter a folder path.");
      await fetch('/api/scan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({folder})
      });
      fetchStatus();
      loadVideos();
    }

    async function generateSeoBatch() {
      await fetch('/api/generate-seo', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({limit: 10})
      });
      fetchStatus();
      loadVideos();
    }

    // -------------------------------------------------------------
    // SMART SCHEDULING & FREQUENCY PLANNER JAVASCRIPT
    // -------------------------------------------------------------
    let currentScheduleFrequency = 'daily';
    let currentScheduleTimeSlots = ['11:00', '17:00'];
    let previewDebounceTimer = null;

    function openScheduleModal(targetType = 'SHORTS') {
      const modal = document.getElementById('modal-smart-schedule');
      modal.classList.remove('hidden');
      modal.classList.add('flex');

      if (activeChannelObj) {
        if (activeChannelObj.schedule_frequency) {
          setScheduleFrequency(activeChannelObj.schedule_frequency);
        }
        if (activeChannelObj.shorts_schedule_times) {
          try {
            const raw = activeChannelObj.shorts_schedule_times;
            const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
            if (Array.isArray(parsed) && parsed.length > 0) currentScheduleTimeSlots = parsed;
          } catch(e) {}
        }
      }

      const radios = document.getElementsByName('sched-target');
      for (const r of radios) {
        if (r.value === targetType) r.checked = true;
      }

      const dateInput = document.getElementById('sched-start-date');
      if (!dateInput.value) {
        setStartDateQuick(1);
      }

      renderTimeSlotsList();
      fetchSchedulePreview();
    }

    function closeScheduleModal() {
      const modal = document.getElementById('modal-smart-schedule');
      modal.classList.add('hidden');
      modal.classList.remove('flex');
    }

    function setStartDateQuick(daysAhead = 1) {
      const d = new Date();
      d.setDate(d.getDate() + daysAhead);
      const yyyy = d.getFullYear();
      const mm = String(d.getMonth() + 1).padStart(2, '0');
      const dd = String(d.getDate()).padStart(2, '0');
      document.getElementById('sched-start-date').value = `${yyyy}-${mm}-${dd}`;
      fetchSchedulePreview();
    }

    function setScheduleFrequency(freq) {
      currentScheduleFrequency = freq;
      
      const cards = ['daily', 'alternate', 'weekly', 'custom'];
      cards.forEach(c => {
        const el = document.getElementById('freq-card-' + c);
        if (!el) return;
        if (c === freq) {
          el.className = 'p-2.5 rounded-lg border border-blue-500 bg-blue-950/40 text-left transition flex flex-col justify-between';
        } else {
          el.className = 'p-2.5 rounded-lg border border-slate-800 bg-slate-950/60 hover:border-slate-700 text-left transition flex flex-col justify-between';
        }
      });

      const customRow = document.getElementById('sched-custom-interval-row');
      if (customRow) {
        if (freq === 'custom') {
          customRow.classList.remove('hidden');
          customRow.classList.add('flex');
        } else {
          customRow.classList.add('hidden');
          customRow.classList.remove('flex');
        }
      }

      fetchSchedulePreview();
    }

    function renderTimeSlotsList() {
      const container = document.getElementById('sched-time-slots-container');
      if (!container) return;

      if (!currentScheduleTimeSlots || currentScheduleTimeSlots.length === 0) {
        container.innerHTML = '<span class="text-xs text-rose-400">⚠️ No time slots set! Add at least one slot.</span>';
        return;
      }

      container.innerHTML = currentScheduleTimeSlots.map((time, idx) => {
        const [h, m] = time.split(':').map(Number);
        const ampm = h >= 12 ? 'PM' : 'AM';
        const h12 = h % 12 === 0 ? 12 : h % 12;
        const time12 = `${h12}:${String(m).padStart(2, '0')} ${ampm}`;

        return `
          <div class="px-2.5 py-1 rounded-full bg-blue-950/80 text-blue-300 border border-blue-800/80 text-xs font-mono font-semibold flex items-center gap-1.5 shadow-sm">
            <i class="fa-regular fa-clock text-[10px]"></i>
            <span>${time} (${time12})</span>
            <button type="button" onclick="removeScheduleTimeSlot(${idx})" class="text-slate-400 hover:text-rose-400 text-xs ml-0.5">&times;</button>
          </div>
        `;
      }).join('');
    }

    function addScheduleTimeSlot() {
      const input = document.getElementById('sched-new-time-input');
      const val = (input.value || '').trim();
      if (!val) return;
      if (!currentScheduleTimeSlots.includes(val)) {
        currentScheduleTimeSlots.push(val);
        currentScheduleTimeSlots.sort();
        renderTimeSlotsList();
        fetchSchedulePreview();
      }
    }

    function removeScheduleTimeSlot(idx) {
      if (currentScheduleTimeSlots.length <= 1) {
        alert("You must keep at least 1 time slot per day.");
        return;
      }
      currentScheduleTimeSlots.splice(idx, 1);
      renderTimeSlotsList();
      fetchSchedulePreview();
    }

    function applyPresetTimes(num) {
      if (num === 1) currentScheduleTimeSlots = ['11:00'];
      else if (num === 2) currentScheduleTimeSlots = ['11:00', '17:00'];
      else if (num === 3) currentScheduleTimeSlots = ['11:00', '16:00', '20:00'];
      renderTimeSlotsList();
      fetchSchedulePreview();
    }

    async function fetchSchedulePreview() {
      clearTimeout(previewDebounceTimer);
      previewDebounceTimer = setTimeout(async () => {
        const tbody = document.getElementById('sched-preview-tbody');
        const summary = document.getElementById('sched-preview-summary');
        if (!tbody || !summary) return;

        let target = 'SHORTS';
        const radios = document.getElementsByName('sched-target');
        for (const r of radios) {
          if (r.checked) target = r.value;
        }

        const startDate = document.getElementById('sched-start-date').value;
        const interval = parseInt(document.getElementById('sched-interval-days').value) || 1;

        try {
          const res = await fetch('/api/schedule-preview', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              frequency: currentScheduleFrequency,
              interval_days: interval,
              time_slots: currentScheduleTimeSlots,
              start_date: startDate,
              target_type: target,
              channel_id: activeChannelId
            })
          });

          if (!res.ok) {
            tbody.innerHTML = '<tr><td class="p-2 text-rose-400">Failed to load preview</td></tr>';
            return;
          }

          const data = await res.json();
          const plan = data.plan || [];
          const count = data.count || plan.length;

          if (plan.length === 0) {
            tbody.innerHTML = '<tr><td class="p-3 text-center text-slate-500">No unscheduled videos found for this target.</td></tr>';
            summary.innerText = "0 videos to schedule";
            return;
          }

          summary.innerText = `Total: ${count} videos across timeline`;

          tbody.innerHTML = plan.map((p, i) => {
            const dt = new Date(p.scheduled_datetime.replace(' ', 'T') + ':00');
            const dayName = dt.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
            const timeStr = dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });

            return `
              <tr class="hover:bg-slate-900/60">
                <td class="p-1.5 text-slate-500 font-mono text-[11px]">#${i+1}</td>
                <td class="p-1.5">
                  <span class="text-amber-300 font-semibold text-xs">Part ${p.part_number || p.id}</span>
                </td>
                <td class="p-1.5 text-slate-300 truncate max-w-[180px]">${p.title || p.file_name}</td>
                <td class="p-1.5 text-right font-mono text-xs">
                  <span class="text-blue-300 font-bold">${dayName}</span>
                  <span class="text-amber-400 font-bold ml-1">@ ${timeStr}</span>
                </td>
              </tr>
            `;
          }).join('');

          if (count > plan.length) {
            tbody.innerHTML += `<tr><td colspan="4" class="p-2 text-center text-[11px] text-slate-500 italic">+ and ${count - plan.length} more videos scheduled successively</td></tr>`;
          }

        } catch (e) {
          console.error("Preview error:", e);
        }
      }, 150);
    }

    async function applySchedulePlan() {
      const btn = document.getElementById('btn-apply-schedule');
      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Applying...';

      let target = 'SHORTS';
      const radios = document.getElementsByName('sched-target');
      for (const r of radios) {
        if (r.checked) target = r.value;
      }

      const startDate = document.getElementById('sched-start-date').value;
      const interval = parseInt(document.getElementById('sched-interval-days').value) || 1;
      const overwrite = document.getElementById('sched-overwrite-toggle').checked;

      try {
        const res = await fetch('/api/schedule-plan', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            frequency: currentScheduleFrequency,
            interval_days: interval,
            time_slots: currentScheduleTimeSlots,
            start_date: startDate,
            target_type: target,
            overwrite_existing: overwrite,
            channel_id: activeChannelId
          })
        });

        const data = await res.json();
        if (res.ok) {
          alert("🎉 " + data.message);
          closeScheduleModal();
          fetchStatus();
          loadVideos();
          loadParts();
        } else {
          alert("Error: " + (data.detail || data.message));
        }
      } catch (err) {
        alert("Failed to apply schedule: " + err);
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-calendar-check mr-1"></i> Apply Schedule Now';
      }
    }

    // -------------------------------------------------------------
    // BATCH UPLOAD MODAL JAVASCRIPT
    // -------------------------------------------------------------
    function openUploadModal(targetType = 'SHORTS') {
      const modal = document.getElementById('modal-upload-shorts');
      modal.classList.remove('hidden');
      modal.classList.add('flex');
    }

    function closeUploadModal() {
      const modal = document.getElementById('modal-upload-shorts');
      modal.classList.add('hidden');
      modal.classList.remove('flex');
    }

    async function submitBatchUpload() {
      let limit = 1;
      const limitRadios = document.getElementsByName('upload-limit');
      for (const r of limitRadios) {
        if (r.checked) limit = parseInt(r.value);
      }

      let headless = false;
      const headlessRadios = document.getElementsByName('upload-headless');
      for (const r of headlessRadios) {
        if (r.checked) headless = (r.value === 'true');
      }

      closeUploadModal();

      try {
        const res = await fetch('/api/upload-next', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({limit: limit, headless: headless, channel_id: activeChannelId})
        });
        const data = await res.json();
        const chName = activeChannelObj ? activeChannelObj.channel_name : 'active channel';
        alert(`🚀 Upload task launched for ${limit} video(s) to "${chName}"!\n\nPlaywright is automating YouTube Studio. Check the Worker status banner at top.`);
        fetchStatus();
      } catch (e) {
        alert("Upload dispatch error: " + e);
      }
    }

    // -------------------------------------------------------------
    // SINGLE VIDEO SCHEDULE & UPLOAD JAVASCRIPT
    // -------------------------------------------------------------
    function openSingleScheduleModal(id, currentDt, title) {
      document.getElementById('single-sched-video-id').value = id;
      document.getElementById('single-sched-title').innerText = title || ('Short #' + id);

      let dVal = '';
      let tVal = '11:00';
      if (currentDt && currentDt.includes(' ')) {
        const parts = currentDt.split(' ');
        dVal = parts[0];
        tVal = parts[1];
      } else {
        const d = new Date();
        d.setDate(d.getDate() + 1);
        const yyyy = d.getFullYear();
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        dVal = `${yyyy}-${mm}-${dd}`;
      }

      document.getElementById('single-sched-date').value = dVal;
      document.getElementById('single-sched-time').value = tVal;

      const modal = document.getElementById('modal-edit-single-schedule');
      modal.classList.remove('hidden');
      modal.classList.add('flex');
    }

    function closeSingleScheduleModal() {
      const modal = document.getElementById('modal-edit-single-schedule');
      modal.classList.add('hidden');
      modal.classList.remove('flex');
    }

    async function saveSingleSchedule() {
      const vidId = document.getElementById('single-sched-video-id').value;
      const dVal = document.getElementById('single-sched-date').value;
      const tVal = document.getElementById('single-sched-time').value;

      if (!dVal || !tVal) {
        alert("Please choose both date and time");
        return;
      }

      const scheduledDt = `${dVal} ${tVal}`;
      try {
        const res = await fetch('/api/update-video-schedule', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({video_id: vidId, scheduled_datetime: scheduledDt})
        });
        const data = await res.json();
        if (res.ok) {
          closeSingleScheduleModal();
          fetchStatus();
          loadVideos();
          loadParts();
        } else {
          alert("Error: " + (data.detail || data.message));
        }
      } catch (e) {
        alert("Update error: " + e);
      }
    }

    async function uploadSingleVideoConfirm(id, title) {
      const chName = activeChannelObj ? activeChannelObj.channel_name : 'Active Channel';
      if (!confirm(`Launch YouTube Studio automation to upload and schedule to channel "${chName}":\n"${title}" (ID #${id})?\n\nA browser window will open to automate YouTube Studio.`)) {
        return;
      }

      try {
        const res = await fetch('/api/upload-single-video', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({video_id: id, headless: false, channel_id: activeChannelId})
        });
        const data = await res.json();
        alert(`🚀 Upload started for Short #${id} to "${chName}"!\nWatch the worker status banner or browser window.`);
        fetchStatus();
      } catch (e) {
        alert("Upload error: " + e);
      }
    }

    async function scheduleAllSlots() {
      openScheduleModal('ALL');
    }

    async function uploadNextVideo() {
      openUploadModal('ALL');
    }

    // Command Panel & Terminal Runner Functions
    let termNextIndex = 0;
    let isPollingActive = false;
    let prevIsRunning = false;

    async function sendCommand(payload) {
      try {
        const res = await fetch('/api/run-command', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) {
          alert(data.message || data.detail || 'Failed to start command');
          return;
        }
        if (currentTab !== 'commands') {
          switchTab('commands');
        }
        pollTerminal();
      } catch (err) {
        alert("Command dispatch error: " + err);
      }
    }

    function runCommandAction(action) {
      if (action === 'scan') {
        const folder = document.getElementById('cmd-scan-path').value.trim();
        if (!folder) return alert("Please specify a folder path.");
        sendCommand({ action: 'scan', folder: folder });
      } else if (action === 'make-shorts') {
        const vidId = document.getElementById('cmd-shorts-video-id').value;
        if (!vidId) return alert("Please select a video first.");
        const parts = parseInt(document.getElementById('cmd-shorts-parts').value || '0');
        const label = document.getElementById('cmd-shorts-label').checked;
        sendCommand({ action: 'make-shorts', video_id: parseInt(vidId), parts: parts, add_parts_label: label });
      } else if (action === 'generate-seo') {
        const limit = parseInt(document.getElementById('cmd-seo-limit').value || '10');
        sendCommand({ action: 'generate-seo', limit: limit });
      } else if (action === 'schedule-all') {
        sendCommand({ action: 'schedule-all' });
      } else if (action === 'upload') {
        const limit = parseInt(document.getElementById('cmd-upload-limit').value || '1');
        const headless = document.getElementById('cmd-upload-headless').checked;
        if (!confirm(`Launch YouTube Studio uploader for up to ${limit} scheduled video(s)?`)) return;
        sendCommand({ action: 'upload', limit: limit, headless: headless });
      } else if (action === 'login') {
        sendCommand({ action: 'login' });
      }
    }

    function runRawCommand(cmdStr) {
      if (!cmdStr) return;
      sendCommand({ command: cmdStr });
    }

    function submitCustomCommand() {
      const input = document.getElementById('term-cmd-input');
      const val = input.value.trim();
      if (!val) return;
      input.value = '';
      runRawCommand(val);
    }

    async function pollTerminal() {
      if (isPollingActive) return;
      isPollingActive = true;
      try {
        const res = await fetch(`/api/terminal-logs?since_index=${termNextIndex}`);
        if (!res.ok) return;
        const data = await res.json();
        
        const screen = document.getElementById('terminal-screen');
        const badge = document.getElementById('term-status-badge');
        const killBtn = document.getElementById('btn-term-kill');

        if (data.lines && data.lines.length > 0) {
          if (termNextIndex === 0 && screen.innerText.includes('Ready. Click any action card')) {
            screen.innerText = '';
          }
          data.lines.forEach(line => {
            screen.textContent += line;
          });
          termNextIndex = data.next_index;

          if (document.getElementById('term-autoscroll').checked) {
            screen.scrollTop = screen.scrollHeight;
          }
        }

        if (data.is_running) {
          badge.innerText = 'RUNNING';
          badge.className = 'px-2 py-0.5 rounded-full text-[10px] bg-amber-900 text-amber-300 font-bold animate-pulse';
          if (killBtn) killBtn.classList.remove('hidden');
        } else {
          if (killBtn) killBtn.classList.add('hidden');
          if (data.returncode === 0) {
            badge.innerText = 'SUCCESS (0)';
            badge.className = 'px-2 py-0.5 rounded-full text-[10px] bg-emerald-900 text-emerald-300 font-bold';
          } else if (data.returncode !== null && data.returncode !== undefined) {
            badge.innerText = `EXIT (${data.returncode})`;
            badge.className = 'px-2 py-0.5 rounded-full text-[10px] bg-rose-900 text-rose-300 font-bold';
          } else {
            badge.innerText = 'IDLE';
            badge.className = 'px-2 py-0.5 rounded-full text-[10px] bg-slate-800 text-slate-400 font-bold';
          }
        }

        // Auto-refresh tables when a command finishes
        if (prevIsRunning && !data.is_running) {
          fetchStatus();
          loadVideos();
          loadParts();
        }
        prevIsRunning = !!data.is_running;

      } catch (e) {
        console.error("Terminal poll error:", e);
      } finally {
        isPollingActive = false;
      }
    }

    async function clearTerminalOutput() {
      await fetch('/api/terminal-clear', { method: 'POST' });
      document.getElementById('terminal-screen').innerText = "Ready. Console cleared.\\n";
      termNextIndex = 0;
      document.getElementById('term-status-badge').innerText = 'IDLE';
      document.getElementById('term-status-badge').className = 'px-2 py-0.5 rounded-full text-[10px] bg-slate-800 text-slate-400 font-bold';
    }

    async function copyTerminalOutput() {
      const text = document.getElementById('terminal-screen').innerText;
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
        alert("Terminal output copied to clipboard!");
      } catch(e) {
        alert("Clipboard copy failed: " + e);
      }
    }

    async function killRunningProcess() {
      if (!confirm("Terminate currently running console process?")) return;
      const res = await fetch('/api/terminal-kill', { method: 'POST' });
      const data = await res.json();
      alert(data.message || "Termination requested");
      pollTerminal();
    }

    // Auto-refresh loop
    setInterval(() => {
      fetchStatus();
      if (currentTab === 'videos') loadVideos();
      if (currentTab === 'parts') loadParts();
      if (currentTab === 'channels') loadChannels();
      if (currentTab === 'logs') loadLogs();
      if (currentTab === 'commands' || prevIsRunning) pollTerminal();
    }, 2000);

    async function initDashboard() {
      await loadChannels();
      await loadChannelSettings();
      fetchStatus();
      loadVideos();
    }
    initDashboard();
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(content=DASHBOARD_HTML)

def start_dashboard(port: int = 8000, open_browser: bool = True):
    print("=" * 60)
    print("      YOUTUBE AI AUTOMATION WEB DASHBOARD")
    print("=" * 60)
    print(f"[*] Dashboard running at: http://localhost:{port}")
    print("[*] Press Ctrl+C in terminal to stop.")
    print("=" * 60)
    
    if open_browser:
        webbrowser.open(f"http://localhost:{port}")

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

if __name__ == "__main__":
    start_dashboard(port=8000, open_browser=True)
