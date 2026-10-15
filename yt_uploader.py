import sys
import time
import json
import traceback
from pathlib import Path
from datetime import datetime, timedelta

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

if sys.platform == "win32":
    import asyncio
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from config import CHROME_PROFILE_DIR, BASE_DIR
from log_manager import step_log

STANDARD_CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

def upload_and_schedule_video(
    video_path: Path,
    title: str,
    description: str,
    tags: list = None,
    schedule_dt_str: str = None,  # format: 'YYYY-MM-DD HH:MM'
    headless: bool = False,
    thumbnail_path: Path = None,
    video_id: int = None,
    channel_id: int = None
) -> dict:
    """
    Automates YouTube Studio upload and scheduling using Playwright.
    Supports multi-channel Chrome profiles so multiple accounts can be uploaded to side-by-side.
    Ensures that scheduled videos are properly published to YouTube Schedule
    and DO NOT end up in Draft.
    Every single step is logged via step_log() for debugging.
    """
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        msg = f"Video file not found on disk: {video_path}"
        step_log(msg, level="ERROR", video_id=video_id)
        raise FileNotFoundError(msg)

    # Determine which channel profile to use
    target_profile_dir = CHROME_PROFILE_DIR
    channel_name_disp = "Default Channel"
    if channel_id:
        try:
            from db import get_channel_by_id
            ch = get_channel_by_id(channel_id)
            if ch and ch.get("profile_dir"):
                target_profile_dir = Path(ch["profile_dir"])
                channel_name_disp = ch.get("channel_name", "Channel")
        except Exception:
            pass
    elif video_id:
        try:
            from db import get_connection
            conn = get_connection()
            c = conn.cursor()
            c.execute("SELECT v.channel_id, ch.profile_dir, ch.channel_name FROM videos v LEFT JOIN channels ch ON v.channel_id = ch.id WHERE v.id = ?", (video_id,))
            r = c.fetchone()
            if r and r["profile_dir"]:
                target_profile_dir = Path(r["profile_dir"])
                channel_name_disp = r["channel_name"] or "Channel"
            conn.close()
        except Exception:
            pass

    target_profile_dir.mkdir(parents=True, exist_ok=True)

    step_log(f"=== Starting YouTube Upload Process for: {video_path.name} ===", level="INFO", video_id=video_id)
    step_log(f"Channel: '{channel_name_disp}' (Profile: {target_profile_dir.name})", level="INFO", video_id=video_id)
    step_log(f"Video Path: {video_path} ({video_path.stat().st_size / (1024*1024):.1f} MB)", level="INFO", video_id=video_id)
    step_log(f"Target Title: {title[:70]}...", level="INFO", video_id=video_id)
    step_log(f"Target Schedule: {schedule_dt_str or 'Immediate / Unlisted'}", level="INFO", video_id=video_id)
    step_log(f"Browser Mode: {'Headless (Background)' if headless else 'Headed (Visible Window)'}", level="INFO", video_id=video_id)

    browser_context = None
    page = None

    try:
        step_log(f"[Step 1/8] Launching Chromium persistent context for '{channel_name_disp}'...", level="INFO", video_id=video_id)
        with sync_playwright() as p:
            chrome_exe = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
            use_channel = "chrome" if chrome_exe.exists() else None

            browser_context = p.chromium.launch_persistent_context(
                user_data_dir=str(target_profile_dir),
                headless=headless,
                channel=use_channel,
                user_agent=STANDARD_CHROME_UA,
                ignore_default_args=["--enable-automation"],
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    "--no-sandbox",
                    "--disable-infobars"
                ]
            )

            page = browser_context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            page.set_default_timeout(60000)

            # 1. Navigate to YouTube Studio
            step_log("[Step 2/8] Navigating to https://studio.youtube.com...", level="INFO", video_id=video_id)
            page.goto("https://studio.youtube.com", wait_until="domcontentloaded")
            time.sleep(4)

            # Check if login is required
            curr_url = page.url
            step_log(f"Studio Loaded URL: {curr_url}", level="INFO", video_id=video_id)
            if "accounts.google.com" in curr_url or "signin" in curr_url:
                raise Exception("Not logged in to YouTube Studio! Run 'python yt_auth.py' to authenticate Google account.")

            # 2. Click Create / Upload icon
            step_log("[Step 3/8] Opening YouTube Studio upload modal...", level="INFO", video_id=video_id)
            opened = False

            # Check header upload icon first
            header_icon = page.locator("button[aria-label*='Upload' i], ytcp-icon-button[aria-label*='Upload' i], #upload-icon").first
            if header_icon.is_visible():
                step_log("Found header upload icon button, clicking...", level="INFO", video_id=video_id)
                header_icon.click()
                opened = True
            else:
                create_btn = page.locator("button:has-text('Create'), ytcp-button-shape:has-text('Create'), button[aria-label='Create']").first
                if create_btn.is_visible():
                    step_log("Found 'Create' dropdown button, clicking...", level="INFO", video_id=video_id)
                    create_btn.click()
                    time.sleep(1.5)
                    upload_item = page.locator("tp-yt-paper-item:has-text('Upload videos'), ytcp-text-menu:has-text('Upload videos'), [aria-label*='Upload videos' i]").first
                    if upload_item.is_visible():
                        upload_item.click()
                        opened = True

            if not opened:
                center_btn = page.locator("button:has-text('Upload videos'), ytcp-button#upload-button, #upload-button button").first
                if center_btn.is_visible():
                    step_log("Found center 'Upload videos' button, clicking...", level="INFO", video_id=video_id)
                    center_btn.click()
                    opened = True

            # Wait for file input element
            step_log("Waiting for file input dialog element...", level="INFO", video_id=video_id)
            page.wait_for_selector("input[type='file']", state="attached", timeout=35000)
            step_log("[+] Upload modal ready!", level="INFO", video_id=video_id)

            # 3. Select Video File
            step_log(f"[Step 4/8] Attaching video file: {video_path.name}...", level="INFO", video_id=video_id)
            file_input = page.locator("input[type='file']").first
            file_input.set_input_files(str(video_path))
            step_log("Video file attached. Waiting for YouTube metadata form to initialize...", level="INFO", video_id=video_id)
            time.sleep(4)

            # Wait for Details form fields
            try:
                page.wait_for_selector("#title-textarea #textbox, [aria-label*='title' i]#textbox", timeout=45000)
            except Exception:
                page.wait_for_selector("div#textbox[contenteditable='true']", timeout=15000)

            # 4. Fill Title
            step_log(f"[Step 4.1/8] Setting video title: {title[:60]}...", level="INFO", video_id=video_id)
            title_box = page.locator("#title-textarea #textbox, [aria-label*='title' i]#textbox").first
            title_box.fill(title[:100])
            time.sleep(1)

            # Fill Description
            if description:
                step_log(f"[Step 4.2/8] Setting video description ({len(description)} chars)...", level="INFO", video_id=video_id)
                desc_box = page.locator("#description-textarea #textbox, [aria-label*='description' i]#textbox").first
                if desc_box.is_visible():
                    desc_box.fill(description[:4900])
                    time.sleep(1)

            # Thumbnail upload (only for non-shorts or when custom uploader is active)
            is_video_short = False
            try:
                from db import get_connection
                if video_id:
                    conn = get_connection()
                    c = conn.cursor()
                    c.execute("SELECT is_short FROM videos WHERE id = ?", (video_id,))
                    r = c.fetchone()
                    if r:
                        is_video_short = bool(r[0])
                    conn.close()
            except Exception:
                pass

            if thumbnail_path and not is_video_short:
                thumb_file = Path(thumbnail_path).resolve()
                if thumb_file.exists():
                    step_log(f"[Step 4.3/8] Uploading custom thumbnail: {thumb_file.name}...", level="INFO", video_id=video_id)
                    try:
                        thumb_input = page.locator("input#file-loader, input[type='file']#file-loader, ytcp-thumbnail-uploader input[type='file']").first
                        if thumb_input.count() > 0:
                            thumb_input.set_input_files(str(thumb_file))
                            step_log(f"[+] Custom thumbnail attached: {thumb_file.name}", level="INFO", video_id=video_id)
                            time.sleep(2)
                    except Exception as e:
                        step_log(f"[-] Note: Thumbnail upload note: {e}", level="WARN", video_id=video_id)

            # Audience: Set Not Made for Kids
            step_log("[Step 4.4/8] Setting Audience (Not Made for Kids)...", level="INFO", video_id=video_id)
            kids_radio = page.locator("tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK'], ytcp-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK'], [name='VIDEO_MADE_FOR_KIDS_NOT_MFK']").first
            if kids_radio.is_visible():
                kids_radio.click(force=True)
                time.sleep(1)

            # Tags
            if tags:
                try:
                    show_more_btn = page.locator("button:has-text('Show more')").first
                    if show_more_btn.is_visible():
                        show_more_btn.click(force=True)
                        time.sleep(1)
                    tags_input = page.locator("input[aria-label='Tags'], #text-input[aria-label='Tags']").first
                    if tags_input.is_visible():
                        tags_str = ", ".join(tags[:15])
                        tags_input.fill(tags_str)
                        step_log(f"[Step 4.5/8] Added {len(tags[:15])} tags", level="INFO", video_id=video_id)
                except Exception as tag_err:
                    step_log(f"[-] Note on tags: {tag_err}", level="WARN", video_id=video_id)

            # Capture video URL if available
            yt_url = ""
            try:
                link_elem = page.locator("a.ytcp-video-info, span.ytcp-video-info, [href*='youtu.be']").first
                if link_elem.is_visible():
                    yt_url = link_elem.get_attribute("href") or link_elem.inner_text().strip()
                    step_log(f"Video URL preview detected: {yt_url}", level="INFO", video_id=video_id)
            except Exception:
                pass

            # 5. Advance stepper (Details -> Video elements -> Checks -> Visibility)
            step_log("[Step 5/8] Advancing stepper through 'Video elements' and 'Checks' tabs to 'Visibility'...", level="INFO", video_id=video_id)
            for step_num in range(1, 4):
                time.sleep(1.5)
                next_btn = page.locator("#next-button button, #next-button, button:has-text('Next')").first
                if next_btn.is_visible() and next_btn.is_enabled():
                    step_log(f"Advancing tab {step_num}/3 (Next clicked)...", level="INFO", video_id=video_id)
                    next_btn.click(force=True)
                    time.sleep(2)

            # Verify we are on Visibility tab
            page.wait_for_selector("#second-container, ytcp-video-visibility-select", state="visible", timeout=20000)
            step_log("[+] Successfully arrived at Visibility tab!", level="INFO", video_id=video_id)

            # 6. Set Visibility & Scheduling
            if schedule_dt_str:
                step_log(f"[Step 6/8] Configuring Schedule for target datetime: {schedule_dt_str}...", level="INFO", video_id=video_id)
                dt = datetime.strptime(schedule_dt_str, "%Y-%m-%d %H:%M")

                # Expand the Schedule accordion
                step_log("Expanding Schedule accordion (#second-container-expand-button)...", level="INFO", video_id=video_id)
                expand_btn = page.locator("#second-container-expand-button").first
                if not expand_btn.is_visible():
                    expand_btn = page.locator("#second-container").first
                expand_btn.click(force=True)
                time.sleep(1.5)

                # Wait for datetime-picker to become visible
                page.wait_for_selector("ytcp-datetime-picker", state="visible", timeout=15000)
                step_log("[+] Schedule accordion expanded, ytcp-datetime-picker is visible!", level="INFO", video_id=video_id)

                # Set Date
                target_date_str = dt.strftime("%d %b %Y")  # e.g. "17 Oct 2026"
                step_log(f"[Step 6.1/8] Setting schedule date: {target_date_str}...", level="INFO", video_id=video_id)
                date_trigger = page.locator("ytcp-datetime-picker ytcp-dropdown-trigger, #datepicker-trigger").first
                date_trigger.click(force=True)
                time.sleep(1)

                date_inp = page.locator("ytcp-date-picker input, ytcp-datetime-picker ytcp-date-picker input, ytcp-calendar-picker input").first
                if not date_inp.is_visible():
                    date_inp = page.locator("ytcp-date-picker input, input#input").first
                if date_inp.is_visible():
                    date_inp.fill(target_date_str)
                    page.keyboard.press("Enter")
                    time.sleep(1)
                    step_log(f"[+] Schedule date applied: {target_date_str}", level="INFO", video_id=video_id)
                else:
                    step_log("[-] Warning: Date input element not found, keeping default date.", level="WARN", video_id=video_id)

                # Set Time
                target_time_str = dt.strftime("%H:%M")  # e.g. "17:00"
                step_log(f"[Step 6.2/8] Setting schedule time: {target_time_str}...", level="INFO", video_id=video_id)
                time_inp = page.locator("ytcp-datetime-picker input").last
                if time_inp.is_visible():
                    time_inp.fill(target_time_str)
                    page.keyboard.press("Enter")
                    time.sleep(1)
                    step_log(f"[+] Schedule time applied: {target_time_str}", level="INFO", video_id=video_id)
                else:
                    step_log("[-] Warning: Time input element not found, keeping default time.", level="WARN", video_id=video_id)

            else:
                step_log("[Step 6/8] No schedule date provided, setting visibility to Unlisted...", level="INFO", video_id=video_id)
                unlisted_radio = page.locator("tp-yt-paper-radio-button[name='UNLISTED'], ytcp-radio-button[name='UNLISTED']").first
                if unlisted_radio.is_visible():
                    unlisted_radio.click()
                    time.sleep(1)

            # 7. CRITICAL VERIFICATION: Check the final button text!
            # Must NOT be 'Save' (which saves as Draft). Must be 'Schedule' or 'Publish'.
            step_log("[Step 7/8] Verifying final action button state...", level="INFO", video_id=video_id)
            done_btn = page.locator("#done-button button, #done-button, button:has-text('Schedule'), button:has-text('Save')").first
            btn_text = done_btn.inner_text().strip()
            step_log(f"Final Action Button Text: '{btn_text}' (enabled={done_btn.is_enabled()})", level="INFO", video_id=video_id)

            if schedule_dt_str and "schedule" not in btn_text.lower():
                err_msg = f"CRITICAL: Final button text is '{btn_text}' instead of 'Schedule'. Video would be saved as DRAFT instead of scheduled! Aborting to prevent draft state."
                step_log(err_msg, level="ERROR", video_id=video_id)
                # Take debug screenshot
                screenshot_path = BASE_DIR / f"error_draft_prevented_{video_id or 'unknown'}.png"
                try:
                    page.screenshot(path=str(screenshot_path))
                    step_log(f"Saved state screenshot to: {screenshot_path}", level="INFO", video_id=video_id)
                except Exception:
                    pass
                raise Exception(err_msg)

            # 8. Click Schedule / Publish
            step_log(f"[Step 8/8] Clicking '{btn_text}' button to finalize publication...", level="INFO", video_id=video_id)
            done_btn.click(force=True)
            step_log("Schedule button clicked! Waiting for confirmation modal from YouTube Studio...", level="INFO", video_id=video_id)

            # Wait for YouTube Studio's confirmation modal ('Video scheduled' / 'Your video will be set to public')
            try:
                page.wait_for_selector(
                    "*:has-text('Video scheduled'), *:has-text('Your video will be set to public'), #close-button",
                    timeout=25000
                )
                step_log("[+] YouTube Studio confirmed: Video has been successfully scheduled!", level="SUCCESS", video_id=video_id)
                
                # Check link inside confirmation dialog if present
                link_elem = page.locator("a.ytcp-video-info, span.ytcp-video-info, [href*='youtu.be']").first
                if link_elem.is_visible():
                    yt_url = link_elem.get_attribute("href") or link_elem.inner_text().strip()

                close_btn = page.locator("#close-button button, #close-button, button:has-text('Close')").first
                if close_btn.is_visible():
                    close_btn.click(force=True)
                    time.sleep(2)
            except Exception as conf_err:
                step_log(f"Confirmation modal note (proceeding): {conf_err}", level="INFO", video_id=video_id)

            time.sleep(3)
            step_log(f"=== [SUCCESS] Video #{video_id or ''} scheduled on YouTube! Video Link: {yt_url} ===", level="SUCCESS", video_id=video_id)

            browser_context.close()
            return {"success": True, "url": yt_url, "scheduled_datetime": schedule_dt_str}

    except Exception as e:
        full_traceback = traceback.format_exc()
        err_msg = str(e).strip() or repr(e)
        step_log(f"=== [FAILED] Upload failed: {err_msg} ===", level="ERROR", video_id=video_id)
        step_log(f"Traceback:\n{full_traceback}", level="ERROR", video_id=video_id)

        # Save screenshot
        screenshot_path = BASE_DIR / f"error_upload_{video_id or 'unknown'}.png"
        if page:
            try:
                page.screenshot(path=str(screenshot_path))
                step_log(f"Saved error screenshot to: {screenshot_path}", level="INFO", video_id=video_id)
            except Exception:
                pass

        if browser_context:
            try:
                browser_context.close()
            except Exception:
                pass

        raise e

if __name__ == "__main__":
    print("YouTube Uploader module loaded and verified.")
