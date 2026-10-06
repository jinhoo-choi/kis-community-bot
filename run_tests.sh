#!/usr/bin/env bash
set -e
echo "=== 1. syntax (CI 기준 py3.11 문법으로 컴파일) ==="
# 로컬 3.12 / CI 3.11 차이로 f-string 백슬래시 SyntaxError 가 CI 에서만 터진 적이 있다.
# feature_version=(3,11) 로 파싱해 로컬에서도 동일하게 잡는다.
python3 - <<'PYEOF'
import ast, glob, sys
bad = 0
for f in glob.glob('**/*.py', recursive=True):
    src = open(f, encoding='utf-8').read()
    try:
        ast.parse(src, filename=f, feature_version=(3, 11))
    except SyntaxError as e:
        print(f"  SyntaxError(py3.11) {f}:{e.lineno}  {e.msg}")
        bad += 1
print('ok' if not bad else f'{bad} file(s) failed')
sys.exit(1 if bad else 0)
PYEOF
echo
echo "=== 1b. workflow YAML ==="
# 워크플로 블록 스칼라에 컬럼0 텍스트(파이썬/여러줄 메시지)를 넣어
# 워크플로 파일 자체가 무효화된 사고가 2회 있었다. 푸시 전에 잡는다.
python3 - <<'PYEOF'
import glob, sys
try:
    import yaml
except ImportError:
    print("  (pyyaml 미설치 - 스킵)"); sys.exit(0)
bad = 0
for f in glob.glob('.github/workflows/*.yml'):
    try:
        d = yaml.safe_load(open(f, encoding='utf-8'))
        n = sum(len(j.get('steps', [])) for j in d['jobs'].values())
        print(f"  OK   {f}  ({n} steps)")
    except Exception as e:
        print(f"  FAIL {f}  {str(e).splitlines()[-1][:80]}")
        bad += 1
sys.exit(1 if bad else 0)
PYEOF
echo
echo "=== 1c. 정적검사 (중복 정의·미사용 심볼) ==="
python3 -m pyflakes src tools main.py config.py 2>/dev/null \
  | grep -v "f-string is missing" \
  | grep -E "redefinition|imported but unused|assigned to but never used" \
  && { echo "  위 항목 정리 필요"; } || echo "  OK    무경고"

echo
echo "=== 2. unit (decide / gate / entity / dedup / rules) ==="
python3 tests/test_decide.py
python3 tests/test_template_reserve.py
python3 tests/test_cost_offline.py
python3 tests/test_budget_offline.py
python3 tests/test_pipeline_offline.py > /tmp/pipeline_offline.log 2>&1 \
  && grep -aE "^[0-9]+/[0-9]+ passed" /tmp/pipeline_offline.log \
  || { grep -aE "FAIL|Traceback" /tmp/pipeline_offline.log; exit 1; }
echo
echo "=== 2b. 전수검사 (설정-코드 정합성) ==="
python3 tools/audit.py
echo
echo "=== 2c. 계약 정합성 (전역 규칙 x 페르소나 x Angle x 필터) ==="
python3 tools/audit_contracts.py
echo
echo "=== 2c-2. 모듈 간 호출 정합성 (시그니처·존재 여부) ==="
python3 tools/audit_calls.py
echo
echo "=== 2d. 파이프라인 정합성 (facts 생성 x 게이트 x claim x 필터) ==="
# 2d 는 캐시(당일 수집 데이터)까지 검사한다. 운영 실행에서 FAIL 이면
# 코드 변경 없이 당일 데이터 1건 때문에 발송 전체가 막힌다
# (실측 2026-09-29 08:00: policy I6 1건으로 스킵 → 12:07 발송).
# 운영(OPS_RUN=1)은 경고만, PR CI 는 종전대로 차단한다.
if [ "${OPS_RUN:-0}" = "1" ]; then
  python3 tools/audit_pipeline.py \
    || echo "::warning::2d 파이프라인 감사 FAIL — 운영 실행이라 경고 처리(발송 진행). PR에서 수정 필요"
else
  python3 tools/audit_pipeline.py
fi
echo
echo "=== 3. E2E 시뮬레이션 ==="
python3 tests/test_e2e.py
echo
echo "ALL TESTS PASSED"
