"""1단계 — 사실 보강 (Gemini + Google 검색 그라운딩).

문제: DART 공시는 '제목'만, 리서치는 '제목+증권사'만 나온다.
이 상태로 글을 쓰면 모델이 배경을 지어내거나(환각), 내용이 텅 빈 글이 나온다.

해결: 글을 쓰기 전에 검색 그라운딩으로 '검증된 배경 사실'만 추출해서 facts 에 덧붙인다.
      여기서는 문체를 만들지 않는다. 오직 사실만.
"""
import concurrent.futures as cf

from src.llm.router import enricher

SYSTEM = """당신은 금융 데이터 리서처입니다. 글을 쓰지 말고 사실만 추출하세요.

[작업]
주어진 공시/리포트 항목에 대해 검색으로 확인 가능한 배경 사실만 정리합니다.

[규칙]
- 확인된 사실만. 추정·전망·의견은 절대 쓰지 않습니다.
- 각 항목은 한 줄. 최대 5줄.
- 수치에는 반드시 기준일을 붙입니다.
- 검색으로 확인되지 않으면 그 줄을 쓰지 않습니다. 억지로 채우지 마세요.
- 투자의견, 목표주가, 수혜 전망은 쓰지 않습니다.
- 확인된 내용이 없으면 정확히 "NONE" 한 단어만 출력합니다.

[출력 형식]
- 사실1
- 사실2
"""

USER = """[종목] {stock}
[유형] {kind}
[제목] {title}

이 항목의 배경 사실을 정리하세요. 회사의 주력 사업, 해당 공시/리포트가 나온 맥락,
최근 확인된 관련 사실 위주로."""


def _one(item: dict) -> dict:
    g = enricher()
    r = g.generate(
        SYSTEM,
        USER.format(
            stock=item.get("stock_name") or "해당 없음",
            kind=item.get("kind", ""),
            title=item.get("title", ""),
        ),
        temperature=0.2,       # 사실 추출이므로 낮게
        max_tokens=400,
    )
    txt = (r.text or "").strip()
    if not r.ok:
        item["_enrich_error"] = r.error[:200]
    if r.ok and txt and txt.upper() != "NONE" and len(txt) > 15:
        item["facts"] = item["facts"] + "\n\n[검색으로 확인된 배경]\n" + txt
        item["enriched"] = True
    else:
        # 리스크봇의 _body_failed 와 같은 역할.
        # 정보가 없는 상태를 '표시'해서 이후 프롬프트에 추측 금지를 주입한다.
        item["enriched"] = False
        item["thin_facts"] = True
    return item


CALLS = [0]   # 실행당 그라운딩 호출 수. 청구액 역산에 필요하다.


def enrich_all(items: list[dict], workers: int = 5) -> list[dict]:
    if enricher() is None:
        print("[enrich] GEMINI_API_KEY 없음 → 보강 스킵")
        for it in items:
            it["thin_facts"] = True
        return items

    CALLS[0] += len(items)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        items = list(ex.map(_one, items))

    n = sum(1 for x in items if x.get("enriched"))
    print(f"[enrich] {n}/{len(items)}건 배경 보강 완료")
    # 보강이 전멸하면 게이트의 '글감부족'이 폭증한다 (실측: enrich 116건일 때 발송 50건,
    # 0건일 때 31건). 조용히 지나가면 원인을 필터에서 찾게 되므로 크게 알린다.
    if items and n == 0:
        errs = [x["_enrich_error"] for x in items if x.get("_enrich_error")]
        print(f"[enrich] ⚠ 전건 실패 — 게이트 글감부족이 급증한다. "
              f"오류 표본: {errs[0] if errs else '(응답은 왔으나 내용 없음)'}")
    return items
