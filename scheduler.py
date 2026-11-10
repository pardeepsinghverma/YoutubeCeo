import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from config import SHORTS_SCHEDULE_TIMES, LONG_SCHEDULE_TIMES
from db import get_connection, mark_video_scheduled, mark_video_published, mark_video_failed, get_videos_by_status
from yt_uploader import upload_and_schedule_video
from log_manager import log_upload
from channel_settings import get_channel_settings

def calculate_slots_plan(
    items: list,
    start_date: datetime,
    frequency: str = "daily",
    interval_days: int = 1,
    time_slots: list = None
) -> list:
    """
    Pure calculation helper: Generates preview schedule datetime strings for a list of items.
    """
    if not items:
        return []

    if frequency == "alternate_days":
        step = 2
    elif frequency == "weekly":
        step = 7
    elif frequency == "every_x_days":
        step = max(1, interval_days or 1)
    else:  # daily
        step = 1

    slots = [s.strip() for s in time_slots if s.strip()] if time_slots else ["11:00", "17:00"]
    if not slots:
        slots = ["11:00"]

    plan = []
    current_day = start_date
    time_idx = 0

    for item in items:
        slot_time = slots[time_idx % len(slots)]
        parts = slot_time.split(":")
        h = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 11
        m = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

        dt = current_day.replace(hour=h, minute=m, second=0, microsecond=0)
        dt_str = dt.strftime("%Y-%m-%d %H:%M")

        plan.append({
            "id": item["id"],
            "title": item.get("title") or item.get("file_name"),
            "file_name": item.get("file_name"),
            "part_number": item.get("part_number"),
            "is_short": item.get("is_short"),
            "scheduled_datetime": dt_str
        })

        time_idx += 1
        if time_idx % len(slots) == 0:
            current_day += timedelta(days=step)

    return plan

def assign_schedule_slots(
    start_date: datetime = None,
    frequency: str = "daily",
    interval_days: int = 1,
    custom_times: list = None,
    target_type: str = "ALL",          # "SHORTS", "LONG", "ALL"
    parent_video_id: int = None,
    overwrite_existing: bool = True,
    video_ids: list = None,
    channel_id: int = None
) -> list:
    """
    Assigns future publishing date-time slots to videos with customizable frequency:
    - frequency: 'daily', 'alternate_days', 'weekly', 'every_x_days'
    - custom_times: list of daily slots, e.g. ['11:00', '17:00']
    - target_type: 'SHORTS', 'LONG', 'ALL'
    - overwrite_existing: if True, re-dates videos that are already scheduled but not yet published
    - channel_id: optional filter to schedule videos for a specific channel
    """
    cfg = get_channel_settings()

    if start_date is None:
        start_date = datetime.now() + timedelta(days=cfg.get("schedule_start_days_ahead", 1))
    elif isinstance(start_date, str):
        try:
            start_date = datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            start_date = datetime.now() + timedelta(days=1)

    start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

    if not custom_times:
        custom_times = cfg.get("shorts_schedule_times", SHORTS_SCHEDULE_TIMES)

    conn = get_connection()
    cursor = conn.cursor()

    assigned_items = []
    try:
        # Build query for target videos
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
            if not overwrite_existing:
                where_clauses.append("(scheduled_datetime IS NULL OR scheduled_datetime = '')")
            if target_type == "SHORTS":
                where_clauses.append("is_short = 1")
            elif target_type == "LONG":
                where_clauses.append("is_short = 0")
            if parent_video_id:
                where_clauses.append("parent_video_id = ?")
                params.append(parent_video_id)

        query = f"SELECT id, title, file_name, part_number, is_short, channel_id FROM videos WHERE {' AND '.join(where_clauses)} ORDER BY part_number ASC, id ASC"
        cursor.execute(query, params)
        rows = [dict(r) for r in cursor.fetchall()]

        # Generate plan
        plan = calculate_slots_plan(
            items=rows,
            start_date=start_date,
            frequency=frequency,
            interval_days=interval_days,
            time_slots=custom_times
        )

        for item in plan:
            cursor.execute("""
                UPDATE videos 
                SET scheduled_datetime = ?, 
                    status = CASE WHEN status = 'FAILED' THEN 'METADATA_READY' ELSE status END,
                    channel_id = COALESCE(?, channel_id),
                    updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (item["scheduled_datetime"], channel_id, item["id"]))
            item["channel_id"] = channel_id
            assigned_items.append(item)

        conn.commit()
        print(f"[OK] Assigned schedule slots to {len(assigned_items)} video(s) (Frequency: {frequency}, Interval: {interval_days}d).")
        return assigned_items
    finally:
        conn.close()

def update_video_schedule(video_id: int, scheduled_dt_str: str) -> bool:
    """
    Manually updates or overrides the scheduled datetime of a single video.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE videos 
            SET scheduled_datetime = ?, 
                status = CASE WHEN status = 'FAILED' THEN 'METADATA_READY' ELSE status END,
                updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (scheduled_dt_str, video_id))
        conn.commit()
        return True
    finally:
        conn.close()

def upload_single_video(video_id: int, headless: bool = False, channel_id: int = None) -> dict:
    """
    Directly uploads and schedules an individual Short/video to YouTube Studio
    using the designated channel's authenticated Chrome profile.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        if not row:
            raise Exception(f"Video #{video_id} not found in database.")
        item = dict(row)
    finally:
        conn.close()

    file_path = Path(item["file_path"])
    if not file_path.exists():
        raise FileNotFoundError(f"Video file missing on disk: {file_path}")

    title = item["title"] or item["file_name"]
    desc = item["description"] or ""
    tags = json.loads(item["tags"]) if item.get("tags") else []
    hashtags = json.loads(item["hashtags"]) if item.get("hashtags") else []
    sched_dt = item["scheduled_datetime"]
    is_short = bool(item["is_short"])
    
    # Priority: explicitly passed channel_id -> active channel -> video's channel_id
    assigned_channel_id = channel_id
    if not assigned_channel_id:
        try:
            from db import get_active_channel
            act = get_active_channel()
            if act:
                assigned_channel_id = act["id"]
        except Exception:
            pass
    if not assigned_channel_id:
        assigned_channel_id = item.get("channel_id") or 1

    # Keep video record in sync with assigned channel
    try:
        conn = get_connection()
        conn.cursor().execute("UPDATE videos SET channel_id = ? WHERE id = ?", (assigned_channel_id, video_id))
        conn.commit()
        conn.close()
    except Exception:
        pass

    # If no scheduled datetime, keep as None (will be published immediately as Public)
    if not sched_dt:
        sched_dt = None

    # Check or auto-generate custom thumbnail
    thumb_path = item.get("thumbnail_path")
    if not thumb_path or not Path(thumb_path).exists():
        try:
            from thumbnail_generator import generate_thumbnail_for_video_id
            print(f"[*] Auto-generating AI thumbnail for video #{video_id}...")
            generated_thumb = generate_thumbnail_for_video_id(video_id)
            if generated_thumb and generated_thumb.exists():
                thumb_path = str(generated_thumb)
        except Exception as t_err:
            print(f"[-] Note: Thumbnail generation note on #{video_id}: {t_err}")

    print(f"[*] Starting single video upload for #{video_id} (Channel #{assigned_channel_id or 1}): {file_path.name}")
    try:
        res = upload_and_schedule_video(
            video_path=file_path,
            title=title,
            description=desc,
            tags=tags,
            schedule_dt_str=sched_dt,
            headless=headless,
            thumbnail_path=Path(thumb_path) if thumb_path else None,
            video_id=video_id,
            channel_id=assigned_channel_id
        )
        yt_url = res.get("url", "")
        is_published = res.get("published_immediately", False)
        final_status = "PUBLISHED" if is_published else "SCHEDULED"

        if is_published:
            mark_video_published(video_id, yt_url=yt_url)
            print(f"[OK] Video #{video_id} successfully published LIVE immediately on YouTube!")
        else:
            mark_video_scheduled(video_id, sched_dt, yt_url=yt_url)
            print(f"[OK] Video #{video_id} successfully scheduled on YouTube for {sched_dt}!")

        # Telegram HITL notification dispatch
        try:
            import asyncio
            from telegram_bot import notify_upload_event
            asyncio.run(notify_upload_event(video_id, final_status, yt_url=yt_url))
        except Exception:
            pass

        log_upload(
            video_id=video_id,
            file_name=file_path.name,
            file_path=str(file_path),
            is_short=is_short,
            title=title,
            description=desc,
            tags=tags,
            hashtags=hashtags,
            scheduled_datetime=sched_dt,
            youtube_url=yt_url,
            status=final_status
        )
        return {"status": final_status, "video_id": video_id, "youtube_url": yt_url, "scheduled_datetime": sched_dt, "published_immediately": is_published}
    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        err_msg = str(e).strip() or repr(e)
        full_err = f"{err_msg}\n{tb_str}"
        print(f"[!] Exception during upload of #{video_id}: {full_err}", flush=True)
        mark_video_failed(video_id, err_msg)
        log_upload(
            video_id=video_id,
            file_name=file_path.name,
            file_path=str(file_path),
            is_short=is_short,
            title=title,
            description=desc,
            tags=tags,
            hashtags=hashtags,
            scheduled_datetime=sched_dt,
            status="FAILED",
            error_message=full_err
        )
        try:
            import asyncio
            from telegram_bot import notify_upload_event
            asyncio.run(notify_upload_event(video_id, "FAILED", error_msg=err_msg))
        except Exception:
            pass
        raise e

def process_upload_queue(limit: int = 1, headless: bool = False, channel_id: int = None):
    """
    Uploads the next N ready videos into YouTube Studio and sets their schedule.
    """
    if not channel_id:
        try:
            from db import get_active_channel
            act = get_active_channel()
            if act:
                channel_id = act["id"]
        except Exception:
            pass

    conn = get_connection()
    cursor = conn.cursor()
    try:
        where = "status = 'METADATA_READY' AND scheduled_datetime IS NOT NULL"
        params = []
        if channel_id:
            where += " AND (channel_id = ? OR channel_id IS NULL)"
            params.append(channel_id)
        params.append(limit)

        cursor.execute(f"""
        SELECT * FROM videos 
        WHERE {where}
        ORDER BY scheduled_datetime ASC 
        LIMIT ?
        """, params)
        items = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

    if not items:
        print(f"[*] No videos waiting in the upload queue for Channel #{channel_id or 'Any'}.")
        return

    print(f"[*] Processing {len(items)} video(s) for automated upload (Channel #{channel_id})...")
    for item in items:
        vid_id = item["id"]
        file_path = Path(item["file_path"])
        title = item["title"]
        desc = item["description"]
        tags = json.loads(item["tags"]) if item.get("tags") else []
        hashtags = json.loads(item["hashtags"]) if item.get("hashtags") else []
        sched_dt = item["scheduled_datetime"]
        is_short = bool(item["is_short"])

        # Check or auto-generate thumbnail
        thumb_path = item.get("thumbnail_path")
        if not thumb_path or not Path(thumb_path).exists():
            try:
                from thumbnail_generator import generate_thumbnail_for_video_id
                generated_thumb = generate_thumbnail_for_video_id(vid_id)
                if generated_thumb and generated_thumb.exists():
                    thumb_path = str(generated_thumb)
            except Exception as t_err:
                print(f"[-] Thumbnail auto-generation error for #{vid_id}: {t_err}")

        try:
            res = upload_and_schedule_video(
                video_path=file_path,
                title=title,
                description=desc,
                tags=tags,
                schedule_dt_str=sched_dt,
                headless=headless,
                thumbnail_path=Path(thumb_path) if thumb_path else None,
                video_id=vid_id,
                channel_id=channel_id or item.get("channel_id")
            )
            yt_url = res.get("url", "")
            is_published = res.get("published_immediately", False)
            final_status = "PUBLISHED" if is_published else "SCHEDULED"

            if is_published:
                mark_video_published(vid_id, yt_url=yt_url)
                print(f"[OK] Video #{vid_id} successfully published LIVE immediately on YouTube!")
            else:
                mark_video_scheduled(vid_id, sched_dt, yt_url=yt_url)
                print(f"[OK] Video #{vid_id} successfully scheduled on YouTube for {sched_dt}!")

            log_upload(
                video_id=vid_id,
                file_name=file_path.name,
                file_path=str(file_path),
                is_short=is_short,
                title=title,
                description=desc,
                tags=tags,
                hashtags=hashtags,
                scheduled_datetime=sched_dt,
                youtube_url=yt_url,
                status=final_status
            )
        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            err_msg = str(e).strip() or repr(e)
            full_err = f"{err_msg}\n{tb_str}"
            print(f"[!] Failed to upload video #{vid_id}: {full_err}", flush=True)
            mark_video_failed(vid_id, err_msg)
            log_upload(
                video_id=vid_id,
                file_name=file_path.name,
                file_path=str(file_path),
                is_short=is_short,
                title=title,
                description=desc,
                tags=tags,
                hashtags=hashtags,
                scheduled_datetime=sched_dt,
                status="FAILED",
                error_message=full_err
            )

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "upload":
        target_vid_id = int(sys.argv[2])
        is_headless = "--headless" in sys.argv
        cli_channel_id = None
        if "--channel" in sys.argv:
            idx = sys.argv.index("--channel")
            if idx + 1 < len(sys.argv) and sys.argv[idx + 1].isdigit():
                cli_channel_id = int(sys.argv[idx + 1])
        print(f"[*] CLI: Executing isolated single video upload for #{target_vid_id} (Channel #{cli_channel_id or 'Active'}, headless={is_headless})...")
        res = upload_single_video(target_vid_id, headless=is_headless, channel_id=cli_channel_id)
        print(f"[OK] CLI: Completed upload for #{target_vid_id}: {res}")
    elif len(sys.argv) > 1 and sys.argv[1] == "queue":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 1
        is_headless = "--headless" in sys.argv
        cli_channel_id = None
        if "--channel" in sys.argv:
            idx = sys.argv.index("--channel")
            if idx + 1 < len(sys.argv) and sys.argv[idx + 1].isdigit():
                cli_channel_id = int(sys.argv[idx + 1])
        print(f"[*] CLI: Executing isolated queue upload (limit={limit}, Channel #{cli_channel_id or 'Active'}, headless={is_headless})...")
        process_upload_queue(limit=limit, headless=is_headless, channel_id=cli_channel_id)
    else:
        assign_schedule_slots()
