# API 비용 절감 0~4단계 구현 인수인계 (2026-09-12)

## 결론

Claude가 `d0647fb`까지 남긴 회신·비용 시뮬레이션과 공동 조율안 8항을 다시 대조한 뒤,
서로 충돌하지 않는 0~4단계를 순서대로 반영했습니다. 각 단계는 별도 커밋과 전체 회귀를
통과한 뒤 다음 단계로 넘어갔습니다.

이번 묶음은 **불필요한 호출을 뒤로 미루고 실제 비용을 측정할 기반**을 완성합니다.
아직 변경 후 실 API·텔레그램 실행은 하지 않았으므로 절감률은 예상값이 아니라 다음 실행의
`llm_usage.cost_per_delivered_usd`로 판정해야 합니다.

## Claude 회신 검토 결과

| 제안 | 결론 | 이유 |
|---|---|---|
| 프롬프트 캐싱 | 계측만 선반영, 활성화는 보류 | Haiku 4.5는 4,096 token 이상이어야 하고, 동시 호출은 첫 응답이 시작되기 전 생성된 캐시를 공유하지 못하므로 고정·가변 블록 분리와 순차 warm-up 실측이 먼저 필요 |
| Gemini implicit caching | 계측만 선반영 | Gemini 3.5 Flash도 최소 4,096 token 조건이며 실제 `cachedContentTokenCount` 적중부터 확인해야 함 |
| 심사 Batch | 비활성 유지 | 50% 단가 장점은 있으나 목표 처리시간이 최대 24시간이고 현재 60분 workflow·단계별 조기 중단과 충돌 |
| 검색 grounding | rescue-only 유지 | 월 5,000 query 무료 구간이어도 모델 token·지연·실패 노출은 남고, 무료량은 프로젝트 전체 사용량에 따라 달라짐 |
| OpenAI API | 보류 | 현재 무료 Gemini 폴백과 결정형 reserve가 우선이며, 새 provider는 키·어댑터·고정 품질평가 뒤 판단 |

공식 근거:

- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Anthropic batch processing](https://platform.claude.com/docs/en/build-with-claude/batch-processing)
- [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini context caching](https://ai.google.dev/gemini-api/docs/caching)
- [Gemini Batch API](https://ai.google.dev/gemini-api/docs/batch-api)

## 구현 커밋

| 순서 | 커밋 | 담당 | 반영 내용 |
|---:|---|---|---|
| 0 | `7c5d241` | Claude | 1·2차 구성 상한은 유지하고, 50건 미달 때만 3차에서 flow 절대상한 해제 |
| 1 | `bb03ecf` | Codex | provider/model/tier/role/시도유형별 호출·API 시도·token·cache·grounding·추정비용 계측 |
| 2 | `ac456f1` | Codex | 정규식 리젝 즉시 재작성 제거; 쓰지 않은 새 후보를 모두 처리한 뒤 목표 미달일 때만 1회 재작성 |
| 3 | `86dc9d1` | Codex | 정상 후보 충분 시 신규 보강 0회; 부족 시 유형 균형 5건 단위 rescue; 성공 7일/NONE 1일/오류 미저장 |
| 4 | `9fca3af` | Codex | 첫 생성 기본 60건; 이후 누적 실수율로 10~60건; 실제 stage 크기 통계 기록 |

## 새 통계 계약

`run_stats.jsonl.llm_usage`는 다음을 기록합니다.

| 필드 | 의미 |
|---|---|
| `calls`, `failed_calls`, `api_attempts` | 애플리케이션 호출·실패·SDK 내부 재시도 포함 시도 수 |
| `input_tokens`, `output_tokens`, `thinking_tokens` | 공급자 응답 usage에서 읽은 실제 token |
| `cache_read_tokens`, `cache_write_tokens` | 캐시 적중·생성 token; 캐싱 도입 판단의 기준선 |
| `grounding_queries` | 요청 수와 별개인 실제 검색 query 수 |
| `by_route` | `provider|model|paid/free|role`별 집계 |
| `by_attempt_type` | initial, provider 재할당, filter 재작성, judge fallback 분리 |
| `estimated_token_cost_usd` | 2026-09-12 공식 표준 API 단가 기준 token 비용 |
| `cost_per_delivered_usd` | Telegram 성공 건수로 나눈 token 비용 |

검색 grounding 무료량 초과비와 Gemini explicit cache의 시간당 저장료는 프로젝트 전체
사용량·보관시간을 이 실행 하나로 알 수 없어 추정비용에서 제외하고, `cost_scope`에 이를
명시합니다. Claude가 보고한 cache read·creation token은 해당 단가로 계산합니다.
`generation_stages`에는 실제 생성 묶음 크기 배열을 남깁니다.

## 동작 불변식

- 미심사 LLM 글은 계속 fail-closed입니다.
- 새 후보가 남아 있는 동안 필터 리젝분을 재호출하지 않습니다.
- 정상 후보의 유형별 기대 발송량이 50 이상이면 신규 검색 grounding은 0회입니다.
- 보강이 필요해도 5건마다 후보량을 다시 계산하고 목표 기대량에 도달하면 중단합니다.
- 생성은 후보가 남고 50건에 못 미치는 동안 계속되며, stage 축소가 목표량을 줄이지 않습니다.
- 검증된 글이 50건 존재하면 3차 배분이 flow 구성 상한 때문에 이를 버리지 않습니다.
- Telegram 성공분만 상태에 기록하고, 실제 성공이 50 미만이면 정상 완료로 처리하지 않습니다.

## 검증

| 검증 | 결과 |
|---|---:|
| Python 3.11 문법 | 통과 |
| workflow YAML | 3개 전부 통과 |
| pyflakes 대상 경고 | 0 |
| 단위 회귀 | 334/334 |
| 설정·프롬프트 감사 | FAIL 0 / WARN 0 |
| 계약 감사 | 268개 조합 중 불가 0 |
| E2E | 20/20 |

## 아직 완료되지 않은 범위

현재 50건 보장은 **검증·심사를 통과한 글이 50건 존재할 때의 배분과 계속 생성**까지입니다.
writer와 judge가 동시에 전면 중단된 상황에도 50개 본문을 준비하려면 공동 조율안의
flow template 22종, 50+15 reserve, 보장 모드 검증기가 추가로 필요합니다.

다음 순서는 다음과 같습니다.

1. Claude가 과거 104건으로 Flash-Lite rejection-only 혼동행렬을 기록합니다.
2. Codex가 Claude가 승인한 template 계약을 별도 모듈로 구현하고 `decide`에서 원문부터
   재검증하는 경로를 연결합니다.
3. template dry-run 50건 뒤 테스트 채널 5건, 그다음 50건을 전송합니다.
4. 새 계측으로 호출·token·비용·수정률을 기준선과 비교합니다.

## 단계별 롤백

각 단계는 독립 커밋이므로 역순으로 revert합니다.

1. 동적 stage: `9fca3af`
2. rescue-only 보강: `86dc9d1`
3. fresh-first 재작성: `ac456f1`
4. 비용 계측: `bb03ecf`
5. 3차 배분 완화: `7c5d241`

단, 최신 통계 스키마를 읽는 외부 소비자가 있다면 계측 커밋 롤백 전에 호환 여부를 확인합니다.
