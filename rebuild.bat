@echo off
setlocal
echo ============================================
echo      Sankaku Uploader - Rebuild Env
echo ============================================
echo [WARNING] This will delete the existing .venv and start fresh.
set /p confirm="Are you sure? (y/n): "
if /i "%confirm%" neq "y" exit /b

if exist .venv (
    echo [INFO] Removing existing virtual environment...
    rmdir /s /q .venv
)

echo [INFO] Starting setup...
call scripts\start.bat
endlocal
