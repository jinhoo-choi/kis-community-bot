#!/usr/bin/env bash
set -e
echo "=== 1. syntax ==="
python3 -c "import ast,glob;[ast.parse(open(f,encoding='utf-8').read()) for f in glob.glob('**/*.py',recursive=True)];print('ok')"
echo
echo "=== 2. unit (decide / gate / entity / dedup / rules) ==="
python3 tests/test_decide.py
echo
echo "=== 3. E2E 시뮬레이션 ==="
python3 tests/test_e2e.py
