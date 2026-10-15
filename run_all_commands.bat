@echo off
title YouTube AI Auto-Publisher - Interactive Command Launcher
color 0A
chcp 65001 >nul

:MENU
cls
echo ====================================================================
echo             YOUTUBE AI AUTO-PUBLISHER - COMMAND CENTER
echo ====================================================================
echo.
echo   [1] Scan Videos Folder         (Register cartoon videos to DB)
echo   [2] Make Multi-Part Shorts     (Convert full video to 35s Shorts)
echo   [3] Generate AI Titles & SEO   (Gemini viral titles, tags, desc)
echo   [4] Auto-Assign Calendar Slots (Schedule 2 Shorts + 1 Long daily)
echo   [5] Upload Next Video Batch    (Automated YouTube Studio uploader)
echo   [6] YouTube Studio Login       (One-time Chrome profile auth)
echo   [7] Start Web Dashboard        (Live UI on http://localhost:8000)
echo   [8] Show Pipeline Status       (Statistics & queue counts)
echo.
echo   [0] Exit
echo.
echo ====================================================================
set /p "CHOICE=Select an option [0-8]: "

if "%CHOICE%"=="1" (
    cls
    call "%~dp01_scan_videos.bat"
    goto MENU
)
if "%CHOICE%"=="2" (
    cls
    echo [*] Generating Shorts from Video ID...
    set /p "VID_ID=Enter Video ID (e.g. 5): "
    if not "!VID_ID!"=="" (
        python main.py make-shorts !VID_ID! --parts 0
        pause
    )
    goto MENU
)
if "%CHOICE%"=="3" (
    cls
    echo [*] Generating AI SEO metadata...
    python main.py generate-seo --limit 10
    pause
    goto MENU
)
if "%CHOICE%"=="4" (
    cls
    echo [*] Auto-assigning future calendar slots...
    python main.py schedule-all
    pause
    goto MENU
)
if "%CHOICE%"=="5" (
    cls
    echo [*] Uploading next ready video...
    python main.py upload --limit 1
    pause
    goto MENU
)
if "%CHOICE%"=="6" (
    cls
    echo [*] Launching Chrome for YouTube Studio Login...
    python main.py login
    pause
    goto MENU
)
if "%CHOICE%"=="7" (
    cls
    echo [*] Starting Web Control Panel...
    start "" "%~dp0start_control_panel.bat"
    goto MENU
)
if "%CHOICE%"=="8" (
    cls
    python main.py status
    echo.
    pause
    goto MENU
)
if "%CHOICE%"=="0" exit /b 0

echo [!] Invalid selection. Please try again.
timeout /t 2 >nul
goto MENU
