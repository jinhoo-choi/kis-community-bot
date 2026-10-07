# 2026-10-07 Actions Node 24 최소 호환 변경

기준 main: `710106e2b2c204367af318321882fed31517087a`. 변경은 검토용 PR; 운영 workflow 및 실발송 미실행.

## 현재 workflow / job 목록

| Workflow | Job | 기존 runner → 변경 | 기존 action → 변경 |
|---|---|---|---|
| daily.yml | run | ubuntu-latest → ubuntu-latest | actions/checkout@v4 → actions/checkout@v5<br>actions/setup-python@v5 → actions/setup-python@v6<br>actions/upload-artifact@v4 → actions/upload-artifact@v6 |
| pr-tests.yml | tests | ubuntu-latest → ubuntu-latest | actions/checkout@v4 → actions/checkout@v5<br>actions/setup-python@v5 → actions/setup-python@v6<br>actions/upload-artifact@v4 → actions/upload-artifact@v6 |
| verify.yml | verify | ubuntu-latest → ubuntu-24.04 | actions/checkout@v4 → actions/checkout@v5 |
| diagnose.yml | run | ubuntu-latest → ubuntu-latest | actions/checkout@v4 → actions/checkout@v5<br>actions/setup-python@v5 → actions/setup-python@v6 |
| judge-eval.yml | evaluate | ubuntu-latest → ubuntu-latest | actions/checkout@v4 → actions/checkout@v5<br>actions/setup-python@v5 → actions/setup-python@v6<br>actions/download-artifact@v4 → actions/download-artifact@v7<br>actions/upload-artifact@v4 → actions/upload-artifact@v6 |

## 확인 근거와 선택

- daily-posts #170 / run 37558718891 / job 112590865872: runner 2.337.0, Ubuntu 24.04; checkout@v4, setup-python@v5, upload-artifact@v4 Node 20 경고 실측.
- judge-eval의 download-artifact@v4도 Node 20. v5/v6 action.yml도 node20이므로 기본 node24인 v7 선택. 현재 입력은 name/path/repository/run-id/github-token이며 이름 기반 다운로드 경로는 v4와 동일.
- verify-keys는 setup-python 없이 이미지 기본 Python/pip을 사용. 26.04 문서상 기본 Python 3.14.4로 바뀌어 미검증 SDK/lxml 설치 환경까지 변하므로 이 job만 ubuntu-24.04 고정. 나머지는 Python 3.11 명시, apt/브라우저/시스템 경로 의존성 없음.
- 기존 중복 발송 회귀 검사에서 checkout@v4 하드코딩만 v\d+로 변경. ref: main 조건은 유지.
- 변경 후 ./run_tests.sh 전체 통과: 단위 474/474, 문장틀 14/14, 비용 43/43, 수율 9/9, 파이프라인 오프라인 회귀, E2E 20/20. 감사 FAIL 0. 기존 캐시 문체 경고 2건은 변경 전과 동일.

## 불변식 / 검증 범위

트리거, schedule, concurrency, permissions, secrets, if 조건, action 입력, Python 버전, cache key/path, artifact name/path/retention, 실행 명령 및 발송 가드는 유지. YAML 파싱 후 이 항목의 변경 전후 동등성 확인.

3개 저장소 17개 workflow / 18개 job 정적 점검. actionlint 1.7.12 통과(기존 self-hosted 사용자 label naver-blog는 외부 검증 설정에 등록, shellcheck/pyflakes 연동 제외); hosted Bash 블록 69개 bash -n 통과. 공식 action.yml로 전체 44개 action 참조의 입력 키 검증 및 hosted 42개 참조 node24 선언 확인. Windows 2개 참조만 보류.

26.04 실제 실행·브라우저 실행·운영 dispatch·재실행·유료 호출·실발송은 하지 않음. 기존 테스트는 로컬 Python 3.12.14 환경; hosted Python 3.11/3.12 런타임이나 Ubuntu 26.04에서의 실행 성공을 의미하지 않음.

## 공식 자료

- https://github.com/actions/runner-images/issues/14748 — 2026-10-19 시작, 2026-11-19 완료 예정.
- https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2604-Readme.md
- https://github.com/actions/checkout/blob/v5/action.yml
- https://github.com/actions/setup-python/blob/v6/README.md
- https://github.com/actions/cache/blob/v5/README.md
- https://github.com/actions/upload-artifact/blob/v6/README.md
- https://github.com/actions/download-artifact/blob/v7/README.md

## 다음 자연 실행의 확인 / 롤백

병합 후 정상 일정의 Set up job runner 버전, Node 20 경고 제거, Python 설치, pip 설치, artifact 업로드를 확인. 운영 검증 완료 주장 없음. 문제 발생 시 이 PR의 변경 커밋만 revert. Windows self-hosted는 설치 버전 확인 전 기존 action 유지.
