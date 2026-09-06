"""크롤링 공통 유틸.

부정여론봇(cafe-monitor)에서 이식한 방어 패턴.

1) 셀렉터 배열 폴백
   단일 셀렉터를 박아두면 사이트 개편 당일 조용히 0건이 된다.
   부정여론봇은 본문 추출에 4단계(ca-fe iframe → naver iframe → any iframe → page)
   폴백을 두고 각 단계마다 셀렉터 배열을 순회한다. 여기서는 HTML 파싱 수준으로 축약.

2) 실패 시 대기 늘려 재시도 (최대 1회)
   일시적 렌더링·응답 지연이 '수집 실패'로 새는 것을 줄인다.

3) 랜덤 지연
   고정 간격은 봇 탐지 신호가 된다.

4) 소스 헬스체크
   가장 위험한 실패는 예외가 아니라 '조용한 0건'이다.
   기대치 미달이면 경고를 띄워 셀렉터 개편을 즉시 알아차린다.
"""
import random
import time

import requests
from bs4 import BeautifulSoup

from config import USER_AGENT

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "ko-KR,ko;q=0.9",
}

_health: dict[str, tuple[int, int]] = {}     # source -> (수집, 기대)


def sleep_jitter(lo: float = 1.0, hi: float = 2.4):
    time.sleep(random.uniform(lo, hi))


def get_soup(url: str, encoding: str = None, retries: int = 1,
             timeout: int = 15) -> BeautifulSoup | None:
    """실패 시 타임아웃을 늘려 1회 재시도. 최종 실패해도 예외 대신 None."""
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout + attempt * 10)
            r.raise_for_status()
            if encoding:
                r.encoding = encoding
            return BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            if attempt >= retries:
                print(f"[crawl] 로드 실패 {url[:70]}: {e}")
                return None
            print(f"[crawl] 재시도 {attempt + 1}/{retries}: {e}")
            time.sleep(2.5)
    return None


def select_rows(soup: BeautifulSoup, selectors: list[str]) -> list:
    """셀렉터 배열을 순서대로 시도해 처음 결과가 나오는 것을 쓴다."""
    for sel in selectors:
        rows = soup.select(sel)
        if rows:
            return rows
    return []


def first_text(node, selectors: list[str], attr: str = None) -> str:
    """여러 셀렉터 후보 중 값이 나오는 첫 번째를 반환."""
    for sel in selectors:
        try:
            el = node.select_one(sel)
        except Exception:
            continue
        if not el:
            continue
        val = (el.get(attr) if attr else el.get_text(strip=True)) or ""
        if val.strip():
            return val.strip()
    return ""


# 하루치 실측 공급량. 요청량이 아니라 '이만큼은 나와야 정상'인 값이다.
# 요청량으로 판정하면 슬롯을 키울 때마다 기대치가 따라 올라 전 소스가 경보를 낸다
# (실측: market 기대 857건 vs 실제 62건 — 수집이 아니라 기준이 망가진 것이었다).
# 하루치 주요공시는 원래 16건이고 조회공시는 전국 2~3건이다. 더 나올 수 없다.
FLOOR = {
    "dart": 8, "naver_research": 5, "hankyung": 2,
    "market": 20, "kind_inquiry": 1, "policy_rss": 3, "telegram_ch": 0,
}


def report(source: str, got: int, expected: int, reason: str = ""):
    """소스별 수집 결과 기록. 실측 하한(FLOOR) 미만이면 경고.

    expected=0 은 '정상적인 0건'(휴장 등)을 뜻하며 경고하지 않는다.
    reason 은 왜 0건인지를 담는다. HTML 소스는 셀렉터 개편, API 소스는 키·응답 문제로
    원인이 다르므로 같은 문구를 쓰면 오진한다.
    """
    # 기대치는 min(요청량, 실측 하한)으로 잡는다. 요청을 적게 했으면 그만큼만 기대한다.
    floor = min(FLOOR.get(source, 1), expected) if expected else 0
    _health[source] = (got, floor)
    if floor == 0:
        print(f"[crawl] {source} {got}건 (정상 0건)")
    elif got == 0:
        print(f"[crawl] ⚠ {source} 수집 0건 — {reason or '원인 미상'}")
    elif got < floor:
        print(f"[crawl] ⚠ {source} {got}건 (하한 {floor}) — {reason or '수집률 저조'}")
    else:
        print(f"[crawl] {source} {got}건")


def health() -> dict:
    """(수집, 기대, 이상여부). run_stats 와 텔레그램 경고에 쓰인다."""
    return {
        s: {"got": g, "expected": e,
            "degraded": bool(e) and (g == 0 or g < e * 0.3)}
        for s, (g, e) in _health.items()
    }


def degraded_sources() -> list[str]:
    return [s for s, v in health().items() if v["degraded"]]
