# API 비용·실행시간 최소 변경 기록 (2026-09-12)

> 이 문서는 최초 최소 변경 1~4의 기록입니다. 이후 전체 구조 재검토와 후속 수정은
> [`HANDOFF_2026-09-12_FULL_REVIEW.md`](HANDOFF_2026-09-12_FULL_REVIEW.md)에 이어집니다.
> 생성 묶음·보강·재작성의 최신 동작은
> [`HANDOFF_2026-09-12_CODEX_COST_IMPLEMENTATION.md`](HANDOFF_2026-09-12_CODEX_COST_IMPLEMENTATION.md)를
> 우선합니다.

Claude 후속 검토용 기록입니다. 품질 기준과 생성 프롬프트는 바꾸지 않고, 이미
비용을 쓴 결과물을 뒤에서 버리거나 동일 호출을 반복하는 네 구간만 수정했습니다.

| 번호 | 변경 | 파일 | 의도 |
|---:|---|---|---|
| 1 | Claude Batch 기본 비활성화 | `config.py` | 600초 대기 후 동일 작업을 동기 재호출하는 중복 제거. 필요 시 `USE_BATCH=1`로만 활성화 |
| 2 | Gemini quota circuit breaker | `src/llm/gemini.py`, `src/llm/router.py` | 최초 429/`RESOURCE_EXHAUSTED`/quota/billing 오류 이후 같은 실행의 Gemini 호출 중단; 이후 작성 물량은 남은 provider로 라우팅 |
| 3 | staged generation | `main.py`, `config.py` | 기본 100건씩 생성·심사·판정하고 50건 확보 시 즉시 중단. `GEN_STAGE_SIZE`로 조정 가능 |
| 4 | 수집 이중 과증폭 제거 | `main.py` | 수율과 공급 상한이 반영된 `SLOT_QUOTA`에 `OVERGEN_RATE`를 다시 곱하지 않음 |

## 의도적으로 건드리지 않은 것

- `fit` 및 최소 심사점수
- derived fact/contentability gate
- 페르소나·Angle·문체 상한
- 정규식 실패 시 다른 provider로 1회 재생성하는 기존 정책
- 최종 `decide_distribution()` 로직

## 확인 포인트

1. 운영 로그에서 `[claude] batch`가 기본적으로 사라지는지
2. Gemini 429 후 `[gemini] quota/billing 오류`가 한 번만 출력되고 후속 단계가 Claude로 가는지
3. 1차 묶음에서 50건 확보 시 다음 묶음의 `[gen]` 호출이 없는지
4. `[crawl] market` 요청량이 기존 428건에서 `SLOT_QUOTA['flow']` 상한(기본 60건) 수준으로 내려가는지

## 롤백 스위치

- Batch만 재활성화: `USE_BATCH=1`
- 묶음 크기 확대: `GEN_STAGE_SIZE=<원하는 건수>`

수집 이중증폭과 circuit breaker는 환경변수 스위치가 아니라 코드 수정입니다.
필요 시 이 커밋을 revert하면 원복됩니다.
