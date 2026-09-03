"""키 검증 헬퍼.

워크플로 YAML 안에 파이썬을 인라인으로 넣으면 블록 스칼라 들여쓰기가 깨져
`could not find expected ':'` 로 워크플로 자체가 무효화된다. 파일로 분리한다.

비밀값 취급 원칙:
  - 레포에 커밋되는 data/verify.txt : ok/fail 과 마스킹된 chat_id 만
  - chat_id 전체 값 : GITHUB_STEP_SUMMARY (Actions UI 전용, 커밋되지 않음)
"""
import json
import os
import sys

VERIFY = "data/verify.txt"


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"_err": f"{type(e).__name__}: {e}"}


def getme():
    d = _load("/tmp/me.json")
    r = d.get("result") or {}
    if d.get("ok"):
        print(f"TELEGRAM_TOKEN   : OK  @{r.get('username','?')} ({r.get('first_name','')})")
        # Privacy Mode 가 ON 이면 그룹에서 봇을 명시하지 않은 메시지가 보이지 않는다
        grp = r.get("can_read_all_group_messages")
        print(f"  privacy_mode   : {'OFF (전체 수신)' if grp else 'ON (봇 명시 필요)'}")
        print(f"  can_join_groups: {r.get('can_join_groups')}")
    else:
        print(f"TELEGRAM_TOKEN   : FAIL  {str(d.get('description') or d.get('_err'))[:60]}")


def webhook():
    """getUpdates 가 비는 가장 흔한 원인: webhook 이 설정되어 있으면 폴링이 막힌다."""
    d = _load("/tmp/wh.json")
    r = d.get("result") or {}
    url = r.get("url") or ""
    if url:
        print(f"  webhook        : 설정됨 → getUpdates 차단 상태  {url[:40]}")
        print("                   해결: deleteWebhook 호출 필요")
    else:
        print("  webhook        : 없음 (getUpdates 정상 사용 가능)")
    if r.get("pending_update_count"):
        print(f"  pending        : {r.get('pending_update_count')}건 대기 중")
    if r.get("last_error_message"):
        print(f"  last_error     : {str(r.get('last_error_message'))[:50]}")


def updates():
    d = _load("/tmp/u.json")
    if not d.get("ok", True) or "_err" in d:
        print(f"  getUpdates     : FAIL {str(d.get('description') or d.get('_err'))[:60]}")
    n = len(d.get("result", []))
    print(f"  update 개수    : {n}")
    if n:
        # 어떤 종류의 업데이트가 왔는지 보여준다 (message 가 아니면 파서가 놓칠 수 있음)
        kinds = set()
        for u in d.get("result", []):
            kinds |= {k for k in u if k != "update_id"}
        print(f"  update 종류    : {sorted(kinds)}")
    chats = {}
    for u in d.get("result", []):
        for k in ("message", "edited_message", "channel_post",
                  "my_chat_member", "chat_member"):
            c = (u.get(k) or {}).get("chat")
            if c:
                chats[c["id"]] = (
                    c.get("type", "?"),
                    c.get("title") or c.get("username") or c.get("first_name") or "",
                )

    with open(VERIFY, "a", encoding="utf-8") as f:
        if not chats:
            f.write("CHAT 후보        : 없음 — 봇 초대 후 그룹에 '/start@봇아이디' 전송 필요\n")
        for cid, (t, title) in chats.items():
            s = str(cid)
            f.write(f"CHAT 후보        : {t:10s} {'*' * max(0, len(s) - 4)}{s[-4:]}  {title}\n")

    sm = os.environ.get("GITHUB_STEP_SUMMARY")
    if not sm:
        return
    with open(sm, "a", encoding="utf-8") as f:
        f.write("## TELEGRAM_CHAT_ID 후보\n\n")
        if not chats:
            f.write("업데이트가 없습니다.\n\n")
            f.write("1. 봇을 그룹에 초대\n")
            f.write("2. 그룹 채팅에 `/start@봇아이디` 전송 "
                    "(Privacy Mode 가 기본 ON 이라 봇을 명시하지 않으면 보이지 않습니다)\n")
            f.write("3. 이 워크플로를 다시 실행\n")
        else:
            f.write("| type | chat_id | 이름 |\n|---|---|---|\n")
            for cid, (t, title) in chats.items():
                f.write(f"| {t} | `{cid}` | {title} |\n")
            f.write("\n위 값을 `TELEGRAM_CHAT_ID` 시크릿으로 등록하세요.\n")


def dart():
    d = _load("/tmp/dart.json")
    st = d.get("status")
    msg = {
        "000": "OK",
        "010": "FAIL 등록되지 않은 키",
        "011": "FAIL 사용 중지된 키",
        "012": "FAIL 접근 불가 IP",
        "013": "OK (해당일 공시 없음)",
        "020": "FAIL 요청 한도 초과",
        "100": "FAIL 필드 오류",
        "800": "FAIL 시스템 점검",
    }.get(st, f"FAIL status={st} {str(d.get('message') or d.get('_err'))[:40]}")
    print(f"DART_API_KEY     : {msg}  총 {d.get('total_count', 0)}건")


def resolve():
    """CHAT_SUFFIX(뒤 4자리)로 chat_id 를 찾아 표준출력한다.

    전체 chat_id 를 퍼블릭 레포에 남기지 않고 테스트 발송을 하기 위한 경로.
    운영에서는 TELEGRAM_CHAT_ID 시크릿을 쓴다 — getUpdates 는 24시간만 보관하므로
    이 방식을 상시 운영에 쓰면 어느 날 조용히 실패한다.
    """
    suffix = (os.environ.get("CHAT_SUFFIX") or "").strip()
    d = _load("/tmp/u.json")
    for u in d.get("result", []):
        for k in ("message", "edited_message", "channel_post",
                  "my_chat_member", "chat_member"):
            c = (u.get(k) or {}).get("chat")
            if c and str(c["id"]).endswith(suffix):
                print(c["id"])
                return
    print("")


def testmsg():
    """테스트 발송 본문. 실제 배포 카드와 같은 형식으로 보여 준다."""
    print(
        "<b>[kis-community-bot] 연결 테스트</b>\n"
        "봇 연결이 정상입니다.\n"
        "이후 이 채널로 매일 아침 게시글 초안이 배포됩니다.\n\n"
        "<b>산일전기 (062040)</b>\n"
        "리포트 · 전문 · 종목방\n"
        "<pre>아래 회색 박스가 실제 게시글 초안입니다.\n"
        "모바일에서 박스를 한 번 탭하면 전체가 복사됩니다.\n"
        "복사해서 커뮤니티에 그대로 붙여넣으시면 됩니다.\n\n"
        "— AI 생성 · 출처 표기\n"
        "※ 투자 판단과 그 책임은 본인에게 있습니다.</pre>"
    )


if __name__ == "__main__":
    {"getme": getme, "updates": updates, "dart": dart,
     "webhook": webhook, "resolve": resolve,
     "testmsg": testmsg}[sys.argv[1]]()
