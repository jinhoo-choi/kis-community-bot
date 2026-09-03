"""텔레그램으로 직원 채널에 배포.

<pre> 로 본문을 감싸면 모바일에서 탭 1회로 전체 복사가 되므로
직원이 커뮤니티에 붙여넣기 하기 쉽다.

중요: <pre> 안에는 '커뮤니티에 그대로 붙여넣을 본문'만 들어간다.
      AI 생성 표기·출처·투자책임 고지는 한국투자 앱이 게시 시 자동으로 붙이므로
      복사 대상에서 제외해야 한다. 안에 두면 직원이 매번 지워야 하고,
      안 지우면 앱 문구와 중복 표기된다.
      → 고지 문구는 <pre> 밖에 참고용으로만 표시한다.
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
        f"<i>{p.get('provider','?')} · 심사 {(p.get('score') or {}).get('total','-')}/20</i>\n"
        f"<a href=\"{html.escape(p['src'])}\">원문 보기</a>\n"
    )
    # <pre> 안 = 복사 대상 (본문만)
    body = html.escape(p["body"].strip())
    # <pre> 밖 = 복사되지 않는 참고 정보. 앱이 자동 표기하므로 여기선 안내만 한다.
    note = html.escape(FOOTER.format(src=p["src"]).replace("\n", " / "))
    return head + f"<pre>{body}</pre>\n<i>↑ 박스만 복사됩니다 · 앱 자동표기: {note}</i>"


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
        }
        # 콜백 버튼은 제거했다. GitHub Actions 는 1회 실행 후 종료하므로
        # 콜백을 수신할 상시 프로세스가 없어 버튼이 동작하지 않는다.
        # 소진율 측정이 필요해지면 webhook 을 별도 구축한다.
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
    v = Counter(p.get("provider", "?") for p in posts)
    scored = [(p.get("score") or {}).get("total") for p in posts]
    scored = [x for x in scored if x]
    avg = f"{sum(scored)/len(scored):.1f}/20" if scored else "-"
    msg = (
        f"<b>오늘의 배포 요약</b>\n"
        f"총 {sent}건 전송 · 평균 심사점수 {avg}\n"
        f"유형: " + ", ".join(f"{k} {v}" for k, v in c.items()) + "\n"
        f"톤: " + ", ".join(f"{k} {v}" for k, v in t.items()) + "\n"
        f"모델: " + ", ".join(f"{k} {n}" for k, n in v.items())
    )
    requests.post(API.format(TELEGRAM_TOKEN, "sendMessage"),
                  json={"chat_id": TELEGRAM_CHAT_ID, "parse_mode": "HTML", "text": msg},
                  timeout=15)


def send_warning(text: str):
    """운영 이상 알림. 게시글 카드와 섞이지 않도록 별도 포맷."""
    if not TELEGRAM_TOKEN:
        return
    requests.post(API.format(TELEGRAM_TOKEN, "sendMessage"),
                  json={"chat_id": TELEGRAM_CHAT_ID, "parse_mode": "HTML",
                        "text": f"<b>[운영 경고]</b>\n{html.escape(text)}"},
                  timeout=15)
