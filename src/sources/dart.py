"""DART OpenAPI - 전일자 주요 공시 수집.

API 키 발급: https://opendart.fss.or.kr (무료)
list.json 은 종목코드(stock_code)를 직접 제공하므로 매핑 환각이 없다.
"""
import requests
from datetime import datetime, timedelta

from config import DART_API_KEY, KST, USER_AGENT
from src import crawl
from src.sources import dart_detail

BASE = "https://opendart.fss.or.kr/api/list.json"

# 커뮤니티에서 반응이 나오는 공시 유형만 화이트리스트
KEYWORDS = [
    "유상증자", "무상증자", "전환사채", "신주인수권", "자기주식",
    "단일판매", "공급계약", "영업(잠정)실적", "매출액또는손익구조",
    "주식분할", "주식병합", "합병", "분할", "타법인주식", "현금·현물배당",
    "최대주주변경", "감자", "임상시험", "특허취득",
    # 실측: disclosure 슬롯을 30 으로 열었는데 수집이 14건뿐이라 5건만 배정됐다.
    # 슬롯 확대의 선행조건은 물량이다. 정형 API/원문으로 수치를 뽑을 수 있는
    # 유형만 추가한다 — 제목만 남는 유형을 늘리면 tier5 만 늘어난다.
    "타법인주식", "유형자산", "주식교환", "주식이전", "영업양수", "영업양도",
    "신탁계약", "교환사채", "자산양수도", "현금배당", "주주환원",
]


def _yesterday() -> str:
    d = datetime.now(KST) - timedelta(days=1)
    # 월요일 실행이면 금요일 공시를 본다
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def fetch(limit: int = 30) -> list[dict]:
    if not DART_API_KEY:
        print("[dart] DART_API_KEY 없음 → 스킵")
        crawl.report("dart", 0, 0, "API 키 미설정")
        return []

    day = _yesterday()
    items, page = [], 1

    while len(items) < limit and page <= 5:
        # 네트워크 예외가 파이프라인 전체를 죽이면 안 된다.
        # 실측: opendart ConnectTimeout 으로 수집 단계에서 잡이 죽었고,
        #      트레이스백에 API 키가 들어간 URL 이 로그에 그대로 찍혔다.
        try:
            r = requests.get(
                BASE,
                params={
                    "crtfc_key": DART_API_KEY,
                    "bgn_de": day,
                    "end_de": day,
                    "corp_cls": "Y",     # Y=유가증권, K=코스닥
                    "page_no": page,
                    "page_count": 100,
                },
                headers={"User-Agent": USER_AGENT},
                timeout=15,
            )
            data = r.json()
        except Exception as e:
            # 예외 객체에 URL(=키 포함)이 들어 있으므로 타입명만 출력한다
            print(f"[dart] 요청 실패 (page {page}): {type(e).__name__}")
            break
        if data.get("status") != "000":
            print(f"[dart] status={data.get('status')} {data.get('message')}")
            break

        for row in data.get("list", []):
            if not row.get("stock_code"):
                continue
            if not any(k in row["report_nm"] for k in KEYWORDS):
                continue
            # 정형 API 가 없는 유형은 제목만 남아 심사에서 전건 fatal 이 된다
            # (실측 2회 연속: "상세 수치는 공개되지 않았다가 내용의 절반").
            # 게이트까지 끌고 가면 생성·심사 호출만 낭비한다. 여기서 버린다.
            if dart_detail.NO_DETAIL_API.search(row["report_nm"]):
                continue
            items.append({
                "id": f"dart-{row['rcept_no']}",
                "kind": "disclosure",
                "stock_code": row["stock_code"],
                "stock_name": row["corp_name"],
                "title": row["report_nm"].strip(),
                "facts": (
                    f"공시일: {row['rcept_dt']}\n"
                    f"회사: {row['corp_name']} ({row['stock_code']})\n"
                    f"공시명: {row['report_nm'].strip()}\n"
                    f"제출인: {row.get('flr_nm', '')}\n"
                    f"※ 공시 제목 외 상세 수치는 제공되지 않음. 수치를 추정하지 말 것."
                ),
                "src": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={row['rcept_no']}",
            })
            if len(items) >= limit:
                break

        if page >= int(data.get("total_page", 1)):
            break
        page += 1
        crawl.sleep_jitter()

    # 제목만으로는 글이 안 된다. 정형 API 로 핵심 수치를 채운다.
    dart_detail.enrich_all(items, day)

    crawl.report("dart", len(items), limit, "DART API 응답 이상 또는 키 만료")
    return items
