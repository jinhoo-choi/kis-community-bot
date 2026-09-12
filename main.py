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
import math
import os
import sys

import config
from src import (state, tickers, generator, telegram_bot, enrich, judge,
                 gate, decide, stats, dedup, crawl, assign, theme_map, facts,
                 template_reserve)
from src.sources import dart, research, market, policy, telegram_ch, kind_inquiry
from src.llm.base import reset_usage


def collect() -> list[dict]:
    q = config.COLLECT_CAP
    items = []
    # 후보 수집과 LLM 생성을 분리한다. 충분한 후보를 모으되 LLM 은 아래에서
    # GEN_STAGE_SIZE 단위로만 호출한다. OVERGEN_RATE 를 다시 곱하지 않는다.
    items += dart.fetch(q["disclosure"])
    items += research.fetch(q["research"])
    flow_items = market.fetch(q["flow"])
    # 조회공시는 특징주의 '왜 올랐는지'를 메우는 유일한 공식 확정 정보다.
    # 독립 항목으로도 쓰고, 같은 종목 특징주에 근거로도 붙인다.
    inquiries = kind_inquiry.fetch(max(3, q["disclosure"] // 4))
    n_att = kind_inquiry.attach_to_flow(flow_items, inquiries)
    # 반대 방향도 채운다. 조회공시 종목이 거래대금 상위에 없으면
    # attach_to_flow 로는 영영 연결되지 않는다 (실측 2/3).
    n_mkt = kind_inquiry.enrich_with_market(inquiries, flow_items)
    if n_att or n_mkt:
        print(f"[kind] 특징주→조회공시 {n_att}건 / 조회공시→시세 {n_mkt}건")
    items += flow_items
    items += inquiries
    items += policy.fetch(q["policy"])
    # 운용사 공식 채널. verified 채널이 없으면 0건 반환한다.
    items += telegram_ch.fetch(max(1, q["policy"] // 2))
    items += policy.make_polls(items, q["poll"])
    return items


def _stage_order(items: list[dict]) -> list[dict]:
    """한 생성 묶음이 특정 소스에 쏠리지 않도록 유형별 후보를 고르게 섞는다."""
    order = {k: i for i, k in enumerate(config.GEN_CAP)}
    used = {k: 0 for k in config.GEN_CAP}

    def key(it):
        k = it["kind"] if it.get("kind") in config.GEN_CAP else "research"
        used[k] += 1
        # 각 유형의 전체 생성 상한에서 현재 항목이 차지하는 상대 위치.
        return used[k] / max(config.GEN_CAP[k], 1), order[k]

    return sorted(items, key=key)


def _drop_no_board(items: list[dict], blocked: list[tuple[str, str]]) -> list[dict]:
    """종목방에 근거 있게 매핑하지 못한 테마 항목을 생성 전에 제외한다."""
    no_board = [it for it in items if it.get("no_stock_fit")]
    if not no_board:
        return items
    blocked.extend((it.get("id", "?"), "tier5:게시판없음") for it in no_board)
    print(f"[theme] 게시판 매칭 실패 {len(no_board)}건 생성 전 제외")
    return [it for it in items if not it.get("no_stock_fit")]


def _prepare_items(raw: list[dict], seen: dict):
    """게이트부터 게시판 매핑·중복 제거까지 결정형 후보 준비를 다시 계산한다."""
    gated, blocked = gate.apply(raw)
    resolved = []
    for item in gated:
        item = tickers.resolve(item)
        item["board"] = tickers.board_of(item)
        resolved.append(item)
    theme_map.assign_all(resolved)
    resolved = _drop_no_board(resolved, blocked)
    items, dup_reasons = dedup.filter_new(
        resolved, {} if config.IGNORE_SEEN else seen["seen"])
    return items, blocked, resolved, dup_reasons


def _pick_candidates(items: list[dict]) -> tuple[list[dict], dict]:
    """유형별 생성 상한을 적용하고 stage 순서로 섞는다."""
    picked, counts = [], {k: 0 for k in config.GEN_CAP}
    for item in items:
        kind = item["kind"] if item["kind"] in config.GEN_CAP else "research"
        if counts[kind] < config.GEN_CAP[kind]:
            counts[kind] += 1
            picked.append(item)
    return _stage_order(picked), counts


def _balanced_rescue(items: list[dict], limit: int) -> list[dict]:
    """보강 예산을 한 유형이 독식하지 않도록 배포 목표가 큰 유형부터 순환한다."""
    by_kind = {}
    for item in items:
        by_kind.setdefault(item.get("kind"), []).append(item)
    out = []
    while len(out) < limit and any(by_kind.values()):
        for kind in sorted(by_kind, key=lambda k: -config.DIST_CAP.get(k, 0)):
            if by_kind[kind] and len(out) < limit:
                out.append(by_kind[kind].pop(0))
    return out


def _next_stage_size(remaining: int, needed: int,
                     attempted: int, deliverable: int) -> int:
    """누적 실수율로 다음 생성량을 계산하되 작은/과대한 묶음을 막는다."""
    if remaining <= 0 or needed <= 0:
        return 0
    observed_yield = (deliverable / attempted) if attempted else config.YIELD
    observed_yield = min(1.0, max(0.02, observed_yield))
    estimated = math.ceil(needed / observed_yield)
    bounded = min(config.GEN_STAGE_MAX, max(config.GEN_STAGE_MIN, estimated))
    return min(remaining, bounded)


def main():
    dry = "--dry-run" in sys.argv
    reset_usage()
    enrich.CALLS[0] = 0
    s = state.prune(state.load())

    raw = collect()
    print(f"[main] 수집 총 {len(raw)}건")

    # 용어 설명은 보강·게이트 이전에 코드가 붙인다. 모델에게 정의를 맡기지 않는다.
    _n_term = facts.annotate_terms(raw)
    if _n_term:
        print(f"[main] 용어 설명 주입 {_n_term}건")

    # 캐시는 외부 호출이 아니므로 먼저 재사용한다. 신규 검색은 아래에서 후보가
    # 실제로 부족할 때만 5건씩 수행한다.
    thin = [x for x in raw if x.get("kind") != "flow" and not gate.has_substance(x)]
    cache_hits = enrich.apply_cached(thin) if config.ENABLE_ENRICH else {"ok": 0, "none": 0}
    if any(cache_hits.values()):
        print(f"[enrich] 선행 API 호출 없이 캐시 적용 {cache_hits}")
    if dry:
        print("[enrich] dry-run — 신규 보강 호출 없음")

    # 하드 게이트 — AI 호출 이전에 구조적으로 배제
    print(f"[main] 발송 목표 {config.TARGET_POSTS}건 / 수율 {config.YIELD:.0%} "
          f"→ 생성 상한 {sum(config.GEN_CAP.values())}건 "
          f"→ 기대 발송 {config.EXPECTED_SENT:.0f}건")
    if config.EXPECTED_SENT < config.TARGET_POSTS * 0.8:
        print(f"[main] ⚠ 공급 상한에 걸려 목표 미달 예상 "
              f"({config.EXPECTED_SENT:.0f} < {config.TARGET_POSTS}). "
              "필터가 아니라 물량 문제다.")
    # 다축 dedup — 같은 사건이 DART/리서치/수급으로 중복 유입되는 것을 잡는다.
    items, blocked, resolved, dup_reasons = _prepare_items(raw, s)
    if config.IGNORE_SEEN:
        print("[main] IGNORE_SEEN=1 — 과거 dedup 이력 무시, 상태 저장 안 함")

    picked, cnt = _pick_candidates(items)
    actual_expected = config.expected_sent(cnt)

    # 캐시를 써도 유형별 기대 발송이 목표보다 작을 때만 tier5 글감부족 후보를
    # 유형 균형 순서로 5건씩 보강한다. 매 묶음 뒤 전체 결정형 후보를 다시 계산한다.
    if config.ENABLE_ENRICH and not dry and actual_expected < config.TARGET_POSTS:
        reasons = dict(blocked)
        rescue_pool = _balanced_rescue([
            x for x in raw
            if x.get("kind") != "flow"
            and reasons.get(x.get("id", "")) == "tier5:글감부족"
            and x.get("_enrich_cache_status") != "none"
        ], config.ENRICH_MAX)
        for start in range(0, len(rescue_pool), config.ENRICH_RESCUE_CHUNK):
            chunk = rescue_pool[start:start + config.ENRICH_RESCUE_CHUNK]
            print(f"[enrich] 후보 부족 rescue {start + 1}~{start + len(chunk)}"
                  f"/{len(rescue_pool)}건")
            enrich.enrich_all(chunk, workers=min(config.ENRICH_RESCUE_CHUNK, len(chunk)))
            items, blocked, resolved, dup_reasons = _prepare_items(raw, s)
            picked, cnt = _pick_candidates(items)
            actual_expected = config.expected_sent(cnt)
            print(f"[enrich] rescue 뒤 기대 발송 {actual_expected:.1f}"
                  f"/{config.TARGET_POSTS}건")
            if actual_expected >= config.TARGET_POSTS:
                break
    elif config.ENABLE_ENRICH and not dry:
        print(f"[enrich] 후보 충분 ({actual_expected:.1f}/{config.TARGET_POSTS})"
              " → 신규 그라운딩 0건")

    enriched_n = sum(1 for x in raw if x.get("enriched"))

    degraded = crawl.degraded_sources()
    if degraded:
        print(f"[main] ⚠ 수집 이상 소스: {degraded}")

    print(f"[main] 생성 대상 {len(picked)}건 {cnt}")
    print(f"[main] 실제 후보 기준 기대 발송 {actual_expected:.1f}건")
    if actual_expected < config.TARGET_POSTS * 0.8:
        print(f"[main] ⚠ 실제 후보 부족 ({actual_expected:.1f} < "
              f"{config.TARGET_POSTS}). 전량 생성해도 목표 미달 가능성이 높다.")

    # API 호출 전에 검증 가능한 flow 원본으로 50+15 reserve를 만든다.
    # LLM은 이 원본을 교체해 품질을 높이는 경로이며, 실패해도 준비량을 줄이지 않는다.
    reserve_goal = config.TARGET_POSTS + template_reserve.RESERVE_EXTRA
    reserve = template_reserve.build(picked, reserve_goal)
    reserve_probe, _ = decide.decide_distribution(
        [dict(p) for p in reserve], target=config.TARGET_POSTS)
    reserve_ready = len(reserve_probe) >= config.TARGET_POSTS
    print(f"[template] 결정형 reserve {len(reserve)}/{reserve_goal}건"
          f" → 배분 dry-run {len(reserve_probe)}/{config.TARGET_POSTS}건")
    if not reserve_ready:
        print("[template] ⚠ 50건 보장 reserve 미달 — 검증 사실 공급을 확인하세요")

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
        print(f"\n───── template reserve ─────\n "
              f"준비 {len(reserve)}/{reserve_goal}, dry-run {len(reserve_probe)}/"
              f"{config.TARGET_POSTS}")
        return

    # 생성→심사→판정을 묶음 단위로 실행한다. 목표를 채우면 남은 후보는 LLM에
    # 보내지 않는다. 기존 함수와 최종 판정 기준은 그대로 재사용한다.
    posts, sent_posts, held, attempted_items, stage_sizes = [], [], [], [], []
    # 정상 모드에서는 최종 50건 중 LLM 승인본 70%(35건)를 확보하면 멈추고,
    # 남은 최대 15건을 reserve로 채운다. reserve가 준비되지 않았으면 기존처럼
    # LLM 승인본만으로 전체 목표를 추적한다.
    llm_target = (template_reserve.normal_llm_target(config.TARGET_POSTS)
                  if reserve_ready else config.TARGET_POSTS)
    start = 0
    next_size = min(config.GEN_STAGE_SIZE, len(picked))
    while start < len(picked) and next_size:
        stage = picked[start:start + next_size]
        start += len(stage)
        stage_sizes.append(len(stage))
        attempted_items.extend(stage)
        made = generator.generate(stage, s["recent_tone"])
        if config.ENABLE_JUDGE:
            made = judge.judge_all(made)
        posts.extend(made)
        sent_posts, held = decide.decide_distribution(posts)
        print(f"[main] 단계 생성 {start}/{len(picked)}건"
              f" → LLM 승인 가능 {len(sent_posts)}/{llm_target}건")
        if len(sent_posts) >= llm_target:
            break
        next_size = _next_stage_size(
            len(picked) - start,
            llm_target - len(sent_posts),
            len(attempted_items),
            len(sent_posts),
        )
    # 정규식 리젝분을 즉시 재호출하면 아직 쓰지 않은 원본보다 비싼 두 번째 시도를
    # 먼저 하게 된다. 전체 원본 후보를 소진하고도 목표가 모자랄 때만 한 번 재작성한다.
    if len(sent_posts) < llm_target and not reserve_ready:
        remade = generator.retry_rejected()
        if config.ENABLE_JUDGE:
            remade = judge.judge_all(remade)
        if remade:
            posts.extend(remade)
            sent_posts, held = decide.decide_distribution(posts)
            print(f"[main] 후보 소진 후 재작성 → 누적 배포 가능 "
                  f"{len(sent_posts)}/{config.TARGET_POSTS}건")

    # 최종 판정은 LLM과 reserve를 한 pool에서 다시 수행한다. decide가 LLM을
    # 우선하며, 같은 원본의 LLM/문장틀이 동시에 뽑히지 않게 막는다.
    if reserve:
        sent_posts, held = decide.decide_distribution(posts + reserve)
        template_n = sum(p.get("provider") == "template" for p in sent_posts)
        print(f"[template] 최종 문장틀 보충 {template_n}건 / "
              f"LLM {len(sent_posts) - template_n}건")
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
    delivered_posts = telegram_bot.send_all(sent_posts)
    sent = len(delivered_posts)

    row = stats.record(**stats.summarize(
        raw, blocked, enriched_n, posts, delivered_posts, held,
        generator.collect_fallbacks(), generation_candidates=picked,
        generation_attempted=attempted_items, generation_stages=stage_sizes,
        delivery_attempted=sent_posts, template_reserve=reserve),
        dedup=dup_reasons, crawl_health=crawl.health())
    telegram_bot.send_summary(sent_posts, sent, row, config.TARGET_POSTS)
    print("[main] filter_log " + stats.detail_log(picked, sent_posts, held))
    if degraded:
        telegram_bot.send_warning(f"수집 이상 소스: {', '.join(degraded)}")
    print("[main] stats " + json.dumps(row, ensure_ascii=False))

    for p_ in delivered_posts:
        dedup.mark(p_, s["seen"], __import__("datetime").datetime.now(config.KST).strftime("%Y-%m-%d"))
    if not config.IGNORE_SEEN:
        state.mark(s, delivered_posts)
        state.save(s)
    if sent != len(sent_posts):
        raise RuntimeError(f"텔레그램 부분 전송: {sent}/{len(sent_posts)}건")
    if sent < config.TARGET_POSTS:
        telegram_bot.send_warning(
            f"발송 목표 미달: {sent}/{config.TARGET_POSTS}건. "
            "후보·필터·심사자 상태를 확인하세요.")
        raise RuntimeError(f"발송 목표 미달: {sent}/{config.TARGET_POSTS}건")


def _install_log_mask():
    """표준출력에서 API 키를 가린다.

    실측: opendart 예외 트레이스백에 crtfc_key 가 들어간 URL 이 그대로 찍혀
    퍼블릭 레포의 run_log.txt 에 커밋됐다. 예외 메시지는 통제할 수 없으므로
    출력 단계에서 막는다.
    """
    import re as _re
    import sys as _sys

    keys = [v for v in (config.DART_API_KEY, config.ANTHROPIC_API_KEY,
                        config.GEMINI_API_KEY, config.GEMINI_FREE_API_KEY,
                        config.TELEGRAM_TOKEN)
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
