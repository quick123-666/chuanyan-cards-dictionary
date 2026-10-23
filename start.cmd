@echo off
rem One-click launcher for the ChuanYan cards server.
rem Starts server.py and opens the browser automatically.
rem Close this window (or press Ctrl+C) to stop the server.

chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 server.py
) else (
    python server.py
)

echo.
echo Server stopped.
pause