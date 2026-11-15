@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem One-click launcher: creates venv on first run and installs dependencies.
if not exist "venv\Scripts\python.exe" (
    echo First run: creating venv and installing dependencies...
    python -m venv venv
    if errorlevel 1 goto fail
    "venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
    if errorlevel 1 goto fail
)

"venv\Scripts\python.exe" weather_app.py
goto end

:fail
echo.
echo Could not prepare the environment. Make sure Python is installed
echo and available as "python".

:end
echo.
pause
