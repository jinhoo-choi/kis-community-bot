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
    else:
        print(f"TELEGRAM_TOKEN   : FAIL  {str(d.get('description') or d.get('_err'))[:60]}")


def updates():
    d = _load("/tmp/u.json")
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


if __name__ == "__main__":
    {"getme": getme, "updates": updates, "dart": dart}[sys.argv[1]]()
