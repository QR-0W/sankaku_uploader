@echo off
setlocal

:: Always operate relative to the project root
cd /d "%~dp0.."
set VENV_PATH=.venv

if not exist "%VENV_PATH%" (
    echo [INFO] Virtual environment not found. Starting first-time setup...
    
    python --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] Python not found! Please install Python 3.12 or higher.
        pause
        exit /b 1
    )

    echo [INFO] Creating virtual environment in %VENV_PATH%...
    python -m venv %VENV_PATH%
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )

    echo [INFO] Upgrading pip...
    "%VENV_PATH%\Scripts\python.exe" -m pip install --upgrade pip

    echo [INFO] Installing dependencies...
    "%VENV_PATH%\Scripts\pip.exe" install -e .[dev]
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )

    echo [INFO] Installing Playwright browsers...
    "%VENV_PATH%\Scripts\playwright.exe" install chromium
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install Playwright browsers.
        pause
        exit /b 1
    )

    echo [SUCCESS] Setup complete!
)

echo [INFO] Launching Sankaku Uploader...
"%VENV_PATH%\Scripts\sankaku-uploader.exe"
if %errorlevel% neq 0 (
    echo [ERROR] Application crashed with exit code %errorlevel%
    pause
)

endlocal
