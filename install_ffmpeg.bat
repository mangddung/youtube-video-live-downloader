@echo off
cd /d "%~dp0"
echo ========================================
echo  FFmpeg Installer
echo ========================================
echo.

echo [1/2] Trying winget...
winget install ffmpeg
if %errorlevel% equ 0 (
    echo.
    echo [OK] FFmpeg installed successfully via winget.
    echo Please restart your PC before using the downloader.
    pause
    exit /b 0
)

echo.
echo [INFO] winget failed. Trying Chocolatey...
where choco >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Chocolatey is not installed either.
    echo.
    echo Manual install:
    echo  1. Go to https://ffmpeg.org/download.html
    echo  2. Download Windows build
    echo  3. Extract and place ffmpeg.exe in C:\ffmpeg\bin\
    echo  4. Restart your PC
    pause
    exit /b 1
)

echo [2/2] Trying Chocolatey...
choco install ffmpeg -y
if %errorlevel% equ 0 (
    echo.
    echo [OK] FFmpeg installed successfully via Chocolatey.
    echo Please restart your PC before using the downloader.
) else (
    echo.
    echo [ERROR] FFmpeg install failed.
    echo Manual install: https://ffmpeg.org/download.html
)

pause
