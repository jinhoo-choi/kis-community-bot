"""증권사 리포트 수집.

- 네이버 금융 리서치(종목분석): URL 쿼리에 종목코드가 들어있어 매핑이 정확하다.
- 한경컨센서스: 전 증권사 집계. 종목코드가 없어 제목 기반 매핑 + 귀속검증이 필요하다.

저작권: 리포트 원문(PDF)은 저장/재배포하지 않는다.
목록의 제목·증권사만 사용하고 원문은 링크로만 연결한다.

크롤링 방어(부정여론봇 이식): 셀렉터 배열 폴백 + 재시도 + 랜덤 지연 + 헬스체크.
단일 셀렉터를 박아두면 사이트 개편 당일부터 조용히 0건이 된다.
"""
import re

from src import crawl

# 폴백 순서대로 시도. 위쪽이 현재 구조, 아래쪽은 구/대체 구조.
NAVER_ROW_SELECTORS = [
    "table.type_1 tr",
    "table.type_1 tbody tr",
    ".box_type_m table tr",
]
HK_ROW_SELECTORS = [
    "table.table_style01 tbody tr",
    "table tbody tr",
    ".table_wrap tbody tr",
]


# 상세 페이지 헤더 줄: "한화투자증권 | 2026.09.04 | 조회 7522 목표가 30,000 | 투자의견 Buy"
# 프로브(data/research_probe.txt)로 확인한 실제 문자열이다. 추측이 아니다.
DETAIL_MAX = 20        # 상세 조회 건수 상한 (요청 수 = 그대로 부하)
_TP = re.compile(r"목표가\s*([\d,]+)")
_OPINION = re.compile(r"투자의견\s*([A-Za-z가-힣.]+)")


def _naver_detail(url: str) -> str:
    """상세 페이지에서 목표가·투자의견만 가져온다.

    목록은 제목·증권사만 준다. 그래서 이 두 값을 Gemini 검색 그라운딩으로
    알아내고 있었는데 1회 약 35원이다(실청구 역산). 상세 페이지에 그냥 있다.
    본문 요약은 리포트 저작물이라 가져오지 않는다 — 수치와 의견만 쓴다.
    """
    soup = crawl.get_soup(url, encoding="euc-kr")
    if soup is None:
        return ""
    crawl.sleep_jitter(0.2, 0.6)     # 연속 요청으로 차단당하지 않게
    for sel in ["div.box_type_m", "table.type_1", "body"]:
        el = soup.select_one(sel)
        if not el:
            continue
        head = el.get_text(" ", strip=True)[:300]
        tp, op = _TP.search(head), _OPINION.search(head)
        if not (tp or op):
            continue
        out = []
        if tp and tp.group(1).replace(",", "") != "0":
            out.append(f"제시 적정가격: {tp.group(1)}원")
        if op and "없음" not in op.group(1):
            out.append(f"투자의견: {op.group(1)}")
        return "\n".join(out)
    return ""


def fetch_naver(limit: int = 12) -> list[dict]:
    url = "https://finance.naver.com/research/company_list.naver"
    out = []
    soup = crawl.get_soup(url, encoding="euc-kr")
    if soup is None:
        crawl.report("naver_research", 0, limit, "페이지 로드 실패")
        return out

    for tr in crawl.select_rows(soup, NAVER_ROW_SELECTORS):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        a_stock, a_title = tds[0].find("a"), tds[1].find("a")
        if not (a_stock and a_title):
            continue

        m = re.search(r"code=(\d{6})", a_stock.get("href", ""))
        if not m:
            continue

        name = a_stock.get_text(strip=True)
        title = a_title.get_text(strip=True)
        _href = a_title.get("href", "")
        _url = _href if _href.startswith("http") else \
            "https://finance.naver.com/research/" + _href.lstrip("/")
        # 배포 상한이 12건이라 전건 상세 조회는 낭비다. 앞쪽만 본다.
        _d = _naver_detail(_url) if len(out) < DETAIL_MAX else ""
        _detail = (_d + "\n") if _d else ""
        out.append({
            "id": "naver-" + re.sub(r"\W", "", a_title.get("href", ""))[-24:],
            "kind": "research",
            "stock_code": m.group(1),
            "stock_name": name,
            "title": title,
            "facts": (
                f"종목: {name} ({m.group(1)})\n"
                f"리포트 제목: {title}\n"
                f"발간: {tds[2].get_text(strip=True)} / {tds[4].get_text(strip=True)}\n"
                f"{_detail}"
                f"※ 제시 수치는 증권사 의견이며 단정하지 말 것."
                + ("" if _detail else "\n※ 목표주가·투자의견 미제공. 추정하지 말 것.")
            ),
            "src": "https://finance.naver.com" + a_title.get("href", ""),
        })
        if len(out) >= limit:
            break

    crawl.report("naver_research", len(out), limit, "셀렉터 개편 의심")
    crawl.sleep_jitter()
    return out


def _undouble(s: str) -> str:
    """한경 제목 셀은 span 이 중복되어 같은 문자열이 두 번 붙어 나온다."""
    s = re.sub(r"\s+", " ", s or "").strip()
    if not s:
        return s
    h = len(s) // 2
    if len(s) % 2 == 0 and s[:h] == s[h:]:          # 완전 2배 반복
        return s[:h]
    # 두 번째 사본은 '종목명' 부터 시작한다. 코드 위치가 아니라 이름 위치에서 잘라야
    # 앞의 종목명이 남지 않는다. (2026-09-03: '조명 롯데지주' 잔여 사례)
    hits = list(re.finditer(r"\(\d{6}\)", s))
    if hits:
        name = s[:hits[0].start()].strip()
        tail = s[hits[0].end():]
        if len(name) >= 2:
            idx = tail.find(name)
            if idx > 0:
                return (s[:hits[0].end()] + tail[:idx]).rstrip()
        if len(hits) >= 2:
            return s[:hits[1].start()].rstrip()
    return s


def _strip_code(title: str) -> tuple[str, str]:
    """'롯데지주(004990) 부제' → ('004990', '롯데지주 부제')"""
    m = re.match(r"\s*(.+?)\((\d{6})\)\s*(.*)", title)
    if not m:
        return "", title
    return m.group(2), f"{m.group(1).strip()} {m.group(3).strip()}".strip()


def fetch_hankyung(limit: int = 8) -> list[dict]:
    """진단으로 확인된 실구조:
      td[0]=작성일 td[1]=제목(span 중복) td[2]=적정가격 td[3]=투자의견
      td[4]=작성자 td[5]=제공출처 td[7]=차트링크(business_code=종목코드)

    기존 파서 결함 3개:
      (1) business_code 미추출 → stock_code=None → 전건 테마글 강등
      (2) 제목 텍스트 2배 중복을 그대로 사용
      (3) tds[-2], tds[-1] 을 작성자/출처로 읽어 빈 문자열이 들어감
    """
    url = "https://consensus.hankyung.com/analysis/list?skinType=business"
    out = []
    soup = crawl.get_soup(url)
    if soup is None:
        crawl.report("hankyung", 0, limit, "페이지 로드 실패")
        return out

    for tr in crawl.select_rows(soup, HK_ROW_SELECTORS):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue

        raw = _undouble(tds[1].get_text(" ", strip=True))
        if not raw:
            continue

        # 종목코드는 차트 링크의 business_code 가 가장 확실하다
        code = ""
        for a in tr.find_all("a", href=True):
            m = re.search(r"business_code=(\d{6})", a["href"])
            if m:
                code = m.group(1)
                break
        code_from_title, title = _strip_code(raw)
        code = code or code_from_title

        report_idx = ""
        pdf = tds[1].find("a", href=True)
        if pdf:
            m = re.search(r"report_idx=(\d+)", pdf["href"])
            report_idx = m.group(1) if m else ""

        # 한국IR협의회 등 비증권사 리포트는 적정가격 0 / 투자의견없음 으로 들어온다.
        # 그대로 넣으면 "적정가격은 0원으로 제시했습니다" 가 생성된다 (실측).
        target = tds[2].get_text(strip=True)
        opinion = tds[3].get_text(strip=True)
        if not re.sub(r"[^\d]", "", target).lstrip("0"):
            target = ""
        if opinion in ("투자의견없음", "NR", "N/R", "-", "없음"):
            opinion = ""
        analyst = tds[4].get_text(strip=True)
        broker = tds[5].get_text(strip=True)

        out.append({
            "id": f"hk-{report_idx or re.sub(chr(92)+'W', '', title)[:20]}",
            "kind": "research",
            "stock_code": code or None,
            "stock_name": None,              # tickers.resolve 가 코드로 역조회
            "title": title,
            "facts": (
                f"리포트 제목: {title}\n"
                + (f"종목코드: {code}\n" if code else "")
                + f"작성: {broker} {analyst}\n"
                + (f"제시 적정가격: {target}원 (해당 증권사 의견)\n" if target else "")
                + (f"투자의견: {opinion} (해당 증권사 의견)\n" if opinion else "")
                + "※ 제목 외 본문 수치는 미제공. 위 수치는 증권사 제시치이며 단정하지 말 것."
            ),
            "src": (f"https://consensus.hankyung.com/analysis/downpdf?report_idx={report_idx}"
                    if report_idx else url),
        })
        if len(out) >= limit:
            break

    crawl.report("hankyung", len(out), limit, "셀렉터 개편 의심")
    crawl.sleep_jitter()
    return out


def fetch(limit: int = 16) -> list[dict]:
    n = int(limit * 0.7)
    return (fetch_naver(n) + fetch_hankyung(limit - n))[:limit]
