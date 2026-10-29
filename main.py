import yt_dlp
import sys
import os
import time
import threading
import shutil
import subprocess
import queue
from datetime import datetime
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import re

# yt-dlp 포맷별 임시 파일명의 포맷 ID (예: "제목.f399.mp4" 의 ".f399", ".f251-drc")
FORMAT_ID_RE = re.compile(r'\.f\d+(?:-\w+)?(?=\.\w+$)')
# YouTube 서명 해제용 JS 런타임 (설치된 것만 사용됨). 없으면 일부 화질이 누락될 수 있음
JS_RUNTIMES = {'deno': {}, 'node': {}}

class YouTubeDownloaderGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("유튜브 다운로더")
        self.root.geometry("650x640")
        self.root.resizable(True, True)

        # 다운로드 상태 변수
        self.is_downloading = False
        self.download_thread = None
        self.stop_flag = threading.Event()
        self.is_live = False
        self.video_info = None  # '영상 조회' 결과 (다운로드 시 재사용)
        self.ffmpeg_path = self._find_ffmpeg()
        self.download_dir = ''
        self._load_config()

        # 작업 스레드 → 메인 스레드 UI 작업 큐 (Tk는 스레드 안전하지 않음)
        self._ui_queue = queue.Queue()

        # 화질 선택 변수
        self.quality_options = []  # [(label, format_string), ...]
        self._fetching_quality = False

        self.setup_ui()
        self.root.after(100, self._process_ui_queue)

        # 프로그램 시작 시 이전 실행에서 남은 임시 파일 정리
        self.cleanup_part_files()
        
    def setup_ui(self):
        # 메인 프레임
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # URL 입력 섹션
        url_frame = ttk.LabelFrame(main_frame, text="유튜브 URL", padding="10")
        url_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # ① URL 주소
        ttk.Label(url_frame, text="① URL 주소", font=("Arial", 8, "bold")).grid(
            row=0, column=0, columnspan=2, sticky=tk.W)
        self.url_var = tk.StringVar()
        self.url_var.trace_add('write', self._on_url_changed)
        ttk.Entry(url_frame, textvariable=self.url_var).grid(
            row=1, column=0, sticky=(tk.W, tk.E), padx=(0, 6))
        self.quality_fetch_btn = ttk.Button(url_frame, text="영상 조회", command=self.fetch_qualities)
        self.quality_fetch_btn.grid(row=1, column=1)

        # ② 화질 선택 + 영상 유형 표시
        ttk.Label(url_frame, text="② 화질 선택", font=("Arial", 8, "bold")).grid(
            row=2, column=0, sticky=tk.W, pady=(10, 0))
        self.video_type_label = tk.Label(url_frame, text="", font=("Arial", 9, "bold"))
        self.video_type_label.grid(row=2, column=1, sticky=tk.E, pady=(10, 0))

        self.quality_combo = ttk.Combobox(url_frame, state="disabled")
        self.quality_combo['values'] = ["먼저 URL 입력 후 '영상 조회'를 눌러주세요"]
        self.quality_combo.current(0)
        self.quality_combo.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E))

        # ③ 저장 폴더
        ttk.Label(url_frame, text="③ 저장 폴더", font=("Arial", 8, "bold")).grid(
            row=4, column=0, columnspan=2, sticky=tk.W, pady=(10, 0))
        self.download_dir_var = tk.StringVar(value=self.download_dir)
        ttk.Entry(url_frame, textvariable=self.download_dir_var, state="readonly").grid(
            row=5, column=0, sticky=(tk.W, tk.E), padx=(0, 6))
        ttk.Button(url_frame, text="변경", command=self._browse_download_dir, width=6).grid(
            row=5, column=1)

        # 다운로드 버튼
        btn_frame = ttk.Frame(url_frame)
        btn_frame.grid(row=6, column=0, columnspan=2, sticky=tk.E, pady=(12, 0))
        self.download_btn = ttk.Button(btn_frame, text="▶ 다운로드 시작",
                                       command=self.start_download, state="disabled")
        self.download_btn.grid(row=0, column=0, padx=(0, 6))
        self.stop_btn = ttk.Button(btn_frame, text="■ 중단",
                                   command=self.stop_download, state="disabled")
        self.stop_btn.grid(row=0, column=1)

        # 진행률 표시 섹션
        progress_frame = ttk.LabelFrame(main_frame, text="다운로드 진행률", padding="10")
        progress_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        
        # 파일명 표시
        self.filename_var = tk.StringVar(value="파일명: 대기 중...")
        filename_label = ttk.Label(progress_frame, textvariable=self.filename_var, font=("Arial", 9))
        filename_label.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5))
        
        # 진행률 메시지
        self.progress_var = tk.StringVar(value="대기 중...")
        progress_label = ttk.Label(progress_frame, textvariable=self.progress_var, font=("Arial", 10, "bold"))
        progress_label.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 5))
        
        # 진행률 바
        self.progress_bar = ttk.Progressbar(progress_frame, mode='determinate')
        self.progress_bar.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(0, 5))
        
        # 연결 상태 표시
        self.connection_var = tk.StringVar(value="연결 상태: 대기 중...")
        connection_label = ttk.Label(progress_frame, textvariable=self.connection_var, font=("Arial", 8))
        connection_label.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(0, 5))
        
        # 스트림 정보 표시
        self.stream_var = tk.StringVar(value="영상 정보: 대기 중...")
        stream_label = ttk.Label(progress_frame, textvariable=self.stream_var, font=("Arial", 8))
        stream_label.grid(row=4, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # 로그 표시 영역
        log_frame = ttk.LabelFrame(main_frame, text="로그", padding="10")
        log_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, width=70)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # 그리드 가중치 설정
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)
        url_frame.columnconfigure(0, weight=1)
        progress_frame.columnconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        
    def _ui(self, func, *args):
        """Tk 위젯 작업을 메인 스레드로 넘김 (어느 스레드에서든 호출 가능)"""
        self._ui_queue.put((func, args))

    def _process_ui_queue(self):
        """메인 스레드에서 쌓인 UI 작업 실행"""
        try:
            while True:
                try:
                    func, args = self._ui_queue.get_nowait()
                except queue.Empty:
                    break
                func(*args)
        finally:
            self.root.after(100, self._process_ui_queue)

    def log_message(self, message):
        """로그 메시지를 텍스트 영역에 추가"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._ui(self._append_log, f"[{timestamp}] {message}\n")

    def _append_log(self, line):
        self.log_text.insert(tk.END, line)
        self.log_text.see(tk.END)


    def _on_url_changed(self, *args):
        """URL 변경 시 화질 목록 초기화 및 다운로드 버튼 비활성"""
        if self.is_downloading:
            return
        self.video_info = None
        self.quality_options = []
        self.quality_combo['values'] = ["먼저 URL 입력 후 '영상 조회'를 눌러주세요"]
        self.quality_combo.current(0)
        self.quality_combo.config(state="disabled")
        self.download_btn.config(state="disabled")
        self.video_type_label.config(text="")

    def fetch_qualities(self):
        """사용 가능한 화질 목록 조회"""
        if self._fetching_quality:
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("오류", "먼저 URL을 입력해주세요.")
            return
        if "youtube.com" not in url and "youtu.be" not in url:
            messagebox.showerror("오류", "유효한 유튜브 URL이 아닙니다.")
            return

        self._fetching_quality = True
        self.quality_fetch_btn.config(state="disabled")
        self.quality_combo.config(state="disabled")
        self.quality_combo['values'] = ["조회 중..."]
        self.quality_combo.current(0)
        self.log_message("화질 목록을 조회 중...")

        thread = threading.Thread(target=self._fetch_qualities_worker, args=(url,))
        thread.daemon = True
        thread.start()

    def _fetch_qualities_worker(self, url):
        """화질 목록 조회 워커 — yt-dlp Python API 방식"""
        try:
            ydl_opts = {'quiet': True, 'no_warnings': True, 'js_runtimes': JS_RUNTIMES}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            self.video_info = info
            self.is_live = bool(info.get('is_live'))
            if self.is_live:
                self._ui(lambda: self.video_type_label.config(text="🔴 라이브 방송", fg="red"))
                self.log_message("🔴 라이브 방송으로 감지되었습니다.")
            else:
                self._ui(lambda: self.video_type_label.config(text="📹 일반 영상", fg="gray"))
                self.log_message("📹 일반 영상으로 감지되었습니다.")

            formats = info.get('formats', [])

            # 최고 오디오 스트림 정보 추출
            audio_formats = [f for f in formats
                             if f.get('vcodec') == 'none' and f.get('acodec') not in (None, 'none')]
            audio_info_str = ""
            if audio_formats:
                best_audio = max(audio_formats, key=lambda f: f.get('abr') or f.get('tbr') or 0)
                acodec = (best_audio.get('acodec') or '').split('.')[0]
                abr = best_audio.get('abr') or best_audio.get('tbr')
                parts = []
                if acodec:
                    parts.append(acodec)
                if abr:
                    parts.append(f"{int(abr)}k")
                audio_info_str = " ".join(parts)

            # 비디오 포맷 필터링 및 중복 제거 (높은 해상도 순)
            seen = set()
            video_entries = []
            video_formats = sorted(
                [f for f in formats if f.get('vcodec') not in (None, 'none') and f.get('height')],
                key=lambda x: (x.get('height', 0), x.get('fps') or 0),
                reverse=True
            )
            for f in video_formats:
                height = f.get('height', 0)
                fps = int(f.get('fps') or 0)
                if (height, fps) in seen:
                    continue
                seen.add((height, fps))
                vcodec = (f.get('vcodec') or '').split('.')[0]
                filesize = f.get('filesize') or f.get('filesize_approx')
                vbr = f.get('vbr') or f.get('tbr')
                video_entries.append((height, fps, vcodec, filesize, vbr))

            self.log_message(f"감지된 해상도: {', '.join(str(h)+'p' for h,*_ in video_entries)}")

            # (라벨, format 문자열, 추가 ydl_opts)
            options = [("최고 화질 (자동)", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best", {})]
            for height, fps, vcodec, filesize, vbr in video_entries:
                fps_str = f" {fps}fps" if fps else ""
                codec_str = f" ({vcodec})" if vcodec else ""

                size_str = ""
                if filesize:
                    mb = filesize / (1024 * 1024)
                    size_str = f" | ~{mb/1024:.1f}GB" if mb >= 1024 else f" | ~{int(mb)}MB"
                elif vbr:
                    size_str = f" | {int(vbr)}kbps"

                audio_str = f" | 오디오: {audio_info_str}" if audio_info_str else ""
                label = f"{height}p{fps_str}{codec_str}{size_str}{audio_str}"
                fmt = (f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]"
                       f"/bestvideo[height<={height}]+bestaudio/best[height<={height}]")
                options.append((label, fmt, {}))

            # 오디오 전용 옵션
            def audio_opt(codec, quality=None):
                pp = {'key': 'FFmpegExtractAudio', 'preferredcodec': codec}
                if quality:
                    pp['preferredquality'] = quality
                return {'postprocessors': [pp], 'merge_output_format': None}

            abr_str = f" ({audio_info_str})" if audio_info_str else ""
            options += [
                (f"🎵 오디오만 - MP3 192k{abr_str}", "bestaudio/best",        audio_opt('mp3', '192')),
                (f"🎵 오디오만 - MP3 320k{abr_str}", "bestaudio/best",        audio_opt('mp3', '320')),
                (f"🎵 오디오만 - M4A{abr_str}",      "bestaudio[ext=m4a]/bestaudio/best", audio_opt('m4a')),
                (f"🎵 오디오만 - WAV{abr_str}",      "bestaudio/best",        audio_opt('wav')),
            ]

            self.quality_options = options
            labels = [opt[0] for opt in options]
            self._ui(self._update_quality_combo, labels)
            self.log_message(f"✅ {len(options)}개의 화질 옵션을 찾았습니다.")

        except Exception as e:
            self.log_message(f"화질 조회 오류: {e}")
            self._ui(lambda: self.quality_fetch_btn.config(state="normal"))
        finally:
            self._fetching_quality = False

    def _update_quality_combo(self, labels):
        """콤보박스 업데이트 (메인 스레드에서 실행)"""
        self.quality_combo['values'] = labels
        self.quality_combo.current(0)
        self.quality_combo.config(state="readonly")
        self.quality_fetch_btn.config(state="normal")
        self.download_btn.config(state="normal")

    def _resolve_outtmpl(self):
        """파일명 중복 시 -1, -2 suffix를 붙인 outtmpl 반환 ('영상 조회' 정보 재사용)"""
        video_dir = self.get_output_dir()
        info = self.video_info
        if not info:
            return os.path.join(video_dir, '%(title)s [%(id)s].%(ext)s')
        title = re.sub(r'[\\/:*?"<>|]', '_', info.get('title', 'video'))
        vid_id = info.get('id', 'unknown')
        base = os.path.join(video_dir, f"{title} [{vid_id}]")
        for ext in ('mp4', 'mkv', 'webm', 'ts', 'mp3', 'm4a', 'wav'):
            if os.path.exists(f"{base}.{ext}"):
                counter = 1
                while os.path.exists(f"{base}-{counter}.{ext}"):
                    counter += 1
                return f"{base}-{counter}.%(ext)s"
        return f"{base}.%(ext)s"

    @staticmethod
    def _find_ffmpeg():
        """C:\\ffmpeg\\bin 우선, 없으면 PATH에서 검색 (못 찾으면 None → yt-dlp 기본 탐색)"""
        default = r"C:\ffmpeg\bin\ffmpeg.exe"
        return default if os.path.exists(default) else shutil.which('ffmpeg')

    @staticmethod
    def _get_base_dir():
        """exe/스크립트가 위치한 폴더 경로 반환"""
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    def _config_path(self):
        return os.path.join(self._get_base_dir(), 'config.json')

    def _load_config(self):
        """저장된 설정 로드. download_dir이 없으면 기본값(video/) 사용"""
        import json
        default = os.path.join(self._get_base_dir(), 'video')
        try:
            with open(self._config_path(), 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            path = cfg.get('download_dir', default)
            self.download_dir = path if os.path.isabs(path) else default
        except Exception:
            self.download_dir = default

    def _save_config(self):
        import json
        try:
            with open(self._config_path(), 'w', encoding='utf-8') as f:
                json.dump({'download_dir': self.download_dir}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log_message(f"설정 저장 오류: {e}")

    def get_output_dir(self):
        """현재 저장 폴더 반환 (없으면 생성)"""
        os.makedirs(self.download_dir, exist_ok=True)
        return self.download_dir

    def _browse_download_dir(self):
        """폴더 선택 다이얼로그"""
        from tkinter import filedialog
        chosen = filedialog.askdirectory(initialdir=self.download_dir, title="저장 폴더 선택")
        if chosen:
            self.download_dir = chosen
            self.download_dir_var.set(chosen)
            self._save_config()
            self.log_message(f"저장 폴더 변경: {chosen}")

    def get_selected_opts(self):
        """현재 선택된 항목의 (format 문자열, 추가 opts) 반환"""
        if not self.quality_options:
            return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best", {}
        idx = self.quality_combo.current()
        if idx < 0 or idx >= len(self.quality_options):
            idx = 0
        entry = self.quality_options[idx]
        return entry[1], entry[2]

    def update_progress(self, message):
        """진행률 메시지 업데이트"""
        self._ui(self.progress_var.set, message)

    def update_connection_status(self, status):
        """연결 상태 업데이트"""
        self._ui(self.connection_var.set, f"연결 상태: {status}")

    def update_stream_info(self, info):
        """영상 정보 업데이트"""
        self._ui(self.stream_var.set, f"영상 정보: {info}")

    def _set_progress_percent(self, percent):
        """진행률 바: 전체 용량을 알 때 (메인 스레드)"""
        self.progress_bar.stop()
        self.progress_bar.config(mode='determinate', value=percent)

    def _set_progress_indeterminate(self):
        """진행률 바: 용량 미확인 (라이브 등) (메인 스레드)"""
        self.progress_bar.config(mode='indeterminate')
        self.progress_bar.start()

    @staticmethod
    def _interrupted_name(filename):
        """'제목.mp4' → '제목_중단_20261028_193600.mp4'"""
        stem, ext = os.path.splitext(filename)
        return f"{stem}_중단_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"

    def cleanup_part_files(self):
        """중단으로 남은 임시 파일 정리.
        조각(-FragN)·.ytdl은 삭제, 1MB 넘는 .part는 '_중단_' 파일로 저장.
        영상/오디오 .part가 따로 남았으면 ffmpeg로 합쳐서 하나로 저장."""
        try:
            video_dir = self.get_output_dir()
            groups = {}  # 포맷 ID를 뺀 원래 파일명 → [.part 경로, ...]
            for name in os.listdir(video_dir):
                path = os.path.join(video_dir, name)
                if re.search(r'-Frag\d+(\.part)?$', name) or name.endswith('.ytdl'):
                    self.safe_remove_file(path)
                elif name.endswith('.part'):
                    base = FORMAT_ID_RE.sub('', name[:-len('.part')])
                    groups.setdefault(base, []).append(path)

            for base, paths in groups.items():
                parts = []
                for path in paths:
                    if os.path.getsize(path) > 1024 * 1024:
                        parts.append(path)
                    else:
                        self.safe_remove_file(path)

                if len(parts) >= 2 and self._merge_parts(parts, os.path.join(video_dir, self._interrupted_name(base))):
                    for path in parts:
                        self.safe_remove_file(path)
                    continue

                # 파일 하나뿐이거나 병합 실패 → 각각 이름만 바꿔 저장 (복사 없음)
                for path in parts:
                    name = os.path.basename(path)[:-len('.part')]
                    if len(parts) == 1:
                        name = FORMAT_ID_RE.sub('', name)
                    final_path = os.path.join(video_dir, self._interrupted_name(name))
                    size_mb = os.path.getsize(path) / (1024 * 1024)
                    os.replace(path, final_path)
                    self.log_message(f"✅ 파일 저장됨: {os.path.basename(final_path)} ({size_mb:.1f}MB)")

        except Exception as e:
            self.log_message(f"파일 정리 중 오류: {e}")

    def _merge_parts(self, parts, final_path):
        """영상/오디오 .part 파일을 ffmpeg로 합침 (재인코딩 없음). 성공 여부 반환"""
        if not self.ffmpeg_path:
            self.log_message("⚠️ ffmpeg를 찾을 수 없어 영상/오디오를 따로 저장합니다.")
            return False
        cmd = [self.ffmpeg_path, '-y', '-loglevel', 'error']
        for path in parts:
            cmd += ['-i', path]
        for i in range(len(parts)):
            cmd += ['-map', str(i)]
        cmd += ['-c', 'copy', final_path]
        self.log_message("영상/오디오 합치는 중...")
        result = subprocess.run(cmd, capture_output=True, text=True, errors='replace',
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode != 0:
            self.log_message(f"⚠️ 합치기 실패, 따로 저장합니다: {result.stderr.strip()[-200:]}")
            if os.path.exists(final_path):
                self.safe_remove_file(final_path)
            return False
        size_mb = os.path.getsize(final_path) / (1024 * 1024)
        self.log_message(f"✅ 파일 저장됨: {os.path.basename(final_path)} ({size_mb:.1f}MB)")
        return True

    def safe_remove_file(self, filepath):
        """파일을 안전하게 삭제 (재시도 로직 포함)"""
        max_retries = 5
        retry_delay = 1  # 초
        
        for attempt in range(max_retries):
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
                    self.log_message(f"🗑️ 임시 파일 삭제됨: {filepath}")
                    return True
            except PermissionError:
                if attempt < max_retries - 1:
                    self.log_message(f"파일 사용 중... {retry_delay}초 후 재시도 ({attempt + 1}/{max_retries})")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # 지수 백오프
                else:
                    self.log_message(f"⚠️ 파일 삭제 실패 (다른 프로세스에서 사용 중): {filepath}")
                    self.log_message("💡 수동으로 삭제하거나 프로그램 재시작 후 삭제하세요.")
            except Exception as e:
                self.log_message(f"파일 삭제 중 오류: {e}")
                break
                
        return False
        
    def start_download(self):
        """다운로드 시작"""
        if self.is_downloading:
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("오류", "유튜브 URL을 입력해주세요.")
            return
            
        if "youtube.com" not in url and "youtu.be" not in url:
            messagebox.showerror("오류", "유효한 유튜브 URL이 아닙니다.")
            return
            
        self.is_downloading = True
        self.stop_flag.clear()  # 중단 플래그 초기화
        self.download_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress_bar.config(mode='indeterminate')
        self.progress_bar.start()
        
        # 상태 정보 초기화
        self.filename_var.set("파일명: 연결 중...")
        self.connection_var.set("연결 상태: 연결 시도 중...")
        self.stream_var.set("영상 정보: 분석 중...")
        
        self.log_text.delete(1.0, tk.END)

        # 위젯 값은 메인 스레드에서 읽어서 넘김
        option_label = self.quality_combo.get() if self.quality_options else '최고 화질 (자동)'
        selected_format, extra_opts = self.get_selected_opts()
        self.download_thread = threading.Thread(
            target=self.download_worker, args=(url, option_label, selected_format, extra_opts))
        self.download_thread.daemon = True
        self.download_thread.start()
        
    def stop_download(self):
        """다운로드 중단 요청.
        실제 중단은 진행률 훅에서 처리 (라이브는 받은 부분까지 yt-dlp가 합쳐서 저장).
        작업 스레드가 끝나야 다운로드 버튼이 다시 켜짐."""
        if not self.is_downloading or self.stop_flag.is_set():
            return
        self.stop_flag.set()
        self.stop_btn.config(state="disabled")
        if self.is_live:
            self.log_message("녹화를 중단합니다. 받은 부분까지 저장 중...")
        else:
            self.log_message("다운로드를 중단합니다...")
        self.update_connection_status("중단 중...")
        self.update_stream_info("연결 해제 중...")
        self.update_progress("중단 중...")

    def download_worker(self, url, option_label, selected_format, extra_opts):
        """다운로드 작업을 수행하는 워커 스레드"""
        try:
            self.record_youtube_live(url, option_label, selected_format, extra_opts)
        except KeyboardInterrupt:
            self.log_message("다운로드가 중단되었습니다.")
        except Exception as e:
            self.log_message(f"오류 발생: {e}")
        finally:
            # 중단됐으면 남은 임시 파일 정리 (ffmpeg 병합 포함, 작업 스레드에서 수행)
            if self.stop_flag.is_set():
                self.cleanup_part_files()
            self._ui(self.download_finished)

    def download_finished(self):
        """작업 스레드 종료 후 UI 상태 복원 (메인 스레드)"""
        self.is_downloading = False
        self.download_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.progress_bar.stop()

        # 상태 정보 복원
        if not self.stop_flag.is_set():
            self.update_progress("완료")
            self.update_connection_status("완료")
            self.update_stream_info("다운로드 완료")
        else:
            self.update_progress("중단됨")
            self.update_connection_status("중단됨")
            self.update_stream_info("연결 해제됨")

    def record_youtube_live(self, url, option_label, selected_format, extra_opts):
        """
        주어진 YouTube 라이브 스트림 URL을 녹화합니다.
        방송이 끝나거나 사용자가 중단할 때까지 계속됩니다.
        """
        # 다운로드 시작 시간 기록
        start_time = datetime.now()
        download_start_time = time.time()

        # 진행률 표시를 위한 변수들
        # 영상/오디오 스트림이 동시에 받아지므로 파일별로 모아서 합산 표시
        file_bytes = {}
        file_speed = {}
        last_update_time = time.time()

        def create_progress_hook(start_time):
            def progress_hook(d):
                nonlocal last_update_time

                if d['status'] == 'downloading':
                    # 중단 요청 시 예외로 yt-dlp를 멈춤
                    # (라이브는 yt-dlp가 스트림별로 받아서 받은 부분까지 저장·병합)
                    if self.stop_flag.is_set():
                        raise KeyboardInterrupt("사용자에 의해 중단됨")

                    # 파일명 업데이트 (.f399/.f140 같은 포맷 ID 제거 → 영상/오디오 모두 같은 이름)
                    key = d.get('filename') or ''
                    if key:
                        filename = FORMAT_ID_RE.sub('', os.path.basename(key))
                        self._ui(self.filename_var.set, f"파일명: {filename}")

                    file_bytes[key] = d.get('downloaded_bytes') or 0
                    file_speed[key] = d.get('speed') or 0

                    # 현재 시간
                    current_time = time.time()
                    elapsed_time = current_time - start_time

                    # 1초마다 업데이트
                    if current_time - last_update_time >= 1.0:
                        last_update_time = current_time

                        elapsed_minutes = int(elapsed_time // 60)
                        elapsed_seconds = int(elapsed_time % 60)

                        # yt-dlp 제공 포맷 문자열
                        pct_str   = (d.get('_percent_str') or '').strip()
                        spd_str   = (d.get('_speed_str')   or '').strip()
                        eta_str   = (d.get('_eta_str')     or '').strip()
                        total_str = (d.get('_total_bytes_str') or
                                     d.get('_total_bytes_estimate_str') or '').strip()

                        # 연결 상태: 속도 표시 (동시 다운로드 중인 스트림 합산)
                        speed_mb = sum(file_speed.values()) / (1024 * 1024)
                        self.update_connection_status(f"연결됨 - 속도: {speed_mb:.1f}MB/s")
                        self.update_stream_info("라이브 스트림 활성" if self.is_live else "다운로드 진행 중")

                        # 퍼센트 파싱 (VOD)
                        percent_val = None
                        if pct_str and '%' in pct_str:
                            try:
                                percent_val = float(pct_str.replace('%', '').strip())
                            except ValueError:
                                pass

                        if percent_val is not None:
                            # 전체 용량 알 때 → determinate 모드
                            self._ui(self._set_progress_percent, percent_val)
                            size_part = f"of {total_str}" if total_str else ""
                            eta_part  = f"ETA {eta_str}" if eta_str else f"{elapsed_minutes:02d}:{elapsed_seconds:02d} 경과"
                            parts = [x for x in [f"📥 {pct_str}", size_part, spd_str, eta_part] if x]
                            progress_msg = " | ".join(parts)
                        else:
                            # 용량 미확인 (라이브 스트림 등) → indeterminate 모드
                            self._ui(self._set_progress_indeterminate)
                            downloaded_mb = sum(file_bytes.values()) / (1024 * 1024)
                            action = "라이브 녹화 중" if self.is_live else "다운로드 중"
                            progress_msg = (f"📥 {action}... {downloaded_mb:.1f}MB"
                                            f" | {speed_mb:.1f}MB/s"
                                            f" | {elapsed_minutes:02d}:{elapsed_seconds:02d}")

                        self.update_progress(progress_msg)
                
                elif d['status'] == 'finished':
                    file_speed[d.get('filename') or ''] = 0  # 끝난 스트림 속도는 합산에서 제외
                    self.log_message("✅ 파일 저장 완료")
                    self._ui(self._set_progress_percent, 100)
                    self.update_connection_status("완료")
                    self.update_stream_info("다운로드 완료")

            return progress_hook

        # 진행률 훅 함수 생성
        progress_hook = create_progress_hook(download_start_time)

        self.log_message(f"선택된 옵션: {option_label}")

        # 파일명 결정 (중복 시 -1, -2 추가)
        outtmpl = self._resolve_outtmpl()


        # yt-dlp 옵션 설정
        ydl_opts = {
            'format': selected_format,
            'outtmpl': outtmpl,
            'ffmpeg_location': self.ffmpeg_path,
            'js_runtimes': JS_RUNTIMES,
            'merge_output_format': 'mp4',
            'progress_hooks': [progress_hook],
            # 라이브는 1 고정: 병렬이면 yt-dlp가 조각 목록(끝없는 generator)을 다 받을 때까지
            # 이어붙이지 않아 -FragN 파일만 수천 개 쌓임
            'concurrent_fragment_downloads': 1 if self.is_live else 4,
            'hls_prefer_native': True,
            'hls_use_mpegts': True,
            'retries': 10,
            'fragment_retries': 10,
            'ignoreerrors': False,
            # 라이브: 요청 시점이 아닌 방송 시작 시점부터 녹화 (DASH 조각 사용)
            'live_from_start': self.is_live,
        }
        # 오디오 전용 등 선택 옵션으로 덮어쓰기
        ydl_opts.update(extra_opts)

        if self.is_live:
            self.log_message(f"📡 라이브 녹화 시작: {start_time.strftime('%H:%M:%S')}")
            self.update_stream_info("라이브 스트림 연결 중...")
        else:
            self.log_message(f"⬇️ 다운로드 시작: {start_time.strftime('%H:%M:%S')}")
            self.update_stream_info("영상 정보 분석 중...")
        self.update_progress("연결 중...")
        self.update_connection_status("YouTube 서버 연결 시도 중...")

        try:
            # yt-dlp 객체를 생성하고 다운로드(녹화) 시작
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    ydl.download([url])
                except KeyboardInterrupt:
                    self.log_message("사용자에 의해 중단됨")
                except SystemExit:
                    self.log_message("강제 중단됨")
                except Exception as e:
                    if self.stop_flag.is_set():
                        self.log_message("다운로드가 중단됨")
                    else:
                        raise e

            # 완료 시간 계산
            end_time = datetime.now()
            total_duration = end_time - start_time

            duration_str = str(total_duration).split('.')[0]
            time_label = "녹화시간" if self.is_live else "소요시간"
            if not self.stop_flag.is_set():
                self.log_message(f"✅ 완료! {time_label}: {duration_str}")
            else:
                self.log_message(f"🛑 중단됨! {time_label}: {duration_str}")

        except KeyboardInterrupt:
            end_time = datetime.now()
            total_duration = end_time - start_time
            time_label = "녹화시간" if self.is_live else "소요시간"
            self.log_message(f"🛑 사용자에 의해 중단됨! {time_label}: {str(total_duration).split('.')[0]}")
        except SystemExit:
            end_time = datetime.now()
            total_duration = end_time - start_time
            time_label = "녹화시간" if self.is_live else "소요시간"
            self.log_message(f"🛑 강제 중단됨! {time_label}: {str(total_duration).split('.')[0]}")
        except yt_dlp.utils.DownloadError as e:
            self.log_message(f"❌ 오류: 다운로드에 실패했습니다.")
            self.log_message(f"   에러 메시지: {e}")
            
            # ffmpeg 관련 오류인 경우 추가 안내
            if "ffmpeg" in str(e).lower():
                self.log_message("💡 해결 방법:")
                self.log_message("   1. ffmpeg 설치:")
                self.log_message("      - Windows: https://ffmpeg.org/download.html 에서 다운로드")
                self.log_message("      - 또는 chocolatey 사용: choco install ffmpeg")
                self.log_message("      - 또는 winget 사용: winget install ffmpeg")
                self.log_message("   2. 설치 후 시스템 PATH에 ffmpeg 추가")
                self.log_message("   3. 컴퓨터 재시작 후 다시 시도")
                self.log_message("   현재 영상은 라이브 스트림(m3u8)이므로 ffmpeg가 필요합니다.")
        except Exception as e:
            end_time = datetime.now()
            total_duration = end_time - start_time
            time_label = "녹화시간" if self.is_live else "소요시간"
            self.log_message(f"❌ 오류 발생: {e}")
            self.log_message(f"⏱️  {time_label}: {str(total_duration).split('.')[0]}")

    def run(self):
        """GUI 실행"""
        self.root.mainloop()

if __name__ == "__main__":
    # GUI 실행
    app = YouTubeDownloaderGUI()
    app.run()