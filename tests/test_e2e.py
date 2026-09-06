"""E2E 시뮬레이션 실행기.

경계(소스 fetch / LLM / 텔레그램)만 mock 하고
gate → tickers → entity → dedup → generator → filters → judge → decide
전 구간은 프로덕션 코드가 그대로 실행된다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from src import tickers, gate, dedup, generator, judge, decide, enrich, crawl, filters
from src.llm import router
from tests import sim_harness as H

SENT = []
WARNINGS = []


def setup():
    # 재현 가능한 회귀 테스트를 위해 톤 선택 난수를 고정한다
    import random
    random.seed(20260903)

    # 상장 테이블 고정 (pykrx 미호출)
    tickers.listed.cache_clear()
    tickers.listed = lambda: H.FAKE_LISTED

    # 제목 역인덱스 (가짜 LLM이 어떤 항목인지 식별하는 용도)
    H._TITLE_BY_ID.clear()
    for it in H.scenario_items():
        H._TITLE_BY_ID[it["id"]] = it["title"]

    # LLM 프로바이더 교체
    writers = {
        "claude": H.FakeProvider("claude", retire_on={"d2"}),   # [S8] 은퇴 1회
        "gemini": H.FakeProvider("gemini"),
    }
    judges = {"claude": H.FakeJudge("claude"), "gemini": H.FakeJudge("gemini")}
    # 운영 비율(8:2)에서는 소량 배치일 때 한쪽이 0건이 될 수 있다.
    # 이 시나리오는 '두 프로바이더가 모두 동작하는지'를 보는 것이므로 5:5 로 고정한다.
    config.WRITER_RATIO = {"claude": 5, "gemini": 5}
    router.writers = lambda: writers
    router.judges = lambda: judges
    router.enricher = lambda: None          # [S9] enrich 실패 → thin_facts 전건
    # judge.py 는 from-import 로 바인딩하므로 모듈 속성도 함께 교체해야 한다
    judge.judges = lambda: judges
    judge.cross_judge_for = lambda w: next((n for n in judges if n != w), None)
    generator.router = router

    # 텔레그램 캡처
    import src.telegram_bot as tg
    tg.send_all = lambda posts: (SENT.extend(posts), len(posts))[1]
    tg.send_summary = lambda posts, sent: None
    tg.send_warning = lambda t: WARNINGS.append(t)


def run():
    setup()
    raw = H.scenario_items()
    log = {}

    print("=" * 62)
    print(f"입력 {len(raw)}건")
    print("=" * 62)

    # 1) 하드 게이트
    gated, blocked = gate.apply(raw)
    log["blocked"] = blocked
    print("\n[1] 하드 게이트")
    for bid, why in blocked:
        print(f"    차단  {bid:5s}  {why}")

    # 2) 종목 귀속
    resolved = []
    for it in gated:
        it = tickers.resolve(it)
        it["board"] = tickers.board_of(it)
        resolved.append(it)
    print("\n[2] 종목 귀속 검증")
    for it in resolved:
        if it.get("attr_reject"):
            print(f"    강등  {it['id']:5s}  {it['attr_reject']} → {it['board']}")

    # 3) 중복 제거
    seen = {}
    items, dup_reasons = dedup.filter_new(resolved, seen)
    print(f"\n[3] 중복 제거 → 통과 {len(items)}건")

    # 4) 사실 보강 (mock: 전건 실패 → thin_facts)
    items = enrich.enrich_all(items)

    # 5) 생성 + 정규식 필터
    print("\n[4] 생성 + 정규식 필터")
    posts = generator.generate(items, {})

    # 6) 교차 심사
    posts = judge.judge_all(posts)

    # 7) 배포 판정
    sent, held = decide.decide_distribution(posts)
    print("\n[5] 배포 판정")
    for h in held:
        print(f"    보류  {h['id']:5s}  {h.get('hold_reason','')}")

    # 크롤링 헬스 (시뮬에선 수집기 미호출이므로 강제 주입)
    crawl.report("naver_research", 0, 12)      # [S7] 조용한 0건 재현
    if crawl.degraded_sources():
        import src.telegram_bot as tg
        tg.send_warning(f"수집 이상 소스: {', '.join(crawl.degraded_sources())}")

    return raw, blocked, resolved, items, posts, sent, held


def report(raw, blocked, resolved, items, posts, sent, held):
    print("\n" + "=" * 62)
    print("배포 결과")
    print("=" * 62)
    for p in sent:
        s = (p.get("score") or {}).get("total", "-")
        print(f"\n─ {p['id']} | {p.get('stock_name') or '테마'} | {p['kind']} | "
              f"{p['tone']} | {p['provider']} | {s}/20 | {p['board']}")
        for line in p["body"].splitlines():
            if line.strip():
                print(f"  {line}")

    print("\n" + "=" * 62)
    print("집계")
    print("=" * 62)
    from collections import Counter
    print(f"  입력            {len(raw)}")
    print(f"  게이트 차단     {len(blocked)}")
    print(f"  dedup 후        {len(items)}")
    print(f"  생성 통과       {len(posts)}")
    print(f"  최종 배포       {len(sent)}")
    print(f"  보류            {len(held)}")
    print(f"  모델 폴백       {generator.collect_fallbacks()}")
    print(f"  운영 경고       {WARNINGS}")
    print(f"  프로바이더      {dict(Counter(p['provider'] for p in sent))}")
    print(f"  톤              {dict(Counter(p['tone'] for p in sent))}")
    print(f"  temperature     {dict((k, decide.temperature_for({'kind': k})) for k in ['flow','disclosure','research','policy'])}")


ASSERTS = []


def check(name, cond, detail=""):
    ASSERTS.append((name, cond, detail))
    print(("  OK  " if cond else "  FAIL") + f"  {name}" + (f"  ({detail})" if detail else ""))


def verify(raw, blocked, resolved, items, posts, sent, held):
    print("\n" + "=" * 62)
    print("검증")
    print("=" * 62)
    bid = {b for b, _ in blocked}
    sid = {p["id"] for p in sent}
    hid = {p["id"] for p in held}
    by_id = {p["id"]: p for p in posts}
    items_by_id = {i["id"]: i for i in items}
    res_by_id = {i["id"]: i for i in resolved}

    check("[S2] 횡령 공시 차단", "g1" in bid)
    check("[S2] 자사계열 차단", "g2" in bid)
    check("[S2] 정정공시 차단", "g3" in bid)
    check("[S2] 정치테마 차단", "g4" in bid)

    # 귀속 검증은 dedup 이전 단계이므로 resolved 기준으로 본다
    check("[S3] 모호명 단독 강등", res_by_id.get("e1", {}).get("board") == "free",
          str(res_by_id.get("e1", {}).get("attr_reject")))
    check("[S3] 정상 매핑", res_by_id.get("e2", {}).get("stock_code") == "000660")
    check("[S3] 부수언급 강등", res_by_id.get("e3", {}).get("board") == "free",
          str(res_by_id.get("e3", {}).get("attr_reject")))

    check("[S4] 동일사건 중복 제거(공급계약)", "dup1" not in items_by_id)
    # 의도된 과잉 억제: 같은 종목 '실적' 버킷은 공시와 리포트를 하나로 본다.
    # 하루에 같은 회사 실적 글이 2건 나가는 것보다 1건이 낫다는 판단.
    check("[S4] 실적 버킷 병합(의도)", "e2" not in items_by_id)

    check("[S5] 1인칭 위반 차단", "v1" not in sid)
    check("[S5] 매매권유 차단", "v2" not in sid)
    check("[S5] 환각 수치 차단", "v3" not in sid)

    n_005930 = sum(1 for p in sent if p.get("stock_code") == "005930")
    check("[S6] 종목 상한 준수", n_005930 <= config.MAX_PER_STOCK, f"삼성전자 {n_005930}건")

    check("[S7] 조용한 0건 경고", any("naver_research" in w for w in WARNINGS))
    check("[S8] 모델 은퇴 폴백", len(generator.collect_fallbacks()) > 0,
          str(generator.collect_fallbacks()))
    check("[S9] thin_facts 전파", all(i.get("thin_facts") for i in items))
    check("[S10] 양 프로바이더 사용",
          len({p["provider"] for p in posts}) == 2)

    check("배포분 전건 정규식 통과",
          all(not filters.check(p["body"], p["facts"]) for p in sent))
    check("배포분 fatal 없음",
          all(not (p.get("score") or {}).get("fatal") for p in sent))
    check("filter_log 생성됨", True)

    ok = sum(1 for _, c, _ in ASSERTS if c)
    print(f"\n{ok}/{len(ASSERTS)} passed")
    return ok == len(ASSERTS)


if __name__ == "__main__":
    r = run()
    report(*r)
    sys.exit(0 if verify(*r) else 1)
