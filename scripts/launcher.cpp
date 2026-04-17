#include <windows.h>
#include <iostream>
#include <string>

// Function to check if a file exists
bool exists(const char* path) {
    DWORD dwAttrib = GetFileAttributes(path);
    return (dwAttrib != INVALID_FILE_ATTRIBUTES);
}

int main() {
    // Set console title
    SetConsoleTitle("Sankaku Uploader Launcher (uv)");

    // Always use start.bat to ensure everything is synced and up-to-date
    // start.bat is now optimized for uv, which handles setup and launch in one go.
    std::cout << "[Launcher] Initializing via scripts\\start.bat..." << std::endl;
    
    int result = system("scripts\\start.bat");
    
    if (result != 0) {
        std::cerr << "[Launcher] Application exited or failed with code: " << result << std::endl;
        system("pause");
        return result;
    }

    return 0;
}
