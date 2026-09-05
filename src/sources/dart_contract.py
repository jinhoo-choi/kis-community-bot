"""단일판매ㆍ공급계약체결 원문 파싱.

미보강 7건 중 5건이 이 유형인데 주요사항보고서 정형 API 가 없다
(프로브 확인: sglPrvsCntrCncln 등 후보 3종 전부 status 101).
document.xml 이 유일한 경로다.

구조(2026-09-04 프로브 확인, rcept_no=20260904900634 서남):
  document.xml → ZIP → HTML. meta 는 euc-kr 이라 쓰여 있으나 실제는 UTF-8.
  euc-kr 로 읽으면 제목이 '⑥쇳留ㅳ怨듦怨쎌껜寃'로 깨져 '계약금액'을 못 찾는다.
  본문은 table 이고 라벨/값이 td 쌍으로 들어간다.

뽑는 값은 넷뿐이다. 커뮤니티 적합성에 필요한 건 '얼마나 큰 계약인가' 하나이고,
그건 계약금액과 매출액 대비 비율로 성립한다. 나머지 필드는 건드리지 않는다.
"""
import io
import re
import zipfile

import requests
from bs4 import BeautifulSoup

from config import DART_API_KEY, USER_AGENT

DOC = "https://opendart.fss.or.kr/api/document.xml"
_HEADERS = {"User-Agent": USER_AGENT}

TITLE_RE = re.compile(r"단일판매|공급계약")

# (표 라벨, 표시 라벨, 단위)
WANTED = [
    ("계약금액 총액", "계약 금액", "원"),
    ("최근 매출액", "최근 매출액", "원"),
    ("매출액 대비", "매출액 대비", "%"),
    ("판매ㆍ공급지역", "공급 지역", ""),
]


def _cells(html: str) -> dict[str, str]:
    """표의 (라벨 → 값). 마지막 td 를 값으로 본다.

    '2. 계약내역 | 조건부 계약여부 | 미해당' 처럼 셀이 셋인 행이 있어
    첫 셀을 라벨로 잡으면 틀린다. 끝에서 두 번째가 라벨이다.
    """
    out = {}
    for tr in BeautifulSoup(html, "html.parser").find_all("tr"):
        cs = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        cs = [c for c in cs if c]
        if len(cs) >= 2:
            out.setdefault(re.sub(r"\s+", " ", cs[-2]), cs[-1])
    return out


def _won(v: str) -> str:
    n = re.sub(r"[^\d]", "", v or "")
    if not n:
        return ""
    n = int(n)
    if n >= 100_0000_0000:
        return f"{n / 1_0000_0000:,.0f}억원"
    if n >= 1_0000_0000:
        return f"{n / 1_0000_0000:,.1f}억원"     # 6.6억을 7억으로 올리면 원문과 어긋난다
    return f"{n:,}원"


def enrich_one(item: dict) -> bool:
    """공급계약 공시의 facts 에 계약금액을 붙인다. 보강했으면 True."""
    if not DART_API_KEY or not TITLE_RE.search(item.get("title", "")):
        return False
    rcept = (item.get("id") or "").replace("dart-", "")
    if not rcept.isdigit():
        return False
    try:
        r = requests.get(DOC, headers=_HEADERS, timeout=30,
                         params={"crtfc_key": DART_API_KEY, "rcept_no": rcept})
        if r.content[:2] != b"PK":
            return False
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        raw = zf.read(zf.namelist()[0])
        try:
            html = raw.decode("utf-8")      # meta 는 euc-kr 이지만 실제는 UTF-8
        except UnicodeDecodeError:
            html = raw.decode("cp949", "ignore")

        cells = _cells(html)
        lines = []
        for key, label, unit in WANTED:
            v = next((val for lab, val in cells.items()
                      if key in lab and not lab.startswith("-")), "")
            v = v.strip()
            if not v or v == "-":
                continue
            if unit == "원":
                v = _won(v)
            elif unit == "%":
                v = f"{v}%" if not v.endswith("%") else v
            if v:
                lines.append(f"- {label}: {v}")
        if not lines:
            return False

        item["facts"] = (
            item["facts"].rstrip()
            + "\n\n[공급계약 상세 — DART 공시 원문]\n"
            + "\n".join(lines)
            + "\n※ 위 수치는 공시 원문 값이다. 그대로 쓰되 계산하거나 합산하지 말 것."
        )
        item["dart_detail"] = "document.xml"
        return True
    except Exception as e:
        print(f"[dart] 계약 원문 실패 {rcept}: {type(e).__name__}")
        return False
