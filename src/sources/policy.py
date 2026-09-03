"""정책·거시 소스.

korea.kr(정책브리핑·기재부·금융위·산업부)은 GitHub Actions 의 해외 IP 에서
전건 ConnectTimeout 이 난다 (진단 실측). 국내 IP 화이트리스트로 보인다.
  → 국내 러너(self-hosted)나 국내 프록시를 쓰면 원 소스를 되살릴 수 있다.
    그전까지는 접근 가능한 대체 소스를 쓴다.

진단에서 살아있음이 확인된 소스만 사용한다.
  연합뉴스 경제 RSS : HTTP 200, 정상 XML
  한국은행          : HTTP 200 (HTML)

주의: 언론사 콘텐츠이므로 제목·요지만 사용하고 본문은 재배포하지 않는다.
      정부 보도자료가 아니므로 '발표'가 아닌 '보도'로 취급한다.
"""
import re
from xml.etree import ElementTree as ET

import requests

from config import USER_AGENT
from src import crawl

FEEDS = [
    ("연합뉴스 경제", "https://www.yna.co.kr/rss/economy.xml"),
    ("연합뉴스 산업", "https://www.yna.co.kr/rss/industry.xml"),
]

# korea.kr 계열. 국내 IP 에서만 열리므로 실패해도 경고 대상에서 제외한다.
OPTIONAL_FEEDS = [
    ("정책브리핑", "https://www.korea.kr/rss/policy.xml"),
    ("기획재정부", "https://www.korea.kr/rss/dept_moef.xml"),
    ("금융위원회", "https://www.korea.kr/rss/dept_fsc.xml"),
    ("산업통상자원부", "https://www.korea.kr/rss/dept_motie.xml"),
]

KEYWORDS = [
    "금리", "물가", "수출", "반도체", "배터리", "이차전지", "원전", "방산",
    "바이오", "세제", "감세", "규제", "지원", "투자", "예산", "환율",
    "부동산", "AI", "인공지능", "자동차", "조선", "공급망", "관세", "무역",
]


def _read(dept: str, url: str, out: list, limit: int, optional: bool) -> bool:
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=12)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        if not optional:
            print(f"[policy] {dept} 실패: {type(e).__name__}")
        return False

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
                f"출처: {dept}\n"
                f"제목: {title}\n"
                f"요지: {desc[:500]}\n"
                f"※ 수혜 종목을 특정하거나 추천하지 말 것. 산업/테마 수준으로만 언급.\n"
                f"※ 언론 보도이며 정부 확정 발표가 아닐 수 있음. 단정하지 말 것."
            ),
            "src": (it.findtext("link") or "").strip(),
        })
        if len(out) >= limit:
            return True
    return True


def fetch(limit: int = 8) -> list[dict]:
    out, ok = [], 0

    for dept, url in FEEDS:
        if len(out) >= limit:
            break
        if _read(dept, url, out, limit, optional=False):
            ok += 1
        crawl.sleep_jitter(0.6, 1.4)

    # 국내 IP 러너로 옮기면 자동으로 살아난다
    for dept, url in OPTIONAL_FEEDS:
        if len(out) >= limit:
            break
        _read(dept, url, out, limit, optional=True)

    crawl.report("policy_rss", len(out), limit if ok else 0,
                 "필수 RSS 전건 실패")
    return out


def make_polls(pool: list[dict], n: int = 3) -> list[dict]:
    """수집된 항목에서 토론 발제글 시드를 파생시킨다."""
    seeds = [x for x in pool if x["kind"] in ("policy", "flow")][:n]
    return [{**s, "id": "poll-" + s["id"], "kind": "poll",
             "facts": s["facts"] + "\n※ 결론을 내리지 말고, 찬반이 갈릴 만한 질문으로 마무리할 것."}
            for s in seeds]
