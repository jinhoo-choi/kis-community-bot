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
echo "=== 2. unit (decide / gate / entity / dedup / rules) ==="
python3 tests/test_decide.py
echo
echo "=== 2b. 전수검사 (설정-코드 정합성) ==="
python3 tools/audit.py
echo
echo "=== 3. E2E 시뮬레이션 ==="
python3 tests/test_e2e.py
echo
echo "ALL TESTS PASSED"
