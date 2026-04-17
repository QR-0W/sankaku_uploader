#include <windows.h>
#include <iostream>
#include <string>
#include <vector>

/**
 * Sankaku Uploader - Minimalist Autonomous Launcher
 * 
 * Functions:
 * 1. Bootstraps 'uv' (Astral Python manager) if missing.
 * 2. Manages environment synchronization and Python 3.12 installation.
 * 3. Handles Playwright browser setup.
 * 4. Launches the main application.
 * 
 * Usage:
 *   launcher.exe           - Standard launch
 *   launcher.exe --rebuild - Clear environment and start fresh
 */

bool fileExists(const std::string& path) {
    DWORD dwAttrib = GetFileAttributesA(path.c_str());
    return (dwAttrib != INVALID_FILE_ATTRIBUTES);
}

int runCommand(const std::string& cmd) {
    return system(cmd.c_str());
}

void trySetupUvPath() {
    char* userProfile = getenv("USERPROFILE");
    if (userProfile) {
        std::string uvBin = std::string(userProfile) + "\\.local\\bin";
        std::string currentPath = getenv("PATH");
        if (currentPath.find(uvBin) == std::string::npos) {
            std::string newPath = "PATH=" + uvBin + ";" + currentPath;
            _putenv(newPath.c_str());
        }
    }
}

int main(int argc, char* argv[]) {
    SetConsoleTitleA("Sankaku Uploader Launcher");

    // 1. Argument Handling
    if (argc > 1 && std::string(argv[1]) == "--rebuild") {
        std::cout << ">>> [Launcher] Rebuild flag detected. Cleaning .venv..." << std::endl;
        if (fileExists(".venv")) {
            runCommand("rmdir /s /q .venv");
        }
    }

    // 2. UV Bootstrap
    trySetupUvPath();
    if (runCommand("uv --version >nul 2>&1") != 0) {
        std::cout << ">>> [Launcher] 'uv' (Python Manager) not found. Installing..." << std::endl;
        runCommand("powershell -ExecutionPolicy ByPass -c \"irm https://astral.sh/uv/install.ps1 | iex\"");
        trySetupUvPath(); // Refresh paths after install
        
        if (runCommand("uv --version >nul 2>&1") != 0) {
            std::cerr << "!!! [Error] Could not install or locate 'uv'. Please install it manually from https://astral.sh/uv" << std::endl;
            system("pause");
            return 1;
        }
    }

    // 3. Environment Sync
    std::cout << ">>> [Launcher] Synchronizing environment..." << std::endl;
    if (runCommand("uv sync") != 0) {
        std::cerr << "!!! [Error] Failed to synchronize dependencies. Check your internet connection." << std::endl;
        system("pause");
        return 1;
    }

    // 4. Playwright Setup
    if (!fileExists(".venv\\playwright_ready")) {
        std::cout << ">>> [Launcher] Installing Playwright browser engines..." << std::endl;
        if (runCommand("uv run playwright install chromium") == 0) {
            runCommand("echo done > .venv\\playwright_ready");
        } else {
            std::cerr << "!!! [Error] Playwright setup failed." << std::endl;
            system("pause");
            return 1;
        }
    }

    // 5. App Launch
    std::cout << ">>> [Launcher] Starting application..." << std::endl;
    // We use 'uv run' to ensure the correct managed Python is used regardless of system paths
    int result = runCommand("uv run sankaku-uploader");

    if (result != 0) {
        std::cerr << "!!! [Error] Application exited with code: " << result << std::endl;
        system("pause");
    }

    return 0;
}
