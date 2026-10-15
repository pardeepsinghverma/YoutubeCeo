@echo off
title YouTube AI Auto-Publisher - Command Control Panel
color 0A
cls
echo ====================================================================
echo        YOUTUBE AI AUTO-PUBLISHER - WEB CONTROL PANEL
echo ====================================================================
echo.
echo [*] Working Directory: %~dp0
echo [*] Launching Dashboard Server on http://localhost:8000 ...
echo [*] Opening browser in 2 seconds...
echo [*] To STOP the server anytime, press Ctrl+C in this window.
echo ====================================================================
echo.

cd /d "%~dp0"

:: Open browser in background after 2 seconds
start "" powershell -NoProfile -Command "Start-Sleep -Seconds 2; Start-Process 'http://localhost:8000'"

:: Start uvicorn dashboard server
python -m uvicorn dashboard:app --host 127.0.0.1 --port 8000 --reload

pause
