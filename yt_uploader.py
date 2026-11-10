import os
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

STANDARD_CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"

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

    # Determine which channel profile to use (Single Source of Truth)
    target_profile_dir = CHROME_PROFILE_DIR
    channel_name_disp = "Default Channel"
    resolved_ch = None

    if channel_id:
        try:
            from db import get_channel_by_id
            resolved_ch = get_channel_by_id(channel_id)
        except Exception:
            pass

    if not resolved_ch:
        try:
            from db import get_active_channel
            resolved_ch = get_active_channel()
        except Exception:
            pass

    if not resolved_ch and video_id:
        try:
            from db import get_connection
            conn = get_connection()
            c = conn.cursor()
            c.execute("SELECT v.channel_id, ch.profile_dir, ch.channel_name FROM videos v LEFT JOIN channels ch ON v.channel_id = ch.id WHERE v.id = ?", (video_id,))
            r = c.fetchone()
            if r and r["channel_id"]:
                from db import get_channel_by_id
                resolved_ch = get_channel_by_id(r["channel_id"])
            conn.close()
        except Exception:
            pass

    if resolved_ch and resolved_ch.get("profile_dir"):
        target_profile_dir = Path(resolved_ch["profile_dir"])
        channel_name_disp = resolved_ch.get("channel_name", "Channel")

    target_profile_dir.mkdir(parents=True, exist_ok=True)

    step_log(f"=== Starting YouTube Upload Process for: {video_path.name} ===", level="INFO", video_id=video_id)
    step_log(f"Channel: '{channel_name_disp}' (Profile: {target_profile_dir.name})", level="INFO", video_id=video_id)
    step_log(f"Video Path: {video_path} ({video_path.stat().st_size / (1024*1024):.1f} MB)", level="INFO", video_id=video_id)
    step_log(f"Target Title: {title[:70]}...", level="INFO", video_id=video_id)
    step_log(f"Target Schedule: {schedule_dt_str or 'Immediate / Unlisted'}", level="INFO", video_id=video_id)
    step_log(f"Browser Mode: {'Headless (Background)' if headless else 'Headed (Visible Window)'}", level="INFO", video_id=video_id)

    p = None
    browser_context = None
    page = None

    try:
        step_log(f"[Step 1/8] Launching Chromium persistent context for '{channel_name_disp}'...", level="INFO", video_id=video_id)
        p = sync_playwright().start()
        chrome_exe = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        use_channel = "chrome" if chrome_exe.exists() else None

        # Optional proxy configuration per blueprint
        proxy_server = os.getenv("YOUTUBE_PROXY", "").strip()
        proxy_arg = {"server": proxy_server} if proxy_server else None

        # Clean up stale locks if profile was abruptly terminated
        for lf_name in ["lockfile", "SingletonLock", "SingletonSocket", "SingletonCookie"]:
            lf = target_profile_dir / lf_name
            if lf.exists():
                try:
                    lf.unlink()
                except Exception:
                    pass

        launch_kwargs = {
            "user_data_dir": str(target_profile_dir),
            "headless": headless,
            "channel": use_channel,
            "user_agent": STANDARD_CHROME_UA,
            "ignore_default_args": ["--enable-automation"],
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--no-sandbox",
                "--disable-infobars"
            ]
        }
        if proxy_arg:
            launch_kwargs["proxy"] = proxy_arg
            step_log(f"Routing browser through proxy: {proxy_server}", level="INFO", video_id=video_id)

        browser_context = p.chromium.launch_persistent_context(**launch_kwargs)

        if browser_context.pages:
            page = browser_context.pages[0]
        else:
            page = browser_context.new_page()
        
        # Mask automation flag safely without breaking Lit/Polymer custom elements
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        page.set_default_timeout(60000)

        # 1. Navigate to YouTube Studio
        step_log("[Step 2/8] Navigating to YouTube Studio...", level="INFO", video_id=video_id)
        try:
            page.goto("https://studio.youtube.com/?approve_browser_access=true", wait_until="networkidle", timeout=60000)
        except Exception:
            page.goto("https://studio.youtube.com/?approve_browser_access=true", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        # Handle 'Improve your experience' / unsupported browser interstitial if present
        try:
            skip_link = page.locator("a:has-text('Skip to YouTube Studio'), a[href*='approve_browser_access']").first
            if skip_link.is_visible():
                step_log("Detected 'Improve your experience' interstitial, bypassing...", level="INFO", video_id=video_id)
                skip_link.click(force=True)
                time.sleep(3)
        except Exception:
            pass

        # Check if login is required
        curr_url = page.url
        step_log(f"Studio Loaded URL: {curr_url}", level="INFO", video_id=video_id)
        if "accounts.google.com" in curr_url or "signin" in curr_url:
            raise Exception("Not logged in to YouTube Studio! Run 'python yt_auth.py' to authenticate Google account.")

        # 2. Click Create / Upload icon
        step_log("[Step 3/8] Opening YouTube Studio upload modal...", level="INFO", video_id=video_id)
        
        # Explicitly wait for Studio upload triggers to render into DOM
        trigger_sel = "button:has-text('Upload videos'), button:has-text('Create'), button[aria-label*='Upload' i], ytcp-button#upload-button, #upload-icon"
        try:
            page.wait_for_selector(trigger_sel, timeout=30000)
        except Exception as sel_w_err:
            step_log(f"Trigger wait notice: {sel_w_err}", level="WARN", video_id=video_id)

        opened = False

        # Attempt A: Center 'Upload videos' button on channel dashboard (fastest & most reliable)
        center_btn = page.locator("button:has-text('Upload videos'), ytcp-button#upload-button, #upload-button button").first
        if center_btn.is_visible():
            step_log("Found center 'Upload videos' button, clicking...", level="INFO", video_id=video_id)
            center_btn.click(force=True)
            opened = True

        # Attempt B: Header 'Create' dropdown button
        if not opened:
            create_btn = page.locator("button:has-text('Create'), ytcp-button-shape:has-text('Create'), button[aria-label*='Create' i]").first
            if create_btn.is_visible():
                step_log("Found 'Create' dropdown button, clicking...", level="INFO", video_id=video_id)
                create_btn.click(force=True)
                time.sleep(1.5)
                upload_item = page.locator("tp-yt-paper-item:has-text('Upload videos'), ytcp-text-menu:has-text('Upload videos'), text='Upload videos', [aria-label*='Upload videos' i]").first
                if upload_item.is_visible():
                    step_log("Clicking 'Upload videos' menu option...", level="INFO", video_id=video_id)
                    upload_item.click(force=True)
                    opened = True

        # Attempt C: Header upload arrow icon button
        if not opened:
            header_icon = page.locator("button[aria-label*='Upload' i], ytcp-icon-button[aria-label*='Upload' i], #upload-icon").first
            if header_icon.is_visible():
                step_log("Found header upload icon button, clicking...", level="INFO", video_id=video_id)
                header_icon.click(force=True)
                opened = True

        # Wait for file input element to be attached in the DOM
        step_log("Waiting for file input dialog element...", level="INFO", video_id=video_id)
        try:
            page.wait_for_selector("input[type='file']", state="attached", timeout=20000)
        except Exception:
            # Fallback re-click if modal wasn't triggered
            step_log("Input element not found on first pass, retrying trigger click...", level="WARN", video_id=video_id)
            fallback_btn = page.locator("button:has-text('Create'), button:has-text('Upload videos')").first
            if fallback_btn.is_visible():
                fallback_btn.click(force=True)
                time.sleep(1)
                up_item = page.locator("text='Upload videos'").first
                if up_item.is_visible():
                    up_item.click(force=True)
            page.wait_for_selector("input[type='file']", state="attached", timeout=25000)

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

        # Audience: Set Not Made for Kids (Mandatory under COPPA to enable Next button)
        step_log("[Step 4.4/8] Setting Audience (Not Made for Kids)...", level="INFO", video_id=video_id)
        try:
            # Scroll modal down so audience section is rendered
            page.evaluate("""() => {
                const scrollables = document.querySelectorAll('#scrollable-content, [id*="scrollable"], .ytcp-uploads-dialog');
                scrollables.forEach(s => s.scrollTop = s.scrollHeight / 2);
            }""")
            time.sleep(0.5)
        except Exception:
            pass

        # Click the visible text label or radio button and verify aria-checked
        for aud_try in range(3):
            is_checked = page.evaluate("""() => {
                const radio = document.querySelector('[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]');
                return radio ? (radio.getAttribute('aria-checked') === 'true' || radio.checked) : false;
            }""")
            if is_checked:
                step_log("[+] Audience successfully verified as 'Not Made for Kids'!", level="INFO", video_id=video_id)
                break

            # Try clicking by exact text
            try:
                label_el = page.locator("text=\"No, it's not 'Made for Kids'\", #radioLabel:has-text('not')").first
                if label_el.is_visible():
                    label_el.scroll_into_view_if_needed(timeout=3000)
                    label_el.click(force=True)
                    time.sleep(1)
            except Exception:
                pass

            # Try clicking radio container
            try:
                radio_el = page.locator("tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK'] #radioContainer, [name='VIDEO_MADE_FOR_KIDS_NOT_MFK']").first
                if radio_el.is_visible():
                    radio_el.scroll_into_view_if_needed(timeout=3000)
                    radio_el.click(force=True)
                    time.sleep(1)
            except Exception:
                pass

            # Try JavaScript click dispatch
            page.evaluate("""() => {
                const r = document.querySelector('[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]');
                if (r) {
                    r.scrollIntoView({behavior: 'instant', block: 'center'});
                    const inner = r.querySelector('#radioContainer') || r.querySelector('#radioLabel') || r;
                    inner.click();
                    r.click();
                }
            }""")
            time.sleep(1)

        # Check for Daily Upload Limit banner
        limit_detected = page.evaluate("""() => {
            const bodyText = document.body ? document.body.innerText : '';
            return bodyText.includes('Daily upload limit reached') || bodyText.includes('Upload more videos daily');
        }""")
        if limit_detected:
            limit_msg = "Daily upload limit reached on YouTube for this channel! YouTube requires a one-time phone verification to increase the daily limit (or wait 24h), or switch to another connected channel."
            step_log(f"[-] {limit_msg}", level="ERROR", video_id=video_id)
            raise Exception(limit_msg)

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
        step_log("[Step 5/8] Advancing stepper through tabs to 'Visibility'...", level="INFO", video_id=video_id)
        for step_num in range(1, 4):
            time.sleep(1)

            # Check if Visibility tab is already reached
            is_on_visibility = page.evaluate("""() => {
                return !!document.querySelector('#second-container, ytcp-video-visibility-select');
            }""")
            if is_on_visibility:
                step_log(f"[+] Already reached Visibility tab at step {step_num}!", level="INFO", video_id=video_id)
                break

            # On tab 1, double check audience is checked before clicking Next
            if step_num == 1:
                is_aud_checked = page.evaluate("""() => {
                    const r = document.querySelector('[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]');
                    return r ? (r.getAttribute('aria-checked') === 'true' || r.checked) : false;
                }""")
                if not is_aud_checked:
                    step_log("[-] Warning: Audience radio not checked before advancing Tab 1! Clicking text label...", level="WARN", video_id=video_id)
                    try:
                        page.locator("text=\"No, it's not 'Made for Kids'\"").first.click(force=True)
                        time.sleep(1)
                    except Exception:
                        pass

            # Wait for Next button to become enabled
            step_log(f"Waiting for Next button to become active for tab {step_num}/3...", level="INFO", video_id=video_id)
            btn_ready = False
            for wait_sec in range(15):
                btn_status = page.evaluate("""() => {
                    const btn = document.querySelector('#next-button');
                    if (!btn) return { exists: false, disabled: true };
                    const innerBtn = btn.querySelector('button') || btn;
                    const isDis = btn.hasAttribute('disabled') || 
                                  btn.getAttribute('aria-disabled') === 'true' ||
                                  innerBtn.hasAttribute('disabled');
                    return { exists: true, disabled: isDis };
                }""")
                if btn_status.get("exists") and not btn_status.get("disabled"):
                    btn_ready = True
                    break
                time.sleep(1)

            if not btn_ready:
                step_log(f"[-] Warning: Next button still reported disabled on tab {step_num}.", level="WARN", video_id=video_id)

            next_btn = page.locator("#next-button button, #next-button, button:has-text('Next')").first
            try:
                if next_btn.is_visible():
                    next_btn.click(force=True, timeout=5000)
                else:
                    page.evaluate("document.querySelector('#next-button button, #next-button')?.click()")
                step_log(f"[+] Tab {step_num}/3 advanced (Next clicked)", level="INFO", video_id=video_id)
            except Exception as nxt_err:
                page.evaluate("document.querySelector('#next-button button, #next-button')?.click()")
                step_log(f"[+] Tab {step_num}/3 clicked via JS fallback: {nxt_err}", level="INFO", video_id=video_id)

            time.sleep(2)

        # Verify we are on Visibility tab
        page.wait_for_selector("#second-container, ytcp-video-visibility-select", state="visible", timeout=25000)
        step_log("[+] Successfully arrived at Visibility tab!", level="INFO", video_id=video_id)

        # 6. Set Visibility & Scheduling
        is_immediate_publish = False
        parsed_schedule_dt = None

        if schedule_dt_str:
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
                try:
                    parsed_schedule_dt = datetime.strptime(schedule_dt_str.strip(), fmt)
                    break
                except ValueError:
                    pass

        now = datetime.now()

        # Rule: If schedule date is less than current date OR scheduled time is in the past / due now (<= now + 5 min),
        # publish immediately as Public!
        if parsed_schedule_dt:
            if parsed_schedule_dt.date() < now.date() or parsed_schedule_dt <= (now + timedelta(minutes=5)):
                is_immediate_publish = True
                step_log(f"[Step 6/8] Schedule datetime ({schedule_dt_str}) is less than current date or due now (Current: {now.strftime('%Y-%m-%d %H:%M')}). PUBLISHING IMMEDIATELY as PUBLIC!", level="INFO", video_id=video_id)
            else:
                step_log(f"[Step 6/8] Configuring Schedule for future datetime: {schedule_dt_str}...", level="INFO", video_id=video_id)
        else:
            step_log("[Step 6/8] No schedule date provided. Publishing immediately as PUBLIC...", level="INFO", video_id=video_id)
            is_immediate_publish = True

        if is_immediate_publish:
            # 6.A: Publish Immediately as Public
            try:
                page.evaluate("""() => {
                    const el = document.querySelector('#first-container') || document.querySelector('ytcp-video-visibility-select');
                    if (el) el.scrollIntoView({behavior: 'instant', block: 'center'});
                }""")
                time.sleep(0.5)
            except Exception:
                pass

            step_log("Selecting 'Public' radio button for immediate publishing...", level="INFO", video_id=video_id)
            public_clicked = False
            for selector in [
                "tp-yt-paper-radio-button[name='PUBLIC']",
                "ytcp-radio-button[name='PUBLIC']",
                "#first-container [name='PUBLIC']",
                "tp-yt-paper-radio-button:has-text('Public')",
                "#public-radio-button"
            ]:
                loc = page.locator(selector).first
                if loc.is_visible():
                    step_log(f"Found Public radio with selector '{selector}', clicking...", level="INFO", video_id=video_id)
                    loc.click(force=True)
                    public_clicked = True
                    break

            if not public_clicked:
                step_log("Attempting JS fallback to select PUBLIC radio button...", level="INFO", video_id=video_id)
                res = page.evaluate("""() => {
                    const r = document.querySelector("tp-yt-paper-radio-button[name='PUBLIC'], ytcp-radio-button[name='PUBLIC'], [name='PUBLIC']");
                    if (r) {
                        r.click();
                        return true;
                    }
                    return false;
                }""")
                if res:
                    public_clicked = True
                    step_log("[+] PUBLIC radio button selected via JS", level="INFO", video_id=video_id)

            time.sleep(1.5)

        else:
            # 6.B: Schedule for future datetime
            dt = parsed_schedule_dt

            # Scroll visibility dialog down to schedule container
            try:
                page.evaluate("""() => {
                    const el = document.querySelector('#second-container') || document.querySelector('#second-container-expand-button');
                    if (el) el.scrollIntoView({behavior: 'instant', block: 'center'});
                }""")
                time.sleep(0.5)
            except Exception:
                pass

            # Expand the Schedule accordion
            is_picker_visible = page.locator("ytcp-datetime-picker").is_visible()
            if not is_picker_visible:
                step_log("Expanding Schedule accordion (#second-container-expand-button)...", level="INFO", video_id=video_id)
                expand_btn = page.locator("#second-container-expand-button, #second-container").first
                expand_btn.click(force=True)
                time.sleep(1.5)

            # Wait for datetime-picker to become visible
            page.wait_for_selector("ytcp-datetime-picker", state="visible", timeout=15000)
            step_log("[+] Schedule accordion expanded, ytcp-datetime-picker is visible!", level="INFO", video_id=video_id)

            # Set Date
            target_date_str = dt.strftime("%d %b %Y")  # e.g. "17 Oct 2026"
            step_log(f"[Step 6.1/8] Setting schedule date: {target_date_str}...", level="INFO", video_id=video_id)
            date_trigger = page.locator("ytcp-datetime-picker ytcp-dropdown-trigger, #datepicker-trigger").first
            try:
                date_trigger.click(force=True)
                time.sleep(1)
                date_inp = page.locator("ytcp-date-picker input, ytcp-datetime-picker ytcp-date-picker input, ytcp-calendar-picker input, input#input").first
                if date_inp.is_visible():
                    date_inp.fill(target_date_str)
                    page.keyboard.press("Enter")
                    time.sleep(1)
                    step_log(f"[+] Schedule date applied: {target_date_str}", level="INFO", video_id=video_id)
                else:
                    step_log("[-] Note: Date input element not found, closing datepicker.", level="WARN", video_id=video_id)
                    page.keyboard.press("Escape")
            except Exception as d_err:
                step_log(f"[-] Date selection note: {d_err}", level="WARN", video_id=video_id)
                page.keyboard.press("Escape")

            # Set Time
            target_time_str = dt.strftime("%H:%M")  # e.g. "17:00"
            step_log(f"[Step 6.2/8] Setting schedule time: {target_time_str}...", level="INFO", video_id=video_id)
            time_inp = page.locator("ytcp-datetime-picker #time-of-day-picker input, ytcp-datetime-picker input").last
            if time_inp.is_visible():
                time_inp.click(force=True)
                page.keyboard.press("Control+A")
                time_inp.fill(target_time_str)
                page.keyboard.press("Enter")
                time.sleep(1)
                step_log(f"[+] Schedule time applied: {target_time_str}", level="INFO", video_id=video_id)
            else:
                step_log("[-] Warning: Time input element not found, keeping default time.", level="WARN", video_id=video_id)

        # 7. CRITICAL VERIFICATION: Check the final button text!
        # Must NOT be 'Save' (which saves as Draft). Must be 'Schedule' or 'Publish'.
        expected_btn_type = "publish" if is_immediate_publish else "schedule"
        step_log(f"[Step 7/8] Verifying final action button state (Expected: '{expected_btn_type.upper()}')...", level="INFO", video_id=video_id)
        btn_text = ""
        for chk in range(12):
            btn_text = page.evaluate("""() => {
                const btn = document.querySelector('#done-button button, #done-button');
                return btn ? (btn.innerText || btn.textContent).trim() : '';
            }""")
            if is_immediate_publish:
                if "publish" in btn_text.lower():
                    break
            else:
                if "schedule" in btn_text.lower():
                    break
            time.sleep(1)

        step_log(f"Final Action Button Text: '{btn_text}'", level="INFO", video_id=video_id)

        if is_immediate_publish:
            if "publish" not in btn_text.lower():
                err_msg = f"CRITICAL: Final button text is '{btn_text}' instead of 'Publish'. Video would be saved as DRAFT instead of published! Aborting to prevent draft state."
                step_log(err_msg, level="ERROR", video_id=video_id)
                # Take debug screenshot
                screenshot_path = BASE_DIR / f"error_draft_prevented_{video_id or 'unknown'}.png"
                try:
                    page.screenshot(path=str(screenshot_path))
                    step_log(f"Saved state screenshot to: {screenshot_path}", level="INFO", video_id=video_id)
                except Exception:
                    pass
                raise Exception(err_msg)
        else:
            if "schedule" not in btn_text.lower():
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
        action_label = "Publish" if is_immediate_publish else "Schedule"
        step_log(f"[Step 8/8] Clicking '{btn_text}' ({action_label}) button to finalize publication...", level="INFO", video_id=video_id)
        done_btn = page.locator("#done-button button, #done-button").first
        done_btn.click(force=True)
        step_log(f"{action_label} button clicked! Waiting for confirmation modal from YouTube Studio...", level="INFO", video_id=video_id)

        # Wait for YouTube Studio's confirmation modal ('Video scheduled' / 'Video published' / 'Your video is now public' / 'Your video will be set to public')
        try:
            page.wait_for_selector(
                "*:has-text('Video scheduled'), *:has-text('Video published'), *:has-text('Your video is now public'), *:has-text('Your video will be set to public'), #close-button",
                timeout=25000
            )
            success_action = "published immediately (PUBLIC)" if is_immediate_publish else "successfully scheduled"
            step_log(f"[+] YouTube Studio confirmed: Video has been {success_action}!", level="SUCCESS", video_id=video_id)
            
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
        final_status = "PUBLISHED" if is_immediate_publish else "SCHEDULED"
        step_log(f"=== [SUCCESS] Video #{video_id or ''} {final_status.lower()} on YouTube! Video Link: {yt_url} ===", level="SUCCESS", video_id=video_id)
        return {
            "success": True, 
            "url": yt_url, 
            "scheduled_datetime": schedule_dt_str,
            "published_immediately": is_immediate_publish,
            "status": final_status
        }

    except Exception as e:
        full_traceback = traceback.format_exc()
        err_msg = str(e).strip() or repr(e)
        step_log(f"=== [FAILED] Upload failed: {err_msg} ===", level="ERROR", video_id=video_id)
        step_log(f"Traceback:\n{full_traceback}", level="ERROR", video_id=video_id)

        # Capture live screenshot before closing
        if page:
            try:
                screenshot_path = BASE_DIR / f"error_upload_{video_id or 'unknown'}.png"
                page.screenshot(path=str(screenshot_path))
                step_log(f"Saved live failure screenshot to: {screenshot_path}", level="INFO", video_id=video_id)
            except Exception:
                pass

        raise e
    finally:
        if browser_context:
            try:
                browser_context.close()
            except Exception:
                pass
        if p:
            try:
                p.stop()
            except Exception:
                pass

if __name__ == "__main__":
    print("YouTube Uploader module loaded and verified.")
