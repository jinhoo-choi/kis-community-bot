"""계약 정합 감사 — 전역 규칙 / 페르소나 / Angle / 필터의 모순을 찾는다.

이게 필요한 이유(실측):
  전역 규칙에 "수치로 시작하지 않습니다"를 넣었는데
  brief_report 계약은 "숫자 하나를 첫 문장에 단독으로 던지고 시작"이었다.
  이 페르소나는 생성될 때마다 리젝됐고, 살아남은 open_talk 이 배포 23건 중
  17건을 차지해 피드가 질문 도배가 됐다.

  규칙 하나를 추가할 때 페르소나 10종 × Angle 11종을 손으로 대조하는 것은
  실패한다. 대조를 코드가 한다.

검사 방식은 둘이다.
  A. 구조 검사 — 문장 수와 어미 지정처럼 서로 산술적으로 모순되는 조합
  B. 실사격   — 계약이 시킨 대로 쓴 문장을 실제 filters.check 에 태운다.
                계약을 지켰는데 리젝되면 그 계약은 지킬 수 없는 계약이다.
"""
import re
import sys

sys.path.insert(0, ".")

from src import angles, filters, rules                      # noqa: E402
from src.personas_v2 import COMPAT, PERSONAS, SLOT_W, SYSTEM_PROMPT   # noqa: E402

FAIL, WARN = [], []


def fail(msg: str) -> None:
    FAIL.append(msg)
    print(f"  FAIL  {msg}")


def warn(msg: str) -> None:
    WARN.append(msg)
    print(f"  WARN  {msg}")


def ok(msg: str) -> None:
    print(f"  OK    {msg}")


def sec(t: str) -> None:
    print(f"\n=== {t} ===")


# ── A. 어미 지정 vs 어미 반복 필터 ────────────────────────────────
# 필터는 같은 어미 4회 이상을 리젝한다. 계약이 어미를 하나로 못박았는데
# 문장 수가 4 이상이면 그 페르소나는 구조적으로 리젝된다.
def check_ending_conflict() -> None:
    sec("어미 지정 vs 어미반복 필터")
    for pid, p in PERSONAS.items():
        d = p["desc"]
        m = re.search(r"종결어미는\s*'([^']+)'\s*로만", d)
        if not m:
            continue
        n = max(int(x) for x in re.findall(r"(\d+)", p["sentences"]) or ["0"])
        if n >= 4:
            fail(f"{pid}: 어미 '{m.group(1)}' 단일 지정인데 {p['sentences']} "
                 f"→ 4회 이상이면 어미반복 리젝. 필터와 충돌")
        else:
            ok(f"{pid}: 어미 단일 지정이나 {p['sentences']} 이라 여유")
    if not any("어미" in f for f in FAIL):
        ok("어미 단일 지정 페르소나 없음 또는 전건 안전")


# ── B. 계약이 시킨 예시 문장을 실제 필터에 태운다 ──────────────────
# desc 안의 예시(예: '...')는 모델이 그대로 베낀다. 예시가 필터를 통과하지
# 못하면 그 페르소나는 매번 리젝된다.
def check_desc_examples() -> None:
    sec("계약 예시문 실사격")
    facts = ("종목: 로보티즈 (108490)\n등락률: 20.32%\n종가: 299,000원\n"
             "거래대금: 2,990억원\n계약 상대: 선민수산")
    for pid, p in PERSONAS.items():
        exs = re.findall(r"예: '([^']+)'", p["desc"])
        for ex in exs:
            errs = filters.check(ex, facts, pid, "reaction", pid)
            # 길이는 예시 한 줄이라 당연히 걸린다. 구조 위반만 본다.
            errs = [e for e in errs if "너무짧음" not in e and "너무김" not in e]
            if errs:
                fail(f"{pid} 예시 «{ex}» → {errs}")
            else:
                ok(f"{pid} 예시 «{ex[:34]}» 통과")
    ok("예시문 점검 완료")


# ── C. 예시문 복제 위험 ───────────────────────────────────────────
# 실측: open_talk 예시 '계약 상대가 어디인지 아시는 분 계신가요?' 가
# 그대로 복제돼 '아시는 분 계신가요?' 로 끝난 글이 7건 나왔다.
def check_example_cloning() -> None:
    sec("예시문 복제 위험")
    for pid, p in PERSONAS.items():
        for ex in re.findall(r"예: '([^']+)'", p["desc"]):
            # 종목명·수치가 안 들어간 예시는 그대로 복사해도 말이 된다 = 위험
            if not re.search(r"\d", ex) and len(ex) > 12:
                warn(f"{pid} 예시 «{ex}» — 수치가 없어 그대로 복제되기 쉽다")
    ok("복제 위험 점검 완료")


# ── D. 전역 금지 vs 페르소나 지시 ─────────────────────────────────
def check_global_vs_persona() -> None:
    sec("전역 금지 vs 페르소나 지시")
    wb = rules.writer_block() + SYSTEM_PROMPT
    pairs = [
        ("평가 금지", r"평가하지|평가 표현", r"감상|해석을 붙|평가"),
        ("사전식 정의 금지", r"사전식 정의|일반론 설명", r"무슨 뜻인지|풀어 줍"),
        ("수치 선두 금지", r"수치로 시작하지 않습니다", r"숫자.{0,12}첫 문장에 단독"),
    ]
    for label, gpat, ppat in pairs:
        if not re.search(gpat, wb):
            continue
        for pid, p in PERSONAS.items():
            if re.search(ppat, p["desc"]):
                warn(f"{label}: 전역은 금지, {pid} 계약은 지시 — 문구 확인 필요")
    ok("전역/페르소나 대조 완료")


# ── E. 전역 금지 vs Angle 지시 ────────────────────────────────────
# 페르소나만 보면 놓친다. Angle 도 첫 문장 구조를 지시한다.
def check_global_vs_angle() -> None:
    sec("전역 금지 vs Angle 지시")
    lead_ban = "수치로 시작하지 않습니다" in SYSTEM_PROMPT
    # 부정형("숫자로 시작하지 않는다")은 준수 문구다. 오탐을 막는다.
    pat = re.compile(r"변화폭을 앞세|숫자를 먼저 던지|첫 문장은 핵심 금액|"
                     r"첫 문장에 그 비교를 놓|숫자로 시작(?!하지 않)")
    for a in angles.ANGLES:
        d = angles.desc(a.id) or ""
        if lead_ban and pat.search(d):
            hit = pat.search(d).group(0)
            fail(f"Angle '{a.id}': 전역은 수치 선두 금지인데 계약은 «{hit}»")
    ok("전역/Angle 대조 완료")


def check_angle_numcap() -> None:
    sec("Angle 수치 요구 vs 페르소나 상한")
    for a in angles.ANGLES:
        d = angles.desc(a.id) or ""
        need = 2 if re.search(r"둘|두 개|2개", d) else 1
        for pid, p in PERSONAS.items():
            if p["num_cap"] < need:
                fail(f"{pid}(cap {p['num_cap']}) × {a.id}(최소 {need}) 계약 불가")
    ok("Angle × 페르소나 수치 계약 정합")


# ── F. 길이 계약 vs 문장 수 ───────────────────────────────────────
def check_length_sentences() -> None:
    sec("문장 수 vs 길이 범위")
    for pid, p in PERSONAS.items():
        n = max(int(x) for x in re.findall(r"(\d+)", p["sentences"]) or ["1"])
        # 한국어 한 문장을 20자로만 잡아도 이 하한은 나와야 한다
        if p["min"] < n * 12:
            warn(f"{pid}: {p['sentences']} 인데 하한 {p['min']}자 — "
                 f"문장당 {p['min'] // n}자면 파편이 된다")
        if p["max"] > n * 70:
            warn(f"{pid}: {p['sentences']} 인데 상한 {p['max']}자 — "
                 f"문장당 {p['max'] // n}자면 길이 지시가 무의미")
    ok("길이/문장수 대조 완료")


# ── G. 시나리오 매트릭스 ──────────────────────────────────────────
# 슬롯 × 페르소나 × Angle 로 실제 생성될 수 있는 조합을 전수 나열하고
# 각 조합이 계약상 성립하는지 본다. 죽은 조합이 많으면 특정 페르소나만
# 살아남아 피드가 한쪽으로 쏠린다 (실측: open_talk 17/23).
def check_scenarios() -> None:
    sec("시나리오 매트릭스 (슬롯 × 페르소나 × Angle)")
    total = dead = 0
    per_slot = {}
    for slot, w in SLOT_W.items():
        alive = []
        for pid, weight in w.items():
            if weight <= 0:
                continue
            allowed = COMPAT.get(pid, set())
            for aid in allowed:
                total += 1
                p = PERSONAS[pid]
                d = angles.desc(aid) or ""
                why = []
                # Angle 이름으로 판정하면 오탐한다 — inquiry 는 '조회공시' 앵글이지
                # 질문형이 아니다. 계약 문구로 본다.
                if p["no_question"] and re.search(r"질문으로 끝|묻는다", d):
                    why.append("질문금지 × 질문요구 Angle")
                # Angle 이 첫 문장 구조를 지시하는데 페르소나도 지시하면 충돌한다
                if re.search(r"첫 문장", d) and re.search(r"첫 문장", p["desc"]):
                    if not (("종목명" in d) == ("종목명" in p["desc"])):
                        why.append("첫 문장 지시 상충")
                # 최소 길이가 문장 수를 감당하지 못하면 매번 너무짧음
                n = max(int(x) for x in re.findall(r"(\d+)", p["sentences"]) or ["1"])
                if p["min"] < n * 10:
                    why.append("문장수 대비 하한 부족")
                if why:
                    dead += 1
                    fail(f"{slot} × {pid} × {aid}: {', '.join(why)}")
                else:
                    alive.append((pid, aid))
        per_slot[slot] = alive
    for slot, alive in per_slot.items():
        personas = {p for p, _ in alive}
        if len(personas) < 3:
            fail(f"슬롯 '{slot}': 성립 페르소나 {len(personas)}종 — 문체가 쏠린다")
        else:
            ok(f"슬롯 '{slot}': 조합 {len(alive)}개 / 페르소나 {len(personas)}종")
    ok(f"전체 조합 {total}개 중 불가 {dead}개")


# ── H. 전역 길이 지시 vs 페르소나 하한 ────────────────────────────
# 실측 #77: 프롬프트는 "250자 이내"만 말하고 하한이 없었다.
# 필터는 페르소나별 하한으로 자르는데 지시는 정반대였고,
# 너무짧음 리젝 36건이 여기서 나왔다.
def check_length_instruction() -> None:
    sec("전역 길이 지시 vs 페르소나 하한")
    if "{min}" not in SYSTEM_PROMPT or "{max}" not in SYSTEM_PROMPT:
        fail("SYSTEM_PROMPT 에 페르소나별 길이 하한/상한 치환자가 없다 — "
             "모델이 하한을 모른 채 쓰게 된다")
    else:
        ok("길이 하한/상한이 프롬프트에 주입된다")
    m = re.search(r"(\d+)자 이내", SYSTEM_PROMPT)
    if m:
        lit = int(m.group(1))
        over = [k for k, p in PERSONAS.items() if p["max"] > lit]
        if over:
            fail(f"프롬프트는 {lit}자 이내라는데 상한이 더 큰 페르소나: {over}")
    ok("길이 지시 점검 완료")


def main() -> None:
    check_ending_conflict()
    check_desc_examples()
    check_example_cloning()
    check_global_vs_persona()
    check_global_vs_angle()
    check_angle_numcap()
    check_length_sentences()
    check_length_instruction()
    check_scenarios()
    print("\n" + "=" * 50)
    print(f"FAIL {len(FAIL)} / WARN {len(WARN)}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
