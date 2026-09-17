"""증권사 리포트 수집.

- 네이버 금융 리서치(종목분석): URL 쿼리에 종목코드가 들어있어 매핑이 정확하다.
- 한경컨센서스: 전 증권사 집계. 종목코드가 없어 제목 기반 매핑 + 귀속검증이 필요하다.

저작권: 리포트 원문(PDF)은 저장/재배포하지 않는다.
목록의 제목·증권사만 사용하고 원문은 링크로만 연결한다.

크롤링 방어(부정여론봇 이식): 셀렉터 배열 폴백 + 재시도 + 랜덤 지연 + 헬스체크.
단일 셀렉터를 박아두면 사이트 개편 당일부터 조용히 0건이 된다.
"""
import os
import re

from bs4 import BeautifulSoup

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
# 상세 조회 건수 상한 (요청 수 = 그대로 부하).
# 20 은 '배포 상한이 12건이라 전건 조회는 낭비' 라는 전제로 잡은 값인데,
# 상세가 없으면 목표주가·투자의견이 없어 게이트(_RESEARCH_SUBSTANCE)에서
# 차단된다. 실측 #122: 네이버 33건 수집 / 상세 20건 → research 게이트 차단 28건,
# 최종 발송 0건. 상한이 곧 후보 손실이라 목록 수집량에 맞춘다.
# 비용은 0 (HTML 크롤링). 늘어나는 것은 요청 수와 실행 시간뿐이다.
DETAIL_MAX = 40
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


RESEARCH_API = "https://m.stock.naver.com/front-api/research"
_TP_API = re.compile(r"목표주가[는를]?\s*([\d,]+)\s*원")
# 값만 받는다. '투자의견 및 목표주가를 제시한다' 에서 '및' 을 값으로 잡아
# 그대로 본문에 나갔다(실측 #125 fatal: 투자의견 '및'은 오류값을 그대로 노출).
_OP_VALUES = ("매수", "중립", "보유", "비중확대", "비중축소", "매도",
              "Buy", "BUY", "Hold", "HOLD", "Neutral", "Sell", "SELL",
              "Outperform", "Marketperform", "Underperform", "Overweight")
_OP_API = re.compile(r"투자의견\s*(" + "|".join(_OP_VALUES) + r")")


GIST_MAX = 180         # 요지로 넘길 최대 길이. 원문을 통째로 싣지 않기 위한 상한


# 회사 소개 보일러플레이트. IR 소개자료는 본문 앞이 대부분 이 내용이라,
# 앞에서부터 자르면 '사업분야는 A, B 로 구성되어 있다' 만 남는다
# (실측 #125: 리서치 발송 8건 중 7건이 한국IR협의회, fit 1점 6건).
_BOILER = re.compile(r"설립(?:된|되었|하였)|연혁|본사(?:는|를)|영위하고 있다|"
                     r"사업분야는|사업 ?부문은|주요 ?제품|지분(?:을|은) 보유|"
                     r"종합 ?솔루션을 제공|기업으로,|회사로,")
# 주가를 보는 사람에게 새로운 것. 수치가 붙은 사실이거나 앞을 내다보는 서술.
_SUBSTANCE_SENT = re.compile(r"[\d,.]+\s*(?:억원|조원|원|%|배|만주|포인트)|"
                             r"전망|추정|예상|목표|상향|하향|개선|둔화|성장|감소|증가|"
                             r"수주|계약|출시|가동|증설|인수|합병|흑자|적자|"
                             r"밸류에이션|멀티플|가이던스")


def _gist(text: str) -> str:
    """본문에서 요지를 만든다. 문장 경계로 자르고 상한을 넘기지 않는다.

    앞에서부터 자르지 않고 **실질 문장을 고른다.** IR 소개자료는 본문 앞이
    회사 연혁·사업 소개라, 앞부분을 그대로 넘기면 '주가를 보는 사람이 새로
    얻는 것' 이 없는 글이 된다(#125 fit 1점 6건).

    고르는 기준
      - 목표가·투자의견 문장은 건너뛴다. 이미 별도 사실이라 중복이다
      - 회사 소개 보일러플레이트는 건너뛴다
      - 수치가 붙었거나 앞을 내다보는 서술을 먼저 담는다
      - 실질 문장이 하나도 없으면 빈 문자열을 돌려준다. 그러면 게이트가
        글감부족으로 막는다 — 빈 내용을 억지로 채우지 않는다
    """
    if not text:
        return ""
    # IR 소개자료는 '1 주요 제품 및 연혁' 같은 목차 머리글이 본문 앞에 붙는다.
    # 목차 번호는 섹션 제목 바로 앞에 올 때만 지운다. 느슨하게 잡으면
    # '영업이익 1조 1,203억원' 의 1 까지 지워 수치를 훼손한다.
    text = re.sub(r"(?:^|(?<=\s))\d{1,2}\s+"
                  r"(?=(?:주요|사업|실적|투자|재무|산업|시장|기업|회사|개요|연혁|"
                  r"현황|전망|요약|성장)[\s가-힣])", "", text)
    sents = [x.strip() for x in re.split(r"(?<=다[.!?])\s+", text) if x.strip()]
    picked = []
    for s in sents:
        if _TP_API.search(s) or _OP_API.search(s):
            continue
        if _BOILER.search(s):
            continue
        if not _SUBSTANCE_SENT.search(s):
            continue
        if len(" ".join(picked + [s])) > GIST_MAX:
            break
        picked.append(s)
    return " ".join(picked).strip()


def fetch_naver_api(limit: int = 12) -> list[dict]:
    """개편된 네이버 리서치 API. 프로브로 확정한 경로다.

      목록 GET /front-api/research/list?category=company&page=1&pageSize=N
      상세 GET /front-api/research/end?researchId={id}&category=company

    구 HTML 파싱보다 낫다. itemCode 가 JSON 에 직접 들어 있어 종목 귀속이
    정확하고, 상세 content 첫 문장에 목표주가·투자의견이 명시된다.

    content 취급 (2026-09-16 변경, 사용자 컴플라이언스 확인 완료)
      종전에는 수치만 뽑고 본문을 버렸다. 그 결과 facts 가 제목·목표가·
      투자의견 셋뿐이라 fit 이 구조적으로 1~2점이었다(#124 리서치 10건 전건,
      심사 사유 '제목만 반복, 근거 내용 부재').
      프로브 실측: content 는 273~586자 애널리스트 요약문이고, 정형 실적
      수치가 있는 것은 5건 중 1건뿐이라 '수치만 더 뽑기' 로는 밀도가 안 는다.
      그래서 요지를 사실로 넘긴다. 다만 원문을 그대로 싣지 않는다.
        - 문장 경계로 잘라 GIST_MAX 자까지만 넘긴다
        - 발간 증권사 표기가 의무다(filters 출처없는목표주가)
        - 원문은 문어체라 그대로 베끼면 literary_style·어미단조에 걸린다.
          모델이 구어체로 다시 쓸 수밖에 없는 구조다
    """
    hdr = {"Accept": "application/json",
           "Referer": "https://m.stock.naver.com/investment/research/company"}
    try:
        r = crawl.requests.get(f"{RESEARCH_API}/list",
                               params={"category": "company", "page": 1,
                                       "pageSize": max(limit, 20)},
                               headers={**crawl.HEADERS, **hdr}, timeout=15)
        items = r.json().get("result") or []
    except Exception as e:
        print(f"[research] 네이버 API 목록 실패: {e}")
        crawl.report("naver_research", 0, limit, "API 목록 실패")
        return []

    out = []
    for it in items[:limit]:
        code, name = it.get("itemCode", ""), it.get("itemName", "")
        if not re.fullmatch(r"\d{6}", code or ""):
            continue          # 0017J0 같은 비정형 코드는 건너뛴다
        tp = op = gist = ""
        try:
            d = crawl.requests.get(f"{RESEARCH_API}/end",
                                   params={"researchId": it["researchId"],
                                           "category": "company"},
                                   headers={**crawl.HEADERS, **hdr}, timeout=15)
            html = ((d.json().get("result") or {}).get("researchContent")
                    or {}).get("content", "")
            full = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
            text = full[:400]
            m1, m2 = _TP_API.search(text), _OP_API.search(text)
            tp = m1.group(1) if m1 else ""
            op = m2.group(1) if m2 else ""
            gist = _gist(full)
        except Exception:
            pass
        crawl.sleep_jitter(0.2, 0.5)

        detail = ""
        if tp and tp.replace(",", "") != "0":
            detail += f"제시 적정가격: {tp}원\n"
        if op and "없음" not in op:
            detail += f"투자의견: {op}\n"
        if gist:
            detail += f"리포트 요지: {gist}\n"
        out.append({
            "id": f"naver-api-{it['researchId']}",
            "kind": "research",
            "stock_code": code,
            "stock_name": name,
            "title": it.get("title", ""),
            "facts": (
                f"종목: {name} ({code})\n"
                f"리포트 제목: {it.get('title', '')}\n"
                f"발간: {it.get('brokerName', '')} / {it.get('writeDate', '')}\n"
                f"{detail}"
                "※ 제시 수치는 증권사 의견이며 단정하지 말 것."
                + ("" if detail else "\n※ 목표주가·투자의견 미제공. 추정하지 말 것.")
            ),
            "src": it.get("endUrl", ""),
        })
    crawl.report("naver_research", len(out), limit, "API 응답 구조 변경 의심")
    return out


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
        written = tds[0].get_text(strip=True)

        out.append({
            "id": f"hk-{report_idx or re.sub(chr(92)+'W', '', title)[:20]}",
            "kind": "research",
            "stock_code": code or None,
            "stock_name": None,              # tickers.resolve 가 코드로 역조회
            "title": title,
            "facts": (
                f"리포트 제목: {title}\n"
                + (f"종목코드: {code}\n" if code else "")
                + (f"발간일: {written}\n" if written else "")
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


# 구 HTML 경로(fetch_naver)는 개편으로 죽었다. 되살릴 일이 있으면 1 로 둔다.
USE_NAVER_HTML = os.environ.get("USE_NAVER_RESEARCH", "0") == "1"


def fetch(limit: int = 16) -> list[dict]:
    # 구 HTML 경로는 개편으로 죽었다. API 경로와 한경을 병행한다.
    n = limit // 2
    return (fetch_naver_api(n) + fetch_hankyung(limit - n))[:limit]
