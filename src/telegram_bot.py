"""텔레그램으로 직원 채널에 배포.

카드는 세 줄이다.

    카테고리 : 공시 · 삼성전자 (005930)
    담당 : 김선임
    [회색 복사 블록]

빼기로 한 것과 이유
  - 원문 링크 / 진행 순번 / provider / 심사 점수
      담당자가 게시할 때 쓰지 않는 정보다. 운영 지표는 run_stats.jsonl 에 남는다.
  - AI 생성·출처·투자책임 고지와 그 안내 문구
      한국투자 앱이 게시 시 자동으로 붙인다. 카드에 적으면 노이즈다.

잘림/복사 관련 (실측 이슈 수정)
  - 조립된 HTML 을 [:4000] 으로 자르면 태그 중간이 끊겨 </pre> 가 사라지고
    메시지가 깨진 채 도착한다. 자르려면 '본문'을 자르고 HTML 은 건드리지 않는다.
  - 복사 버튼은 <pre><code class="language-..."> 형태에서 안정적으로 표시된다.
    plain <pre> 는 클라이언트에 따라 길게 누르기를 요구한다.
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

BODY_LIMIT = 3000          # 본문만 자른다. 카드 전체를 자르지 않는다.


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def card(p: dict, idx: int = 0, total: int = 0) -> str:
    """배포 카드.

    담당자가 필요한 건 세 가지뿐이다: 어떤 종류인지 / 내 것인지 / 무엇을 복사하는지.
    나머지(원문 링크, 심사 점수, 고지 문구 안내)는 전부 뺐다.
    고지 문구는 한국투자 앱이 게시 시 자동으로 붙이므로 여기서 설명할 필요가 없다.
    """
    kind = KIND_LABEL.get(p.get("kind"), p.get("kind", ""))
    who = _esc(p.get("assignee") or "미지정")

    # 종목이 있는 건은 카테고리 옆에 종목명(코드)까지 붙인다
    if p.get("stock_code"):
        cat = f"{kind} · {_esc(p.get('stock_name') or '')} ({p['stock_code']})"
    else:
        cat = kind

    body = p.get("body", "").strip()
    if len(body) > BODY_LIMIT:
        body = body[:BODY_LIMIT].rstrip() + "…"

    return (
        f"카테고리 : {cat}\n"
        f"담당 : {who}\n"
        f'<pre><code class="language-복사">{_esc(body)}</code></pre>'
    )


# 하위 호환 (테스트에서 사용)
def _text(p: dict) -> str:
    return card(p)


def _post(method: str, payload: dict):
    return requests.post(API.format(TELEGRAM_TOKEN, method), json=payload, timeout=20)


def send_all(posts: list[dict]) -> int:
    if not TELEGRAM_TOKEN:
        print("[tg] TELEGRAM_TOKEN 없음 → 스킵")
        return 0

    total = len(posts)
    sent = 0
    for i, p in enumerate(posts, 1):
        r = _post("sendMessage", {
            "chat_id": TELEGRAM_CHAT_ID,
            "parse_mode": "HTML",
            "text": card(p, i, total),
            "disable_web_page_preview": True,
        })
        if r.ok:
            sent += 1
        else:
            print(f"[tg] 실패 {p['id']}: {r.text[:300]}")
        time.sleep(0.06)          # 초당 ~30건 제한 회피
    return sent


def send_brief(posts: list[dict], stats_row: dict = None):
    """배포 시작 전에 보내는 개요. 담당자가 자기 할당량을 먼저 파악한다."""
    if not TELEGRAM_TOKEN or not posts:
        return
    from collections import Counter
    from src import assign

    by_person = Counter(p.get("assignee") or "미지정" for p in posts)
    by_board = Counter(BOARD_LABEL.get(p.get("board", "stock"), "종목방") for p in posts)

    lines = [f"<b>오늘의 게시글 {len(posts)}건</b>", ""]
    for who, n in by_person.most_common():
        stocks = [p.get("stock_name") or "테마" for p in posts
                  if (p.get("assignee") or "미지정") == who]
        lines.append(f"· <b>{_esc(who)}</b> {n}건 — {_esc(', '.join(stocks))}")
    lines += ["", f"게시판: " + ", ".join(f"{k} {v}" for k, v in by_board.items())]
    lines.append("아래 카드를 순서대로 확인하고, 담당 건만 게시해 주세요.")

    _post("sendMessage", {"chat_id": TELEGRAM_CHAT_ID, "parse_mode": "HTML",
                          "text": "\n".join(lines), "disable_web_page_preview": True})


def send_summary(posts: list[dict], sent: int, stats_row: dict = None):
    """배포 후 운영 요약. 임원·관리자용 지표는 여기에만 모은다."""
    if not TELEGRAM_TOKEN:
        return
    from collections import Counter
    from src import assign

    c = Counter(KIND_LABEL.get(p["kind"], p["kind"]) for p in posts)
    scored = [(p.get("score") or {}).get("total") for p in posts]
    scored = [x for x in scored if x]
    avg = f"{sum(scored)/len(scored):.1f}/20" if scored else "미측정"

    lines = [
        f"<b>배포 완료</b>  {sent}/{len(posts)}건 전송",
        f"담당: {assign.summary(posts)}",
        f"유형: " + (", ".join(f"{k} {v}" for k, v in c.items()) or "-"),
        f"문체: {len({(p.get('tone'), p.get('angle')) for p in posts})}가지 조합"
        f" ({__import__('config').PERSONA_MODE})",
        f"품질 심사 평균: {avg}",
    ]
    if stats_row:
        held = stats_row.get("held", 0)
        blocked = stats_row.get("gate_blocked", 0)
        lines.append(f"필터: 게이트 차단 {blocked}건 · 배포 보류 {held}건")
        deg = [k for k, v in (stats_row.get("crawl_health") or {}).items()
               if v.get("degraded")]
        if deg:
            lines.append(f"⚠ 수집 이상: {', '.join(deg)}")

    _post("sendMessage", {"chat_id": TELEGRAM_CHAT_ID, "parse_mode": "HTML",
                          "text": "\n".join(lines), "disable_web_page_preview": True})


def send_warning(text: str):
    """운영 이상 알림. 게시글 카드와 섞이지 않도록 별도 포맷."""
    if not TELEGRAM_TOKEN:
        return
    _post("sendMessage", {"chat_id": TELEGRAM_CHAT_ID, "parse_mode": "HTML",
                          "text": f"<b>[운영 경고]</b>\n{_esc(text)}"})
