"""텔레그램으로 직원 채널에 배포.

<pre> 로 본문을 감싸면 모바일에서 탭 1회로 전체 복사가 되므로
직원이 커뮤니티에 붙여넣기 하기 쉽다.
"""
import html
import time
import requests

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, FOOTER
from src.personas import TONES

API = "https://api.telegram.org/bot{}/{}"

BOARD_LABEL = {"stock": "종목방", "free": "자유게시판"}
KIND_LABEL = {
    "disclosure": "공시", "research": "리포트", "flow": "특징주",
    "policy": "정책", "poll": "발제", "theme": "테마",
}


def _text(p: dict) -> str:
    name = html.escape(p.get("stock_name") or "테마")
    code = " ({})".format(p["stock_code"]) if p.get("stock_code") else ""
    head = (
        f"<b>{name}{code}</b>\n"
        f"{KIND_LABEL.get(p['kind'], p['kind'])} · {TONES[p['tone']]['name']} · "
        f"{BOARD_LABEL.get(p.get('board', 'stock'))}\n"
        f"<a href=\"{html.escape(p['src'])}\">원문 보기</a>\n"
    )
    body = p["body"].strip() + "\n\n" + FOOTER.format(src=p["src"])
    return head + f"<pre>{html.escape(body)}</pre>"


def send_all(posts: list[dict]) -> int:
    if not TELEGRAM_TOKEN:
        print("[tg] TELEGRAM_TOKEN 없음 → 스킵")
        return 0

    sent = 0
    for i, p in enumerate(posts, 1):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "parse_mode": "HTML",
            "text": _text(p)[:4000],
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "✅ 게시완료", "callback_data": f"done:{p['id']}"},
                    {"text": "🚫 반려",     "callback_data": f"drop:{p['id']}"},
                ]]
            },
        }
        r = requests.post(API.format(TELEGRAM_TOKEN, "sendMessage"), json=payload, timeout=15)
        if r.ok:
            sent += 1
        else:
            print(f"[tg] 실패 {p['id']}: {r.text[:200]}")
        time.sleep(0.06)          # 초당 ~30건 제한 회피

    return sent


def send_summary(posts: list[dict], sent: int):
    from collections import Counter
    c = Counter(p["kind"] for p in posts)
    t = Counter(TONES[p["tone"]]["name"] for p in posts)
    msg = (
        f"<b>오늘의 배포 요약</b>\n"
        f"총 {sent}건 전송\n"
        f"유형: " + ", ".join(f"{k} {v}" for k, v in c.items()) + "\n"
        f"톤: " + ", ".join(f"{k} {v}" for k, v in t.items())
    )
    requests.post(API.format(TELEGRAM_TOKEN, "sendMessage"),
                  json={"chat_id": TELEGRAM_CHAT_ID, "parse_mode": "HTML", "text": msg},
                  timeout=15)
