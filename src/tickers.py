"""종목 매핑. 원칙: LLM이 만든 종목코드는 절대 신뢰하지 않는다.
반드시 실제 상장 리스트와 대조해서 존재하는 것만 통과시킨다.

상장 리스트 소스: KRX KIND 상장법인목록 (corpList.do).
pykrx 는 사용하지 않는다 — 2026년 기준 KRX_ID/KRX_PW 계정을 요구해
GitHub Actions 에서 `KRX 로그인 실패` 로 전건 실패했다 (dry-run 실측).
"""
import functools
import json
import os
import re
import time

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


NAVER_SUM = ("https://finance.naver.com/sise/sise_market_sum.naver"
             "?sosok={sosok}&page={page}")


def _from_naver(max_page: int = 8) -> dict[str, str]:
    """KRX 가 막혔을 때의 폴백. 시총 상위부터 받아 주요 종목을 커버한다.

    전 종목을 받지는 못하지만, 리포트·기사에 등장하는 종목은 대부분 상위권이라
    제목 매칭 용도로는 충분하다. 전건 실패보다 낫다.
    """
    table = {}
    for sosok in (0, 1):                    # 0=KOSPI, 1=KOSDAQ
        for page in range(1, max_page + 1):
            try:
                r = requests.get(NAVER_SUM.format(sosok=sosok, page=page),
                                 headers=_HEADERS, timeout=15)
                r.encoding = "euc-kr"
                soup = BeautifulSoup(r.text, "html.parser")
                found = 0
                for a in soup.select("a[href*='code=']"):
                    m = re.search(r"code=(\d{6})", a.get("href", ""))
                    nm = a.get_text(strip=True)
                    if m and nm and not _EXCLUDE_NAME.search(nm):
                        table[nm] = m.group(1)
                        found += 1
                if not found:
                    break
            except Exception:
                break
    return table


_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://kind.krx.co.kr/corpgeneral/corpList.do?method=loadInitPage",
}


# 상장사 목록은 하루에 몇 종목 바뀌지 않는다. 그런데 KIND 가 403 을 내면
# 네이버 폴백이 665종목만 주고(정상 2,700+), 2,000종목이 통째로 사라진다.
# 그러면 공시·리포트의 종목명->코드 변환이 실패해 테마글로 강등되고
# 게이트에서 글감부족으로 떨어진다 (실측 #76: 글감부족 38 -> 44, 공시 3 -> 1).
# 마지막으로 성공한 목록을 디스크에 두고 실패 시 그걸 쓴다.
_CACHE_PATH = "data/listed_cache.json"
_CACHE_TTL = 30 * 86400


def _cache_read() -> dict[str, str]:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            c = json.load(f)
        if time.time() - c.get("ts", 0) < _CACHE_TTL and len(c.get("map", {})) > 1000:
            return c["map"]
    except Exception:
        pass
    return {}


def _cache_write(table: dict[str, str]) -> None:
    if len(table) < 1000:
        return
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "map": table}, f, ensure_ascii=False)
    except Exception as e:
        print(f"[tickers] 캐시 저장 실패: {e}")


@functools.lru_cache(maxsize=1)
def listed() -> dict[str, str]:
    """{종목명: 종목코드}. 실패 시 빈 dict (호출부는 전부 테마글로 강등)."""
    try:
        # KIND 는 단순 UA 로는 403 을 내는 경우가 있다 (실측). 브라우저 헤더 + 재시도.
        r = None
        for attempt in range(3):
            r = requests.get(KIND_URL, timeout=25, headers=_HEADERS)
            if r.status_code == 200:
                break
            print(f"[tickers] KIND {r.status_code} 재시도 {attempt + 1}/3")
            time.sleep(2 + attempt * 3)
        r.raise_for_status()
        r.encoding = "euc-kr"
        html = r.text

        table = {}
        # 1.2MB 급 표는 html.parser 가 중간에 트리를 끊는다. lxml 우선.
        for parser in ("lxml", "html.parser"):
            try:
                soup = BeautifulSoup(html, parser)
            except Exception:
                continue
            for tr in soup.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 3:
                    continue
                # 컬럼: 회사명 | 시장구분 | 종목코드 | 업종 | 주요제품 | 상장일 | ...
                name = tds[0].get_text(strip=True)
                code = tds[2].get_text(strip=True)
                if not re.fullmatch(r"\d{6}", code):
                    # 헤더 변경 대비: 행 안에서 6자리 셀을 찾아본다
                    code = next((t.get_text(strip=True) for t in tds[1:5]
                                 if re.fullmatch(r"\d{6}", t.get_text(strip=True))), "")
                    if not code:
                        continue
                if name and not _EXCLUDE_NAME.search(name):
                    table[name] = code
            if len(table) > 1000:
                print(f"[tickers] 상장 {len(table)}종목 로드 ({parser})")
                _cache_write(table)
                return table

        # 폴백: 태그 트리 없이 원문에서 직접 추출
        # 회사명 → (시장구분 셀) → 종목코드 순서를 반영한다
        _fallback_re = (r"<td[^>]*>\s*([^<>]{2,40}?)\s*</td>\s*"
                        r"<td[^>]*>.*?</td>\s*"
                        r"<td[^>]*>\s*(\d{6})\s*</td>")
        for name, code in re.findall(_fallback_re, html, re.S):
            name = name.strip()
            if name and not _EXCLUDE_NAME.search(name):
                table[name] = code

        if len(table) < 1000:
            print(f"[tickers] ⚠ 상장 {len(table)}종목만 파싱됨 — 소스 구조 변경 의심")
        else:
            print(f"[tickers] 상장 {len(table)}종목 로드 (regex)")
            _cache_write(table)
        return table
    except Exception as e:
        print(f"[tickers] ⚠ KIND 실패: {e}")
        cached = _cache_read()
        t = _from_naver()
        # 네이버 폴백은 665종목뿐이라 캐시(2,700+)가 있으면 그쪽이 낫다.
        if len(cached) > len(t):
            print(f"[tickers] 캐시 {len(cached)}종목 사용 (네이버 폴백 {len(t)}종목보다 큼)")
            return cached
        if t:
            print(f"[tickers] 네이버 폴백 {len(t)}종목 로드")
        else:
            print("[tickers] ⚠ 폴백도 캐시도 없음 — 전건 테마글로 강등된다")
        return t


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
