"""정책·거시 소스.

korea.kr 및 각 부처 사이트(기재부·금융위·산업부·금감원)는 GitHub Actions 의
해외 IP 에서 전건 ConnectTimeout 이 발생한다 (dry-run + 진단 실측).
국내 IP 화이트리스트로 보이며 러너에서는 우회할 수 없다.

대체로 진단에서 살아남은 소스를 쓴다.
  - 연합뉴스 경제 RSS : HTTP 200, 정상 XML
  - 한국은행         : HTTP 200 (HTML)

주의: 정부 보도자료가 아니라 언론 기사이므로 성격이 다르다.
저작권상 제목·요지만 사용하고 원문은 링크로만 연결한다.
정부 원문이 필요하면 국내 IP self-hosted runner 가 유일한 해법이다.
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

KEYWORDS = [
    "금리", "물가", "수출", "반도체", "배터리", "이차전지", "원전", "방산",
    "바이오", "세제", "감세", "규제", "지원", "투자", "예산", "환율",
    "부동산", "AI", "인공지능", "자동차", "조선", "공급망", "관세",
    "기준금리", "한국은행", "금융위", "정부", "정책",
]

# 개별 종목 주가를 직접 다루는 기사는 제외 — 정책/테마 슬롯의 목적이 아니다
_SKIP = re.compile(r"급등|급락|상한가|하한가|신고가|목표주가|투자의견|\[표\]|\[특징주\]")


def fetch(limit: int = 8) -> list[dict]:
    out, feed_ok = [], 0

    for dept, url in FEEDS:
        if len(out) >= limit:
            break
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            feed_ok += 1

            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                desc = re.sub(r"<[^>]+>", "", it.findtext("description") or "").strip()
                if not title or _SKIP.search(title):
                    continue
                if not any(k in title + desc for k in KEYWORDS):
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
                        f"요지: {desc[:600]}\n"
                        f"※ 수혜 종목을 특정하거나 추천하지 말 것. 산업/테마 수준으로만 언급.\n"
                        f"※ 기사 문장을 그대로 옮기지 말 것. 사실만 자기 표현으로 재구성."
                    ),
                    "src": (it.findtext("link") or "").strip(),
                })
                if len(out) >= limit:
                    break
        except Exception as e:
            print(f"[policy] {dept} 실패: {type(e).__name__} {e}")
        crawl.sleep_jitter()

    # 피드가 하나도 안 열렸으면 '0건'이 아니라 '장애'다
    crawl.report("policy_rss", len(out), limit,
                 "RSS 피드 전건 실패" if feed_ok == 0 else "키워드 매칭 0건")
    return out


def make_polls(pool: list[dict], n: int = 3) -> list[dict]:
    """수집된 항목에서 토론 발제글 시드를 파생시킨다."""
    seeds = [x for x in pool if x["kind"] in ("policy", "flow")][:n]
    return [{**s, "id": "poll-" + s["id"], "kind": "poll",
             "facts": s["facts"] + "\n※ 결론을 내리지 말고, 찬반이 갈릴 만한 질문으로 마무리할 것."}
            for s in seeds]
