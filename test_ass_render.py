import subprocess
from pathlib import Path
from config import FFMPEG_PATH

ass_content = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,52,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,0,2,40,40,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:05.00,Default,,0,0,0,,Teen behno ki lalchi bhabhi
Dialogue: 0,0:00:05.50,0:00:09.00,Default,,0,0,0,,Karishma, Reshma aur Manisha 3 behne hain
Dialogue: 0,0:00:09.50,0:00:13.00,Default,,0,0,0,,Unka ek bhai bhi hai Mayank
"""

ass_path = Path("shorts_output/test_style.ass")
with open(ass_path, "w", encoding="utf-8") as f:
    f.write(ass_content)

video_in = Path(r"D:\Videos\Animations\500+2D Cartoon Bundle-20260920T075834Z-1-005\500+2D Cartoon Bundle\3 बहनो की लालची भाभी _ Kahaniya in Hindi _ Stories _ Hindi Stories _ Mazedar Kahani _ Funny _ Comedy(360P).mp4")
output_test = Path("shorts_output/test_ass_short.mp4")

# Prepare safe path for ffmpeg subtitles filter on Windows
safe_sub_path = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")

filter_complex = (
    "[0:v]scale=1080:-2,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black[vbg];"
    "[vbg]drawtext=text='\"Nanad-Bhabhi Ki Ladai | Part 1\"':fontcolor=white:fontsize=46:x=(w-text_w)/2:y=240:box=1:boxcolor=black@0.6:boxborderw=12[vtxt];"
    f"[vtxt]subtitles='{safe_sub_path}'[vout]"
)

cmd = [
    FFMPEG_PATH, "-y",
    "-ss", "0", "-t", "10",
    "-i", str(video_in),
    "-filter_complex", filter_complex,
    "-map", "[vout]", "-map", "0:a?",
    "-c:v", "libx264", "-pix_fmt", "yuv420p",
    "-c:a", "aac",
    str(output_test)
]

subprocess.run(cmd, check=True)

# Extract snapshot at 7s
cmd_snap = [FFMPEG_PATH, "-y", "-ss", "7", "-i", str(output_test), "-vframes", "1", "-q:v", "2", "shorts_output/ass_snapshot.jpg"]
subprocess.run(cmd_snap, check=True)
print("ASS Snapshot created successfully!")
