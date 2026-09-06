"""전수검사 — 설정과 코드의 정합성을 기계적으로 확인한다.

수동 확인은 놓친다. 지금까지 놓친 것들:
  - 프롬프트에 미치환 필드가 남아 워크플로가 죽음
  - Format 지시(3~4문장)와 Global 규칙(최소 5문장)이 동시 만족 불가
  - 프롬프트 길이 지시와 필터 상하한이 어긋나 멀쩡한 글이 리젝
  - 페르소나 숫자 상한이 Angle 계약보다 작아 계약이 불가능
"""
import re
import sys

sys.path.insert(0, ".")

import config
from src import angles, filters, rules
from src import personas as P
from src.personas_v2 import PERSONAS, SLOT_W, SYSTEM_PROMPT as SP2

FAIL = []
WARN = []


def fail(msg):
    FAIL.append(msg)
    print(f"  FAIL  {msg}")


def warn(msg):
    WARN.append(msg)
    print(f"  WARN  {msg}")


def ok(msg):
    print(f"  OK    {msg}")


def sec(t):
    print(f"\n=== {t} ===")


# 1. 프롬프트 미치환 필드
sec("프롬프트 치환")
for pid in PERSONAS:
    s, _ = P.build_messages_v2({"kind": "flow", "title": "t", "facts": "등락률: 1.0%"},
                               pid, "reaction")
    left = re.findall(r"\{[a-z_]+\}", s)
    if left:
        fail(f"v2 {pid}: 미치환 {left}")
if not FAIL:
    ok(f"v2 페르소나 {len(PERSONAS)}종 전건 치환 완료")

# 2. 길이 규칙 정합성 (프롬프트 지시 vs 필터 경계)
sec("길이 규칙")
for pid, p in PERSONAS.items():
    lo, hi = P.len_bounds(pid)
    m = re.findall(r"(\d+)", p["sentences"])
    smin = int(m[0]) if m else 3
    # 한국어 문장 평균 25~45자로 잡고 하한이 현실적인지 본다
    if lo > smin * 45:
        fail(f"{pid}: 하한 {lo}자 > 최소문장수 {smin}×45자")
    if hi < smin * 20:
        fail(f"{pid}: 상한 {hi}자 < 최소문장수 {smin}×20자")
    if hi > 330:
        warn(f"{pid}: 상한 {hi}자가 과도")
if not [f for f in FAIL if "자" in f]:
    ok("v2 길이 지시와 필터 경계 정합")

# 3. 숫자 상한 vs Angle 계약
sec("숫자 상한 vs Angle 계약")
NUM_HUNGRY = {"reaction": 2, "amount": 2, "ratio": 2, "compare": 2}
for pid, p in PERSONAS.items():
    for aid, need in NUM_HUNGRY.items():
        if p["num_cap"] < need:
            fail(f"{pid}(cap {p['num_cap']}) × {aid}(최소 {need}개) 계약 불가")
ok("페르소나 숫자 상한이 Angle 최소 요구를 충족")

# 4. 슬롯 가중치
sec("슬롯 가중치")
for slot, w in SLOT_W.items():
    missing = set(PERSONAS) - set(w)
    if missing:
        fail(f"v2 {slot}: 페르소나 누락 {missing}")
    if sum(w.values()) == 0:
        fail(f"v2 {slot}: 전건 가중치 0")
    live = [k for k, v in w.items() if v > 0]
    if len(live) < 4:
        warn(f"v2 {slot}: 선택 가능 페르소나 {len(live)}종뿐")
ok("v2 슬롯 가중치 정합")

# 5. 규칙 단일 소스 파생
sec("규칙 단일 소스")
wb, jb = rules.writer_block(), rules.judge_block()
for r in rules.RULES:
    if r.writer not in wb:
        fail(f"규칙 {r.id}: 작성 프롬프트 미반영")
    if r.fatal and r.judge not in jb:
        fail(f"규칙 {r.id}: 심사 프롬프트 미반영")
ok(f"규칙 {len(rules.RULES)}종이 작성·심사 프롬프트에 파생됨")
for blk, nm in ((wb, "v2 프롬프트"),):
    s, _ = P.build_messages_v2({"kind": "flow", "title": "t", "facts": "x"},
                               "fact_note", "reaction")
    if wb not in s:
        fail(f"{nm}에 규칙 블록 미주입")
ok("v2 프롬프트에 규칙 블록 주입 확인")

# 6. Angle 계약
sec("Angle")
for a in angles.ANGLES:
    if not a.require:
        fail(f"Angle {a.id}: eligibility 정규식 없음")
    if "첫 문장" not in a.contract and a.id != "uncertainty":
        warn(f"Angle {a.id}: 계약에 첫 문장 규칙 없음")
allow_missing = [a.id for a in angles.ANGLES if not a.forbid_missing]
if set(allow_missing) != {"inquiry", "uncertainty"}:
    fail(f"미확인 표현 허용 Angle 이 예상과 다름: {allow_missing}")
ok(f"Angle {len(angles.ANGLES)}종 / 미확인 허용 {allow_missing}")

# 7. 필터 자체 검증
sec("필터 동작")
cases = [
    ("1인칭", "저는 이거 물렸는데 오늘 좀 올랐네요. 거래도 많았어요. 계속 보고 있습니다.", True),
    ("매매권유", "지금 비중확대 하세요. 목표가 6만원 갑니다. 반드시 오릅니다. 기회입니다.", True),
    ("기사체", "에이프로젠이 유상증자를 결정했다. 규모는 200억원이다. 공시가 나왔다.", True),
    ("당신", "당신의 판단은 어떤가요. 저평가 구간이라는 의견이 있습니다. 확인해 보세요.", True),
    ("수치선두", "20.32% 상승. 로보티즈 종가는 299,000원입니다. "
                "거래대금은 2,990억원이었습니다.", True),
    ("정상", "로보티즈가 어제 20.32% 올랐습니다. 종가는 299,000원입니다. "
             "거래대금은 2,990억원이었습니다. 코스닥 거래대금 상위권에 "
             "이름을 올렸고, 장 초반부터 거래가 몰렸습니다.", False),
]
for nm, body, should in cases:
    _facts = ("등락률: 20.32%\n종가: 299,000원\n거래대금: 2,990억원")
    errs = filters.check(body, _facts, "fact_note", "reaction", "fact_note")
    if bool(errs) != should:
        fail(f"필터 {nm}: 기대 {should} 실제 {errs}")
    else:
        ok(f"필터 {nm}: {errs or '통과'}")

# 8. 설정
sec("설정")
if sum(config.SLOT_QUOTA.values()) < config.TARGET_POSTS:
    warn(f"슬롯 합 {sum(config.SLOT_QUOTA.values())} < 목표 {config.TARGET_POSTS}")
ok(f"TARGET={config.TARGET_POSTS} QUOTA={config.SLOT_QUOTA} OVERGEN={config.OVERGEN_RATE}")

print(f"\n{'=' * 50}")
print(f"FAIL {len(FAIL)} / WARN {len(WARN)}")
sys.exit(1 if FAIL else 0)
