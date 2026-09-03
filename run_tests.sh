#!/usr/bin/env bash
set -e
echo "=== syntax ==="
python3 -c "import ast,glob;[ast.parse(open(f,encoding='utf-8').read()) for f in glob.glob('**/*.py',recursive=True)];print('ok')"
echo "=== decide / gate / rules ==="
python3 tests/test_decide.py
