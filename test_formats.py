"""화질 옵션 자가 점검: python test_formats.py (yt-dlp 필요, 네트워크 불필요)
라벨의 해상도와 yt-dlp가 실제로 고르는 포맷이 일치하는지 확인."""
import yt_dlp

from main import BEST_FORMAT, build_quality_options


def video(fid, height, vcodec, ext, tbr, acodec='none'):
    return {'format_id': fid, 'url': f'http://x/{fid}', 'protocol': 'https', 'ext': ext,
            'height': height, 'width': height * 16 // 9, 'fps': 30, 'tbr': tbr,
            'vcodec': vcodec, 'acodec': acodec}


def audio(fid, acodec, ext, abr):
    return {'format_id': fid, 'url': f'http://x/{fid}', 'protocol': 'https', 'ext': ext,
            'abr': abr, 'tbr': abr, 'vcodec': 'none', 'acodec': acodec}


def pick(formats, fmt):
    """yt-dlp가 format 문자열로 실제 고르는 포맷 ID 목록"""
    with yt_dlp.YoutubeDL({'format': fmt, 'quiet': True}) as ydl:
        info = ydl.process_ie_result(
            {'id': 'x', 'title': 't', 'extractor': 'test', 'webpage_url': 'http://x',
             'formats': [dict(f) for f in formats]}, download=False)
    return [f['format_id'] for f in info.get('requested_formats') or [info]]


# 일반 영상: 1080p까지는 H.264(mp4), 2160p는 VP9(webm)만 있음
vod = [
    video('136', 720, 'avc1.4d401f', 'mp4', 2000),
    video('137', 1080, 'avc1.640028', 'mp4', 4000),
    video('248', 1080, 'vp09.00.40.08', 'webm', 2500),
    video('313', 2160, 'vp09.00.50.08', 'webm', 15000),
    audio('140', 'mp4a.40.2', 'm4a', 128),
    audio('251', 'opus', 'webm', 160),
]
opts = build_quality_options(vod)
labels = [o[0] for o in opts]
assert [l.split('p')[0] for l in labels[1:4]] == ['2160', '1080', '720'], labels  # 해상도마다 하나씩
assert 'vp09' in labels[1] and 'opus' in labels[1], labels[1]
assert 'avc1' in labels[2] and 'mp4a' in labels[2], labels[2]
assert pick(vod, opts[0][1]) == ['313', '251']  # 최고 화질 = 실제 최고 해상도 (mp4 1080p 아님)
assert pick(vod, opts[1][1]) == ['313', '251']  # 2160p → 2160p
assert pick(vod, opts[2][1]) == ['137', '140']  # 1080p → H.264 + AAC
assert pick(vod, opts[3][1]) == ['136', '140']

# 라이브(HLS): 영상+오디오가 합쳐진 포맷뿐
hls = [video('95', 720, 'avc1.4d401f', 'mp4', 2000, acodec='mp4a.40.2'),
       video('96', 1080, 'avc1.640028', 'mp4', 4000, acodec='mp4a.40.2')]
opts = build_quality_options(hls)
assert pick(hls, opts[0][1]) == ['96'] and pick(hls, opts[2][1]) == ['95']

# 예정된 방송: 포맷 없음 → 일반 해상도 목록
opts = build_quality_options([])
assert opts[0][1] == BEST_FORMAT and [o[0] for o in opts[1:5]] == ['1080p', '720p', '480p', '360p']
assert pick(vod, opts[1][1]) == ['137', '140']
# 고른 해상도가 실제로는 없으면 그 이하에서 받음 (1080p 선택, 720p만 있음)
assert pick([vod[0]] + vod[4:], opts[1][1]) == ['136', '251']
print('ok')
