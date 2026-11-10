"""
Autonomous Competitor Discovery & Ingestion Pipeline
Harvests YouTube channel metadata (videos, shorts) without downloading media streams
using yt-dlp flat-playlist extraction and computes view velocity per the autonomous blueprint.
"""

import sys
import re
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from yt_dlp import YoutubeDL
from db import add_discovery_channel, save_discovery_videos, get_top_discovery_videos

def calculate_view_velocity(view_count: int, upload_date_str: str) -> float:
    """
    Computes view velocity: views divided by days elapsed since upload.
    Identifies viral breakout videos vs older slow-accumulating videos.
    upload_date_str is in YYYYMMDD format (from yt-dlp).
    """
    if not view_count or view_count <= 0:
        return 0.0

    days_elapsed = 1.0
    if upload_date_str and len(upload_date_str) == 8:
        try:
            pub_date = datetime.strptime(upload_date_str, "%Y%m%d")
            delta = (datetime.now() - pub_date).total_seconds() / 86400.0
            days_elapsed = max(delta, 0.1)
        except Exception:
            days_elapsed = 1.0

    return round(float(view_count) / days_elapsed, 2)

def extract_channel_metadata(channel_url: str, max_items: int = 50, scan_shorts: bool = True, channel_id: int = None) -> Dict[str, Any]:
    """
    Scrapes channel video telemetry with flat playlist extraction (no media downloads).
    Traverses /videos and /shorts tabs with organic delays.
    """
    clean_url = channel_url.strip().rstrip("/")
    # Normalize base channel URL
    base_url = re.sub(r'/(videos|shorts|streams|featured|playlists)$', '', clean_url)

    targets = [f"{base_url}/videos"]
    if scan_shorts:
        targets.append(f"{base_url}/shorts")

    ydl_opts = {
        "extract_flat": True,           # Blueprint: --flat-playlist directive
        "dump_single_json": True,
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "socket_timeout": 15,
    }

    all_scraped_items = []
    channel_title = ""
    channel_handle = ""
    subscriber_count = 0

    with YoutubeDL(ydl_opts) as ydl:
        for tab_url in targets:
            try:
                print(f"[*] DiscoveryEngine: Scanning {tab_url}...")
                info = ydl.extract_info(tab_url, download=False)
                if not info:
                    continue

                if not channel_title and info.get("channel"):
                    channel_title = info.get("channel")
                elif not channel_title and info.get("uploader"):
                    channel_title = info.get("uploader")
                if not channel_handle and info.get("channel_id"):
                    channel_handle = info.get("channel_id")
                if not subscriber_count and info.get("channel_follower_count"):
                    subscriber_count = int(info.get("channel_follower_count") or 0)

                entries = info.get("entries") or []
                is_shorts_tab = "/shorts" in tab_url

                for entry in entries[:max_items]:
                    if not entry:
                        continue
                    yt_id = entry.get("id")
                    if not yt_id:
                        continue

                    v_title = entry.get("title", "")
                    duration = float(entry.get("duration") or 0)
                    views = int(entry.get("view_count") or 0)
                    upload_date = entry.get("upload_date") or ""
                    v_url = entry.get("url") or f"https://www.youtube.com/watch?v={yt_id}"

                    # Best thumbnail
                    thumb_url = entry.get("thumbnail") or ""
                    if not thumb_url and entry.get("thumbnails"):
                        thumb_url = entry["thumbnails"][-1].get("url", "")

                    is_short = is_shorts_tab or (duration > 0 and duration <= 60)
                    velocity = calculate_view_velocity(views, upload_date)

                    all_scraped_items.append({
                        "youtube_id": yt_id,
                        "title": v_title,
                        "duration_seconds": duration,
                        "view_count": views,
                        "upload_date": upload_date,
                        "view_velocity": velocity,
                        "thumbnail_url": thumb_url,
                        "video_url": v_url,
                        "is_short": is_short,
                        "tags": entry.get("tags") or []
                    })

                # Organic sleep interval to respect rate-limiting
                time.sleep(1.0)
            except Exception as tab_err:
                print(f"[-] Tab scan note on {tab_url}: {tab_err}")

    # Remove duplicates preserving order
    seen_ids = set()
    unique_items = []
    for item in all_scraped_items:
        if item["youtube_id"] not in seen_ids:
            seen_ids.add(item["youtube_id"])
            unique_items.append(item)

    # Sort by velocity descending
    unique_items.sort(key=lambda x: (x["view_velocity"], x["view_count"]), reverse=True)

    # Persist in SQLite
    ch_id = add_discovery_channel(
        channel_url=base_url,
        channel_name=channel_title or base_url.split("/")[-1],
        handle=channel_handle,
        subscriber_count=subscriber_count,
        channel_id=channel_id
    )
    saved_count = save_discovery_videos(ch_id, unique_items)

    print(f"[OK] DiscoveryEngine: Extracted {len(unique_items)} items for '{channel_title or base_url}' (Saved: {saved_count}).")
    return {
        "channel_id": ch_id,
        "channel_name": channel_title or base_url.split("/")[-1],
        "channel_url": base_url,
        "scraped_count": len(unique_items),
        "top_items": unique_items[:15]
    }

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = sys.argv[1]
        print(f"Scanning target: {target}")
        res = extract_channel_metadata(target, max_items=20)
        print(f"Scraped {res['scraped_count']} videos. Top 3 by velocity:")
        for v in res["top_items"][:3]:
            print(f"- [{v['view_velocity']} views/day] {v['title']} ({v['view_count']} views)")
    else:
        top = get_top_discovery_videos(limit=5)
        print("Top discovery videos in DB:", len(top))
