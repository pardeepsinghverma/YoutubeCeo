# AI Agent Operational Rules & Guidelines — YoutubeCeo

> [!IMPORTANT]
> **CRITICAL RULE FOR ALL AI AGENTS**:
> Before making ANY code modifications, schema changes, or running diagnostic scripts in this codebase, you **MUST FIRST READ** the following architectural documents:
> 1. `ARCHITECTURE.md` — Complete system architecture, database schema, media pipeline, and core invariants.
> 2. `README.md` — API endpoints, CLI reference, and operational guides.

---

## 1. Core Architectural Invariants

### Invariant 1: Windows Subprocess UTF-8 Encoding
- **The Pitfall**: On Windows, Python's default encoding for `subprocess.run(..., text=True)` is `cp1252` (`charmap`).
- **The Crash**: Any FFmpeg, ffprobe, or python subprocess output containing Hindi or non-ASCII characters (e.g., byte `0x81`) will crash with:
  `'charmap' codec can't decode byte 0x81 in position ...: character maps to <undefined>`.
- **The Mandate**: You MUST always specify `encoding="utf-8", errors="replace"` in every `subprocess.run`, `subprocess.Popen`, and `open()` call that reads text.

### Invariant 2: Multi-Tenant Channel Isolation
- **The Pitfall**: Assuming `channel_id = 1` is always present or hardcoding `1`. Channel 1 can be deleted by the user!
- **The Mandate**:
  - Always resolve active channel dynamically via `get_active_channel()` or `get_channel_by_id(channel_id)`.
  - Always filter queries in `db.py` by `channel_id = ?`.
  - Never allow deleting the last remaining channel in the system (`COUNT(*) > 1`).
  - When deleting a channel, reassign active status to a fallback remaining channel.

### Invariant 3: Chrome Profile Singleton & Concurrency
- **The Pitfall**: Playwright `launch_persistent_context` fails or closes unexpectedly (`Target page, context or browser has been closed`) if another Chrome instance is using the same `user_data_dir`, or if stale lock files exist.
- **The Mandate**:
  - Clean up `lockfile`, `SingletonLock`, and `SingletonSocket` before calling `launch_persistent_context`.
  - Terminate orphaned Chrome processes if needed.

### Invariant 4: No Native Browser Alerts (`alert()` / `confirm()`)
- **The Pitfall**: Native browser `alert()` and `confirm()` freeze the browser execution thread, look like Windows system errors, and break UX.
- **The Mandate**:
  - In frontend code (`templates/index.html`), NEVER call `window.alert()` or `window.confirm()`.
  - Always use `showToast(message, type)` for notifications.
  - Always use `await showConfirm(title, message, confirmText, isDanger)` for confirmation modals.

### Invariant 5: Worker Process Supervision & Cancellation
- **The Mandate**:
  - Any long-running subprocess must assign its handle to `CURRENT_WORKER_PROC`.
  - Check `AUTO_UPLOAD_STOP` periodically in sequential loops.
  - Endpoint `POST /api/worker/stop` must terminate `CURRENT_WORKER_PROC`, stop orphan Chrome instances, and set `WORKER_STATE["is_busy"] = False`.

---

## 2. Directory Layout & Key Files

- `dashboard.py`: FastAPI server, REST API, worker daemon, and background task handlers.
- `db.py`: SQLite persistence layer, multi-channel logic, telegram config, and competitor discovery tables.
- `shorts_generator.py`: Video slicing, NVENC hardware detection, Gemini AI Hinglish subtitles, ASS subtitle styling.
- `yt_uploader.py`: Playwright browser automation for YouTube Studio scheduling.
- `yt_auth.py`: Direct Chrome launch and login flow for Google Account authentication.
- `scheduler.py`: Calendar drip-schedule calculation, slot assignment, and queue processing.
- `channel_manager.py`: Multi-tenant Chrome profile manager and session cookie heartbeat.
- `channel_settings.py`: Channel branding, logo watermark, outro settings, and thumbnail art prompts.
- `media_inspector.py`: Video telemetry analysis (ffprobe, duration, aspect ratio, audio extraction).
- `media_evasion.py`: NVIDIA NVENC hardware detection and anti-fingerprint acoustic/visual filters.
- `discovery_engine.py`: Competitor radar, yt-dlp flat playlist scraping, view velocity metrics.
- `master_autopilot.py`: Complete autonomous ingestion-to-upload pipeline orchestrator.
- `telegram_bot.py`: Telegram Bot HITL (human-in-the-loop) review cards and remote control.
- `ai_seo.py`: Multimodal SEO metadata generation with Gemini.
- `thumbnail_generator.py`: 3D Pixar animated AI thumbnail prompts & Pillow compositing.
- `log_manager.py`: Real-time structured activity logging and live upload step telemetry.
- `templates/index.html`: Responsive SPA dashboard (Pagination, Toast Engine, 7-Day Calendar Board, Bulk Actions, Stepper HUD).
