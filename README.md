# YoutubeCeo — Autonomous YouTube Multi-Tenant Studio

**Autonomous Media Engine • Multi-Channel Management • AI Hinglish Slicing • Scheduled Studio Uploads**

YoutubeCeo is an enterprise-grade YouTube automation suite that turns long-form videos into high-engagement, viral 9:16 vertical Shorts, complete with Gemini AI Hinglish subtitles, neon glow styling, automated drip scheduling, and hands-free publishing directly to YouTube Studio.

---

## 🌟 Key Features

- **Multi-Tenant Fleet Architecture**: Manage multiple isolated YouTube channels simultaneously. Each channel maintains its own independent Google session, Chrome profile, video queue, branding watermarks, and publishing schedule.
- **35-Second Temporal Slicing Engine**: Automatically slices 10–20 minute landscape videos into 35-second vertical Shorts with a 5-second overlap and hardware-accelerated NVIDIA NVENC GPU encoding.
- **Gemini AI Hinglish Subtitles & Viral Hooks**: Uses Google Gemini to transcribe spoken Hindi audio into synchronized, fast-paced Hinglish subtitles with ASS neon styling and high-converting header hooks.
- **Hands-Off Autonomous Auto-Upload**: Sequentially uploads ready Shorts to YouTube Studio one by one with safe 4-second delay buffers to prevent rate limiting, accompanied by real-time progress monitoring.
- **Interactive Web Dashboard**:
  - **In-App Toast & Confirm Modal Engine**: Replaces jarring browser alerts with sleek, glassmorphic toast notifications.
  - **50-Row Video Queue Pagination**: Smoothly handles catalogs of 500+ videos without DOM freezing or scroll lag.
  - **Floating Bulk Action Dock**: Multi-select videos for batch AI SEO, bulk scheduling, bulk status updates, and bulk deletion.
  - **Live Slicing & Upload Stepper HUD**: 4-stage visual progress monitor with instant worker cancellation.
  - **1-Click Retry**: One-click recovery on any failed video upload.
- **Competitor Radar & Discovery**: Scrapes competitor channels to identify viral outlier videos and trending topics.
- **Telegram HITL Integration**: Dispatches review cards to your Telegram Bot with video previews and instant Approve/Reject callbacks.

---

## 🛠️ System Requirements

- **Operating System**: Windows 10/11 x64 (recommended) or Linux
- **Python**: 3.10 or higher
- **FFmpeg**: Bundled with NVIDIA NVENC support (`h264_nvenc`)
- **Browser**: Google Chrome / Playwright Chromium
- **API Key**: Free Google Gemini API Key from [Google AI Studio](https://aistudio.google.com/)

---

## 🚀 Quick Start

### 1. Clone the Repository
```bash
git clone https://github.com/pardeepsinghverma/YoutubeCeo.git
cd YoutubeCeo
```

### 2. Configure Environment
Create a `.env` file in the project root:
```env
GEMINI_API_KEY=your_gemini_api_key_here
PORT=8000
HOST=127.0.0.1
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### 4. Launch the Dashboard
Start the FastAPI server:
```bash
python -m uvicorn dashboard:app --host 127.0.0.1 --port 8000 --reload
```
Open **`http://127.0.0.1:8000`** in your browser.

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

---

## 📡 REST API Reference

### Videos & Queue
- `GET /api/videos?channel_id={cid}&filter={status}&video_type={type}` — List videos with optional filtering.
- `POST /api/videos/{id}/status` — Update individual video status (`METADATA_READY`, `SCHEDULED`, etc.).
- `POST /api/videos/{id}/retry` — Reset failed video and optionally re-trigger upload.
- `POST /api/videos/bulk-delete` — Bulk delete videos and their sliced children (`{"video_ids": [...]}`).
- `POST /api/videos/bulk-status` — Bulk update status for multiple videos (`{"video_ids": [...], "status": "..."}`).

### Shorts & Slicing
- `POST /api/create-shorts` — Trigger whole-video slicing pipeline (`{"video_id": int, "num_parts": int, "add_parts_label": bool}`).
- `POST /api/auto-upload` — Launch autonomous sequential auto-upload (`{"channel_id": int, "parent_id": int, "headless": bool}`).
- `POST /api/worker/stop` — Immediately halts any running worker task, child processes, and automation browsers.

### Multi-Channel Fleet
- `GET /api/channels` — List all registered channels with authentication status.
- `POST /api/channels` — Register a new channel profile.
- `POST /api/channels/{id}/set-active` — Switch current active channel workspace.
- `DELETE /api/channels/{id}` — Delete channel and clean up its associated records.
- `POST /api/channels/{id}/reset` — Reset operational state back to zero while preserving login cookies & branding.

### Competitor Discovery & Radar
- `GET /api/discovery/channels?channel_id={cid}` — List tracked competitor channels with video counts.
- `GET /api/discovery/videos?limit={n}&is_short={bool}&channel_id={cid}` — Query top breakout videos ranked by view velocity.
- `POST /api/discovery/scan` — Harvest competitor channel metadata flat playlist (`{"channel_url": "...", "max_items": 40, "channel_id": int}`).
- `DELETE /api/discovery/channels/{id}` — Remove a tracked competitor channel and its scraped videos.
- `DELETE /api/discovery/videos/{id}` — Remove an individual competitor video.

---

## ⚠️ Critical Gotchas & Developer Guidelines

Before making any code modifications or debugging, **all developers and AI agents MUST consult [ARCHITECTURE.md](file:///c:/ReactNative/Personal/uploader/ARCHITECTURE.md)**.

1. **UTF-8 Subprocess Output**: Always pass `encoding="utf-8", errors="replace"` to any `subprocess.run` or `subprocess.Popen` call to prevent Windows `cp1252` charmap crashes on Hindi/Unicode titles.
2. **Channel Scoping**: Never assume Channel `#1` exists. Always filter queries by `channel_id`.
3. **Chrome Singleton Lock**: Clean up stale lockfiles (`lockfile`, `SingletonLock`) before spawning Playwright contexts.
4. **No Native Browser Alerts**: Use `showToast(msg, type)` and `await showConfirm(title, message)`.
