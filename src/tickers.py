"""종목 매핑. 원칙: LLM이 만든 종목코드는 절대 신뢰하지 않는다.
반드시 실제 상장 리스트와 대조해서 존재하는 것만 통과시킨다.

상장 리스트 소스: KRX KIND 상장법인목록 (corpList.do).
pykrx 는 사용하지 않는다 — 2026년 기준 KRX_ID/KRX_PW 계정을 요구해
GitHub Actions 에서 `KRX 로그인 실패` 로 전건 실패했다 (dry-run 실측).
"""
import functools
import re

import requests
from bs4 import BeautifulSoup

from src import entity

KIND_URL = ("https://kind.krx.co.kr/corpgeneral/corpList.do"
            "?method=download&searchType=13")

# ETF/ETN/스팩/리츠는 커뮤니티 종목글 대상이 아니다
_EXCLUDE_NAME = re.compile(
    r"KODEX|TIGER|KBSTAR|ARIRANG|HANARO|SOL |ACE |PLUS |RISE |WON |TIMEFOLIO|"
    r"KOSEF|파워|스팩|리츠$|제\d+호"
)


@functools.lru_cache(maxsize=1)
def listed() -> dict[str, str]:
    """{종목명: 종목코드}. 실패 시 빈 dict (호출부는 전부 테마글로 강등)."""
    try:
        r = requests.get(KIND_URL, timeout=25,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")

        table = {}
        for tr in soup.select("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            name = tds[0].get_text(strip=True)
            code = tds[1].get_text(strip=True)
            if not re.fullmatch(r"\d{6}", code):
                continue
            if not name or _EXCLUDE_NAME.search(name):
                continue
            table[name] = code

        print(f"[tickers] 상장 {len(table)}종목 로드")
        return table
    except Exception as e:
        print(f"[tickers] ⚠ 상장리스트 로드 실패: {e}")
        return {}


@functools.lru_cache(maxsize=1)
def by_code() -> dict[str, str]:
    return {v: k for k, v in listed().items()}


def name_of(code: str) -> str:
    return by_code().get(code, "")


def resolve(item: dict) -> dict:
    """stock_code 가 없으면 제목에서 종목을 찾아 채운다.

    매칭됐다고 바로 확정하지 않는다. 인사이트봇 2026-08-02 오탐(그룹명 단독
    언급이 계열사로 승격)과 같은 함정이라 반드시 귀속검증을 통과해야 한다.
    """
    if item.get("stock_code"):
        if not item.get("stock_name"):
            item["stock_name"] = name_of(item["stock_code"])
        return item

    table = listed()
    if not table:
        # 상장리스트 자체를 못 받은 상황. '귀속 실패'와 구분해서 표시한다.
        item.setdefault("attr_reject", []).append("상장리스트없음")
        item["board"] = "free"
        return item

    title = item.get("title", "")
    facts = item.get("facts", "")

    for name in sorted(table, key=len, reverse=True):
        if len(name) < 2:
            continue
        if not re.search(rf"(?<![가-힣A-Za-z]){re.escape(name)}(?![가-힣A-Za-z])", title):
            continue

        ok, why = entity.verify_attribution(name, title, facts)
        if not ok:
            item.setdefault("attr_reject", []).append(why)
            continue
        if entity.is_incidental(name, title, facts):
            item.setdefault("attr_reject", []).append(f"부수언급({name})")
            continue

        item["stock_code"] = table[name]
        item["stock_name"] = name
        return item

    item.setdefault("attr_reject", []).append("종목명미검출")
    item["kind"] = "theme" if item["kind"] == "research" else item["kind"]
    item["board"] = "free"
    return item


def board_of(item: dict) -> str:
    return "stock" if item.get("stock_code") else "free"
