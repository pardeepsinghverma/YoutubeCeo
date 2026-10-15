# Automated YouTube AI SEO & Bulk Uploader Platform

A minimal-cost ($0 recurring expenses) platform to automatically analyze, generate AI viral SEO metadata, and schedule 500+ videos (Shorts and Long-form) directly to YouTube Studio.

---

## Quick Start (3 Steps)

### Step 1: Add your Free Gemini API Key
Create a `.env` file in this directory (or edit `.env.example`):
```bash
GEMINI_API_KEY=your_gemini_api_key_here
```
> Get a free key at [Google AI Studio](https://aistudio.google.com/). Free tier allows 1,500 requests per day (more than enough for 500 videos at $0 cost).

### Step 2: One-Time YouTube Studio Login
Run the browser login command:
```bash
python main.py login
```
A browser window will open. Log into your Google / YouTube channel. Once inside YouTube Studio, press **ENTER** in your terminal. Your session will be saved locally in `chrome_profile/` so you never have to log in again.

### Step 3: Scan Your Videos & Generate AI Metadata
Point the scanner to the folder where your 500 videos are located:
```bash
python main.py scan "C:\path\to\your\500_videos"
```
The system will:
1. Automatically detect whether each video is a **Short** (vertical, <60s) or **Long-form**.
2. Register all videos in local SQLite (`videos.db`).

---

## Commands Reference

| Command | Description |
| :--- | :--- |
| `python main.py scan <folder>` | Scans folder for videos, analyzes duration/dimensions, and adds to queue. |
| `python main.py generate-seo [--limit 20]` | Transcribes audio and prompts Gemini 2.0 Flash to generate viral titles, chapters, descriptions, and tags. |
| `python main.py schedule-all` | Calculates future drip release slots (2 Shorts/day + 1 Long/day) across the calendar. |
| `python main.py list` | Displays a table of all videos, status, and scheduled dates. |
| `python main.py upload [--limit 1]` | Launches automated Playwright worker to upload and set the native YouTube schedule for the next ready videos. |
| `python main.py dashboard` | Launches interactive real-time web dashboard at `http://localhost:8000`. |
| `python main.py logs [--limit 15]` | Displays recent upload history from `upload_logs.json`. |
| `python main.py status` | Shows current summary of the 500-video pipeline. |

---

## How the AI SEO Engine Works
- **For Shorts (<60s)**: High-curiosity loop hooks, concise mobile titles (<40 chars), `#shorts` tag, and 3 targeted hashtags.
- **For Long-Form (>1min)**: High-CTR titles (<55 chars), first-200-char search hook, automated **Timestamp Chapters** (indexed by Google Search), key takeaway bullets, and tags.
