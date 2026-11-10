"""
Human-in-the-Loop (HITL) Telegram Bot Orchestrator
Provides mobile-first review, thumbnail approval, title editing,
and zero-quota publishing triggers per the autonomous architectural blueprint.
"""

import sys
import os
import asyncio
from pathlib import Path
from typing import Optional

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)

from config import BASE_DIR
from db import (
    get_connection,
    get_telegram_config,
    get_stats,
    get_active_channel,
    get_all_channels,
    mark_video_scheduled,
    mark_video_failed
)

def get_bot_credentials() -> tuple[str, str, bool]:
    """Returns (token, chat_id, is_enabled) from DB or environment fallback."""
    cfg = get_telegram_config()
    token = cfg.get("bot_token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = cfg.get("chat_id") or os.getenv("TELEGRAM_CHAT_ID", "")
    is_enabled = bool(cfg.get("is_enabled", 0)) or bool(os.getenv("TELEGRAM_ENABLED", "0") in ("1", "true", "True"))
    return token.strip(), str(chat_id).strip(), is_enabled

# ==============================================================================
# Telegram Outbound Notification Helpers
# ==============================================================================

async def send_video_review_card(video_id: int) -> bool:
    """
    Sends a rich mobile review card with thumbnail preview and inline action buttons.
    """
    token, default_chat_id, enabled = get_bot_credentials()
    if not token or not default_chat_id:
        return False

    conn = get_connection()
    try:
        row = conn.cursor().execute("""
        SELECT v.*, c.channel_name 
        FROM videos v
        LEFT JOIN channels c ON c.id = v.channel_id
        WHERE v.id = ?
        """, (video_id,)).fetchone()
        if not row:
            return False
        v = dict(row)
    finally:
        conn.close()

    ch_name = v.get("channel_name") or "Default Channel"
    title = v.get("title") or v.get("file_name")
    part_num = v.get("part_number")
    sched_dt = v.get("scheduled_datetime") or "Immediate (No date)"
    thumb_path = v.get("thumbnail_path")

    header = f"🎬 *Part {part_num} Ready for Review*" if part_num else "🎬 *Video Ready for Review*"
    caption = (
        f"{header}\n\n"
        f"📌 *Title:* `{title}`\n"
        f"📺 *Channel:* {ch_name} (ID: #{v.get('channel_id') or 1})\n"
        f"⏱️ *Duration:* {v.get('duration_seconds', 0):.0f}s | *Format:* Vertical 9:16 Short\n"
        f"🗓️ *Target Schedule:* `{sched_dt}`\n\n"
        f"Tap an action below to autopilot this release:"
    )

    # Compact callback payloads (strictly <= 64 bytes)
    keyboard = [
        [
            InlineKeyboardButton("🚀 Publish Now", callback_data=f"up:{video_id}"),
            InlineKeyboardButton("🗓️ Schedule Slot", callback_data=f"sc:{video_id}")
        ],
        [
            InlineKeyboardButton("🎨 New Thumbnail", callback_data=f"th:{video_id}"),
            InlineKeyboardButton("❌ Skip / Reject", callback_data=f"rj:{video_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        from telegram import Bot
        bot = Bot(token=token)
        if thumb_path and Path(thumb_path).exists():
            with open(thumb_path, "rb") as photo_f:
                await bot.send_photo(
                    chat_id=default_chat_id,
                    photo=photo_f,
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
        else:
            await bot.send_message(
                chat_id=default_chat_id,
                text=caption,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        return True
    except Exception as e:
        print(f"[-] Telegram review card dispatch error: {e}", flush=True)
        return False

async def notify_upload_event(video_id: int, status: str, yt_url: str = "", error_msg: str = "") -> bool:
    """
    Notifies operator when upload finishes or encounters an error.
    """
    token, default_chat_id, enabled = get_bot_credentials()
    if not token or not default_chat_id or not enabled:
        return False

    conn = get_connection()
    try:
        row = conn.cursor().execute("SELECT title, file_name, channel_id FROM videos WHERE id = ?", (video_id,)).fetchone()
        v = dict(row) if row else {}
    finally:
        conn.close()

    title = v.get("title") or v.get("file_name") or f"Video #{video_id}"

    if status in ("PUBLISHED", "SUCCESS"):
        msg = (
            f"🎉 *Video Successfully Published!*\n\n"
            f"📌 *Title:* {title}\n"
            f"🔗 *Watch Link:* {yt_url or 'Live in Studio'}\n"
            f"⚡ *Status:* PUBLISHED (Live Public)"
        )
    elif status == "SCHEDULED":
        msg = (
            f"📅 *Video Successfully Scheduled!*\n\n"
            f"📌 *Title:* {title}\n"
            f"🔗 *Link:* {yt_url or 'Configured in Studio'}\n"
            f"⚡ *Status:* SCHEDULED"
        )
    else:
        msg = (
            f"⚠️ *Upload Failed on Video #{video_id}*\n\n"
            f"📌 *Title:* {title}\n"
            f"❌ *Error:* `{error_msg[:200]}`"
        )

    try:
        from telegram import Bot
        bot = Bot(token=token)
        await bot.send_message(chat_id=default_chat_id, text=msg, parse_mode="Markdown")
        return True
    except Exception as e:
        print(f"[-] Telegram notification error: {e}", flush=True)
        return False

# ==============================================================================
# Telegram Bot Callback Query Routing & State Handlers
# ==============================================================================

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Routes inline button clicks: Publish Now, Schedule, Regenerate Thumb, or Reject.
    Strictly calls query.answer() immediately to prevent client loading spinner hangs.
    """
    query = update.callback_query
    # CRITICAL BLUEPRINT REQUIREMENT: Acknowledge callback immediately
    await query.answer()

    data = query.data or ""
    if ":" not in data:
        return

    action, vid_str = data.split(":", 1)
    if not vid_str.isdigit():
        return
    video_id = int(vid_str)

    conn = get_connection()
    try:
        row = conn.cursor().execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        v = dict(row) if row else None
    finally:
        conn.close()

    if not v:
        await query.edit_message_caption(caption="⚠️ Video record not found in database.")
        return

    title = v.get("title") or v.get("file_name")

    if action == "up":
        # 1. Publish immediately
        await query.edit_message_caption(
            caption=f"🚀 *Approved for Immediate Upload!*\n\n`{title}`\n\nWorker is now launching Playwright to upload and publish live to YouTube..."
        )
        # Trigger background single video upload CLI
        asyncio.create_task(_run_upload_subprocess(video_id, channel_id=v.get("channel_id") or 1))

    elif action == "sc":
        # 2. Schedule for target slot
        sched_dt = v.get("scheduled_datetime") or "Tomorrow 11:00"
        await query.edit_message_caption(
            caption=f"🗓️ *Approved for Scheduled Release!*\n\n`{title}`\n\nQueued for automated slot: `{sched_dt}`."
        )
        asyncio.create_task(_run_upload_subprocess(video_id, channel_id=v.get("channel_id") or 1))

    elif action == "th":
        # 3. Regenerate 3D Thumbnail
        await query.edit_message_caption(
            caption=f"🎨 *Regenerating 3D AI Thumbnail...*\n\n`{title}`\n\nDispatching new prompt to Flux / Pollinations AI cluster..."
        )
        asyncio.create_task(_run_thumb_regen(video_id))

    elif action == "rj":
        # 4. Reject / Skip
        mark_video_failed(video_id, "Rejected by user via Telegram Bot")
        await query.edit_message_caption(
            caption=f"❌ *Video Skipped*\n\n`{title}` marked as Skipped/Failed."
        )

async def _run_upload_subprocess(video_id: int, channel_id: int = 1):
    """Spawns isolated upload subprocess without blocking the bot event loop."""
    try:
        cmd = [sys.executable, str(BASE_DIR / "scheduler.py"), "upload", str(video_id), "--channel", str(channel_id)]
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(BASE_DIR))
        await proc.wait()
    except Exception as e:
        print(f"[!] Subprocess upload trigger error on #{video_id}: {e}")

async def _run_thumb_regen(video_id: int):
    """Regenerates AI thumbnail and sends updated review card."""
    try:
        from thumbnail_generator import generate_thumbnail_for_video_id
        loop = asyncio.get_running_loop()
        new_thumb = await loop.run_in_executor(None, generate_thumbnail_for_video_id, video_id, True)
        if new_thumb and new_thumb.exists():
            await send_video_review_card(video_id)
    except Exception as e:
        print(f"[!] Thumbnail regen error: {e}")

# ==============================================================================
# Telegram Command Handlers (/start, /status, /queue, /channels)
# ==============================================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *Autonomous YouTube Management Bot*\n\n"
        "Your system is online and ready for Human-in-the-Loop review.\n\n"
        "Commands:\n"
        "• `/status` - Live pipeline stats and worker health\n"
        "• `/queue` - Next pending videos awaiting upload\n"
        "• `/channels` - Connected YouTube channel profiles\n"
        "• `/review <id>` - Manually trigger review card for a video"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_stats()
    act = get_active_channel()
    ch_name = act["channel_name"] if act else "None"
    
    text = (
        "📊 *System Telemetry:*\n\n"
        f"• *Active Channel:* {ch_name} (ID: #{act['id'] if act else 1})\n"
        f"• *Total Media:* {stats.get('total', 0)}\n"
        f"• *Shorts (Parts):* {stats.get('parts_count', 0)}\n"
        f"• *Ready to Schedule:* {stats.get('ready_to_schedule', 0)}\n"
        f"• *Scheduled on YouTube:* {stats.get('scheduled', 0)}\n"
        f"• *Live Published:* {stats.get('published', 0)}\n"
        f"• *Failed:* {stats.get('failed', 0)}\n\n"
        "💡 *SQLite WAL Mode:* Active (Memory-mapped 256MB)"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        rows = cursor.execute("""
        SELECT id, title, file_name, part_number, scheduled_datetime, status 
        FROM videos 
        WHERE status IN ('METADATA_READY', 'SCHEDULED')
        ORDER BY id ASC LIMIT 5
        """).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()

    if not items:
        await update.message.reply_text("✨ Queue is empty. No videos waiting.")
        return

    lines = ["📋 *Next Videos in Queue:*"]
    for it in items:
        p_txt = f" (Part {it['part_number']})" if it.get("part_number") else ""
        lines.append(f"• `#{it['id']}` *{it.get('title') or it.get('file_name')}*{p_txt}")
        lines.append(f"  └ Status: `{it['status']}` | Slot: `{it.get('scheduled_datetime') or 'Immediate'}`")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: `/review <video_id>` (e.g. `/review 159`)", parse_mode="Markdown")
        return
    vid_id = int(args[0])
    success = await send_video_review_card(vid_id)
    if not success:
        await update.message.reply_text(f"Could not dispatch review card for video #{vid_id}. Check video ID.")

# ==============================================================================
# Bot Polling / Runner
# ==============================================================================

def run_telegram_bot():
    """Starts the Telegram bot polling engine."""
    token, chat_id, enabled = get_bot_credentials()
    if not token:
        print("[!] Telegram Bot: No token configured in DB or TELEGRAM_BOT_TOKEN environment. Bot inactive.")
        return

    print(f"[*] Starting Telegram Bot polling (Target Chat ID: {chat_id or 'Any'})...")
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("review", cmd_review))
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    run_telegram_bot()
