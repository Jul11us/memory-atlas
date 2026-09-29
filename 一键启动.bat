@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Memory Atlas
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 (
    python start_website.py
    goto finished
)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 (
    py -3 start_website.py
    goto finished
)
echo Python 3.10 or newer is required. Install Python and try again.
echo https://www.python.org/downloads/
pause
exit /b 1
:finished
if errorlevel 1 pause
