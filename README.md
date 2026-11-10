# YoutubeCeo — Autonomous YouTube Multi-Tenant Studio

**Autonomous Media Engine • Multi-Channel Management • AI Hinglish Slicing • Scheduled Studio Uploads**

[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-blue.svg)](https://github.com/pardeepsinghverma/YoutubeCeo)
[![Python](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688.svg)](https://fastapi.tiangolo.com)
[![Playwright](https://img.shields.io/badge/Automation-Playwright-orange.svg)](https://playwright.dev)
[![Gemini](https://img.shields.io/badge/AI-Google%20Gemini%20Flash-4285F4.svg)](https://aistudio.google.com)

**YoutubeCeo** is an enterprise-grade YouTube automation suite that transforms raw long-form videos into high-engagement, viral 9:16 vertical Shorts, complete with Google Gemini AI Hinglish subtitles, Content ID evasion tactics, automated drip calendar scheduling, and hands-free publishing directly to YouTube Studio without third-party API quotas.

---

## 🌟 Key Features

- **Multi-Tenant Fleet Architecture**: Manage multiple isolated YouTube channels simultaneously. Each channel maintains its own independent Google session, Chrome profile, video queue, branding watermarks, and publishing schedule.
- **35-Second Temporal Slicing Engine**: Automatically slices 10–20 minute landscape videos into 35-second vertical Shorts with a 5-second overlap and hardware-accelerated NVIDIA NVENC GPU encoding (`h264_nvenc`).
- **Gemini AI Hinglish Subtitles & Viral Hooks**: Uses Google Gemini Flash to transcribe spoken Hindi audio into synchronized, fast-paced Hinglish subtitles with ASS neon styling and high-converting header hooks.
- **Content ID & Acoustic Evasion Engine**: Shifts audio tempo by $+3.5\%$ (`atempo=1.035`) using WSOLA (Waveform Similarity Overlap-Add) and applies perceptual visual hash disruption to alter audio/video fingerprint vectors while naturally preserving vocal pitch.
- **3D Pixar AI Thumbnails**: Synthesizes 3D animated style thumbnails via Pollinations AI or enhanced FFmpeg keyframe extraction, composited with dual contrast gradient masks, circular channel logo watermarks, and "PART X" pill badges.
- **Hands-Off Autonomous Auto-Upload**: Sequentially uploads ready Shorts to YouTube Studio one by one with safe 4-second delay buffers to prevent rate limiting, accompanied by real-time progress monitoring.
- **Interactive 7-Day Visual Calendar Board**: Weekly schedule board displaying upcoming drops with day cards, Today highlight, week-by-week navigation (`< Oct 25 - Oct 31 >`), and 1-click Reschedule & Studio Upload triggers, alongside a togglable Timeline List view.
- **Zero-Alert In-App Toast & Modal Engine**: Complete elimination of native browser `alert()` and `confirm()` dialogs in favor of sleek, glassmorphic toast notifications and async confirmation modals.
- **50-Row Video Queue Pagination**: Smoothly handles catalogs of 500+ videos (`25 | 50 | 100 | All`) with search, filter, and page controls.
- **Floating Bulk Actions Dock**: Slides up when 1+ videos are selected to perform batch AI SEO, bulk scheduling, bulk status updates, and bulk deletion.
- **Live Slicing & Upload Stepper HUD**: 4-stage visual progress monitor (Audio ➔ Gemini AI ➔ 9:16 Render ➔ Studio Upload) with instant worker cancellation.
- **Competitor Radar & Discovery**: Scrapes competitor channels with yt-dlp flat playlist extraction to identify breakout viral outlier videos based on calculated view velocity ($\text{views} / \text{days}$).
- **Telegram HITL Integration**: Dispatches mobile review cards to your Telegram Bot with video previews and instant Approve/Reject inline callback buttons ($\le 64$ bytes).
- **Master Autopilot Pipeline**: 1-click fully autonomous end-to-end pipeline (Folder Scan ➔ Multimodal AI SEO ➔ NVENC Slicing ➔ 3D AI Thumbnail ➔ Calendar Scheduling ➔ YouTube Studio Upload ➔ Telegram Dispatch).

---

## 🛠️ System Requirements

- **Operating System**: Windows 10/11 x64 (recommended) or Linux
- **Python**: 3.10 or higher
- **FFmpeg**: Bundled with NVIDIA NVENC support (`h264_nvenc` or CPU `libx264`)
- **Browser**: Google Chrome / Playwright Chromium
- **API Key**: Google Gemini API Key from [Google AI Studio](https://aistudio.google.com/)

---

## 🚀 Quick Start Guide

### 1. Clone the Repository
```bash
git clone https://github.com/pardeepsinghverma/YoutubeCeo.git
cd YoutubeCeo
```

### 2. Set Up Virtual Environment & Dependencies
```bash
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

### 3. Environment Configuration (`.env`)
Create a `.env` file in the root directory:
```env
# Google Gemini API Key for AI SEO & Subtitles
GEMINI_API_KEY=your_actual_gemini_api_key_here

# Scheduling Configuration
DEFAULT_SCHEDULE_FREQUENCY=daily
SHORTS_DAILY_LIMIT=2
LONG_DAILY_LIMIT=1
SHORTS_SCHEDULE_TIMES=["11:00", "17:00"]
LONG_SCHEDULE_TIMES=["19:30"]

# Telegram HITL Review Bot (Optional)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_NOTIFICATIONS_ENABLED=false
```

### 4. Start the Dashboard Server
```bash
python main.py dashboard
```
Open your browser to: **`http://127.0.0.1:8000`**

---

## 💻 Command-Line Interface (CLI) Reference

The `main.py` entrypoint provides a full suite of administrative CLI commands:

```bash
# Scan a folder for raw video files (.mp4, .mkv, .mov)
python main.py scan "C:\Videos\Source"

# Display a formatted table of all videos in the database
python main.py list

# Generate Gemini AI SEO metadata (titles, descriptions, tags) for pending videos
python main.py generate-seo 10

# Calculate and assign calendar drip schedule slots across all pending videos
python main.py schedule-all

# Show system telemetry, queue counts, and worker status
python main.py status

# Slice a long video into 35-second vertical Shorts with Hinglish subtitles
python main.py make-shorts 1 --parts 5

# Generate 3D Pixar AI thumbnails for all videos without thumbnails
python main.py generate-thumbnails --type SHORTS

# Launch YouTube Studio upload for next N scheduled videos
python main.py upload 1 --headless

# Launch real Google Chrome to log into YouTube Studio for a channel
python main.py login channel_1

# Display recent execution log records
python main.py logs 50

# Launch the FastAPI web dashboard server
python main.py dashboard
```

---

## 📐 Signal Processing & Mathematical Models

### Temporal Video Slicing
- **Window Size**: $W = 35.0$ seconds.
- **Overlap**: $O = 5.0$ seconds.
- **Effective Stride**: $S = W - O = 30.0$ seconds.
- **Effective Duration Cap**: $T_{\text{effective}} = \min(T_{\text{total}}, 900.0)$ seconds (15 minutes).
- **Maximum Parts Count**:
  $$\text{Max Parts} = \min\left(30, \left\lfloor \frac{T_{\text{effective}} - 5.0}{30.0} \right\rfloor + 1\right)$$
- **Start Timestamp for Part $p$**:
  $$t_{\text{start}}(p) = (p - 1) \times 30.0 \quad \text{for } p \in \{1, 2, \dots, N\}$$
- **Micro-Short Protection**: Slices under $10.0$ seconds are automatically pruned.

### Content ID Evasion & Signal Perturbation
- **Acoustic WSOLA Shift**: `asetpts=PTS-STARTPTS,atempo=1.035`  
  Sped up by $+3.5\%$ using Waveform Similarity Overlap-Add. Alters phonetic peak intervals and shifts 12-semitone STFT chromagram analysis windows without modifying pitch. Decouples AcoustID and Chromaprint sub-fingerprints.
- **Visual Hash Perturbation Matrix**:  
  Crops $2.5\%$ inwards ($\frac{W}{1.025} \times \frac{H}{1.025}$), scales to $1080 \times 1920$, and applies subtle color correction (`contrast=1.03, brightness=0.01, saturation=1.04`), shifting perceptual DCT image hashes (pHash/dHash) beyond matching thresholds.

### Competitor View Velocity
$$\text{View Velocity} = \frac{\text{View Count}}{\max(\Delta t_{\text{days}}, 0.1)} \quad (\text{views per day})$$
Videos with $\text{View Velocity} \ge 10,000 \text{ views/day}$ are flagged as high-potential breakout content.

---

## 🖥️ Dashboard Views Walkthrough

| View | Purpose | Key Actions |
| :--- | :--- | :--- |
| **Mission Control** | Overview of channel KPIs, recent uploads, and attention items. | Launch Autopilot, Quick Upload, Studio Link. |
| **Video Queue** | Inventory of all long-form and short videos for the channel. | Pagination (50/page), Search, Multi-select Bulk Dock, AI SEO. |
| **Shorts & Slicing** | Categorized accordion view grouping sliced shorts under parent videos. | `[+ Slice Long Video]`, `[⚡ Auto Upload]`, `[Schedule All]`. |
| **Publishing Calendar** | Visual 7-day calendar board & timeline table of scheduled drops. | 7-Day Board, Week navigator, 1-click Reschedule & Studio Upload. |
| **Competitor Radar** | Monitor competitor channels for viral inspiration. | Scan radar, filter outliers by view velocity. |
| **Branding & AI Art** | Channel identity, logos, watermark placement, and outro styles. | Upload channel logo, toggle outro call to action. |
| **Channel Settings** | Channel details, source folder picker, and Chrome authentication. | Connect Studio, Re-authenticate, Reset or Delete Channel. |
| **Fleet Overview** | Consolidated multi-channel management grid. | Switch channel, Connect New Channel, Fleet Reset. |
| **Activity & Logs** | Real-time execution logs and Playwright upload step telemetry. | Filter logs by severity, export logs to JSON. |
| **Integrations & AI** | Telegram Bot token, chat ID configuration, and test pings. | Save bot config, send test verification ping. |
| **Terminal & Runner** | Integrated web terminal for running local CLI commands. | Run CLI scripts, inspect outputs, kill running processes. |

---

## 📡 REST API Reference (63 Endpoints)

### 1. Videos & Queue Management
- `GET /api/videos` — List videos with multi-tenant filtering (`channel_id`, `filter`, `video_type`, `parent_id`).
- `POST /api/videos/{id}/status` — Update individual video status (`METADATA_READY`, `SCHEDULED`, etc.).
- `POST /api/videos/{id}/retry` — Reset failed video and optionally re-trigger upload.
- `DELETE /api/videos/{id}` — Delete a video and its sliced children from DB and disk.
- `DELETE /api/videos/parent/{parent_id}/parts` — Delete all sliced parts belonging to a parent video.
- `POST /api/videos/bulk-delete` — Bulk delete videos (`{"video_ids": [...]}`).
- `POST /api/videos/bulk-status` — Bulk update status for multiple videos (`{"video_ids": [...], "status": "..."}`).
- `POST /api/assign-videos-channel` — Assign a batch of videos to a specific channel (`{"video_ids": [...], "channel_id": int}`).
- `GET /api/video-file/{id}` — Stream video file directly to browser for preview.
- `POST /api/scan-folder` — Recursively scan folder for video files.

### 2. Shorts & Slicing
- `POST /api/create-shorts` — Trigger whole-video slicing pipeline (`{"video_id": int, "num_parts": int, "add_parts_label": bool}`).
- `POST /api/auto-upload` — Launch autonomous sequential auto-upload (`{"channel_id": int, "parent_id": int, "headless": bool}`).
- `POST /api/upload-single-video` — Trigger one-shot Studio upload for a single video (`{"video_id": int, "channel_id": int}`).
- `POST /api/upload-next` — Batch upload next $N$ pending videos for active channel.
- `POST /api/worker/stop` — Immediately halts any running worker task, child processes, and automation browsers.
- `GET /api/worker/status` — Read live worker state, progress percentage, and current step.

### 3. Multi-Channel Fleet & Profiles
- `GET /api/channels` — List all registered channels with authentication status.
- `POST /api/channels/create` — Register a new channel profile.
- `POST /api/channels/{id}/update` — Update channel name, handle, frequency, and branding.
- `DELETE /api/channels/{id}` — Delete channel and clean up its associated records with fallback re-assignment.
- `POST /api/channels/{id}/set-active` — Switch current active channel workspace.
- `POST /api/channels/{id}/login` — Launch real Chrome instance for signing in to Google/YouTube Studio.
- `POST /api/channels/{id}/verify` — Verify Google session cookies directly from profile store (<50ms).
- `POST /api/channels/{id}/source-folder` — Save default raw media folder for this channel.
- `POST /api/channels/{id}/reset` — Reset operational state back to zero while preserving login cookies & branding.

### 4. Channel Branding & AI Art
- `GET /api/channel-settings` — Retrieve active channel branding and schedule settings.
- `POST /api/channel-settings` — Save branding options (watermark, outro duration, logo opacity).
- `POST /api/upload-logo` — Upload channel watermark logo file (PNG/JPG).
- `GET /api/logo-image` — Serve current channel logo watermark image.

### 5. AI SEO & 3D Pixar Thumbnails
- `POST /api/generate-seo` — Generate Gemini AI SEO metadata for a batch or specific videos.
- `POST /api/generate-thumbnail` — Generate 3D Pixar AI thumbnail for a video (synchronous).
- `POST /api/generate-single-thumbnail/{id}` — Generate 3D Pixar AI thumbnail in background.
- `POST /api/generate-all-thumbnails` — Generate missing thumbnails for all videos in channel.
- `GET /api/thumbnail/{video_id}` — Serve generated thumbnail image.
- `GET /api/thumbnail-prompt/{video_id}` — View synthesized visual prompt for thumbnail.

### 6. Calendar & Smart Scheduling
- `POST /api/schedule-plan` — Calculate and commit schedule slot assignments.
- `POST /api/schedule-preview` — Generate day-by-day preview of scheduled drops.
- `POST /api/schedule-all` — Commit algorithmic schedule slots across pending videos.
- `POST /api/update-video-schedule` — Manually adjust date and time for an individual video.

### 7. Competitor Discovery & Trend Radar
- `GET /api/discovery/channels` — List tracked competitor channels with video counts.
- `GET /api/discovery/videos` — Query top breakout videos ranked by view velocity.
- `POST /api/discovery/scan` — Harvest competitor channel metadata flat playlist.
- `DELETE /api/discovery/channels/{id}` — Remove a tracked competitor channel and its scraped videos.
- `DELETE /api/discovery/videos/{id}` — Remove an individual competitor video.

### 8. Master Autopilot Orchestrator
- `POST /api/master/run` — Launch complete hands-off 7-stage autonomous pipeline.

### 9. Telegram HITL Integration
- `GET /api/telegram/config` — Retrieve Telegram Bot token and notification settings.
- `POST /api/telegram/config` — Save Telegram Bot credentials and review preferences.
- `POST /api/telegram/test` — Send connection test message to Telegram chat.
- `POST /api/telegram/send-review/{video_id}` — Dispatch interactive review card to Telegram.

### 10. System Status & OS Terminal
- `GET /api/system-status` — Return active channel stats, worker daemon state, and queue counts.
- `GET /api/logs` — Retrieve recent activity logs.
- `GET /api/live-upload-logs` — Retrieve Playwright upload telemetry step logs.
- `POST /api/run-command` — Execute CLI command in backend OS terminal.
- `GET /api/terminal-logs` — Read live output buffer from web terminal.
- `POST /api/terminal-clear` — Clear terminal buffer.
- `POST /api/terminal-kill` — Terminate active process running in web terminal.
- `GET /` — Serve main SPA dashboard HTML (`templates/index.html`).
- `GET /docs` — Interactive Swagger API documentation.
- `GET /openapi.json` — OpenAPI schema JSON specification.

---

## 🛠️ Troubleshooting & Frequently Asked Questions

### 1. `charmap codec can't decode byte 0x81` Error on Windows
- **Cause**: Windows default encoding for subprocesses is `cp1252` (`charmap`). When FFmpeg or Python prints Hindi titles or Unicode characters, the subprocess crashes.
- **Solution**: All subprocesses in YoutubeCeo are enforced with `encoding="utf-8", errors="replace"`. If writing custom scripts, always specify this parameter.

### 2. Chrome Automation Crashes with `Target page, context or browser has been closed`
- **Cause**: Chrome locks profile directories using `SingletonLock` and `SingletonSocket`. If another Chrome instance is using the profile, Playwright fails.
- **Solution**: Close any manual Chrome windows using that profile. YoutubeCeo automatically deletes stale lockfiles before launching, but ensure no background Chrome processes are stuck.

### 3. Slicing Fails with `NVENC not found`
- **Cause**: The machine does not have an NVIDIA GPU or the NVIDIA graphics driver does not support NVENC.
- **Solution**: YoutubeCeo automatically detects NVENC at runtime via a test probe; if NVENC is unavailable, it gracefully falls back to CPU encoding (`libx264 -preset fast -crf 22`).

### 4. YouTube Upload Stops at "Save" Button
- **Cause**: YouTube Studio requires all mandatory fields to be satisfied before the button turns into "Schedule" or "Publish". If an element is missing, YouTube displays a disabled "Save" button.
- **Solution**: YoutubeCeo enforces an **Anti-Draft Assertion**: if the button says "Save", it aborts the upload and logs the validation issue rather than creating an unlisted draft.

---

## ⚠️ Critical Engineering Invariants

Before making any code modifications or running diagnostic scripts, **all developers and AI agents MUST consult [ARCHITECTURE.md](file:///c:/ReactNative\Personal\uploader\ARCHITECTURE.md)**:

1. **UTF-8 Subprocess Output**: Always pass `encoding="utf-8", errors="replace"` in all `subprocess.run`, `subprocess.Popen`, and `open()` calls.
2. **Multi-Tenant Channel Isolation**: Never assume Channel `#1` exists. Always filter by `channel_id`.
3. **Chrome Singleton Lock**: Clean up stale lockfiles (`lockfile`, `SingletonLock`, `SingletonSocket`) before spawning Playwright contexts.
4. **No Native Browser Alerts**: Use `showToast(msg, type)` and `await showConfirm(title, message)`.
5. **Worker Cancellation**: Always check `AUTO_UPLOAD_STOP` in loops and assign active subprocesses to `CURRENT_WORKER_PROC`.
