@echo off
cd /d "%~dp0"

echo ========================================
echo  YouTube Downloader - EXE Build
echo ========================================
echo.
echo Working folder: %CD%
echo.

echo [1/3] Installing PyInstaller...
pip install pyinstaller
if errorlevel 1 (
    echo.
    echo [ERROR] pip failed. Check Python installation and PATH.
    pause
    exit /b 1
)

echo.
echo [2/3] Installing packages from requirements.txt...
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Package install failed.
    pause
    exit /b 1
)

echo.
echo [3/3] Building EXE (1~3 min)...
echo.
pyinstaller --onefile --windowed ^
    --name "YouTubeDownloader" ^
    --hidden-import yt_dlp ^
    --hidden-import yt_dlp.extractor ^
    --hidden-import yt_dlp.postprocessor ^
    --hidden-import psutil ^
    --collect-all yt_dlp ^
    main.py

if errorlevel 1 (
    echo.
    echo [BUILD FAILED] Check errors above.
    pause
    exit /b 1
)

echo.
echo ========================================
echo  Done! dist\YouTubeDownloader.exe
echo ========================================
echo.
echo NOTE: C:\ffmpeg\bin\ffmpeg.exe is required to run.
echo.
pause
