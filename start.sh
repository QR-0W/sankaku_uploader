#!/bin/bash

# Path to virtual environment
VENV_PATH=".venv"

if [ ! -d "$VENV_PATH" ]; then
    echo "[INFO] Virtual environment not found. Starting first-time setup..."
    
    # Check if Python is installed
    if ! command -v python3 &> /dev/null; then
        echo "[ERROR] python3 not found! Please install Python 3.12 or higher."
        exit 1
    fi

    echo "[INFO] Creating virtual environment in $VENV_PATH..."
    python3 -m venv "$VENV_PATH"
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create virtual environment."
        exit 1
    fi

    echo "[INFO] Upgrading pip..."
    "$VENV_PATH/bin/python" -m pip install --upgrade pip

    echo "[INFO] Installing dependencies..."
    "$VENV_PATH/bin/pip" install -e .[dev]
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to install dependencies."
        exit 1
    fi

    echo "[INFO] Installing Playwright browsers..."
    "$VENV_PATH/bin/playwright" install chromium
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to install Playwright browsers."
        exit 1
    fi

    echo "[SUCCESS] Setup complete!"
fi

echo "[INFO] Launching Sankaku Uploader..."
"$VENV_PATH/bin/sankaku-uploader"
if [ $? -ne 0 ]; then
    echo "[ERROR] Application crashed with exit code $?"
    exit 1
fi
