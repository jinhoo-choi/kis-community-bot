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
from src.llm.base import record_usage

# 같은 공시/리포트를 반복 실행마다 다시 그라운딩하고 있었다.
# 09-05~06 이틀간 22회 실행에서 대상은 거의 동일한 항목들이었다.
# 항목 id 로 캐시하면 재실행 비용이 0 이 된다. 원문이 바뀌지 않는 자료라 안전하다.
CACHE_PATH = "data/enrich_cache.json"
CACHE_TTL_BY_STATUS = {
    "ok": 7 * 86400,        # 같은 원문에서 확인된 사실은 일주일 재사용
    "none": 1 * 86400,      # 오늘 없던 배경은 다음 날 다시 확인
}


def _load_cache() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        return {}
    now = time.time()
    return {k: v for k, v in c.items()
            if v.get("status") in CACHE_TTL_BY_STATUS
            and now - v.get("ts", 0) < CACHE_TTL_BY_STATUS[v["status"]]}


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
- 반드시 Google 검색 도구를 사용합니다. 검색 근거 URL이 없는 답은 사용하지 않습니다.
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
    if g is None or not g.available():
        item["_enrich_error"] = "보강 프로바이더 사용 불가"
        item["_enrich_status"] = "error"
        item["enriched"] = False
        item["thin_facts"] = True
        return item
    CALLS[0] += 1
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
    record_usage(r, "enrich")
    txt = (r.text or "").strip()
    if not r.ok:
        item["_enrich_error"] = r.error[:200]
        item["_enrich_status"] = "error"
    if r.ok and txt.upper() == "NONE":
        item["_enrich_status"] = "none"
    elif r.ok and txt and len(txt) > 15 and r.sources:
        item["facts"] = item["facts"] + "\n\n[검색으로 확인된 배경]\n" + txt
        item["_enrich_text"] = txt        # 캐시 저장용
        item["enrich_sources"] = r.sources
        item["_enrich_status"] = "ok"
        item["enriched"] = True
    else:
        if r.ok and txt and txt.upper() != "NONE" and not r.sources:
            item["_enrich_error"] = "검색 근거 URL 없음"
            item["_enrich_status"] = "error"
        # 리스크봇의 _body_failed 와 같은 역할.
        # 정보가 없는 상태를 '표시'해서 이후 프롬프트에 추측 금지를 주입한다.
        item["enriched"] = False
        item["thin_facts"] = True
    return item


CALLS = [0]   # 실행당 그라운딩 호출 수. 청구액 역산에 필요하다.


def _apply_cached(item: dict, cached: dict) -> bool:
    status = cached.get("status")
    if status == "ok" and cached.get("text") and cached.get("sources"):
        if cached["text"] not in item.get("facts", ""):
            item["facts"] = (item.get("facts", "")
                             + "\n\n[검색으로 확인된 배경]\n" + cached["text"])
        item["enrich_sources"] = cached["sources"]
        item["enriched"] = True
    elif status == "none":
        item["enriched"] = False
        item["thin_facts"] = True
    else:
        return False
    item["_enrich_cache_status"] = status
    return True


def apply_cached(items: list[dict]) -> dict:
    """외부 호출 없이 유효 캐시만 적용하고 상태별 적중 수를 돌려준다."""
    cache = _load_cache()
    counts = {"ok": 0, "none": 0}
    for item in items:
        cached = cache.get(item.get("id", ""))
        if cached and _apply_cached(item, cached):
            counts[cached["status"]] += 1
    return counts


def enrich_all(items: list[dict], workers: int = 5) -> list[dict]:
    cache = _load_cache()
    hits, miss = [], []
    for it in items:
        c = cache.get(it.get("id", ""))
        if c and _apply_cached(it, c):
            hits.append(it)
        else:
            miss.append(it)

    # 키가 없어도 캐시분은 살린다 (로컬/무료 테스트에서 유용)
    g = enricher()
    if g is None or not g.available():
        print(f"[enrich] GEMINI_API_KEY 없음 → 캐시 {len(hits)}건만 사용, "
              f"{len(miss)}건 스킵")
        for it in miss:
            it["thin_facts"] = True
        return hits + miss

    if hits:
        print(f"[enrich] 캐시 적중 {len(hits)}건 → 그라운딩 {len(miss)}건만 호출")
    # 한꺼번에 전부 submit 하면 Google 검색 도구가 작동하지 않는 날에도 40건을
    # 모두 과금한다. 첫 worker 묶음에 정상 응답(ok/NONE)이 하나도 없으면
    # 프로바이더 경로 자체가 망가진 것으로 보고 이번 실행의 나머지를 건너뛴다.
    done = []
    for start in range(0, len(miss), workers):
        chunk = miss[start:start + workers]
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            chunk = list(ex.map(_one, chunk))
        done.extend(chunk)
        healthy = any(x.get("_enrich_status") in ("ok", "none") for x in chunk)
        if start == 0 and not healthy and start + len(chunk) < len(miss):
            rest = miss[start + len(chunk):]
            for it in rest:
                it["_enrich_status"] = "skipped"
                it["_enrich_error"] = "보강 첫 묶음 유효 응답 0 — 후속 호출 중단"
                it["enriched"] = False
                it["thin_facts"] = True
            print(f"[enrich] ⚠ 첫 {len(chunk)}건 유효 응답 0 → "
                  f"나머지 {len(rest)}건 호출 중단")
            done.extend(rest)
            break
    miss = done

    now = time.time()
    for it in miss:
        status = it.get("_enrich_status")
        # 전송·quota 오류를 '배경 없음'으로 캐시하면 복구 후에도 3일간 재시도하지 않는다.
        if status in ("ok", "none"):
            cache[it.get("id", "")] = {
                "ts": now,
                "status": status,
                "text": it.get("_enrich_text", ""),
                "sources": it.get("enrich_sources", []),
            }
    _save_cache(cache)
    items = hits + miss

    n = sum(1 for x in items if x.get("enriched"))
    print(f"[enrich] {n}/{len(items)}건 배경 보강 완료")
    # 보강이 전멸하면 게이트의 '글감부족'이 폭증한다 (실측: enrich 116건일 때 발송 50건,
    # 0건일 때 31건). 조용히 지나가면 원인을 필터에서 찾게 되므로 크게 알린다.
    if items and n == 0:
        errs = [x["_enrich_error"] for x in items if x.get("_enrich_error")]
        if errs:
            print(f"[enrich] ⚠ 전건 실패 — 게이트 글감부족이 급증한다. "
                  f"오류 표본: {errs[0]}")
        else:
            print("[enrich] 검색으로 확인된 추가 배경 없음")
    return items
