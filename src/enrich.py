"""1단계 — 사실 보강 (Gemini + Google 검색 그라운딩).

문제: DART 공시는 '제목'만, 리서치는 '제목+증권사'만 나온다.
이 상태로 글을 쓰면 모델이 배경을 지어내거나(환각), 내용이 텅 빈 글이 나온다.

해결: 글을 쓰기 전에 검색 그라운딩으로 '검증된 배경 사실'만 추출해서 facts 에 덧붙인다.
      여기서는 문체를 만들지 않는다. 오직 사실만.
"""
import concurrent.futures as cf
import json
import os
import time

from src.llm.router import enricher

# 같은 공시/리포트를 반복 실행마다 다시 그라운딩하고 있었다.
# 09-05~06 이틀간 22회 실행에서 대상은 거의 동일한 항목들이었다.
# 항목 id 로 캐시하면 재실행 비용이 0 이 된다. 원문이 바뀌지 않는 자료라 안전하다.
CACHE_PATH = "data/enrich_cache.json"
CACHE_TTL = 3 * 86400        # 자료는 며칠이면 낡는다


def _load_cache() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        return {}
    now = time.time()
    return {k: v for k, v in c.items() if now - v.get("ts", 0) < CACHE_TTL}


def _save_cache(c: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
    except Exception as e:
        print(f"[enrich] 캐시 저장 실패: {e}")

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
        item["_enrich_text"] = txt        # 캐시 저장용
        item["enriched"] = True
    else:
        # 리스크봇의 _body_failed 와 같은 역할.
        # 정보가 없는 상태를 '표시'해서 이후 프롬프트에 추측 금지를 주입한다.
        item["enriched"] = False
        item["thin_facts"] = True
    return item


CALLS = [0]   # 실행당 그라운딩 호출 수. 청구액 역산에 필요하다.


def enrich_all(items: list[dict], workers: int = 5) -> list[dict]:
    cache = _load_cache()
    hits, miss = [], []
    for it in items:
        c = cache.get(it.get("id", ""))
        if c and c.get("text"):
            it["facts"] = it["facts"] + "\n\n[검색으로 확인된 배경]\n" + c["text"]
            it["enriched"] = True
            hits.append(it)
        elif c:                      # 이전에 '배경 없음'으로 확인된 항목
            it["enriched"] = False
            it["thin_facts"] = True
            hits.append(it)
        else:
            miss.append(it)

    # 키가 없어도 캐시분은 살린다 (로컬/무료 테스트에서 유용)
    if enricher() is None:
        print(f"[enrich] GEMINI_API_KEY 없음 → 캐시 {len(hits)}건만 사용, "
              f"{len(miss)}건 스킵")
        for it in miss:
            it["thin_facts"] = True
        return hits + miss

    CALLS[0] += len(miss)
    if hits:
        print(f"[enrich] 캐시 적중 {len(hits)}건 → 그라운딩 {len(miss)}건만 호출")
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        miss = list(ex.map(_one, miss))

    now = time.time()
    for it in miss:
        cache[it.get("id", "")] = {"ts": now, "text": it.get("_enrich_text", "")}
    _save_cache(cache)
    items = hits + miss

    n = sum(1 for x in items if x.get("enriched"))
    print(f"[enrich] {n}/{len(items)}건 배경 보강 완료")
    # 보강이 전멸하면 게이트의 '글감부족'이 폭증한다 (실측: enrich 116건일 때 발송 50건,
    # 0건일 때 31건). 조용히 지나가면 원인을 필터에서 찾게 되므로 크게 알린다.
    if items and n == 0:
        errs = [x["_enrich_error"] for x in items if x.get("_enrich_error")]
        print(f"[enrich] ⚠ 전건 실패 — 게이트 글감부족이 급증한다. "
              f"오류 표본: {errs[0] if errs else '(응답은 왔으나 내용 없음)'}")
    return items
