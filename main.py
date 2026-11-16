import yt_dlp
import yt_dlp.downloader.external as ytdl_external
import sys
import os
import time
import threading
import shutil
import subprocess
import queue
import json
import gc
import glob
from datetime import datetime
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import re

# yt-dlp 포맷별 임시 파일명의 포맷 ID (예: "제목.f399.mp4" 의 ".f399", ".f251-drc")
FORMAT_ID_RE = re.compile(r'\.f\d+(?:-\w+)?(?=\.\w+$)')
# YouTube 서명 해제용 JS 런타임 (설치된 것만 사용됨). 없으면 일부 화질이 누락될 수 있음
JS_RUNTIMES = {'deno': {}, 'node': {}}
# '영상 조회' 제한 시간(초). 넘으면 조회를 포기하고 UI를 되돌림
FETCH_TIMEOUT = 30
# 조회 공통 옵션. noplaylist: &list= 등이 붙은 URL도 재생목록이 아닌 해당 영상만 조회
FETCH_OPTS = {'quiet': True, 'no_warnings': True, 'js_runtimes': JS_RUNTIMES,
              'noplaylist': True, 'socket_timeout': 15}
# 예정된 방송 대기 중 시작 여부 확인 간격(초): 시작 시각 전에는 최대 MAX, 지난 뒤에는 MIN
WAIT_POLL_MIN, WAIT_POLL_MAX = 30, 600
# 이 프로그램이 받은 파일 표시: 파일명의 ' [영상ID]'
# ponytail: 같은 폴더에 yt-dlp 기본 이름으로 받는 다른 프로그램의 임시 파일은 구분 못 함.
#           필요해지면 받은 파일 목록을 따로 기록해서 대조할 것
OWN_FILE_RE = re.compile(r' \[[\w-]{11}\]')
BEST_FORMAT = "bestvideo+bestaudio/best"


class TrackedPopen(ytdl_external.Popen):
    """yt-dlp가 다운로드용으로 띄우는 ffmpeg 프로세스를 기억해 둠.
    라이브를 '지금부터' 받을 때 yt-dlp는 ffmpeg로 받는데, 이 경로는 진행률 훅이 불리지 않아
    stop_flag로는 멈출 수 없음 → 중단 요청 시 여기서 직접 종료시킴."""
    running = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.running[:] = [p for p in self.running if p.poll() is None] + [self]

    @classmethod
    def stop_all(cls):
        for proc in cls.running:
            try:
                # 'q': ffmpeg에 정상 종료 요청 → 받은 부분까지 재생 가능한 파일로 마무리
                proc.stdin.write(b'q')
                proc.stdin.flush()
            except (OSError, ValueError):
                pass
            # 10초 안에 안 끝나면 강제 종료 (남은 .part는 cleanup_part_files가 '_중단_' 파일로 저장)
            threading.Timer(10, lambda p=proc: p.poll() is None and p.kill()).start()


ytdl_external.Popen = TrackedPopen


def height_format(height):
    """해당 해상도의 format 문자열. 호환성 좋은 H.264+AAC 우선, 없으면 같은 해상도의 다른 코덱,
    그래도 없으면(조회 때와 포맷 구성이 다른 라이브 등) 그 이하 해상도"""
    return (f"bestvideo[height={height}][vcodec^=avc1]+bestaudio[ext=m4a]"
            f"/bestvideo[height={height}]+bestaudio"
            f"/bestvideo[height<={height}]+bestaudio/best[height<={height}]")


def build_quality_options(formats):
    """yt-dlp 포맷 목록 → [(라벨, format 문자열, 추가 ydl_opts), ...].
    해상도마다 하나씩, 라벨은 height_format()이 실제로 고를 포맷 기준.
    포맷이 없으면(예정된 방송) 일반적인 해상도 목록을 제공."""
    videos = [f for f in formats if f.get('vcodec') not in (None, 'none') and f.get('height')]
    videos = [f for f in videos if f.get('acodec') == 'none'] or videos  # 영상 전용 우선 (bestvideo 대상)
    audios = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') not in (None, 'none')]

    def audio_desc(m4a_only=False):
        cands = [f for f in audios if f.get('ext') == 'm4a'] if m4a_only else []
        cands = cands or audios
        if not cands:
            return ""
        best = max(cands, key=lambda f: f.get('abr') or f.get('tbr') or 0)
        abr = best.get('abr') or best.get('tbr')
        return " ".join(x for x in [(best.get('acodec') or '').split('.')[0],
                                    f"{int(abr)}k" if abr else ""] if x)

    def rank(f):  # height_format()의 선택 순서: H.264 우선, 그다음 fps·비트레이트
        return ((f.get('vcodec') or '').startswith('avc1'), f.get('fps') or 0, f.get('tbr') or 0)

    best = {}  # 해상도 → 대표 포맷
    for f in videos:
        if f['height'] not in best or rank(f) > rank(best[f['height']]):
            best[f['height']] = f

    options = [("최고 화질 (자동)", (height_format(max(best)) + "/" if best else "") + BEST_FORMAT, {})]
    for height in sorted(best, reverse=True):
        f = best[height]
        label = f"{height}p"
        if f.get('fps'):
            label += f" {round(f['fps'])}fps"
        vcodec = (f.get('vcodec') or '').split('.')[0]
        if vcodec:
            label += f" ({vcodec})"
        filesize = f.get('filesize') or f.get('filesize_approx')
        if filesize:
            mb = filesize / (1024 * 1024)
            label += f" | ~{mb/1024:.1f}GB" if mb >= 1024 else f" | ~{int(mb)}MB"
        elif f.get('vbr') or f.get('tbr'):
            label += f" | {int(f.get('vbr') or f.get('tbr'))}kbps"
        audio = audio_desc(m4a_only=rank(f)[0])
        if audio:
            label += f" | 오디오: {audio}"
        options.append((label, height_format(height), {}))
    if not best:
        options += [(f"{height}p", height_format(height), {}) for height in (1080, 720, 480, 360)]

    # 오디오 전용 옵션
    def audio_opt(codec, quality=None):
        pp = {'key': 'FFmpegExtractAudio', 'preferredcodec': codec}
        if quality:
            pp['preferredquality'] = quality
        return {'postprocessors': [pp], 'merge_output_format': None}

    src = f" ({audio_desc()})" if audio_desc() else ""
    src_m4a = f" ({audio_desc(True)})" if audio_desc(True) else ""
    options += [
        (f"🎵 오디오만 - MP3 192k{src}", "bestaudio/best", audio_opt('mp3', '192')),
        (f"🎵 오디오만 - MP3 320k{src}", "bestaudio/best", audio_opt('mp3', '320')),
        (f"🎵 오디오만 - M4A{src_m4a}", "bestaudio[ext=m4a]/bestaudio/best", audio_opt('m4a')),
        (f"🎵 오디오만 - WAV{src}", "bestaudio/best", audio_opt('wav')),
    ]
    return options

# 채팅 JSON의 메시지 종류 → TXT 앞에 붙일 표시
CHAT_KINDS = {
    'liveChatTextMessageRenderer': '',
    'liveChatPaidMessageRenderer': '[슈퍼챗 {amount}] ',
    'liveChatPaidStickerRenderer': '[스티커 {amount}] ',
    'liveChatMembershipItemRenderer': '[멤버십] ',
}


def chat_json_to_txt(json_path, txt_path):
    """yt-dlp .live_chat.json → '[시간] 닉네임: 내용' 텍스트. 저장한 메시지 수 반환.
    시간: 다시보기는 영상 기준 위치, 실시간은 메시지를 보낸 시각."""
    lines = []
    with open(json_path, encoding='utf-8') as f:
        for raw in f:
            try:
                entry = json.loads(raw)
            except ValueError:
                continue  # 중단 시 잘린 마지막 줄
            replay = entry.get('replayChatItemAction', {})
            for action in replay.get('actions', []):
                item = action.get('addChatItemAction', {}).get('item', {})
                for kind, r in item.items():
                    if kind not in CHAT_KINDS:
                        continue
                    if entry.get('isLive'):
                        when = datetime.fromtimestamp(int(r.get('timestampUsec', 0)) / 1e6).strftime('%H:%M:%S')
                    else:
                        when = r.get('timestampText', {}).get('simpleText', '')
                    runs = (r.get('message') or r.get('headerSubtext') or {}).get('runs', [])
                    text = ''.join(run.get('text') or (run.get('emoji', {}).get('shortcuts') or [''])[0]
                                   for run in runs)
                    tag = CHAT_KINDS[kind].format(amount=r.get('purchaseAmountText', {}).get('simpleText', ''))
                    author = r.get('authorName', {}).get('simpleText', '')
                    lines.append(f"[{when}] {tag}{author}: {text}")
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + ('\n' if lines else ''))
    return len(lines)

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
        self.is_upcoming = False  # 예정된 방송 (시작까지 기다렸다가 녹화)
        self.has_chat = False     # 채팅이 있는 영상 (라이브·다시보기)
        self._failed = False      # 마지막 다운로드가 오류로 끝났는지
        self.video_info = None  # '영상 조회' 결과 (다운로드 시 재사용)
        self.ffmpeg_path = self._find_ffmpeg()
        self.download_dir = ''
        self._load_config()

        # 작업 스레드 → 메인 스레드 UI 작업 큐 (Tk는 스레드 안전하지 않음)
        self._ui_queue = queue.Queue()

        # 화질 선택 변수
        self.quality_options = []  # [(label, format_string), ...]
        self._fetching_quality = False
        self._fetch_id = 0  # 조회 요청 번호 (시간 초과로 버린 조회의 늦은 결과 무시용)

        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._process_ui_queue)
        if not self.ffmpeg_path:
            self.log_message("⚠️ ffmpeg를 찾을 수 없습니다. 영상/오디오 병합과 오디오 변환이 실패할 수 있습니다. "
                             "(install_ffmpeg.bat 실행)")

        # 프로그램 시작 시 이전 실행에서 남은 임시 파일 정리
        # (큰 파일 병합이 창 표시를 막지 않도록 별도 스레드. 끝나기 전에는 다운로드 시작 불가)
        self._cleanup_thread = threading.Thread(target=self.cleanup_part_files, daemon=True)
        self._cleanup_thread.start()

    def _on_close(self):
        """창 닫기. 다운로드 중이면 확인"""
        if self.is_downloading and not messagebox.askokcancel(
                "종료 확인", "다운로드(녹화)가 진행 중입니다. 종료할까요?\n"
                "받은 부분은 다음 실행 때 '_중단_' 파일로 저장됩니다."):
            return
        self.root.destroy()

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
        self.url_entry = ttk.Entry(url_frame, textvariable=self.url_var)
        self.url_entry.grid(row=1, column=0, sticky=(tk.W, tk.E), padx=(0, 6))
        self.url_entry.bind('<Return>', lambda e: self.fetch_qualities())
        self.quality_fetch_btn = ttk.Button(url_frame, text="영상 조회", command=self.fetch_qualities)
        self.quality_fetch_btn.grid(row=1, column=1, sticky=(tk.W, tk.E))

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
        dir_btn_frame = ttk.Frame(url_frame)
        dir_btn_frame.grid(row=5, column=1)
        self.dir_btn = ttk.Button(dir_btn_frame, text="변경", command=self._browse_download_dir, width=5)
        self.dir_btn.grid(row=0, column=0, padx=(0, 4))
        ttk.Button(dir_btn_frame, text="열기", width=5,
                   command=lambda: os.startfile(self.get_output_dir())).grid(row=0, column=1)

        # 다운로드 버튼
        btn_frame = ttk.Frame(url_frame)
        btn_frame.grid(row=6, column=0, columnspan=2, sticky=tk.E, pady=(12, 0))
        # 체크박스는 해당하는 영상일 때만 표시. 체크 상태는 config.json에 기억
        # 라이브·예정된 방송: 방송 시작 시점부터 받을지, 지금부터 받을지
        self.from_start_var = tk.BooleanVar(value=self.live_from_start)
        self.from_start_check = ttk.Checkbutton(btn_frame, text="⏮ 방송 처음부터", variable=self.from_start_var,
                                                command=self._save_config)
        self.from_start_check.grid(row=0, column=0, padx=(0, 12))
        self.from_start_check.grid_remove()
        # 채팅이 있는 영상(라이브·다시보기)
        self.chat_var = tk.BooleanVar(value=self.save_chat)
        self.chat_check = ttk.Checkbutton(btn_frame, text="💬 채팅도 저장", variable=self.chat_var,
                                          command=self._save_config)
        self.chat_check.grid(row=0, column=1, padx=(0, 12))
        self.chat_check.grid_remove()
        self.download_btn = ttk.Button(btn_frame, text="▶ 다운로드 시작",
                                       command=self.start_download, state="disabled")
        self.download_btn.grid(row=0, column=2, padx=(0, 6))
        self.stop_btn = ttk.Button(btn_frame, text="■ 중단",
                                   command=self.stop_download, state="disabled")
        self.stop_btn.grid(row=0, column=3)

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
        
        # 읽기 전용 (쓸 때만 잠깐 풀어줌)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, width=70, state="disabled")
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.minsize(520, 480)

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
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, line)
        self.log_text.config(state="disabled")
        self.log_text.see(tk.END)

    def _set_inputs_locked(self, locked):
        """다운로드 중에는 URL·조회·화질·폴더·옵션을 못 바꾸게 잠금 (진행 중인 작업이 참조함)"""
        state = "disabled" if locked else "normal"
        for widget in (self.url_entry, self.quality_fetch_btn, self.dir_btn,
                       self.from_start_check, self.chat_check):
            widget.config(state=state)
        self.quality_combo.config(state="disabled" if locked else "readonly")

    def _on_url_changed(self, *args):
        """URL 변경 시 화질 목록 초기화 및 다운로드 버튼 비활성"""
        if self.is_downloading:
            return
        if self._fetching_quality:
            # 진행 중인 조회는 이전 URL의 것 → 결과를 버림
            self._fetch_id += 1
            self._fetching_quality = False
            self.quality_fetch_btn.config(state="normal")
        self.from_start_check.grid_remove()
        self.video_info = None
        self.quality_options = []
        self.quality_combo['values'] = ["먼저 URL 입력 후 '영상 조회'를 눌러주세요"]
        self.quality_combo.current(0)
        self.quality_combo.config(state="disabled")
        self.download_btn.config(state="disabled")
        self.video_type_label.config(text="")
        self.chat_check.grid_remove()

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

        self._fetch_id += 1
        thread = threading.Thread(target=self._fetch_qualities_worker, args=(url, self._fetch_id))
        thread.daemon = True
        thread.start()
        self.root.after(FETCH_TIMEOUT * 1000, self._fetch_failed, self._fetch_id,
                        f"⏱️ 영상 조회가 {FETCH_TIMEOUT}초를 넘어 중단했습니다. URL을 확인 후 다시 시도해주세요.")

    def _fetch_failed(self, fetch_id, message):
        """조회 실패·시간 초과 (메인 스레드). 워커는 못 죽이므로 늦은 결과만 버리고 UI 복원"""
        if not self._fetching_quality or fetch_id != self._fetch_id:
            return
        self._fetch_id += 1  # 늦게 도착하는 결과 무시
        self._fetching_quality = False
        self.quality_combo['values'] = ["조회 실패 — 다시 '영상 조회'를 눌러주세요"]
        self.quality_combo.current(0)
        self.quality_fetch_btn.config(state="normal")
        self.log_message(message)

    def _fetch_qualities_worker(self, url, fetch_id):
        """영상 정보 조회 워커. 결과 반영은 메인 스레드(_apply_fetch_result)에서"""
        try:
            try:
                with yt_dlp.YoutubeDL(FETCH_OPTS) as ydl:
                    info = ydl.extract_info(url, download=False)
            except yt_dlp.utils.DownloadError:
                # 예정된 방송은 포맷이 없어 오류가 남 → 포맷 없이 다시 조회해서 예정된 방송인지 확인
                with yt_dlp.YoutubeDL({**FETCH_OPTS, 'ignore_no_formats_error': True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                if info.get('live_status') != 'is_upcoming':
                    raise
            if info.get('_type') == 'playlist':
                raise ValueError("재생목록 URL은 지원하지 않습니다. 개별 영상 URL을 입력해주세요.")
            options = build_quality_options(info.get('formats') or [])
            self._ui(self._apply_fetch_result, fetch_id, info, options)
        except Exception as e:
            self._ui(self._fetch_failed, fetch_id, f"화질 조회 오류: {e}")

    def _apply_fetch_result(self, fetch_id, info, options):
        """조회 결과를 화면에 반영 (메인 스레드)"""
        if fetch_id != self._fetch_id:
            return  # 시간 초과·URL 변경으로 이미 버린 조회
        self._fetching_quality = False
        self.video_info = info
        self.is_live = bool(info.get('is_live'))
        self.is_upcoming = info.get('live_status') == 'is_upcoming'
        self.has_chat = self.is_upcoming or 'live_chat' in (info.get('subtitles') or {})

        if self.is_upcoming:
            start = info.get('release_timestamp')
            when = datetime.fromtimestamp(start).strftime('%m/%d %H:%M') if start else "미정"
            self.video_type_label.config(text="⏰ 예정된 방송", fg="darkorange")
            self.log_message(f"⏰ 예정된 방송입니다 (시작 예정: {when}). "
                             "다운로드를 시작하면 방송이 시작될 때까지 기다렸다가 녹화합니다.")
        elif self.is_live:
            self.video_type_label.config(text="🔴 라이브 방송", fg="red")
            self.log_message("🔴 라이브 방송으로 감지되었습니다.")
        else:
            self.video_type_label.config(text="📹 일반 영상", fg="gray")
            self.log_message("📹 일반 영상으로 감지되었습니다.")
        if self.is_live or self.is_upcoming:
            self.from_start_check.grid()
        if self.has_chat:
            self.chat_check.grid()

        self.quality_options = options
        self.quality_combo['values'] = [opt[0] for opt in options]
        self.quality_combo.current(0)
        self.quality_combo.config(state="readonly")
        self.quality_fetch_btn.config(state="normal")
        self.download_btn.config(state="normal")
        self.log_message(f"✅ {len(options)}개의 화질 옵션을 찾았습니다.")

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
        """저장된 설정 로드. download_dir이 없으면 기본값(video/), 채팅 저장은 기본 해제"""
        import json
        default = os.path.join(self._get_base_dir(), 'video')
        self.save_chat = False
        self.live_from_start = True  # 라이브를 방송 시작 시점부터 받기
        try:
            with open(self._config_path(), 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            path = cfg.get('download_dir', default)
            self.download_dir = path if os.path.isabs(path) else default
            self.save_chat = bool(cfg.get('save_chat', False))
            self.live_from_start = bool(cfg.get('live_from_start', True))
        except Exception:
            self.download_dir = default

    def _save_config(self):
        import json
        try:
            with open(self._config_path(), 'w', encoding='utf-8') as f:
                json.dump({'download_dir': self.download_dir, 'save_chat': self.chat_var.get(),
                           'live_from_start': self.from_start_var.get()},
                          f, ensure_ascii=False, indent=2)
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
            return BEST_FORMAT, {}
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
        영상/오디오 .part가 따로 남았으면 ffmpeg로 합쳐서 하나로 저장.
        이 프로그램이 받은 파일(' [영상ID]' 포함)만 대상 — 저장 폴더의 다른 프로그램 파일은 건드리지 않음."""
        try:
            video_dir = self.get_output_dir()
            groups = {}  # 포맷 ID를 뺀 원래 파일명 → [.part 경로, ...]
            for name in os.listdir(video_dir):
                if not OWN_FILE_RE.search(name):
                    continue
                path = os.path.join(video_dir, name)
                if re.search(r'-Frag\d+(\.part)?$', name) or name.endswith('.ytdl'):
                    self.safe_remove_file(path)
                elif name.endswith('.live_chat.json.part'):
                    self._finalize_chat(path[:-len('.part')])
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
        if self._cleanup_thread.is_alive():
            # 정리 중에 받기 시작하면 새 임시 파일까지 정리 대상이 됨
            messagebox.showinfo("안내", "이전 임시 파일을 정리하는 중입니다. 잠시 후 다시 시도해주세요.")
            return

        # 위젯 값은 메인 스레드에서, 입력을 잠그기 전에 읽어서 넘김
        option_label = self.quality_combo.get() if self.quality_options else '최고 화질 (자동)'
        selected_format, extra_opts = self.get_selected_opts()

        self.is_downloading = True
        self._failed = False
        self.stop_flag.clear()  # 중단 플래그 초기화
        self._set_inputs_locked(True)
        self.download_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress_bar.config(mode='indeterminate')
        self.progress_bar.start()

        # 상태 정보 초기화
        self.filename_var.set("파일명: 연결 중...")
        self.connection_var.set("연결 상태: 연결 시도 중...")
        self.stream_var.set("영상 정보: 분석 중...")

        self.log_text.config(state="normal")
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state="disabled")

        self.download_thread = threading.Thread(
            target=self.download_worker,
            args=(url, option_label, selected_format, extra_opts,
                  self.has_chat and self.chat_var.get(), self.from_start_var.get()))
        self.download_thread.daemon = True
        self.download_thread.start()
        
    def stop_download(self):
        """다운로드 중단 요청.
        실제 중단은 진행률 훅에서 처리 (라이브는 받은 부분까지 yt-dlp가 합쳐서 저장).
        작업 스레드가 끝나야 다운로드 버튼이 다시 켜짐."""
        if not self.is_downloading or self.stop_flag.is_set():
            return
        self.stop_flag.set()
        TrackedPopen.stop_all()
        self.stop_btn.config(state="disabled")
        if self.is_live:
            self.log_message("녹화를 중단합니다. 받은 부분까지 저장 중...")
        else:
            self.log_message("다운로드를 중단합니다...")
        self.update_connection_status("중단 중...")
        self.update_stream_info("연결 해제 중...")
        self.update_progress("중단 중...")

    def _wait_for_live(self, url):
        """예정된 방송이 시작될 때까지 대기 (작업 스레드). 시작되면 True, 중단되면 False"""
        self.update_connection_status("방송 시작 대기 중")
        self.update_stream_info("예정된 방송")
        while True:
            start = (self.video_info or {}).get('release_timestamp')
            when = datetime.fromtimestamp(start).strftime('%m/%d %H:%M') if start else "미정"
            self.update_progress(f"⏰ 방송 시작 대기 중... (예정: {when})")
            remain = start - time.time() if start else 0
            if self.stop_flag.wait(min(max(remain, WAIT_POLL_MIN), WAIT_POLL_MAX)):
                return False
            try:
                with yt_dlp.YoutubeDL({**FETCH_OPTS, 'ignore_no_formats_error': True}) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception as e:
                self.log_message(f"방송 상태 확인 실패 (다시 시도 예정): {e}")
                continue
            self.video_info = info
            if info.get('live_status') != 'is_upcoming':
                self.is_live = bool(info.get('is_live'))
                self.is_upcoming = False
                self.log_message("🔴 방송이 시작되었습니다.")
                return True

    def download_worker(self, url, option_label, selected_format, extra_opts, save_chat, from_start):
        """다운로드 작업을 수행하는 워커 스레드"""
        try:
            if self.is_upcoming and not self._wait_for_live(url):
                return
            self.record_youtube_live(url, option_label, selected_format, extra_opts, save_chat, from_start)
        except KeyboardInterrupt:
            self.log_message("다운로드가 중단되었습니다.")
        except Exception as e:
            self._failed = True
            self.log_message(f"오류 발생: {e}")
        finally:
            # 중단됐으면 남은 임시 파일 정리 (ffmpeg 병합 포함, 작업 스레드에서 수행)
            if self.stop_flag.is_set():
                self.cleanup_part_files()
            self._ui(self.download_finished)

    def download_finished(self):
        """작업 스레드 종료 후 UI 상태 복원 (메인 스레드)"""
        self.is_downloading = False
        self._set_inputs_locked(False)
        self.download_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.progress_bar.stop()

        # 상태 정보 복원
        if self.stop_flag.is_set():
            self.update_progress("중단됨")
            self.update_connection_status("중단됨")
            self.update_stream_info("연결 해제됨")
        elif self._failed:
            self._set_progress_percent(0)
            self.update_progress("❌ 실패 — 아래 로그를 확인해주세요")
            self.update_connection_status("실패")
            self.update_stream_info("다운로드 실패")
        else:
            self._set_progress_percent(100)
            self.update_progress("완료")
            self.update_connection_status("완료")
            self.update_stream_info("다운로드 완료")

    def record_youtube_live(self, url, option_label, selected_format, extra_opts, save_chat, from_start):
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
                    # 스트림 하나(영상 또는 오디오)를 다 받은 것. 남은 스트림·병합·변환이 있을 수 있어
                    # '완료' 표시는 전체가 끝난 뒤 download_finished에서 함
                    file_speed[d.get('filename') or ''] = 0  # 끝난 스트림 속도는 합산에서 제외
                    self._ui(self._set_progress_indeterminate)
                    self.update_progress("⏳ 마무리 중 (병합/변환)...")
                    self.update_stream_info("마무리 중")

            return progress_hook

        # 진행률 훅 함수 생성
        progress_hook = create_progress_hook(download_start_time)

        self.log_message(f"선택된 옵션: {option_label}")

        # 파일명 결정 (중복 시 -1, -2 추가)
        outtmpl = self._resolve_outtmpl()

        # 채팅은 별도 스레드: 라이브 채팅은 방송이 끝나야 끝나서 영상과 동시에 받아야 함
        chat_stop = threading.Event()
        chat_thread = None
        if save_chat:
            if 'live_chat' in (self.video_info or {}).get('subtitles', {}):
                chat_thread = threading.Thread(target=self._download_chat,
                                               args=(url, outtmpl, chat_stop), daemon=True)
                chat_thread.start()
            else:
                self.log_message("💬 채팅이 없는 영상이라 채팅은 저장하지 않습니다.")

        # yt-dlp 옵션 설정
        ydl_opts = {
            'format': selected_format,
            'outtmpl': outtmpl,
            'ffmpeg_location': self.ffmpeg_path,
            'js_runtimes': JS_RUNTIMES,
            'noplaylist': True,
            'merge_output_format': 'mp4',
            'progress_hooks': [progress_hook],
            # 라이브는 1 고정: 병렬이면 yt-dlp가 조각 목록(끝없는 generator)을 다 받을 때까지
            # 이어붙이지 않아 -FragN 파일만 수천 개 쌓임
            'concurrent_fragment_downloads': 1 if self.is_live else 4,
            'hls_prefer_native': True,
            # hls_use_mpegts는 지정하지 않음: 라이브는 yt-dlp가 받는 동안 MPEG-TS(중단에 안전)로 쓰고
            # 끝나면 정식 MP4로 변환함. True로 고정하면 .mp4 안에 MPEG-TS가 그대로 남음
            'retries': 10,
            'fragment_retries': 10,
            'ignoreerrors': False,
            # 라이브: 요청 시점이 아닌 방송 시작 시점부터 녹화 (DASH 조각 사용). 끄면 지금부터
            'live_from_start': self.is_live and from_start,
        }
        if self.is_live:
            # 처음부터 받는 중에는 영상·오디오가 따로 따라잡아서 도중에 끊으면 길이가 다름
            # (오디오가 훨씬 앞서 있음) → 합칠 때 짧은 쪽에 맞춤
            ydl_opts['postprocessor_args'] = {'merger': ['-shortest']}
            # '지금부터'는 ffmpeg가 영상+오디오를 한 번에 받아 .mp4 이름에 MPEG-TS 내용이 남고,
            # yt-dlp는 이 경우 자동 변환을 건너뜀 → 정식 MP4로 변환 (MPEG-TS일 때만 동작)
            ydl_opts['postprocessors'] = [{'key': 'FFmpegFixupM3u8'}]
        # 오디오 전용 등 선택 옵션으로 덮어쓰기
        ydl_opts.update(extra_opts)

        if self.is_live:
            self.log_message(f"📡 라이브 녹화 시작 ({'방송 처음부터' if from_start else '지금부터'}): "
                             f"{start_time.strftime('%H:%M:%S')}")
            self.update_stream_info("라이브 스트림 연결 중...")
        else:
            self.log_message(f"⬇️ 다운로드 시작: {start_time.strftime('%H:%M:%S')}")
            self.update_stream_info("영상 정보 분석 중...")
        self.update_progress("연결 중...")
        self.update_connection_status("YouTube 서버 연결 시도 중...")

        # '지금부터' 라이브는 ffmpeg가 받아서 진행률 훅이 불리지 않음 → 받은 파일 크기로 진행 표시
        size_poll_done = threading.Event()
        if self.is_live and not from_start:
            def poll_size():
                pattern = glob.escape(outtmpl[:-len('.%(ext)s')]) + '*'
                while not size_poll_done.wait(1) and not self.stop_flag.is_set():
                    try:
                        size = sum(os.path.getsize(p) for p in glob.glob(pattern) if '.live_chat.' not in p)
                    except OSError:
                        continue  # 이름이 바뀌는 순간
                    if size:
                        elapsed = int(time.time() - download_start_time)
                        self._ui(self.filename_var.set,
                                 f"파일명: {os.path.basename(outtmpl[:-len('.%(ext)s')])}")
                        self.update_connection_status("연결됨")
                        self.update_stream_info("라이브 스트림 활성")
                        self.update_progress(f"📥 라이브 녹화 중... {size / (1024 * 1024):.1f}MB"
                                             f" | {elapsed // 60:02d}:{elapsed % 60:02d}")
            threading.Thread(target=poll_size, daemon=True).start()

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
            self._failed = True
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
                self.log_message("   영상/오디오 병합과 오디오 변환에 ffmpeg가 필요합니다.")
        except Exception as e:
            self._failed = True
            end_time = datetime.now()
            total_duration = end_time - start_time
            time_label = "녹화시간" if self.is_live else "소요시간"
            self.log_message(f"❌ 오류 발생: {e}")
            self.log_message(f"⏱️  {time_label}: {str(total_duration).split('.')[0]}")
        finally:
            size_poll_done.set()
            if chat_thread:
                # 라이브 채팅은 영상과 함께 종료. 다시보기 채팅은 끝까지 받음
                if self.is_live:
                    chat_stop.set()
                if chat_thread.is_alive():
                    self.update_progress("💬 채팅 저장 마무리 중...")
                chat_thread.join()

    def _download_chat(self, url, outtmpl, chat_stop):
        """채팅을 .live_chat.json으로 저장 후 .txt 변환 (작업 스레드)"""
        chat_file = None

        def chat_hook(d):
            nonlocal chat_file
            chat_file = d.get('filename') or chat_file
            if d['status'] == 'downloading' and (self.stop_flag.is_set() or chat_stop.is_set()):
                raise KeyboardInterrupt("채팅 저장 중단")

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'writesubtitles': True,
            'subtitleslangs': ['live_chat'],
            'outtmpl': outtmpl,
            'js_runtimes': JS_RUNTIMES,
            'noplaylist': True,
            'progress_hooks': [chat_hook],
        }
        self.log_message("💬 채팅 저장 시작")
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except KeyboardInterrupt:
            pass
        except Exception as e:
            self.log_message(f"💬 채팅 저장 오류: {e}")
        # 중단 예외로 끝나면 yt-dlp가 채팅 파일 핸들을 닫지 않음 → 순환 참조 정리로 닫히게 함
        gc.collect()

        if chat_file:
            self._finalize_chat(chat_file)
        else:
            self.log_message("💬 채팅을 받지 못했습니다.")

    def _finalize_chat(self, chat_file):
        """중단으로 .part로 남은 채팅 JSON을 정식 이름으로 바꾸고 .txt 생성"""
        for frag in glob.glob(glob.escape(chat_file) + '.part-Frag*'):
            self.safe_remove_file(frag)
        if os.path.exists(chat_file + '.part'):
            os.replace(chat_file + '.part', chat_file)
        if not os.path.exists(chat_file):
            return
        txt_path = chat_file[:-len('.json')] + '.txt'
        count = chat_json_to_txt(chat_file, txt_path)
        self.log_message(f"💬 채팅 저장됨: {os.path.basename(txt_path)} ({count}개)")

    def run(self):
        """GUI 실행"""
        self.root.mainloop()

if __name__ == "__main__":
    # GUI 실행
    app = YouTubeDownloaderGUI()
    app.run()