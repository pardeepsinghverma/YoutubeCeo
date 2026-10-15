import sys
import os
import json
import time
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
from dotenv import load_dotenv
from config import GEMINI_API_KEY
from media_inspector import extract_audio, extract_thumbnail_frame

load_dotenv()

SYSTEM_INSTRUCTION_SHORTS = """
You are an elite YouTube growth strategist and SEO algorithm expert specializing in YouTube Shorts.
Your goal is to maximize Click-Through Rate (CTR), viewer retention, and algorithmic reach.

Requirements for YouTube Shorts:
1. Primary Title: Under 40 characters. Punchy, curiosity-invoking, ending with '#shorts'.
2. Title Alternatives: 2 additional high-converting title options.
3. Description: 2-3 engaging sentences describing the hook and takeaway, followed by 3 targeted hashtags (e.g. #shorts, #trending, #niche).
4. Tags: Array of 8 to 12 relevant comma-separated tags.
5. Category: Default category ID (e.g. '24' for Entertainment, '28' for Tech, '27' for Education).

Return valid JSON conforming to the requested schema.
"""

SYSTEM_INSTRUCTION_LONG = """
You are an elite YouTube growth strategist and SEO algorithm expert specializing in high-reach Long-Form videos.
Your goal is to optimize for both YouTube Home/Suggested Recommendations (High CTR) and Search Discovery.

Requirements for Long-Form Videos:
1. Primary Title: Under 55 characters. The primary keyword MUST appear in the first 45 characters (to prevent mobile truncation). High curiosity or high value proposition.
2. Title Alternatives: 
   - Option A: Viral Curiosity Hook (for browse/suggested)
   - Option B: High-Volume Search SEO (for search ranking)
   - Option C: Hybrid
3. Description:
   - Line 1-2 (Above the fold, 150-200 chars): Clear hook stating what the video is and why to watch it with primary keywords.
   - Chapters / Timestamps: Structured timestamps (e.g. '0:00 Intro', '01:30 Key Concept', '04:15 Summary').
   - Summary & Key Takeaways: 3 bullet points with semantic/LSI keywords.
   - Call to Action: Friendly invite to subscribe and comment.
   - 3 Targeted Hashtags at the end.
4. Tags: Array of 12 to 18 high-intent tags (mix of exact match, long-tail phrases, and broad niche).
5. Category: Default category ID (e.g. '28' for Science & Tech, '27' for Education).

Return valid JSON conforming to the requested schema.
"""

def generate_mock_seo(file_name: str, is_short: bool) -> dict:
    """Fallback generator when GEMINI_API_KEY is not configured yet."""
    base_name = Path(file_name).stem.replace("_", " ").title()
    if is_short:
        return {
            "selected_title": f"{base_name[:30]}! #shorts",
            "title_alternatives": [
                f"{base_name[:30]}! #shorts",
                f"You Won't Believe This: {base_name[:20]} #shorts",
                f"Secret Trick for {base_name[:25]} #shorts"
            ],
            "description": f"Check out this quick breakdown on {base_name}! Don't forget to like and subscribe for daily updates.\n\n#shorts #trending #viral",
            "hashtags": ["#shorts", "#trending", "#viral"],
            "tags": [base_name.lower(), "shorts", "quick tips", "viral", "trending"],
            "category_id": "28"
        }
    else:
        return {
            "selected_title": f"The Ultimate Guide to {base_name[:30]}",
            "title_alternatives": [
                f"The Ultimate Guide to {base_name[:30]}",
                f"Why Everyone is Wrong About {base_name[:25]}",
                f"How to Master {base_name[:25]} in 2026"
            ],
            "description": f"In this video, we dive deep into {base_name}. Learn the exact framework and actionable steps to achieve results.\n\n⏱️ TIMESTAMPS:\n0:00 - Introduction\n01:15 - Core Concepts\n03:45 - Step-by-Step Breakdown\n06:20 - Final Takeaway\n\n📌 Key Takeaways:\n- Comprehensive overview of {base_name}\n- Common mistakes to avoid\n- Best practices for peak performance\n\n🔔 Subscribe for more in-depth breakdowns!\n\n#{base_name.replace(' ', '')} #Tutorial #Guide",
            "hashtags": [f"#{base_name.replace(' ', '')}", "#Tutorial", "#Guide"],
            "tags": [base_name.lower(), "tutorial", "guide", "how to", "best practices", "explained", "2026"],
            "category_id": "28"
        }

def generate_video_seo(video_path: Path, is_short: bool, extra_context: str = "") -> dict:
    """
    Analyzes the video file using Gemini 2.0 Flash to produce viral SEO metadata.
    """
    video_path = Path(video_path)
    api_key = os.getenv("GEMINI_API_KEY", "") or GEMINI_API_KEY
    
    if not api_key:
        print(f"[!] GEMINI_API_KEY not found in environment. Using smart preview template for: {video_path.name}")
        return generate_mock_seo(video_path.name, is_short)

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        
        # 1. Extract audio
        audio_file = extract_audio(video_path)
        
        # 2. Upload audio file to Gemini File API
        print(f"[*] Uploading audio for {video_path.name} to Gemini...")
        uploaded_file = client.files.upload(file=str(audio_file))

        system_instruction = SYSTEM_INSTRUCTION_SHORTS if is_short else SYSTEM_INSTRUCTION_LONG
        
        prompt = f"""
        Analyze this audio recording from the video file '{video_path.name}'.
        Video Type: {'YouTube Short (Vertical, <60s)' if is_short else 'Long-Form Video (>1min)'}
        Additional Creator Context: {extra_context if extra_context else 'None provided'}

        Generate high-performing, viral, SEO-optimized metadata.
        Return a single valid JSON object with the following fields:
        {{
            "selected_title": "string",
            "title_alternatives": ["string", "string", "string"],
            "description": "string",
            "hashtags": ["string", "string", "string"],
            "tags": ["string", "string", ...],
            "category_id": "string"
        }}
        """

        response = None
        for model_name in ["gemini-3.8-flash", "gemini-3.1-flash-lite"]:
            for attempt in range(3):
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=[uploaded_file, prompt],
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            response_mime_type="application/json",
                            temperature=0.7
                        )
                    )
                    break
                except Exception as model_err:
                    if "503" in str(model_err) or "UNAVAILABLE" in str(model_err):
                        time.sleep(2)
                        continue
                    break
            if response:
                break

        if not response or not response.text:
            raise Exception("Empty response from Gemini")

        raw_text = response.text.strip()
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()

        metadata = json.loads(raw_text)
        return metadata

    except Exception as e:
        print(f"[!] Error calling Gemini API: {e}. Falling back to preview template.")
        return generate_mock_seo(video_path.name, is_short)

if __name__ == "__main__":
    test_short = Path("test_videos/sample_short.mp4")
    result = generate_video_seo(test_short, is_short=True)
    print("SEO Metadata Result:")
    print(json.dumps(result, indent=2))
