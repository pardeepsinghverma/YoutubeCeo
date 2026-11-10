"""
Media Evasion and Hardware Acceleration Engine
Implements Content ID evasion tactics (acoustic tempo micro-shifts & visual perceptual hash disruption)
plus hardware-accelerated video encoding (NVENC / CUDA) per the autonomous blueprint.
"""

import sys
import subprocess
from typing import Dict, Any, Tuple
from config import FFMPEG_PATH

_ENCODER_CACHE = None

def detect_hardware_encoder() -> Tuple[str, list]:
    """
    Detects if NVIDIA hardware acceleration (NVENC) is operational.
    Returns (encoder_name, extra_flags).
    Fallback to CPU libx264 if NVENC is unavailable.
    """
    global _ENCODER_CACHE
    if _ENCODER_CACHE is not None:
        return _ENCODER_CACHE

    try:
        # Quick probe using standard dimension and NVENC
        probe_cmd = [
            FFMPEG_PATH, "-y",
            "-f", "lavfi", "-i", "testsrc=s=640x360:d=0.2",
            "-pix_fmt", "yuv420p",
            "-c:v", "h264_nvenc",
            "-f", "null", "-"
        ]
        res = subprocess.run(probe_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
        if res.returncode == 0:
            print("[+] MediaEngine: NVIDIA NVENC GPU hardware acceleration detected and ACTIVE!")
            _ENCODER_CACHE = ("h264_nvenc", ["-preset", "p4", "-cq", "23"])
            return _ENCODER_CACHE
    except Exception as e:
        print(f"[-] MediaEngine NVENC check note: {e}")

    print("[*] MediaEngine: Using software libx264 encoder (fast preset)")
    _ENCODER_CACHE = ("libx264", ["-preset", "fast", "-crf", "22"])
    return _ENCODER_CACHE

def build_anti_fingerprint_audio_filter(speed_factor: float = 1.035) -> str:
    """
    Acoustic evasion: Shifting tempo by ~3.5% disrupts Chromaprint / AcoustID 12-semitone chroma
    distributions over time, reducing Content ID confidence score below match thresholds.
    atempo preserves pitch naturally.
    """
    if speed_factor <= 1.0 or speed_factor > 1.15:
        speed_factor = 1.035
    return f"asetpts=PTS-STARTPTS,atempo={speed_factor:.3f}"

def build_perceptual_hash_video_filter(zoom: float = 1.025, contrast: float = 1.03, brightness: float = 0.01, saturation: float = 1.04) -> str:
    """
    Visual evasion: Subtle micro-crop & zoom + slight contrast/saturation grade
    modifies spatial pixel luminance vectors against deep-neural frame matching.
    """
    # e.g. crop central region by zoom factor and rescale back to 1080:1920
    crop_w = f"iw/{zoom:.3f}"
    crop_h = f"ih/{zoom:.3f}"
    color_eq = f"eq=contrast={contrast:.2f}:brightness={brightness:.2f}:saturation={saturation:.2f}"
    return f"crop={crop_w}:{crop_h},(iw-{crop_w})/2:(ih-{crop_h})/2,scale=1080:1920,{color_eq}"

def get_evasion_filter_chain(enable_evasion: bool = True, speed_factor: float = 1.035) -> Dict[str, Any]:
    """
    Convenience wrapper returning evasion parameters for FFmpeg processing.
    """
    encoder, enc_flags = detect_hardware_encoder()
    if not enable_evasion:
        return {
            "encoder": encoder,
            "encoder_flags": enc_flags,
            "audio_filter": "asetpts=PTS-STARTPTS",
            "video_filter": "scale=1080:1920",
            "speed_factor": 1.0
        }

    return {
        "encoder": encoder,
        "encoder_flags": enc_flags,
        "audio_filter": build_anti_fingerprint_audio_filter(speed_factor),
        "video_filter": build_perceptual_hash_video_filter(zoom=1.025),
        "speed_factor": speed_factor
    }

if __name__ == "__main__":
    enc, flags = detect_hardware_encoder()
    print("Detected Encoder:", enc, flags)
    print("Audio Evasion Filter:", build_anti_fingerprint_audio_filter())
    print("Video Evasion Filter:", build_perceptual_hash_video_filter())
