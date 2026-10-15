import sys
import argparse
import json
from pathlib import Path

# Force UTF-8 on Windows console
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
from db import init_db, add_video, get_stats, get_videos_by_status, update_video_metadata
from media_inspector import inspect_video
from ai_seo import generate_video_seo
from yt_auth import run_login_flow
from scheduler import assign_schedule_slots, process_upload_queue

def cmd_scan(folder_path: str):
    folder = Path(folder_path).resolve()
    if not folder.exists() or not folder.is_dir():
        print(f"[!] Invalid folder path: {folder}")
        return

    print(f"[*] Scanning folder: {folder}")
    supported_extensions = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
    video_files = [f for f in folder.rglob("*") if f.suffix.lower() in supported_extensions]

    if not video_files:
        print("[!] No video files found in directory.")
        return

    print(f"[*] Found {len(video_files)} video file(s). Analyzing dimensions & duration...")
    added_count = 0
    for idx, vf in enumerate(video_files, 1):
        try:
            info = inspect_video(vf)
            row_id = add_video(
                file_path=vf,
                file_name=vf.name,
                file_size_mb=info["file_size_mb"],
                duration_seconds=info["duration_seconds"],
                width=info["width"],
                height=info["height"],
                is_short=info["is_short"]
            )
            type_str = "[SHORT]" if info["is_short"] else "[LONG]"
            print(f"  [{idx}/{len(video_files)}] {type_str} | {info['duration_seconds']}s | {vf.name}")
            if row_id:
                added_count += 1
        except Exception as e:
            print(f"  [!] Failed to inspect {vf.name}: {e}")

    print(f"\n[OK] Scan complete! {added_count} new video(s) added to database.")
    cmd_status()

def cmd_generate_seo(limit: int = 10):
    pending = get_videos_by_status("SCANNED")
    if not pending:
        print("[*] No pending videos need AI SEO generation.")
        return

    to_process = pending[:limit]
    print(f"[*] Generating AI SEO metadata for {len(to_process)} video(s)...")

    for idx, item in enumerate(to_process, 1):
        vid_id = item["id"]
        v_path = Path(item["file_path"])
        is_short = bool(item["is_short"])

        print(f"\n--- [{idx}/{len(to_process)}] Processing: {v_path.name} ({'Short' if is_short else 'Long-Form'}) ---")
        seo_meta = generate_video_seo(v_path, is_short=is_short)

        title = seo_meta.get("selected_title", v_path.stem)
        alts = seo_meta.get("title_alternatives", [])
        desc = seo_meta.get("description", "")
        tags = seo_meta.get("tags", [])
        hashtags = seo_meta.get("hashtags", [])

        update_video_metadata(vid_id, title, alts, desc, tags, hashtags)
        print(f"  [+] Title: {title}")
        print(f"  [+] Hashtags: {hashtags}")
        print(f"  [OK] Saved to database as METADATA_READY.")

    cmd_status()

def cmd_list(status_filter: str = None):
    videos = get_videos_by_status(status_filter)
    if not videos:
        print("[*] No videos found matching filter.")
        return

    print("\n" + "=" * 80)
    print(f" {'ID':<4} | {'TYPE':<7} | {'STATUS':<15} | {'SCHEDULED FOR':<16} | {'TITLE'}")
    print("=" * 80)
    for v in videos:
        v_type = "SHORT" if v["is_short"] else "LONG"
        sched = v["scheduled_datetime"] or "Not assigned"
        title = (v["title"] or v["file_name"])[:35]
        print(f" {v['id']:<4} | {v_type:<7} | {v['status']:<15} | {sched:<16} | {title}")
    print("=" * 80 + "\n")

def cmd_status():
    stats = get_stats()
    print("\n" + "=" * 50)
    print("      YOUTUBE AUTOMATION PIPELINE STATUS")
    print("=" * 50)
    print(f" Total Videos:       {stats.get('total', 0)}")
    print(f"   - Shorts:         {stats.get('shorts', 0)}")
    print(f"   - Long-form:      {stats.get('long_form', 0)}")
    print("-" * 50)
    print(f" Pending AI SEO:     {stats.get('pending_ai', 0)}")
    print(f" Ready to Schedule:  {stats.get('ready_to_schedule', 0)}")
    print(f" Scheduled:          {stats.get('scheduled', 0)}")
    print(f" Failed:             {stats.get('failed', 0)}")
    print("=" * 50 + "\n")

def cmd_logs(limit: int = 15):
    from log_manager import get_logs
    from config import LOGS_JSON_PATH

    logs = get_logs()
    if not logs:
        print(f"[*] No upload logs found yet in {LOGS_JSON_PATH.name}.")
        return

    print("\n" + "=" * 90)
    print(f" UPLOAD LOGS HISTORY ({len(logs)} total) - File: {LOGS_JSON_PATH}")
    print("=" * 90)
    print(f" {'#':<3} | {'LOGGED AT':<19} | {'STATUS':<7} | {'TYPE':<5} | {'SCHEDULED FOR':<16} | {'TITLE'}")
    print("-" * 90)
    for log in logs[-limit:]:
        status = log.get("status", "UNKNOWN")
        v_type = "SHORT" if log.get("video_type") == "SHORT" else "LONG"
        logged_at = (log.get("logged_at") or "")[:19].replace("T", " ")
        sched = log.get("scheduled_for", "N/A")
        title = (log.get("title") or log.get("file_name", ""))[:28]
        print(f" {log.get('log_id', 0):<3} | {logged_at:<19} | {status:<7} | {v_type:<5} | {sched:<16} | {title}")
    print("=" * 90 + "\n")

def main():
    init_db()
    parser = argparse.ArgumentParser(description="YouTube Automated AI SEO Uploader Platform")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # logs
    logs_parser = subparsers.add_parser("logs", help="Display upload logs from JSON")
    logs_parser.add_argument("--limit", type=int, default=15, help="Number of recent logs to show")

    # scan
    scan_parser = subparsers.add_parser("scan", help="Scan a directory for video files")
    scan_parser.add_argument("folder", type=str, help="Path to video folder")

    # generate-seo
    seo_parser = subparsers.add_parser("generate-seo", help="Generate AI titles and SEO descriptions")
    seo_parser.add_argument("--limit", type=int, default=10, help="Max videos to process in this run")

    # login
    subparsers.add_parser("login", help="Launch browser for one-time YouTube Studio login")

    # schedule-all
    subparsers.add_parser("schedule-all", help="Assign future calendar slots to ready videos")

    # upload
    upload_parser = subparsers.add_parser("upload", help="Upload next batch of ready videos")
    upload_parser.add_argument("--limit", type=int, default=1, help="Number of videos to upload")
    upload_parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")

    # status
    subparsers.add_parser("status", help="Show current pipeline statistics")

    # list
    list_parser = subparsers.add_parser("list", help="List videos and details")
    list_parser.add_argument("--filter", type=str, default=None, help="Filter by status (e.g. SCANNED, METADATA_READY, SCHEDULED)")

    # dashboard
    dash_parser = subparsers.add_parser("dashboard", help="Start web dashboard in browser")
    dash_parser.add_argument("--port", type=int, default=8000, help="Port to run dashboard on (default 8000)")

    # make-shorts
    shorts_parser = subparsers.add_parser("make-shorts", help="Split a video into vertical Shorts")
    shorts_parser.add_argument("video_id", type=int, help="ID of video to cut")
    shorts_parser.add_argument("--parts", type=int, default=0, help="Number of 35s parts (default 0 = whole video up to 15 mins/30 parts)")
    shorts_parser.add_argument("--no-parts-label", dest="add_parts_label", action="store_false", default=True, help="Disable 'Part: 1' banner and title labels")

    # generate-thumbnails
    thumb_parser = subparsers.add_parser("generate-thumbnails", help="Generate AI custom thumbnails with Part badges & logo")
    thumb_parser.add_argument("--type", type=str, default="SHORTS", choices=["SHORTS", "LONG", "ALL"], help="Target video type")

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args.folder)
    elif args.command == "generate-seo":
        cmd_generate_seo(args.limit)
    elif args.command == "login":
        run_login_flow()
    elif args.command == "schedule-all":
        assign_schedule_slots()
        cmd_status()
    elif args.command == "upload":
        process_upload_queue(limit=args.limit, headless=args.headless)
        cmd_status()
    elif args.command == "status":
        cmd_status()
    elif args.command == "list":
        cmd_list(args.filter)
    elif args.command == "logs":
        cmd_logs(args.limit)
    elif args.command == "dashboard":
        from dashboard import start_dashboard
        start_dashboard(port=args.port)
    elif args.command == "generate-thumbnails":
        from thumbnail_generator import generate_all_missing_thumbnails
        count = generate_all_missing_thumbnails(target_type=args.type)
        print(f"[OK] Generated {count} custom thumbnail(s) for {args.type}!")
    elif args.command == "make-shorts":
        from shorts_generator import create_shorts_from_video
        from db import get_connection
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT file_path FROM videos WHERE id = ?", (args.video_id,))
        row = c.fetchone()
        conn.close()
        if row:
            create_shorts_from_video(
                Path(row["file_path"]), 
                num_parts=args.parts, 
                add_parts_label=args.add_parts_label, 
                parent_video_id=args.video_id
            )
            assign_schedule_slots()
            cmd_status()
        else:
            print(f"[!] Video #{args.video_id} not found.")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
