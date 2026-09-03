"""배포 판정 테스트. main() 이 아니라 decide.py 의 실제 함수를 호출한다."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.decide import decide_distribution, temperature_for
from src.gate import is_hard_excluded
from src import rules, entity, dedup

def P(i, code="005930", total=18, fatal=None, kind="disclosure"):
    return {"id": f"p{i}", "stock_code": code, "kind": kind, "provider": "claude",
            "score": {"total": total, "fatal": fatal or []}}

def run(name, cond):
    print(("  OK  " if cond else "  FAIL") + f"  {name}")
    return cond

def main():
    ok = []

    # 1) 종목 상한: 같은 종목 5건 → 2건만
    sent, held = decide_distribution([P(i) for i in range(5)], target=50, per_stock=2)
    ok.append(run("종목상한 2건 적용", len(sent) == 2 and len(held) == 3))

    # 2) 치명 위반은 점수와 무관하게 보류
    sent, held = decide_distribution([P(1, total=20, fatal=["환각수치"])], per_stock=2)
    ok.append(run("fatal 즉시 보류", len(sent) == 0 and "fatal" in held[0]["hold_reason"]))

    # 3) 저점수 컷
    sent, _ = decide_distribution([P(1, total=9)], min_score=14)
    ok.append(run("저점수 컷", len(sent) == 0))

    # 4) 정렬 우선: 고점수가 상한을 먼저 차지
    posts = [P(1, total=12), P(2, total=20), P(3, total=15)]
    sent, _ = decide_distribution(posts, per_stock=1, min_score=10)
    ok.append(run("고점수 우선 선점", sent[0]["score"]["total"] == 20))

    # 5) 테마글은 종목 상한에서 제외
    posts = [P(i, code=None, kind="policy") for i in range(4)]
    sent, _ = decide_distribution(posts, per_stock=1, per_kind_cap={"policy": 4})
    ok.append(run("테마글 종목상한 면제", len(sent) == 4))

    # 6) 유형 상한
    posts = [P(i, code=f"00000{i}") for i in range(9)]
    sent, _ = decide_distribution(posts, per_kind_cap={"disclosure": 3})
    ok.append(run("유형상한 적용", len(sent) == 3))

    # 7) 게이트: 자사 계열 배제
    ok.append(run("이해상충 차단", is_hard_excluded(
        {"title": "실적 발표", "stock_code": "071050"})[0]))

    # 8) 게이트: 법적 사안 배제
    ok.append(run("횡령 공시 차단", is_hard_excluded(
        {"title": "횡령·배임 혐의 발생", "stock_code": "005930"})[0]))

    # 9) 게이트: 정상 공시는 통과
    ok.append(run("정상 공시 통과", not is_hard_excluded(
        {"title": "단일판매·공급계약 체결", "stock_code": "005930"})[0]))

    # 10) 게이트: 정정공시 노이즈 배제
    ok.append(run("정정신고 차단", is_hard_excluded(
        {"title": "[기재정정]주주총회소집결의", "stock_code": "005930"})[0]))

    # 11) temperature 차등
    ok.append(run("flow 저온 / policy 고온",
                  temperature_for({"kind": "flow"}) < temperature_for({"kind": "policy"})))

    # 12) 규칙 단일 소스: 작성/심사 프롬프트가 같은 개수에서 파생
    ok.append(run("규칙 단일소스 연결",
                  len(rules.writer_block().splitlines()) == len(rules.RULES)
                  and len(rules.judge_block()) > 0))


    # ── 귀속 검증 (인사이트봇 2026-08-02 사례 이식)
    ok.append(run("모호명 단독 거부", not entity.verify_attribution(
        "대상", "대상 기업 실적 점검 리포트")[0]))
    ok.append(run("모호명+주체신호 인정", entity.verify_attribution(
        "대상", "대상, 3분기 영업이익 증가")[0]))
    ok.append(run("일반 종목명 인정", entity.verify_attribution(
        "한미반도체", "한미반도체 공급계약 체결")[0]))
    ok.append(run("제목 미등장 거부", not entity.verify_attribution(
        "한미반도체", "반도체 업황 점검", "한미반도체 언급")[0]))

    # ── 부수 언급 (fail-open 확인)
    ok.append(run("부수언급 드롭", entity.is_incidental(
        "한미반도체", "SK하이닉스 실적 점검", "밸류체인 한미반도체, 협력사 수혜")))
    ok.append(run("주체는 fail-open 유지", not entity.is_incidental(
        "한미반도체", "한미반도체 공급계약", "한미반도체 계약 체결")))

    # ── 다축 dedup
    seen = {}
    a = {"id": "dart-1", "stock_code": "005930", "title": "유상증자 결정"}
    b = {"id": "naver-9", "stock_code": "005930", "title": "삼성전자 유상증자 영향 점검"}
    dedup.mark(a, seen, "2026-09-03")
    ok.append(run("다른 소스 같은 사건 중복 제거", dedup.is_dup(b, seen)[0]))
    c = {"id": "dart-2", "stock_code": "005930", "title": "자기주식 취득 결정"}
    ok.append(run("다른 사건은 통과", not dedup.is_dup(c, seen)[0]))
    ok.append(run("dedup 키 조회·저장 일치",
                  set(dedup.keys(a)) & set(seen.keys()) == set(dedup.keys(a))))

    print(f"\n{sum(ok)}/{len(ok)} passed")
    sys.exit(0 if all(ok) else 1)

if __name__ == "__main__":
    main()
