"""한국투자증권 커뮤니티 - AI 게시글 생성/배포 파이프라인.

수집 → [하드 게이트] → 종목매핑 → 중복제거
     → [1] Gemini 검색 그라운딩으로 사실 보강 (실패 시 thin_facts 플래그)
     → [2] Claude + Gemini 병렬 작성 (슬롯별 temperature 차등)
     → [3] 정규식 검수 → 교차 LLM 심사
     → [4] 배포 판정 (점수·종목상한·유형상한)
     → 텔레그램 배포 → 상태·통계 저장

설계 원칙
  - 구조적 규칙(gate)은 확률적 AI 판단보다 항상 선행한다.
  - 금지 규칙은 src/rules.py 단일 소스에서 3곳(작성·심사·정규식)에 파생된다.
  - 판정 로직은 src/decide.py 순수 함수로 분리되어 테스트가 프로덕션 코드를 호출한다.
  - 미탐 > 오탐. 애매하면 배포하지 않는다 (리스크 모니터링과 반대 방향).
"""
import json
import os
import sys

import config
from src import (state, tickers, generator, telegram_bot, enrich, judge,
                 gate, decide, stats, dedup, crawl, assign, theme_map)
from src.sources import dart, research, market, policy, telegram_ch, kind_inquiry


def collect() -> list[dict]:
    q, over = config.SLOT_QUOTA, config.OVERGEN_RATE
    items = []
    items += dart.fetch(int(q["disclosure"] * over))
    items += research.fetch(int(q["research"] * over))
    flow_items = market.fetch(int(q["flow"] * over))
    # 조회공시는 특징주의 '왜 올랐는지'를 메우는 유일한 공식 확정 정보다.
    # 독립 항목으로도 쓰고, 같은 종목 특징주에 근거로도 붙인다.
    inquiries = kind_inquiry.fetch(max(3, int(q["disclosure"] * over / 4)))
    n_att = kind_inquiry.attach_to_flow(flow_items, inquiries)
    # 반대 방향도 채운다. 조회공시 종목이 거래대금 상위에 없으면
    # attach_to_flow 로는 영영 연결되지 않는다 (실측 2/3).
    n_mkt = kind_inquiry.enrich_with_market(inquiries, flow_items)
    if n_att or n_mkt:
        print(f"[kind] 특징주→조회공시 {n_att}건 / 조회공시→시세 {n_mkt}건")
    items += flow_items
    items += inquiries
    items += policy.fetch(int(q["policy"] * over))
    # 운용사 공식 채널. verified 채널이 없으면 0건 반환한다.
    items += telegram_ch.fetch(max(1, int(q["policy"] * over / 2)))
    items += policy.make_polls(items, q["poll"])
    return items


def main():
    dry = "--dry-run" in sys.argv
    s = state.prune(state.load())

    raw = collect()
    print(f"[main] 수집 총 {len(raw)}건")

    # 사실 보강을 게이트보다 먼저 한다.
    # 순서가 반대면 '보강하면 글감이 되는' 공시·리포트가 tier5(글감부족)로 미리 잘려
    # 수치가 확실한 flow(특징주)만 살아남는 편향이 생긴다 (실측: 3건 전부 특징주).
    # 다만 flow(특징주)는 시세 수치가 이미 facts 에 있어 보강 없이도 게이트를 통과한다
    # (실측: 게이트 차단 60건 중 flow 는 0건). 수집 287건 중 185건이 flow 이므로
    # 전건 보강은 검색 그라운딩 호출을 3배 가까이 낭비한다.
    # dry-run 은 '수집만 실행'이라고 안내하면서 보강을 돌리고 있었다 — 스킵한다.
    # 나아가 '보강 없이도 글감이 되는' 항목은 그라운딩해도 얻는 게 없다.
    # 실제로 필요한 건 지금 글감부족으로 잘릴 항목뿐이다.
    if config.ENABLE_ENRICH and not dry:
        targets = [x for x in raw
                   if x.get("kind") != "flow" and not gate.has_substance(x)]
        # 상한을 앞에서부터 자르면 수집 순서상 한 유형이 예산을 독식한다
        # (naver_research 30건이 먼저 오면 공시·정책은 한 건도 못 받는다).
        # 배포 상한이 있는 유형끼리 번갈아 뽑아 예산을 나눈다.
        _byk = {}
        for x in targets:
            _byk.setdefault(x.get("kind"), []).append(x)
        targets, _i = [], 0
        while len(targets) < config.ENRICH_MAX and any(_byk.values()):
            for k in sorted(_byk, key=lambda k: -config.DIST_CAP.get(k, 0)):
                if _byk[k] and len(targets) < config.ENRICH_MAX:
                    targets.append(_byk[k].pop(0))
        _ids = {id(x) for x in targets}
        skipped = [x for x in raw if id(x) not in _ids]
        print(f"[enrich] 그라운딩 대상 {len(targets)}건 "
              f"(수집 {len(raw)}건 중, 상한 {config.ENRICH_MAX})")
        raw = enrich.enrich_all(targets) + skipped
    elif dry:
        print("[enrich] dry-run — 보강 스킵 (그라운딩 호출 없음)")
    enriched_n = sum(1 for x in raw if x.get("enriched"))

    # 하드 게이트 — AI 호출 이전에 구조적으로 배제
    print(f"[main] 발송 목표 {config.TARGET_POSTS}건 / 수율 {config.YIELD:.0%} "
          f"→ 생성 대상 {sum(config.SLOT_QUOTA.values())}건 "
          f"→ 기대 발송 {config.EXPECTED_SENT:.0f}건")
    if config.EXPECTED_SENT < config.TARGET_POSTS * 0.8:
        print(f"[main] ⚠ 공급 상한에 걸려 목표 미달 예상 "
              f"({config.EXPECTED_SENT:.0f} < {config.TARGET_POSTS}). "
              "필터가 아니라 물량 문제다.")
    gated, blocked = gate.apply(raw)

    resolved = []
    for it in gated:
        it = tickers.resolve(it)
        it["board"] = tickers.board_of(it)
        resolved.append(it)

    # 커뮤니티에 종목방만 있어 테마글도 어딘가에는 올라가야 한다.
    # 관련 섹터 대표주에 배정하고, 본문에서는 종목을 언급하지 않게 지시를 넣는다.
    theme_map.assign_all(resolved)

    # 다축 dedup — 같은 사건이 DART/리서치/수급으로 중복 유입되는 것을 잡는다
    # 반복 테스트에서는 과거 이력을 무시한다(배치 내부 중복은 그대로 잡는다)
    items, dup_reasons = dedup.filter_new(resolved, {} if config.IGNORE_SEEN else s["seen"])
    if config.IGNORE_SEEN:
        print("[main] IGNORE_SEEN=1 — 과거 dedup 이력 무시, 상태 저장 안 함")

    degraded = crawl.degraded_sources()
    if degraded:
        print(f"[main] ⚠ 수집 이상 소스: {degraded}")

    picked, cnt = [], {k: 0 for k in config.SLOT_QUOTA}
    for it in items:
        k = it["kind"] if it["kind"] in config.SLOT_QUOTA else "research"
        if cnt[k] < int(config.SLOT_QUOTA[k] * config.OVERGEN_RATE):
            cnt[k] += 1
            picked.append(it)
    print(f"[main] 생성 대상 {len(picked)}건 {cnt}")

    if dry:
        print("\n───── 수집 표본 ─────")
        for it in picked[:15]:
            print(f"  [{it['kind']:10s}] {it.get('stock_name') or '테마':16s} "
                  f"{(it.get('stock_code') or '-'):>7s}  {it['title'][:44]}")
        print("\n───── 게이트 차단 ─────")
        # 15건만 찍으면 어느 소스가 죽는지 안 보인다. 소스별로 센다.
        _src = lambda i: i.split("-")[0]
        _bycnt = {}
        for bid, why in blocked:
            _bycnt.setdefault((_src(bid), why), 0)
            _bycnt[(_src(bid), why)] += 1
        for (sc, why), n in sorted(_bycnt.items(), key=lambda x: -x[1]):
            print(f"  {sc:10s} {why:20s} {n}건")
        # 소스별 표본 1건의 facts 를 그대로 본다 — 판정식이 뭘 못 본 건지 확인용
        _seen = set()
        _bmap = {b: w for b, w in blocked}
        for it in raw:
            sc = _src(it["id"])
            if it["id"] in _bmap and sc not in _seen:
                _seen.add(sc)
                print(f"\n  --- {sc} 차단 표본 ({_bmap[it['id']]}) {it['id']} ---")
                print("  " + it.get("facts", "")[:400].replace("\n", "\n  "))
        print("\n───── 귀속 강등 ─────")
        for it in resolved:
            if it.get("attr_reject"):
                print(f"  {it['id']:26s} {it['attr_reject']} → {it['board']}")
        print("\n───── 크롤링 헬스 ─────")
        print(json.dumps(crawl.health(), ensure_ascii=False, indent=1))
        print(f"\n───── dedup ─────\n {dup_reasons}")
        return

    posts = generator.generate(picked, s["recent_tone"])

    if config.ENABLE_JUDGE:
        posts = judge.judge_all(posts)

    sent_posts, held = decide.decide_distribution(posts)
    # 담당자 배정은 최종 배포분이 확정된 뒤에 한다.
    # 보류될 글까지 배정하면 담당자별 건수가 실제와 달라진다.
    sent_posts = assign.assign(sent_posts)
    print(f"[main] 배포 {len(sent_posts)}건 / 보류 {len(held)}건")
    for h in held[:5]:
        print(f"   보류 {h['id']} ({h.get('provider')}) - {h.get('hold_reason','')}")

    os.makedirs(os.path.dirname(config.OUTPUT_PATH), exist_ok=True)
    with open(config.OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(sent_posts, f, ensure_ascii=False, indent=1)

    telegram_bot.send_brief(sent_posts)
    sent = telegram_bot.send_all(sent_posts)

    row = stats.record(**stats.summarize(
        raw, blocked, enriched_n, posts, sent_posts, held,
        generator.collect_fallbacks()),
        dedup=dup_reasons, crawl_health=crawl.health())
    telegram_bot.send_summary(sent_posts, sent, row)
    print("[main] filter_log " + stats.detail_log(picked, sent_posts, held))
    if degraded:
        telegram_bot.send_warning(f"수집 이상 소스: {', '.join(degraded)}")
    print("[main] stats " + json.dumps(row, ensure_ascii=False))

    for p_ in sent_posts:
        dedup.mark(p_, s["seen"], __import__("datetime").datetime.now(config.KST).strftime("%Y-%m-%d"))
    if not config.IGNORE_SEEN:
        state.mark(s, sent_posts)
        state.save(s)


def _install_log_mask():
    """표준출력에서 API 키를 가린다.

    실측: opendart 예외 트레이스백에 crtfc_key 가 들어간 URL 이 그대로 찍혀
    퍼블릭 레포의 run_log.txt 에 커밋됐다. 예외 메시지는 통제할 수 없으므로
    출력 단계에서 막는다.
    """
    import re as _re
    import sys as _sys

    keys = [v for v in (config.DART_API_KEY, config.ANTHROPIC_API_KEY,
                        config.GEMINI_API_KEY, config.TELEGRAM_TOKEN)
            if v and len(v) >= 12]
    pat = _re.compile("|".join(_re.escape(k) for k in keys)) if keys else None

    class _Masked:
        def __init__(self, s):
            self._s = s

        def write(self, t):
            if pat:
                t = pat.sub("***", t)
            t = _re.sub(r"(crtfc_key|api[_-]?key|token)=[^&\s\'\")]+",
                        r"\1=***", t, flags=_re.I)
            self._s.write(t)

        def flush(self):
            self._s.flush()

    _sys.stdout = _Masked(_sys.stdout)
    _sys.stderr = _Masked(_sys.stderr)


if __name__ == "__main__":
    _install_log_mask()
    main()
