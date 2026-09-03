"""정책·거시 소스 (RSS). 종목코드가 없으므로 '테마글'로 분류되어
자유게시판/테마방에 게시된다.
"""
import re
import time
import requests
from xml.etree import ElementTree as ET

from config import REQUEST_DELAY, USER_AGENT

FEEDS = [
    ("정책브리핑", "https://www.korea.kr/rss/policy.xml"),
    ("기획재정부", "https://www.korea.kr/rss/dept_moef.xml"),
    ("금융위원회", "https://www.korea.kr/rss/dept_fsc.xml"),
    ("산업통상자원부", "https://www.korea.kr/rss/dept_motie.xml"),
]

# 증시와 연결되는 키워드만
KEYWORDS = [
    "금리", "물가", "수출", "반도체", "배터리", "이차전지", "원전", "방산",
    "바이오", "세제", "감세", "규제", "지원", "투자", "예산", "환율",
    "부동산", "AI", "인공지능", "자동차", "조선", "면세", "공급망",
]


def fetch(limit: int = 8) -> list[dict]:
    out = []
    for dept, url in FEEDS:
        if len(out) >= limit:
            break
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
            root = ET.fromstring(r.content)
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                desc = re.sub(r"<[^>]+>", "", it.findtext("description") or "").strip()
                if not title or not any(k in title + desc for k in KEYWORDS):
                    continue
                out.append({
                    "id": "pol-" + re.sub(r"\W", "", title)[:24],
                    "kind": "policy",
                    "stock_code": None,
                    "stock_name": None,
                    "title": title,
                    "facts": (
                        f"발표기관: {dept}\n"
                        f"제목: {title}\n"
                        f"내용: {desc[:800]}\n"
                        f"※ 수혜 종목을 특정하거나 추천하지 말 것. 산업/테마 수준으로만 언급."
                    ),
                    "src": (it.findtext("link") or "").strip(),
                })
                if len(out) >= limit:
                    break
        except Exception as e:
            print(f"[policy] {dept} 실패: {e}")
        time.sleep(REQUEST_DELAY)

    print(f"[policy] {len(out)}건 수집")
    return out


def make_polls(pool: list[dict], n: int = 3) -> list[dict]:
    """수집된 항목에서 토론 발제글 시드를 파생시킨다."""
    seeds = [x for x in pool if x["kind"] in ("policy", "flow")][:n]
    out = []
    for s in seeds:
        out.append({
            **s,
            "id": "poll-" + s["id"],
            "kind": "poll",
            "facts": s["facts"] + "\n※ 결론을 내리지 말고, 찬반이 갈릴 만한 질문을 던져 마무리할 것.",
        })
    return out
