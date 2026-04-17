#!/bin/bash

# Move to project root
cd "$(dirname "$0")/.."

# 1. Check for uv and bootstrap if missing
if ! command -v uv &> /dev/null; then
    echo "[INFO] uv not found. Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.cargo/env
fi

# 2. Sync environment
echo "[INFO] Syncing environment and dependencies (using uv)..."
uv sync
if [ $? -ne 0 ]; then
    echo "[ERROR] uv sync failed."
    exit 1
fi

# 3. One-time Playwright setup
if [ ! -f ".venv/playwright_ready" ]; then
    echo "[INFO] Installing Playwright browsers..."
    uv run playwright install chromium
    if [ $? -eq 0 ]; then
        echo "done" > ".venv/playwright_ready"
    else
        echo "[ERROR] Playwright browser installation failed."
        exit 1
    fi
fi

# 4. Launch Application
echo "[INFO] Launching Sankaku Uploader..."
uv run sankaku-uploader
if [ $? -ne 0 ]; then
    echo "[ERROR] Application crashed with exit code $?"
    exit 1
fi
