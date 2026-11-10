"""
Master Autopilot Orchestrator
Coordinates the complete end-to-end autonomous publishing pipeline:
1. Ingestion / Folder Scan
2. Multimodal AI SEO Generation
3. Multi-Part Vertical Shorts Slicing (5s overlap, NVENC GPU, Hinglish subtitles, Content ID evasion)
4. Autonomous 3D AI Thumbnail Synthesis (Flux + Pillow compositing)
5. Algorithmic Scheduling (Daily time slots)
6. Zero-Quota Stealth YouTube Upload (Playwright persistent profile)
7. Human-in-the-Loop Telegram Dispatch (Mobile review cards & live alerts)
"""

import sys
import os
import time
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, Callable

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from config import BASE_DIR
from db import (
    get_connection,
    get_active_channel,
    get_channel_by_id,
    get_all_channels,
    add_video,
    update_video_metadata
)
from log_manager import step_log

class MasterAutopilot:
    def __init__(self, progress_callback: Optional[Callable[[str, int], None]] = None):
        self.progress_callback = progress_callback or (lambda msg, pct: None)

    def log(self, msg: str, level: str = "INFO", pct: int = 0):
        step_log(f"[MasterAutopilot] {msg}", level=level)
        self.progress_callback(msg, pct)

    def run_channel_pipeline(
        self,
        channel_id: int,
        raw_folder_path: Optional[str] = None,
        max_videos_to_process: int = 2,
        slice_parts: bool = True,
        parts_per_video: int = 3,
        auto_upload: bool = True,
        headless: bool = True
    ) -> Dict[str, Any]:
        """
        Executes the full master pipeline for a specific channel.
        """
        target_ch = get_channel_by_id(channel_id)
        if not target_ch:
            raise ValueError(f"Channel #{channel_id} not found in database.")

        ch_name = target_ch["channel_name"]
        self.log(f"Starting Master Autopilot for Channel '{ch_name}' (ID #{channel_id})...", "INFO", 5)

        results = {
            "channel_id": channel_id,
            "channel_name": ch_name,
            "scanned": 0,
            "seo_generated": 0,
            "shorts_created": 0,
            "thumbnails_created": 0,
            "scheduled": 0,
            "uploaded": 0,
            "errors": []
        }

        # -------------------------------------------------------------
        # STEP 1: Scan / Ingest Raw Media
        # -------------------------------------------------------------
        if raw_folder_path and Path(raw_folder_path).exists():
            self.log(f"[Step 1/6] Scanning raw folder: {raw_folder_path}...", "INFO", 10)
            from media_inspector import inspect_video
            folder = Path(raw_folder_path).resolve()
            supported = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
            media_files = [f for f in folder.rglob("*") if f.suffix.lower() in supported]

            scanned_count = 0
            for f in media_files:
                try:
                    info = inspect_video(f)
                    row_id = add_video(
                        file_path=f,
                        file_name=f.name,
                        file_size_mb=info["file_size_mb"],
                        duration_seconds=info["duration_seconds"],
                        width=info["width"],
                        height=info["height"],
                        is_short=info["is_short"],
                        channel_id=channel_id
                    )
                    if row_id:
                        scanned_count += 1
                except Exception:
                    pass
            results["scanned"] = scanned_count
            self.log(f"[+] Step 1 Complete: Ingested {scanned_count} new raw video(s).", "SUCCESS", 20)
        else:
            self.log("[Step 1/6] Ingestion skipped (no new folder path supplied). Using existing queue.", "INFO", 20)

        # -------------------------------------------------------------
        # STEP 2: Generate Multimodal AI SEO Metadata
        # -------------------------------------------------------------
        self.log(f"[Step 2/6] Generating AI SEO metadata for '{ch_name}'...", "INFO", 25)
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT id, file_path, file_name, is_short 
            FROM videos 
            WHERE (channel_id = ? OR channel_id IS NULL OR channel_id = 1) 
              AND status = 'SCANNED'
            ORDER BY id ASC 
            LIMIT ?
            """, (channel_id, max_videos_to_process))
            pending_seo = [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

        if pending_seo:
            from ai_seo import generate_video_seo
            for item in pending_seo:
                vid_id = item["id"]
                try:
                    self.log(f"Generating Gemini SEO for Video #{vid_id} ({item['file_name']})...", "INFO", 30)
                    seo = generate_video_seo(
                        video_path=Path(item["file_path"]),
                        is_short=bool(item["is_short"]),
                        channel_niche=target_ch.get("thumbnail_mood") or "Storytelling"
                    )
                    update_video_metadata(
                        video_id=vid_id,
                        title=seo.get("title", item["file_name"]),
                        title_alternatives=seo.get("title_alternatives", []),
                        description=seo.get("description", ""),
                        tags=seo.get("tags", []),
                        hashtags=seo.get("hashtags", [])
                    )
                    results["seo_generated"] += 1
                except Exception as e:
                    err_msg = f"SEO failed for #{vid_id}: {e}"
                    self.log(err_msg, "WARN", 35)
                    results["errors"].append(err_msg)
            self.log(f"[+] Step 2 Complete: Generated SEO for {results['seo_generated']} video(s).", "SUCCESS", 40)
        else:
            self.log("[Step 2/6] No pending SCANNED videos requiring SEO.", "INFO", 40)

        # -------------------------------------------------------------
        # STEP 3: Multi-Part Shorts Slicing (5s Overlap + NVENC + Subtitles)
        # -------------------------------------------------------------
        if slice_parts:
            self.log(f"[Step 3/6] Slicing long videos into 35s vertical Shorts (NVENC + Content ID evasion)...", "INFO", 45)
            from shorts_generator import create_shorts_from_video
            conn = get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                SELECT id, file_path, duration_seconds 
                FROM videos 
                WHERE (channel_id = ? OR channel_id IS NULL)
                  AND is_short = 0
                  AND id NOT IN (SELECT DISTINCT parent_video_id FROM videos WHERE parent_video_id IS NOT NULL)
                ORDER BY id ASC 
                LIMIT ?
                """, (channel_id, max_videos_to_process))
                long_videos = [dict(r) for r in cursor.fetchall()]
            finally:
                conn.close()

            for lv in long_videos:
                try:
                    self.log(f"Slicing Video #{lv['id']} ({Path(lv['file_path']).name}) into Shorts...", "INFO", 50)
                    created = create_shorts_from_video(
                        video_path=Path(lv["file_path"]),
                        num_parts=parts_per_video,
                        add_parts_label=True,
                        parent_video_id=lv["id"]
                    )
                    results["shorts_created"] += len(created)
                except Exception as e:
                    err_msg = f"Slicing failed on #{lv['id']}: {e}"
                    self.log(err_msg, "WARN", 55)
                    results["errors"].append(err_msg)
            self.log(f"[+] Step 3 Complete: Sliced {results['shorts_created']} vertical Short(s).", "SUCCESS", 60)
        else:
            self.log("[Step 3/6] Slicing skipped by configuration.", "INFO", 60)

        # -------------------------------------------------------------
        # STEP 4: Autonomous 3D AI Thumbnail Synthesis
        # -------------------------------------------------------------
        self.log(f"[Step 4/6] Checking AI Thumbnails for '{ch_name}'...", "INFO", 65)
        from thumbnail_generator import generate_thumbnail_for_video_id
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT id FROM videos 
            WHERE (channel_id = ? OR channel_id IS NULL)
              AND (thumbnail_path IS NULL OR thumbnail_path = '')
              AND (part_number IS NOT NULL OR is_short = 1)
            ORDER BY id ASC LIMIT ?
            """, (channel_id, 10))
            need_thumb = [r["id"] for r in cursor.fetchall()]
        finally:
            conn.close()

        for vid_id in need_thumb:
            try:
                self.log(f"Synthesizing 3D thumbnail for Video #{vid_id}...", "INFO", 70)
                thumb = generate_thumbnail_for_video_id(vid_id)
                if thumb and thumb.exists():
                    results["thumbnails_created"] += 1
            except Exception as e:
                err_msg = f"Thumbnail generation failed on #{vid_id}: {e}"
                self.log(err_msg, "WARN", 72)
                results["errors"].append(err_msg)
        self.log(f"[+] Step 4 Complete: Created {results['thumbnails_created']} new AI thumbnail(s).", "SUCCESS", 75)

        # -------------------------------------------------------------
        # STEP 5: Algorithmic Scheduling (Daily Time Slots)
        # -------------------------------------------------------------
        self.log(f"[Step 5/6] Assigning schedule slots based on '{ch_name}' schedule rules...", "INFO", 80)
        from scheduler import assign_schedule_slots
        import json
        daily_times = ["11:00", "17:00"]
        if target_ch.get("shorts_schedule_times"):
            try:
                daily_times = json.loads(target_ch["shorts_schedule_times"])
            except Exception:
                pass

        try:
            assigned = assign_schedule_slots(
                custom_times=daily_times,
                frequency=target_ch.get("schedule_frequency", "daily"),
                interval_days=target_ch.get("schedule_interval_days", 1),
                channel_id=channel_id,
                target_type="SHORTS"
            )
            results["scheduled"] = len(assigned)
            self.log(f"[+] Step 5 Complete: Scheduled {len(assigned)} video(s) for '{ch_name}'.", "SUCCESS", 85)
        except Exception as e:
            err_msg = f"Scheduling failed: {e}"
            self.log(err_msg, "WARN", 85)
            results["errors"].append(err_msg)

        # -------------------------------------------------------------
        # STEP 6: Zero-Quota Stealth Upload & Telegram HITL
        # -------------------------------------------------------------
        if auto_upload:
            self.log(f"[Step 6/6] Launching Zero-Quota Playwright uploader for '{ch_name}'...", "INFO", 90)
            conn = get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                SELECT id FROM videos 
                WHERE (channel_id = ? OR channel_id IS NULL)
                  AND status = 'METADATA_READY'
                  AND scheduled_datetime IS NOT NULL
                ORDER BY scheduled_datetime ASC LIMIT ?
                """, (channel_id, max_videos_to_process))
                ready_to_upload = [r["id"] for r in cursor.fetchall()]
            finally:
                conn.close()

            from scheduler import upload_single_video
            for vid_id in ready_to_upload:
                try:
                    self.log(f"Uploading Video #{vid_id} via Playwright stealth...", "INFO", 92)
                    upload_res = upload_single_video(vid_id, headless=headless, channel_id=channel_id)
                    results["uploaded"] += 1
                    self.log(f"[+] Video #{vid_id} published successfully! ({upload_res.get('status')})", "SUCCESS", 95)
                except Exception as e:
                    err_msg = f"Upload failed on #{vid_id}: {e}"
                    self.log(err_msg, "ERROR", 95)
                    results["errors"].append(err_msg)
            self.log(f"[+] Step 6 Complete: Uploaded {results['uploaded']} video(s) to YouTube.", "SUCCESS", 98)
        else:
            self.log("[Step 6/6] Auto-upload disabled. Videos remain METADATA_READY awaiting manual trigger or approval.", "INFO", 98)

        self.log(f"🎉 Master Autopilot completed for '{ch_name}'! (Uploaded: {results['uploaded']}, Scheduled: {results['scheduled']}, Shorts: {results['shorts_created']})", "SUCCESS", 100)
        return results

    def run_all_channels_pipeline(self, max_per_channel: int = 2, auto_upload: bool = True) -> list:
        """
        Sequentially runs autopilot across all active channels with complete profile isolation.
        """
        channels = get_all_channels()
        all_results = []
        self.log(f"Initiating Master Autopilot across {len(channels)} channel(s)...", "INFO", 0)

        for ch in channels:
            ch_id = ch["id"]
            try:
                res = self.run_channel_pipeline(
                    channel_id=ch_id,
                    max_videos_to_process=max_per_channel,
                    auto_upload=auto_upload
                )
                all_results.append(res)
            except Exception as e:
                self.log(f"Channel #{ch_id} ({ch.get('channel_name')}) error: {e}", "ERROR")

        self.log("All channels processed by Master Autopilot.", "SUCCESS", 100)
        return all_results

if __name__ == "__main__":
    act = get_active_channel()
    ch_id = act["id"] if act else 1
    autopilot = MasterAutopilot()
    print(f"[*] CLI: Triggering Master Autopilot for Channel #{ch_id}...")
    res = autopilot.run_channel_pipeline(channel_id=ch_id, max_videos_to_process=1, auto_upload=False)
    print("Master Autopilot Result:", res)
