# YouTube Downloader GUI / 유튜브 다운로더

A simple desktop GUI for downloading YouTube videos and live streams, built with Python + yt-dlp.  
Python + yt-dlp 기반의 유튜브 영상 및 라이브 방송 다운로더 데스크톱 앱입니다.

---

## Features / 주요 기능

- **Video & Live stream** — downloads both regular videos and live broadcasts  
  일반 영상 및 라이브 방송 모두 다운로드
- **Quality selection** — fetch available resolutions (1080p, 720p, …) before downloading  
  다운로드 전 화질 목록 조회 및 선택
- **Audio extraction** — export audio-only as MP3 (192k / 320k), M4A, or WAV  
  오디오 전용 추출 (MP3 / M4A / WAV)
- **Live indicator** — detects and labels live vs. regular video after fetch  
  영상 조회 시 라이브 방송 / 일반 영상 자동 표시
- **Custom save folder** — choose any folder; remembered across sessions via `config.json`  
  저장 폴더 직접 지정, 재실행 시에도 기억
- **Clean stop** — kills internal ffmpeg child processes immediately on cancel  
  중단 시 내부 ffmpeg 자식 프로세스까지 즉시 강제 종료
- **Standalone EXE** — build a single `.exe` with `build_exe.bat`  
  `build_exe.bat` 실행 한 번으로 단독 실행 EXE 빌드

---

## Requirements / 실행 요건

| | |
|---|---|
| Python | 3.9 or later |
| ffmpeg | Required at `C:\ffmpeg\bin\ffmpeg.exe` — run `install_ffmpeg.bat` to install |

> **ffmpeg 설치**: `install_ffmpeg.bat`을 실행하거나 [ffmpeg.org](https://ffmpeg.org/download.html)에서 수동 설치 후 `C:\ffmpeg\bin\` 경로에 배치하세요.

---

## Quick Start / 실행 방법

### Run from source / 소스에서 실행

```bash
pip install -r requirements.txt
python main.py
```

### Build standalone EXE / EXE 빌드

```
build_exe.bat 더블클릭
```

Output: `dist\YouTubeDownloader.exe`

---

## Usage / 사용법

```
① URL 주소   유튜브 URL 붙여넣기 → [영상 조회] 클릭
② 화질 선택  원하는 화질 또는 오디오 형식 선택
③ 저장 폴더  저장 위치 확인 또는 [변경] 클릭
             [▶ 다운로드 시작] 클릭
```

Downloaded files are saved in the `video/` folder next to the exe (or the selected folder).  
다운로드된 파일은 EXE 옆 `video/` 폴더 (또는 직접 지정한 폴더)에 저장됩니다.

---

## Dependencies / 의존성

| Package | Version | Purpose |
|---|---|---|
| yt-dlp | 2026.3.17 | Core download engine |
| psutil | 7.0.0 | Kill child ffmpeg processes on stop |
| mutagen | 1.47.0 | Audio metadata tagging |
| websockets | 14.2 | Live stream support |
| Brotli | 1.1.0 | Compressed stream support |
| pycryptodomex | 3.20.0 | DRM stream support |
| certifi / urllib3 / idna / charset-normalizer | pinned | requests dependencies |

---

## License / 라이선스

MIT

---

## Disclaimer / 면책 조항

This tool is intended for personal use only, such as downloading content you own or have permission to download (e.g. your own streams, Creative Commons content).  
Downloading copyrighted content without the rights holder's permission may violate YouTube's Terms of Service and applicable copyright laws.  
The author is not responsible for any misuse of this software.

이 도구는 자신이 권리를 보유하거나 다운로드 허가를 받은 콘텐츠(예: 본인 방송, CC 라이선스 영상)의 개인적 이용을 목적으로 합니다.  
저작권자의 허가 없이 저작물을 다운로드하는 것은 YouTube 이용약관 및 저작권법에 위반될 수 있습니다.  
소프트웨어의 오남용에 대한 책임은 사용자 본인에게 있습니다.
