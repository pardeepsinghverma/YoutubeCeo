import sys
import os
import subprocess
import json
from pathlib import Path
from dotenv import load_dotenv

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from google import genai
from config import FFMPEG_PATH

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# 1. Extract 35s audio
folder = Path(r"D:\Videos\Animations\500+2D Cartoon Bundle-20260920T075834Z-1-005\500+2D Cartoon Bundle")
sample_vid = list(folder.glob("*.mp4"))[0]
audio_35 = Path("shorts_output/sample_part1.mp3")
audio_35.parent.mkdir(exist_ok=True)

cmd = [FFMPEG_PATH, "-y", "-ss", "0", "-t", "35", "-i", str(sample_vid), "-vn", "-ac", "1", "-b:a", "64k", str(audio_35)]
subprocess.run(cmd, check=True)

# 2. Upload to Gemini
up = client.files.upload(file=str(audio_35))

prompt = """
Listen to this 35-second Hindi audio clip.
1. Transcribe the spoken Hindi dialogue and convert it into Hinglish (Hindi written in Roman English alphabet, e.g. 'Arre dekho kya hua', 'Yeh toh bohot galat hai').
2. Format as a standard SRT subtitle string with timestamps between 00:00:00,000 and 00:00:35,000. Keep subtitle chunks short (3-6 words per chunk) so they look punchy in video captions.
3. Suggest a punchy 1-line top hook text (in English or Hinglish, in quotes) for the header of the Short.

Return JSON:
{
    "top_hook_text": "string",
    "srt_content": "string",
    "short_title": "string #shorts"
}
"""

res = client.models.generate_content(
    model="gemini-3.1-flash-lite",
    contents=[up, prompt],
    config={"response_mime_type": "application/json"}
)

data = json.loads(res.text)
print("Top Hook:", data.get("top_hook_text"))
print("Short Title:", data.get("short_title"))
print("SRT Content:\n", data.get("srt_content"))

srt_file = Path("shorts_output/sample_part1.srt")
with open(srt_file, "w", encoding="utf-8") as f:
    f.write(data.get("srt_content", ""))
print("Saved SRT to:", srt_file)
