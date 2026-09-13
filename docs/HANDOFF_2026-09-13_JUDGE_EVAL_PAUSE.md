# Claude 인계 — 104건 심사 평가 실행 전 일시중지 (2026-09-13)

## 결론

네트워크 재연결과 원격 동기화는 정상입니다. 평가 도구는 PR #3으로 `main`에
병합됐지만, **104건 유료 평가는 실행하지 않았습니다.** 이 문서 작성 시점의 최신 Actions는
`daily-posts #112`이며 `judge-eval-104` 실행은 0건입니다.

사용자가 작업 일시중지를 지시했습니다. 다음 작업자는 코드를 더 바꾸거나 Actions를
실행하기 전에 이 문서와 `docs/STATE.md`를 읽고, 아래 두 외부 실행의 조건을 각각 확인해야
합니다.

1. 104건 심사 평가는 과거 게시글·facts를 Anthropic과 Google에 전송하고 유료 호출을
   발생시키므로 사용자의 **명시적 실행 승인**이 필요합니다.
2. Telegram 재검증은 사용자가 `@commwrite_bot` 비공개 채팅에 새 메시지를 보내 suffix
   `2744`가 다시 조회될 때까지 실행하지 않습니다.

## 기준 커밋과 원격 상태

| 항목 | 값 |
|---|---|
| 시작 기준 | `88dba8868fe8c081c800238abac78a9307904766` |
| 평가 구현 | `ea7c62e8e6b9109e7e85a0708600c8c2ea61304d` |
| PR | [#3](https://github.com/jinhoo-choi/kis-community-bot/pull/3) |
| 종료 기준 / `origin/main` | `455ca90d6040be7be1adad49eb28979b876b48b0` |
| 원격 연결 확인 | 2026-09-13 02:43 UTC, `git pull`·GitHub API 조회 정상 |
| 로컬 상태 | `main`, `origin/main`과 일치, 문서 작성 전 clean |
| 최신 Actions | [daily-posts #112](https://github.com/jinhoo-choi/kis-community-bot/actions/runs/34731215278), 실패 |
| 평가 Actions | 미실행 — 새 run ID와 평가 비용 모두 없음 |

`#112`는 코드 회귀 실패가 아닙니다. 새 필터 기준으로 본문 5건을 준비했지만 저장된
`TELEGRAM_TEST_CHAT_ID`가 비어 있고, 24시간 보관인 `getUpdates`에서 suffix `2744`를
복원하지 못해 발송 0/5로 실패했습니다. 해당 실행 비용은 `$0.195939`, 호출은 39회입니다.

## 커밋별 변경과 불변식

| 커밋 | 변경 | 반드시 유지할 불변식 |
|---|---|---|
| `ea7c62e` | `.github/workflows/judge-eval.yml`, `tools/evaluate_judges.py`, 계약 테스트 4건 추가 | 측정 전용이며 운영 라우팅·Telegram 발송·상태 데이터를 변경하지 않음 |
| `455ca90` | PR #3 병합 | 평가 결과가 나와도 네 채택 기준을 모두 통과하기 전 Flash-Lite를 운영에 연결하지 않음 |

평가 계약은 다음과 같습니다.

- 고정 표본은 Actions [#102](https://github.com/jinhoo-choi/kis-community-bot/actions/runs/34681905042)의
  `filter-log-34681905042` 중 결정형 필터 통과 104건입니다.
- 당시 저장된 Gemini 점수는 정답으로 재사용하지 않습니다. 동일 body와 복원 facts를
  Sonnet 5와 Gemini 3.1 Flash-Lite에 각각 새로 심사시킵니다.
- facts 104건 중 98건은 현재 시세 캐시와 Git 이력에서 복원합니다. 누락 6건은
  OpenDART 4건, 네이버 리서치 1건, 한경컨센서스 1건의 공식 경로로 복원합니다.
- 6건 facts 복원이 모두 성공하기 전에는 유료 모델 호출을 시작하지 않습니다.
- Flash-Lite의 사전 탈락 축은 `factual`, `compliant`, `fatal`뿐입니다.
  `natural`이나 `fit`만 낮다는 이유로 탈락시키지 않습니다.
- Flash-Lite 호출 실패 또는 JSON 파싱 실패는 탈락시키지 않고 Sonnet으로 넘기는
  fail-open입니다. 최종 승인은 계속 Sonnet 기준입니다.
- 지정 모델 계약은 `claude-sonnet-5`, `gemini-3.1-flash-lite`입니다. 다른 모델로
  폴백되면 채택하지 않습니다.

## 채택 기준

아래 네 조건과 응답·모델 계약을 모두 통과할 때만 별도 PR에서 rejection-only 라우팅을
구현합니다.

| 기준 | 하한/상한 |
|---|---:|
| Sonnet fatal 재현율 | 80% 이상 |
| Sonnet 최종 통과군 false reject | 15% 이하 |
| 준비량 | baseline 50건 → tiered 50건 유지 |
| 표준 유료단가 건당 심사비 | Sonnet-only 대비 20% 이상 절감 |
| Sonnet 유효 응답 | 104/104 |
| 모델 계약 | 두 지정 모델 전건 일치 |

실제 평가를 실행하면 과거 게시글과 facts 104건이 Anthropic과 Google에 각각 전송되고,
최대 208회 기본 호출이 발생합니다. 직전 안내한 예상 비용은 약 `$0.4~$0.8`이며 재시도에
따라 늘 수 있습니다. **사용자의 `104건 평가 실행 승인`과 같은 명시 승인을 받은 뒤에만**
`judge-eval.yml`을 수동 실행합니다.

## 검증 결과

평가 구현 반영 전 전체 회귀와 반영 후 관련 회귀가 모두 통과했습니다.

| 검증 | 결과 |
|---|---:|
| 단위 | 353/353 |
| 문장틀 계약 | 14/14 |
| 설정·프롬프트 감사 | FAIL 0 / WARN 0 |
| Persona×Angle 계약 | 268/268 |
| E2E | 20/20 |
| `py_compile` / `pyflakes` | 통과 |
| workflow YAML | 파싱 통과 |
| 고정 로그 | 104건, 중복 ID 0 |

## 다음 작업자가 처음 볼 항목

1. `git pull --ff-only` 후 `origin/main == 455ca90`인지 확인합니다.
2. `docs/STATE.md`, 이 문서, `git show ea7c62e`를 대조합니다.
3. Actions 목록에 `judge-eval-104`가 새로 실행되지 않았는지 확인합니다.
4. 사용자가 104건의 외부 모델 전송과 유료 호출을 명시 승인했을 때만
   `judge-eval.yml`을 `main`에서 한 번 실행합니다.
5. 결과 artifact `judge-eval-104-<run_id>`의 `report.json`과 `summary.md`에서 모든 gate를
   확인합니다. gate 실패는 유효한 평가 결과이며 재실행으로 덮지 않습니다.
6. 모든 gate가 통과하면 운영 라우팅은 별도 PR로 구현하고 전체 회귀 후 다시 검토합니다.
   하나라도 실패하면 Flash-Lite 전환을 보류하고 프롬프트 캐싱 실험으로 넘어갑니다.
7. Telegram은 사용자가 `@commwrite_bot`에 새 메시지를 보낸 뒤 `verify.yml`로 suffix
   `2744`를 먼저 검증하고, ACK가 확인된 경우에만 `daily.yml` 5건을 실행합니다.

## 의도적으로 남긴 것

- Flash-Lite는 운영 라우팅에 연결하지 않았습니다.
- 프롬프트 캐싱은 104건 판정 다음 단계로 남겼습니다.
- Batch는 현재 60분 workflow와 충돌하므로 비활성 상태를 유지합니다.
- `daily-posts #112`의 준비 본문을 재발송하지 않았습니다. suffix가 만료된 상태에서
  다시 실행하면 약 `$0.20`의 비용만 반복될 수 있기 때문입니다.
- 평가 결과 파일은 아직 없습니다. 실행되지 않았으므로 추정값을 결과처럼 문서화하지
  않습니다.

## 인증·보안

사용자가 채팅에 제공한 GitHub PAT는 브랜치 push, PR #3 생성·병합에만 일회성 입력으로
사용했고 파일, remote URL, Git 설정에는 저장하지 않았습니다. 토큰 문자열은 이 문서나
저장소에 남기지 않습니다. 이미 대화에 노출된 토큰이므로 다음 작업자는 임의로 재사용하지
말고, 필요하면 사용자에게 재승인 또는 교체를 요청해야 합니다. 작업 종료 후 폐기·재발급이
권장됩니다.

## 롤백

평가 도구만 제거해야 할 때는 다른 변경과 섞지 말고 병합 커밋 단위로
`git revert -m 1 455ca90`을 사용합니다. 현재 평가 도구는 운영 경로에서 호출되지 않으므로,
실행하지 않는 것만으로도 운영 동작에는 영향이 없습니다.
