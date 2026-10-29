"""chat_json_to_txt 자가 점검: python test_chat.py"""
import json
import os
import tempfile
from datetime import datetime

from main import chat_json_to_txt


def msg(kind, author, runs, **extra):
    return {'addChatItemAction': {'item': {kind: {
        'authorName': {'simpleText': author}, 'message': {'runs': runs}, **extra}}}}


ts = 1761649200_000000  # timestampUsec
lines = [
    # 다시보기: 영상 기준 시간(timestampText)
    {'replayChatItemAction': {'actions': [msg('liveChatTextMessageRenderer', '@a', [
        {'text': '안녕 '}, {'emoji': {'shortcuts': [':wave:']}}], timestampText={'simpleText': '1:02:03'})],
        'videoOffsetTimeMsec': '3723000'}},
    # 실시간: 보낸 시각(timestampUsec)
    {'replayChatItemAction': {'actions': [msg('liveChatPaidMessageRenderer', '@b', [{'text': '후원'}],
        timestampUsec=str(ts), purchaseAmountText={'simpleText': '₩1,000'})]},
     'videoOffsetTimeMsec': '0', 'isLive': True},
    # 메시지 아닌 이벤트는 무시
    {'replayChatItemAction': {'actions': [{'markChatItemAsDeletedAction': {}}]}},
]

with tempfile.TemporaryDirectory() as d:
    src, dst = os.path.join(d, 'c.json'), os.path.join(d, 'c.txt')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('\n'.join(json.dumps(x, ensure_ascii=False) for x in lines))
        f.write('\n{"replayChatItemAc')  # 중단으로 잘린 마지막 줄
    assert chat_json_to_txt(src, dst) == 2
    got = open(dst, encoding='utf-8').read().splitlines()

live_time = datetime.fromtimestamp(ts / 1e6).strftime('%H:%M:%S')
assert got == ['[1:02:03] @a: 안녕 :wave:', f'[{live_time}] [슈퍼챗 ₩1,000] @b: 후원'], got
print('ok')
