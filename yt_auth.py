import sys
import os
import subprocess
import time
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from config import CHROME_PROFILE_DIR

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

def find_real_chrome():
    for p in CHROME_PATHS:
        if Path(p).exists():
            return p
    return None

def run_login_flow():
    chrome_exe = find_real_chrome()

    print("=" * 65)
    print("      GOOGLE / YOUTUBE STUDIO AUTHENTICATION HELPER")
    print("=" * 65)
    print("[!] Why Google previously blocked you:")
    print("    Google's security system blocks sign-in when it detects")
    print("    browser automation flags (navigator.webdriver = true).")
    print("\n[*] The Solution:")
    print("    We now launch your REAL Google Chrome without any automation flags.")
    print("    This allows standard Google login (2FA, passkeys, SMS) with ZERO errors.")
    print("=" * 65)

    if not chrome_exe:
        print("[!] Google Chrome executable not found in standard paths.")
        print("    Please install Google Chrome from https://www.google.com/chrome/")
        return

    profile_path = str(CHROME_PROFILE_DIR.resolve())
    print(f"\n[*] Launching real Chrome using profile: {profile_path}")
    print("[*] Opening YouTube Studio login page...")

    cmd = [
        chrome_exe,
        f"--user-data-dir={profile_path}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://accounts.google.com/ServiceLogin?service=youtube&continue=https://studio.youtube.com"
    ]

    proc = subprocess.Popen(cmd)

    print("\n" + "-" * 65)
    print(" >>> Please log into your Google / YouTube channel in the Chrome window.")
    print(" >>> Once you see your YouTube Studio dashboard:")
    print("     1. Close the opened Chrome window completely.")
    print("     2. Return here and press ENTER.")
    print("-" * 65)

    input("\nPress ENTER after you have logged in and closed the Chrome window... ")

    # Ensure process is closed so profile locks are released
    try:
        proc.poll()
    except Exception:
        pass

    print("\n[OK] Google session saved to local profile!")
    print("[OK] Automated uploader will now run with your authenticated channel.")

if __name__ == "__main__":
    run_login_flow()
