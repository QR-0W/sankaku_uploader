#include <windows.h>
#include <iostream>
#include <string>

// Function to check if a directory/file exists
bool exists(const char* path) {
    DWORD dwAttrib = GetFileAttributes(path);
    return (dwAttrib != INVALID_FILE_ATTRIBUTES);
}

int main() {
    // Set console title
    SetConsoleTitle("Sankaku Uploader Launcher");

    if (!exists(".venv")) {
        std::cout << "============================================" << std::endl;
        std::cout << "   Sankaku Uploader - First Time Setup" << std::endl;
        std::cout << "============================================" << std::endl;
        std::cout << "[Launcher] Environment not found. Triggering automated setup..." << std::endl;
        
        // Execute start.bat from scripts folder
        int result = system("scripts\\start.bat");
        if (result != 0) {
            std::cerr << "[Launcher] Setup failed. Please check the logs above." << std::endl;
            system("pause");
            return result;
        }
    } else {
        std::cout << "[Launcher] Virtual environment detected." << std::endl;
        std::cout << "[Launcher] Starting application..." << std::endl;
        
        // Execute the entry point in the venv (root relative)
        int result = system(".venv\\Scripts\\sankaku-uploader.exe");
        
        // If the entry point isn't found (maybe setup was partial), fallback to start.bat
        if (result != 0 && !exists(".venv\\Scripts\\sankaku-uploader.exe")) {
            std::cout << "[Launcher] App entry point not found. Repairing via scripts\\start.bat..." << std::endl;
            system("scripts\\start.bat");
        }
    }

    return 0;
}
