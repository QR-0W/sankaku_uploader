@echo off
setlocal

:: Move to project root
cd /d "%~dp0.."

:: 1. Check for uv and bootstrap if missing
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] uv not found. Installing uv...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    
    :: Add uv to PATH for this session
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    
    :: Verify installation
    where uv >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install uv automatically. 
        echo Please install it manually from https://astral.sh/uv
        pause
        exit /b 1
    )
)

:: 2. Sync environment (uv automatically manages Python 3.12 via pyproject.toml)
echo [INFO] Syncing environment and dependencies (using uv)...
uv sync
if %errorlevel% neq 0 (
    echo [ERROR] uv sync failed. Please check your internet connection.
    pause
    exit /b 1
)

:: 3. One-time Playwright setup
if not exist ".venv\playwright_ready" (
    echo [INFO] Installing Playwright browsers...
    uv run playwright install chromium
    if %errorlevel% equ 0 (
        echo done > ".venv\playwright_ready"
    ) else (
        echo [ERROR] Playwright browser installation failed.
        pause
        exit /b 1
    )
)

:: 4. Launch Application
echo [INFO] Launching Sankaku Uploader...
uv run sankaku-uploader
if %errorlevel% neq 0 (
    echo [ERROR] Application crashed with exit code %errorlevel%
    pause
)

endlocal
