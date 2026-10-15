@echo off
title YouTube AI Auto-Publisher - [1] Scan Videos Folder
color 0B
chcp 65001 >nul
cls

echo ====================================================================
echo        YOUTUBE AI AUTO-PUBLISHER - [COMMAND 1] SCAN VIDEOS
echo ====================================================================
echo.
echo [*] Working Directory: %~dp0
cd /d "%~dp0"

:: Check if folder path passed as command line argument or drag-and-drop
set "TARGET_FOLDER=%~1"

if "%TARGET_FOLDER%"=="" (
    set "DEFAULT_FOLDER=D:\Videos\Animations\500+2D Cartoon Bundle-20260920T075834Z-1-005\500+2D Cartoon Bundle"
    echo Default Cartoon Folder:
    echo "!DEFAULT_FOLDER!"
    echo.
    echo Press [ENTER] to scan default folder, or paste custom folder path:
    set /p "INPUT_FOLDER=> "
)

if not "%INPUT_FOLDER%"=="" (
    set "TARGET_FOLDER=%INPUT_FOLDER%"
) else (
    if "%TARGET_FOLDER%"=="" set "TARGET_FOLDER=D:\Videos\Animations\500+2D Cartoon Bundle-20260920T075834Z-1-005\500+2D Cartoon Bundle"
)

:: Strip surrounding quotes if user pasted with quotes
set "TARGET_FOLDER=%TARGET_FOLDER:"=%"

echo.
echo [*] Scanning Directory: "%TARGET_FOLDER%"
echo [*] Inspecting video resolutions, durations, and updating SQLite database...
echo.

python main.py scan "%TARGET_FOLDER%"

echo.
echo ====================================================================
echo [OK] Scan finished. Press any key to exit.
echo ====================================================================
pause >nul
