from typing import Optional, List, Dict, Any
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from config import DB_PATH

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path TEXT UNIQUE NOT NULL,
        file_name TEXT NOT NULL,
        file_size_mb REAL,
        duration_seconds REAL,
        width INTEGER,
        height INTEGER,
        is_short BOOLEAN DEFAULT 0,
        status TEXT DEFAULT 'SCANNED', 
        -- Status values: 'SCANNED', 'AI_PROCESSING', 'METADATA_READY', 'SCHEDULED', 'PUBLISHED', 'FAILED'
        
        -- AI SEO Metadata
        title TEXT,
        title_alternatives TEXT, -- JSON array of alternatives
        description TEXT,
        tags TEXT,               -- JSON array of tags
        hashtags TEXT,           -- JSON array of hashtags
        category_id TEXT DEFAULT '28',
        
        -- YouTube Publication Details
        scheduled_datetime TEXT, -- e.g. '2026-10-15 18:00'
        youtube_video_id TEXT,
        youtube_url TEXT,
        last_error TEXT,
        parent_video_id INTEGER DEFAULT NULL,
        part_number INTEGER DEFAULT NULL,
        
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_key TEXT UNIQUE NOT NULL,
        channel_name TEXT NOT NULL,
        handle TEXT DEFAULT '',
        profile_dir TEXT NOT NULL,
        logo_path TEXT DEFAULT '',
        watermark_text TEXT DEFAULT 'Watch next part on {channel_name}',
        thumbnail_style TEXT DEFAULT '3D Pixar Animation',
        thumbnail_mood TEXT DEFAULT 'Joyful, safe, playful, magical, full of adventure',
        thumbnail_prompt_template TEXT DEFAULT '',
        schedule_frequency TEXT DEFAULT 'daily',
        schedule_interval_days INTEGER DEFAULT 1,
        shorts_schedule_times TEXT DEFAULT '["11:00", "17:00"]',
        long_schedule_times TEXT DEFAULT '["19:30"]',
        is_active BOOLEAN DEFAULT 0,
        is_authenticated BOOLEAN DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    try:
        cursor.execute("ALTER TABLE videos ADD COLUMN parent_video_id INTEGER DEFAULT NULL")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE videos ADD COLUMN part_number INTEGER DEFAULT NULL")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE videos ADD COLUMN thumbnail_path TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE videos ADD COLUMN thumbnail_prompt TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE videos ADD COLUMN channel_id INTEGER DEFAULT 1")
    except Exception:
        pass

    for col, col_type in [
        ("show_logo", "BOOLEAN DEFAULT 1"),
        ("logo_position", "TEXT DEFAULT 'top_right'"),
        ("logo_size", "INTEGER DEFAULT 110"),
        ("logo_opacity", "REAL DEFAULT 0.9"),
        ("show_outro", "BOOLEAN DEFAULT 1"),
        ("outro_duration_sec", "REAL DEFAULT 3.5")
    ]:
        try:
            cursor.execute(f"ALTER TABLE channels ADD COLUMN {col} {col_type}")
        except Exception:
            pass

    # Competitor Discovery Tables
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS discovery_channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_url TEXT UNIQUE NOT NULL,
        channel_name TEXT DEFAULT '',
        handle TEXT DEFAULT '',
        subscriber_count INTEGER DEFAULT 0,
        total_videos INTEGER DEFAULT 0,
        last_scanned TIMESTAMP DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        channel_id INTEGER DEFAULT 1
    );
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS discovery_videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discovery_channel_id INTEGER,
        youtube_id TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        duration_seconds REAL DEFAULT 0,
        view_count INTEGER DEFAULT 0,
        upload_date TEXT DEFAULT '',
        view_velocity REAL DEFAULT 0.0,
        thumbnail_url TEXT DEFAULT '',
        video_url TEXT DEFAULT '',
        is_short BOOLEAN DEFAULT 0,
        tags_json TEXT DEFAULT '[]',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(discovery_channel_id) REFERENCES discovery_channels(id) ON DELETE CASCADE
    );
    ''')

    try:
        cursor.execute("ALTER TABLE discovery_channels ADD COLUMN channel_id INTEGER DEFAULT 1")
    except Exception:
        pass

    # Telegram HITL Configuration Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS telegram_config (
        id INTEGER PRIMARY KEY DEFAULT 1,
        bot_token TEXT DEFAULT '',
        chat_id TEXT DEFAULT '',
        is_enabled BOOLEAN DEFAULT 0,
        notify_on_ready BOOLEAN DEFAULT 1,
        notify_on_upload BOOLEAN DEFAULT 1,
        auto_approve BOOLEAN DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    # Seed default channel from existing channel_settings if table is empty
    cursor.execute("SELECT COUNT(*) FROM channels")
    if cursor.fetchone()[0] == 0:
        ch_name = "Lil Kido Stories"
        ch_logo = ""
        try:
            from channel_settings import get_channel_settings
            cfg = get_channel_settings()
            ch_name = cfg.get("channel_name", "Lil Kido Stories")
            ch_logo = cfg.get("logo_path", "")
        except Exception:
            pass

        from config import CHROME_PROFILE_DIR
        cursor.execute("""
        INSERT INTO channels (
            id, channel_key, channel_name, handle, profile_dir, logo_path,
            watermark_text, is_active, is_authenticated
        ) VALUES (
            1, 'channel_1', ?, '@LilKidoStories', ?, ?,
            'Watch next part on {channel_name}', 1, 1
        )
        """, (ch_name, str(CHROME_PROFILE_DIR), ch_logo))

    conn.commit()
    conn.close()

def add_video(file_path, file_name, file_size_mb, duration_seconds, width, height, is_short, parent_video_id=None, part_number=None, channel_id=None):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        resolved_channel_id = channel_id
        if not resolved_channel_id and parent_video_id:
            cursor.execute("SELECT channel_id FROM videos WHERE id = ?", (parent_video_id,))
            p_row = cursor.fetchone()
            if p_row and p_row["channel_id"]:
                resolved_channel_id = p_row["channel_id"]
        if not resolved_channel_id:
            cursor.execute("SELECT id FROM channels WHERE is_active = 1 LIMIT 1")
            act_row = cursor.fetchone()
            resolved_channel_id = act_row["id"] if act_row else 1

        cursor.execute("""
        INSERT INTO videos (file_path, file_name, file_size_mb, duration_seconds, width, height, is_short, status, parent_video_id, part_number, channel_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'SCANNED', ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET 
            file_name = excluded.file_name,
            file_size_mb = excluded.file_size_mb,
            duration_seconds = excluded.duration_seconds,
            width = excluded.width,
            height = excluded.height,
            is_short = excluded.is_short,
            parent_video_id = COALESCE(excluded.parent_video_id, videos.parent_video_id),
            part_number = COALESCE(excluded.part_number, videos.part_number),
            channel_id = COALESCE(videos.channel_id, excluded.channel_id),
            updated_at = CURRENT_TIMESTAMP
        """, (str(file_path), file_name, file_size_mb, duration_seconds, width, height, 1 if is_short else 0, parent_video_id, part_number, resolved_channel_id))
        conn.commit()
        if cursor.lastrowid:
            return cursor.lastrowid
        cursor.execute("SELECT id FROM videos WHERE file_path = ?", (str(file_path),))
        row = cursor.fetchone()
        return row["id"] if row else 0
    finally:
        conn.close()

def update_video_metadata(video_id, title, title_alts, description, tags, hashtags):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE videos 
        SET title = ?, 
            title_alternatives = ?, 
            description = ?, 
            tags = ?, 
            hashtags = ?, 
            status = 'METADATA_READY',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (
            title,
            json.dumps(title_alts) if isinstance(title_alts, list) else title_alts,
            description,
            json.dumps(tags) if isinstance(tags, list) else tags,
            json.dumps(hashtags) if isinstance(hashtags, list) else hashtags,
            video_id
        ))
        conn.commit()
    finally:
        conn.close()

def mark_video_scheduled(video_id, scheduled_dt_str, yt_url=None, yt_id=None):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE videos 
        SET status = 'SCHEDULED', 
            scheduled_datetime = ?, 
            youtube_url = ?, 
            youtube_video_id = ?, 
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (scheduled_dt_str, yt_url, yt_id, video_id))
        conn.commit()
    finally:
        conn.close()

def mark_video_published(video_id, yt_url=None, yt_id=None):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE videos 
        SET status = 'PUBLISHED', 
            youtube_url = COALESCE(?, youtube_url), 
            youtube_video_id = COALESCE(?, youtube_video_id), 
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (yt_url, yt_id, video_id))
        conn.commit()
    finally:
        conn.close()

def mark_video_failed(video_id, error_msg):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE videos 
        SET status = 'FAILED', 
            last_error = ?, 
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (error_msg, video_id))
        conn.commit()
    finally:
        conn.close()


def update_video_status(video_id: int, new_status: str, error_msg: Optional[str] = None):
    """Updates a video's status (SCANNED, METADATA_READY, SCHEDULED, PUBLISHED, FAILED). Clears last_error for ready/active states."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        status_upper = new_status.strip().upper()
        if status_upper in ("SCHEDULED", "PUBLISHED", "METADATA_READY", "SCANNED"):
            cursor.execute("""
            UPDATE videos 
            SET status = ?, 
                last_error = NULL, 
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """, (status_upper, video_id))
        else:
            cursor.execute("""
            UPDATE videos 
            SET status = ?, 
                last_error = COALESCE(?, last_error), 
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """, (status_upper, error_msg, video_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def bulk_update_video_status(video_ids: list[int], new_status: str) -> int:
    """Updates status for all given video IDs. Clears last_error if status is set to METADATA_READY, SCHEDULED, or PUBLISHED. Returns count updated."""
    if not video_ids:
        return 0
    cleaned_ids = list(dict.fromkeys(int(v) for v in video_ids))
    status_upper = new_status.strip().upper()
    placeholders = ",".join("?" for _ in cleaned_ids)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if status_upper in ("METADATA_READY", "SCHEDULED", "PUBLISHED", "SCANNED"):
            cursor.execute(
                f"""
                UPDATE videos 
                SET status = ?, 
                    last_error = NULL, 
                    updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                [status_upper] + cleaned_ids
            )
        else:
            cursor.execute(
                f"""
                UPDATE videos 
                SET status = ?, 
                    updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                [status_upper] + cleaned_ids
            )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()

def retry_video_upload(video_id: int):
    """Resets last_error to NULL and status to METADATA_READY for the video."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE videos 
        SET status = 'METADATA_READY', 
            last_error = NULL, 
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (int(video_id),))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def delete_video(video_id: int) -> bool:
    """Deletes the specified video and any child parts if it is a parent video."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM videos WHERE id = ? OR parent_video_id = ?", (int(video_id), int(video_id)))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def delete_video_parts(parent_video_id: int) -> int:
    """Deletes all generated short parts that belong to a specific parent video."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM videos WHERE parent_video_id = ?", (int(parent_video_id),))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()

def bulk_delete_videos(video_ids: list[int]) -> int:
    """Deletes the specified videos and any sliced children if they are parent videos. Returns count deleted."""
    if not video_ids:
        return 0
    cleaned_ids = list(dict.fromkeys(int(v) for v in video_ids))
    placeholders = ",".join("?" for _ in cleaned_ids)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"DELETE FROM videos WHERE id IN ({placeholders}) OR parent_video_id IN ({placeholders})",
            cleaned_ids + cleaned_ids
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()

def update_video_thumbnail(video_id: int, thumbnail_path: str, thumbnail_prompt: str = None):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if thumbnail_prompt:
            cursor.execute("""
            UPDATE videos 
            SET thumbnail_path = ?, 
                thumbnail_prompt = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """, (str(thumbnail_path) if thumbnail_path else None, thumbnail_prompt, video_id))
        else:
            cursor.execute("""
            UPDATE videos 
            SET thumbnail_path = ?, 
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """, (str(thumbnail_path) if thumbnail_path else None, video_id))
        conn.commit()
    finally:
        conn.close()

def get_all_channels():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT 
            c.*,
            (SELECT COUNT(*) FROM videos v WHERE v.channel_id = c.id) as video_count,
            (SELECT COUNT(*) FROM videos v WHERE v.channel_id = c.id AND v.is_short = 1) as shorts_count,
            (SELECT COUNT(*) FROM videos v WHERE v.channel_id = c.id AND v.status = 'SCHEDULED') as scheduled_count,
            (SELECT COUNT(*) FROM videos v WHERE v.channel_id = c.id AND v.status = 'PUBLISHED') as published_count
        FROM channels c
        ORDER BY c.id ASC
        """)
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

def get_channel_by_id(channel_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM channels WHERE id = ?", (channel_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_channel_by_key(channel_key: str):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM channels WHERE channel_key = ?", (channel_key,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_active_channel():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM channels WHERE is_active = 1 LIMIT 1")
        row = cursor.fetchone()
        if not row:
            cursor.execute("SELECT * FROM channels ORDER BY id ASC LIMIT 1")
            row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def set_active_channel(channel_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE channels SET is_active = 0")
        cursor.execute("UPDATE channels SET is_active = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (channel_id,))
        conn.commit()
        return get_channel_by_id(channel_id)
    finally:
        conn.close()

def add_channel(
    channel_name: str,
    handle: str = "",
    profile_dir: str = None,
    logo_path: str = "",
    watermark_text: str = None,
    thumbnail_style: str = "3D Pixar Animation",
    thumbnail_mood: str = "Joyful, safe, playful, magical, full of adventure",
    thumbnail_prompt_template: str = "",
    schedule_frequency: str = "daily",
    schedule_interval_days: int = 1,
    shorts_schedule_times: list = None,
    long_schedule_times: list = None
):
    from config import PROFILES_BASE_DIR
    import uuid
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Generate safe key
        safe_slug = "".join([c if c.isalnum() else "_" for c in channel_name.lower()]).strip("_")[:15] or "channel"
        unique_key = f"{safe_slug}_{uuid.uuid4().hex[:6]}"

        if not profile_dir:
            profile_path = PROFILES_BASE_DIR / unique_key
            profile_path.mkdir(exist_ok=True, parents=True)
            profile_dir = str(profile_path)

        watermark = watermark_text or f"Watch next part on {channel_name}"
        shorts_times_json = json.dumps(shorts_schedule_times or ["11:00", "17:00"])
        long_times_json = json.dumps(long_schedule_times or ["19:30"])

        cursor.execute("""
        INSERT INTO channels (
            channel_key, channel_name, handle, profile_dir, logo_path,
            watermark_text, thumbnail_style, thumbnail_mood, thumbnail_prompt_template,
            schedule_frequency, schedule_interval_days, shorts_schedule_times, long_schedule_times,
            is_active, is_authenticated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
        """, (
            unique_key, channel_name, handle, profile_dir, logo_path,
            watermark, thumbnail_style, thumbnail_mood, thumbnail_prompt_template,
            schedule_frequency, schedule_interval_days, shorts_times_json, long_times_json
        ))
        conn.commit()
        new_id = cursor.lastrowid
        return get_channel_by_id(new_id)
    finally:
        conn.close()

def update_channel(channel_id: int, **fields):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        allowed = {
            "channel_name", "handle", "profile_dir", "logo_path", "watermark_text",
            "thumbnail_style", "thumbnail_mood", "thumbnail_prompt_template",
            "schedule_frequency", "schedule_interval_days", "shorts_schedule_times",
            "long_schedule_times", "is_active", "is_authenticated",
            "show_logo", "logo_position", "logo_size", "logo_opacity",
            "show_outro", "outro_duration_sec"
        }
        updates = []
        params = []
        for k, v in fields.items():
            if k in allowed:
                if isinstance(v, (list, dict)):
                    v = json.dumps(v)
                updates.append(f"{k} = ?")
                params.append(v)
        if not updates:
            return get_channel_by_id(channel_id)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(channel_id)
        query = f"UPDATE channels SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, params)
        conn.commit()
        return get_channel_by_id(channel_id)
    finally:
        conn.close()

def delete_channel(channel_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM channels")
        if cursor.fetchone()[0] <= 1:
            raise Exception("Cannot delete the only channel in the system.")
        cursor.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
        # Reassign orphan videos to channel 1
        cursor.execute("UPDATE videos SET channel_id = 1 WHERE channel_id = ?", (channel_id,))
        conn.commit()
        return True
    finally:
        conn.close()

def set_channel_auth_status(channel_id: int, is_authenticated: bool):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE channels SET is_authenticated = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (1 if is_authenticated else 0, channel_id))
        conn.commit()
    finally:
        conn.close()

def assign_videos_to_channel(video_ids: list, channel_id: int):
    if not video_ids:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    try:
        placeholders = ",".join(["?"] * len(video_ids))
        cursor.execute(f"UPDATE videos SET channel_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})", [channel_id] + video_ids)
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()

def get_videos_by_status(status=None, video_type=None, parent_id=None, channel_id=None):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        query = """
        SELECT 
            v.*,
            p.title AS parent_title,
            p.file_name AS parent_file_name,
            (SELECT COUNT(*) FROM videos c WHERE c.parent_video_id = v.id) AS generated_parts_count,
            ch.channel_name,
            ch.channel_key
        FROM videos v
        LEFT JOIN videos p ON v.parent_video_id = p.id
        LEFT JOIN channels ch ON v.channel_id = ch.id
        WHERE 1=1
        """
        params = []
        if channel_id:
            query += " AND v.channel_id = ?"
            params.append(channel_id)
        if status and status != "ALL":
            query += " AND v.status = ?"
            params.append(status)
        if video_type:
            if video_type == "SHORTS":
                query += " AND v.is_short = 1"
            elif video_type == "LONG":
                query += " AND v.is_short = 0"
            elif video_type == "PARTS":
                query += " AND (v.part_number IS NOT NULL OR v.parent_video_id IS NOT NULL)"
        if parent_id:
            query += " AND v.parent_video_id = ?"
            params.append(parent_id)

        query += " ORDER BY v.id ASC"
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def get_stats(channel_id=None):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        where = "WHERE channel_id = ?" if channel_id else ""
        params = [channel_id] if channel_id else []
        cursor.execute(f"""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN is_short = 1 THEN 1 ELSE 0 END) as shorts,
            SUM(CASE WHEN is_short = 0 THEN 1 ELSE 0 END) as long_form,
            SUM(CASE WHEN part_number IS NOT NULL OR parent_video_id IS NOT NULL THEN 1 ELSE 0 END) as parts_count,
            SUM(CASE WHEN status = 'SCANNED' THEN 1 ELSE 0 END) as pending_ai,
            SUM(CASE WHEN status = 'METADATA_READY' THEN 1 ELSE 0 END) as ready_to_schedule,
            SUM(CASE WHEN status = 'SCHEDULED' THEN 1 ELSE 0 END) as scheduled,
            SUM(CASE WHEN status = 'PUBLISHED' THEN 1 ELSE 0 END) as published,
            SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed
        FROM videos
        {where}
        """, params)
        row = cursor.fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()

# ==============================================================================
# Competitor Discovery & Ingestion Persistence Layer
# ==============================================================================

def add_discovery_channel(channel_url: str, channel_name: str, handle: str = "", subscriber_count: int = 0, channel_id: Optional[int] = None) -> int:
    """
    Inserts or updates a tracked competitor channel in SQLite.
    Resolves multi-tenant channel_id dynamically.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        resolved_cid = channel_id
        if resolved_cid is None:
            act = get_active_channel()
            resolved_cid = act["id"] if act else 1

        cursor.execute("""
        INSERT INTO discovery_channels (channel_url, channel_name, handle, subscriber_count, last_scanned, channel_id)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
        ON CONFLICT(channel_url) DO UPDATE SET
            channel_name = CASE WHEN excluded.channel_name != '' THEN excluded.channel_name ELSE discovery_channels.channel_name END,
            handle = CASE WHEN excluded.handle != '' THEN excluded.handle ELSE discovery_channels.handle END,
            subscriber_count = CASE WHEN excluded.subscriber_count > 0 THEN excluded.subscriber_count ELSE discovery_channels.subscriber_count END,
            last_scanned = CURRENT_TIMESTAMP,
            channel_id = COALESCE(excluded.channel_id, discovery_channels.channel_id)
        """, (channel_url, channel_name, handle or "", subscriber_count, resolved_cid))
        conn.commit()

        cursor.execute("SELECT id FROM discovery_channels WHERE channel_url = ?", (channel_url,))
        row = cursor.fetchone()
        return row["id"] if row else 0
    finally:
        conn.close()

def save_discovery_videos(discovery_channel_id: int, items: List[Dict[str, Any]]) -> int:
    """
    Saves an array of scraped competitor video metadata items to SQLite.
    Updates the parent competitor channel's total_videos count.
    """
    if not items:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    saved = 0
    try:
        for item in items:
            yt_id = item.get("youtube_id")
            if not yt_id:
                continue
            title = item.get("title", "")
            duration = float(item.get("duration_seconds") or 0)
            views = int(item.get("view_count") or 0)
            upload_date = item.get("upload_date") or ""
            velocity = float(item.get("view_velocity") or 0.0)
            thumb = item.get("thumbnail_url") or ""
            v_url = item.get("video_url") or f"https://www.youtube.com/watch?v={yt_id}"
            is_short = 1 if item.get("is_short") else 0
            tags = item.get("tags") or []
            tags_json = json.dumps(tags) if isinstance(tags, (list, dict)) else str(tags)

            cursor.execute("""
            INSERT INTO discovery_videos (
                discovery_channel_id, youtube_id, title, duration_seconds, view_count,
                upload_date, view_velocity, thumbnail_url, video_url, is_short, tags_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(youtube_id) DO UPDATE SET
                discovery_channel_id = excluded.discovery_channel_id,
                title = excluded.title,
                duration_seconds = excluded.duration_seconds,
                view_count = excluded.view_count,
                upload_date = excluded.upload_date,
                view_velocity = excluded.view_velocity,
                thumbnail_url = excluded.thumbnail_url,
                video_url = excluded.video_url,
                is_short = excluded.is_short,
                tags_json = excluded.tags_json
            """, (discovery_channel_id, yt_id, title, duration, views, upload_date, velocity, thumb, v_url, is_short, tags_json))
            saved += 1

        cursor.execute("""
        UPDATE discovery_channels
        SET total_videos = (SELECT COUNT(*) FROM discovery_videos WHERE discovery_channel_id = ?)
        WHERE id = ?
        """, (discovery_channel_id, discovery_channel_id))

        conn.commit()
        return saved
    finally:
        conn.close()

def get_discovery_channels(channel_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Returns all tracked competitor channels with their scraped video counts.
    Filters by multi-tenant channel_id if specified.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        query = """
        SELECT 
            dc.*,
            (SELECT COUNT(*) FROM discovery_videos dv WHERE dv.discovery_channel_id = dc.id) as scraped_video_count
        FROM discovery_channels dc
        """
        params = []
        if channel_id:
            query += " WHERE (dc.channel_id = ? OR dc.channel_id IS NULL)"
            params.append(channel_id)
        query += " ORDER BY dc.last_scanned DESC, dc.id DESC"
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def get_top_discovery_videos(limit: int = 50, is_short: Optional[bool] = None, channel_id: Optional[int] = None, parent_channel_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Queries top performing competitor videos sorted by view velocity and total views.
    Filters by Shorts vs Long-form and multi-tenant channel scoping.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        scope_cid = parent_channel_id if parent_channel_id is not None else channel_id
        query = """
        SELECT 
            dv.*,
            dc.channel_name as competitor_name,
            dc.channel_url as competitor_url,
            dc.handle as competitor_handle
        FROM discovery_videos dv
        JOIN discovery_channels dc ON dv.discovery_channel_id = dc.id
        WHERE 1=1
        """
        params = []
        if scope_cid:
            query += " AND (dc.channel_id = ? OR dc.channel_id IS NULL)"
            params.append(scope_cid)
        if is_short is not None:
            query += " AND dv.is_short = ?"
            params.append(1 if is_short else 0)

        query += " ORDER BY dv.view_velocity DESC, dv.view_count DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def delete_discovery_channel(channel_id: int) -> bool:
    """
    Removes a competitor channel and all its scraped videos.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM discovery_videos WHERE discovery_channel_id = ?", (channel_id,))
        cursor.execute("DELETE FROM discovery_channels WHERE id = ?", (channel_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def delete_discovery_video(video_id: int) -> bool:
    """
    Removes a single discovery video and recalculates the competitor's total video count.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT discovery_channel_id FROM discovery_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        ch_id = row["discovery_channel_id"] if row else None

        cursor.execute("DELETE FROM discovery_videos WHERE id = ?", (video_id,))
        deleted = cursor.rowcount > 0

        if ch_id:
            cursor.execute("""
            UPDATE discovery_channels
            SET total_videos = (SELECT COUNT(*) FROM discovery_videos WHERE discovery_channel_id = ?)
            WHERE id = ?
            """, (ch_id, ch_id))

        conn.commit()
        return deleted
    finally:
        conn.close()

# ==============================================================================
# Telegram HITL Configuration Persistence Layer
# ==============================================================================

def get_telegram_config() -> Dict[str, Any]:
    """
    Retrieves Telegram Bot HITL configuration for human-in-the-loop review cards.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM telegram_config WHERE id = 1")
        row = cursor.fetchone()
        if not row:
            cursor.execute("INSERT OR IGNORE INTO telegram_config (id, bot_token, chat_id, is_enabled) VALUES (1, '', '', 0)")
            conn.commit()
            cursor.execute("SELECT * FROM telegram_config WHERE id = 1")
            row = cursor.fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()

def save_telegram_config(bot_token: str, chat_id: str, is_enabled: bool = False, notify_on_ready: bool = True, notify_on_upload: bool = True, auto_approve: bool = False) -> bool:
    """
    Upserts Telegram Bot HITL configuration.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO telegram_config (id, bot_token, chat_id, is_enabled, notify_on_ready, notify_on_upload, auto_approve, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            bot_token = excluded.bot_token,
            chat_id = excluded.chat_id,
            is_enabled = excluded.is_enabled,
            notify_on_ready = excluded.notify_on_ready,
            notify_on_upload = excluded.notify_on_upload,
            auto_approve = excluded.auto_approve,
            updated_at = CURRENT_TIMESTAMP
        """, (bot_token, chat_id, 1 if is_enabled else 0, 1 if notify_on_ready else 0, 1 if notify_on_upload else 0, 1 if auto_approve else 0))
        conn.commit()
        return True
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully at:", DB_PATH)
