"""KRX KIND 조회공시 수집.

조회공시(현저한 시황변동 / 풍문·보도 해명)는 거래소 공시라 DART 에 없다.
이 봇의 최대 약점 — 특징주 글이 "왜 올랐는지 모른다"로 끝나는 문제 — 를
메울 수 있는 유일한 공식 확정 정보다. 회사가 직접 답변하기 때문이다.

구조(2026-09-04 프로브 확인):
  POST /disclosure/todaydisclosure.do  method=searchTodayDisclosureSub
  td[0]=시각  td[1]=종목명(약칭)  td[2]=공시제목  td[3]=회사 정식명
  제목 링크의 onclick 에 openDisclsViewer('{접수번호14자리}','')

종목코드는 응답에 없다(KIND 내부 회사코드만 있음). tickers.listed() 로 이름 매칭한다.
"""
import re

import requests
from bs4 import BeautifulSoup

from src import crawl

URL = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"
VIEWER = "https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno={}"

PAYLOAD = {
    "method": "searchTodayDisclosureSub", "currentPageSize": "100",
    "pageIndex": "1", "orderMode": "0", "orderStat": "D",
    "forward": "todaydisclosure_sub", "marketType": "",
}

# 조회공시 계열 제목
INQUIRY_RE = re.compile(r"조회공시|현저한\s*시황변동|풍문\s*또는\s*보도|해명|답변")

# 제목에 드러나는 답변 성격. 이것만으로도 커뮤니티 글감이 된다.
STANCE = [
    (r"미확정", "회사는 '미확정'이라고 답변"),
    (r"부인", "회사는 해당 내용을 부인"),
    (r"확정", "회사는 사실이라고 확인"),
    (r"재답변|지연", "답변이 지연 또는 재요구된 상태"),
]


def _stance(title: str) -> str:
    for pat, label in STANCE:
        if re.search(pat, title):
            return label
    return "답변 내용은 원문 확인 필요"


def fetch(limit: int = 6) -> list[dict]:
    from src import tickers

    try:
        s = requests.Session()
        s.headers.update({**crawl.HEADERS,
                          "Referer": "https://kind.krx.co.kr/disclosure/todaydisclosure.do"})
        s.get("https://kind.krx.co.kr/disclosure/todaydisclosure.do", timeout=20)
        r = s.post(URL, data=PAYLOAD, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"[kind] 조회공시 로드 실패: {e}")
        crawl.report("kind_inquiry", 0, limit, "KIND 응답 실패")
        return []

    table = tickers.listed()
    out = []
    for tr in soup.select("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        title = tds[2].get_text(" ", strip=True)
        if not INQUIRY_RE.search(title):
            continue

        name = tds[1].get_text(" ", strip=True)
        code = table.get(name, "")
        when = tds[0].get_text(strip=True)

        a = tds[2].find("a")
        m = re.search(r"openDisclsViewer\('(\d{10,})'", (a.get("onclick") or "") if a else "")
        acptno = m.group(1) if m else ""

        out.append({
            "id": f"kind-{acptno or re.sub(chr(92) + 'W', '', name + title)[:20]}",
            "kind": "disclosure",
            "stock_code": code or None,
            "stock_name": name,
            "title": title,
            "facts": (
                f"거래소 조회공시\n"
                f"종목: {name}" + (f" ({code})\n" if code else "\n")
                + f"공시 시각: {when}\n"
                f"공시 제목: {title}\n"
                f"답변 성격: {_stance(title)}\n"
                "※ 거래소가 시황 변동이나 풍문에 대해 회사에 답변을 요구한 건이다.\n"
                "※ 답변 성격 이상을 추측하지 말 것. 주가 방향을 예단하지 말 것."
            ),
            "src": VIEWER.format(acptno) if acptno else URL,
        })
        if len(out) >= limit:
            break

    crawl.report("kind_inquiry", len(out), limit, "KIND 제목 구조 변경 의심")
    return out


def attach_to_flow(flow_items: list[dict], inquiries: list[dict]) -> int:
    """특징주 항목에 같은 종목의 조회공시를 붙인다.

    이게 이 소스의 핵심 가치다. "20% 올랐는데 이유는 모른다"가
    "20% 올랐고 거래소가 조회공시를 요구했으며 회사는 이렇게 답했다"가 된다.
    """
    by_code = {q["stock_code"]: q for q in inquiries if q.get("stock_code")}
    n = 0
    for it in flow_items:
        q = by_code.get(it.get("stock_code"))
        if not q:
            continue
        it["facts"] = (
            it["facts"].rstrip()
            + f"\n\n[같은 날 거래소 조회공시]\n"
            f"- 공시 제목: {q['title']}\n"
            f"- 답변 성격: {_stance(q['title'])}\n"
            "※ 이 조회공시는 확정된 공식 정보다. 다만 답변 성격 이상을 추측하지 말 것."
        )
        it["has_inquiry"] = True
        n += 1
    return n


def enrich_with_market(inquiries: list[dict], flow_items: list[dict]) -> int:
    """조회공시 항목에 같은 종목 시세를 붙인다.

    attach_to_flow 는 반대 방향이라 놓치는 게 있었다 (실측 연결 2/3).
    market.fetch 는 거래대금 상위 + 등락률 1.5% 이상만 담는데, 조회공시가 나온
    종목이 그 명단에 없으면 조회공시 글은 시세 없이 "회사가 부인했다" 한 줄로 끝난다.
    거꾸로 조회공시 쪽에서 시세를 끌어온다. 조회공시 + 시세는 flow 슬롯이
    구조적으로 못 만드는 조합이다 — 등락과 그에 대한 회사의 공식 답변이 함께 있다.
    """
    from src import facts as _facts
    from src.sources import market as _mk

    have = {it.get("stock_code") for it in flow_items}
    n = 0
    for q in inquiries:
        code = q.get("stock_code")
        if not code or code in have or "종가" in q.get("facts", ""):
            continue
        r = {"code": code}
        _mk._add_history(r)                     # 기존 함수 재사용
        close, prev = r.get("close_hist"), r.get("prev_close")
        if not close or not prev:
            continue
        r["pct"] = (close - prev) / prev * 100
        r["close"] = close
        if _facts.sanity_errors(r):             # 가격제한폭 등 불변식
            continue
        q["facts"] = (q["facts"].rstrip() + "\n\n[같은 날 시세]\n"
                      + f"종가: {close:,}원\n등락률: {r['pct']:.2f}%\n"
                      + "".join(f"{l}\n" for l in _facts.evaluate(r))
                      + "※ 등락과 조회공시의 인과를 단정하지 말 것.")
        n += 1
        crawl.sleep_jitter(0.4, 0.9)
    return n
