"""다축 중복 제거.

현재 이 봇의 dedup 은 `item["id"]` 단일 축이다.
그런데 같은 사건이 서로 다른 소스로 3번 들어온다.
  DART      : "유상증자 결정"
  네이버리서치: "○○ 유상증자 영향 점검"
  pykrx     : "○○ 전일 -12% 하락"
id 가 다르므로 전부 통과하고, 같은 종목 같은 사건으로 3건이 생성된다.

인사이트봇에서 event_key / SUBJ:: / 마일스톤 등 다축 키를 쓴 이유가 이것이다.
다만 그쪽의 실패 교훈 두 가지를 반영한다.
  - 2026-08-02: 조회부에만 정규화를 적용하고 저장부에 빠뜨려 dedup 이 영구 미스
    → 여기서는 keys() 하나만 쓰고 조회·저장이 같은 함수를 호출한다.
  - 2026-07-27: 수치 마일스톤 정규식은 재보도에도 매번 매칭되어 억제를 뚫음
    → 수치 기반 축을 쓰지 않는다.
"""
import re
from difflib import SequenceMatcher

# 공시·리포트 제목에서 사건 유형을 뽑는다. 표현이 달라도 같은 사건이면 같은 값.
EVENT_TYPES = [
    ("유상증자", r"유상증자"),
    ("무상증자", r"무상증자"),
    ("전환사채", r"전환사채|CB\b|신주인수권|BW\b"),
    ("자기주식", r"자기주식|자사주"),
    ("공급계약", r"공급계약|단일판매|수주"),
    ("실적", r"실적|잠정|손익구조|영업이익|매출액"),
    ("배당", r"배당"),
    ("합병분할", r"합병|분할"),
    ("최대주주", r"최대주주|경영권"),
    ("감자", r"감자"),
    ("임상", r"임상|허가|승인"),
    ("급등락", r"[-+]?\d+(\.\d+)?%\s*(상승|하락)"),
]

_NOISE = re.compile(r"[\[\]()\-–—·,.:;\"'’“”]|주식회사|㈜|\s+")


def normalize_title(t: str) -> str:
    return _NOISE.sub("", t or "").lower()


def event_type(title: str) -> str:
    for name, pat in EVENT_TYPES:
        if re.search(pat, title or ""):
            return name
    return ""


def keys(item: dict) -> list[str]:
    """이 항목이 점유하는 모든 dedup 축. 조회와 저장이 반드시 이 함수를 공유한다."""
    out = [f"ID::{item.get('id','')}"]
    code = item.get("stock_code")

    if code:
        ev = event_type(item.get("title", ""))
        if ev:
            # 같은 종목 + 같은 사건유형 = 같은 사건으로 본다
            out.append(f"EVT::{code}::{ev}")
        out.append(f"TTL::{code}::{normalize_title(item.get('title',''))[:40]}")
    return out


def is_dup(item: dict, seen: dict, titles: list[str] = None,
           sim_threshold: float = 0.78) -> tuple[bool, str]:
    for k in keys(item):
        if k in seen:
            return True, k.split("::")[0]

    # 제목 유사도 축 — 소스마다 표현만 다른 같은 사건을 잡는다
    if titles:
        n = normalize_title(item.get("title", ""))
        for t in titles:
            if n and SequenceMatcher(None, n, t).ratio() >= sim_threshold:
                return True, "SIM"
    return False, ""


def mark(item: dict, seen: dict, day: str):
    for k in keys(item):
        seen[k] = day


def filter_new(items: list[dict], seen: dict) -> tuple[list[dict], dict]:
    """중복 제거. (통과목록, 사유별 카운트)"""
    from collections import Counter
    passed, titles, reasons = [], [], Counter()

    for it in items:
        dup, why = is_dup(it, seen, titles)
        if dup:
            reasons[why] += 1
            continue
        passed.append(it)
        titles.append(normalize_title(it.get("title", "")))

    if reasons:
        print(f"[dedup] 제외 {sum(reasons.values())}건 {dict(reasons)}")
    return passed, dict(reasons)
