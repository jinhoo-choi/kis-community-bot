# Claude 인계 — 게시글 생성 프로세스 전면 재검수 (2026-09-14)

## 결론

`#121` 의 정규식 리젝을 **추측 없이 전수 재현**해 원인을 귀속시켰습니다. 근거는
커밋된 `data/run_log.txt` 와 `data/market_cache.json` 의 실제 flow 197건입니다.
LLM 호출 0건, 비용 0원.

리젝 상위 5종 중 4종의 원인이 **코드가 만든 입력과 코드가 만든 필터의 충돌**이었습니다.
모델 품질 문제가 아닙니다.

| `#121` 리젝 | 건수 | 원인 | 조치 |
|---|---:|---|---|
| `flow:상대날짜` | 60 | facts 가 준 `전일 종가 대비` 를 필터가 리젝 | PR #18 |
| `flow:선정외주장` | 15 | 필터가 `kind` 를 잃어 앵커 미적용 | PR #20 |
| `flow:근거없는수치['20']` | 12 | 기간 단위를 주장 값으로 오인 | PR #20 |
| `disclosure:선정외주장` | 2 | 위와 동일 | PR #20 |
| `flow:주장과다` | 5 | 한 숫자를 여러 claim 이 공유 (일부) | PR #20 |

리서치 29건 전건 보류는 별건으로 PR #19 에서 처리했습니다
([`HANDOFF_2026-09-14_NONFLOW_SUPPLY_FIX.md`](HANDOFF_2026-09-14_NONFLOW_SUPPLY_FIX.md)).

## 기준 커밋

| 항목 | 값 |
|---|---|
| 시작 | `09a2add` |
| 종료 / `origin/main` | `60beecf` |
| PR | #18 → #19 → #20 순 병합, 셋 다 `pr-tests` success |

## 관통하는 실패 계열

세 PR 의 버그가 모두 같은 모양입니다.

> **코드가 facts 에 넣은 표현을, 코드가 만든 필터·게이트·근거 검사가 위반으로 판정한다.**

| PR | 코드가 넣은 것 | 코드가 거부한 것 |
|---|---|---|
| #18 | `시가 출발: 전일 종가 대비 X%` | 본문의 `전일` |
| #19 | `용어 설명: 투자의견은 …` | (거부가 아니라 반대로) 값 없는 리포트를 통과시킴 |
| #20 | `20일 이동평균 대비: 34.3% 위` | 본문의 `20` |
| #20 | `ANCHOR_TYPES` 가 강제한 주장 | 같은 주장을 '선정 외' 로 판정 |

`tools/audit_contracts.py` 는 이 계열을 잡지 못합니다. 감사 범위가
**전역 규칙 × 페르소나 × Angle** 이라, facts 생성 코드와 필터·게이트 사이는
대상 밖입니다. 다음 작업자가 감사 도구를 손본다면 여기가 우선순위입니다.

구체적으로 이런 불변식을 감사에 넣을 수 있습니다.

1. facts 생성부가 만드는 모든 줄은 어떤 `CLAIM_SPEC` 에 매핑되거나, 매핑되지
   않는다면 프롬프트에서 제거돼야 한다
2. 각 `CLAIM_SPEC` 의 value 를 그대로 인용한 본문은 어떤 필터에도 걸리지 않아야 한다
3. `claims.select` 는 프롬프트 경로와 필터 경로에서 같은 집합을 내야 한다
4. `facts.annotate_terms` 가 붙이는 사전 문구는 게이트의 글감 판정과
   `claims.build` 의 값 추출 대상에서 빠져야 한다

## PR #20 상세

### 1. 필터가 `kind` 를 잃는다

`filters.check` 가 `grounding_errors` 에 `{"facts":…, "angle":…}` 만 넘겼습니다.
`claims.select` 는 `ANCHOR_TYPES.get(item.get("kind",""))` 로 앵커를 정하므로
**앵커가 프롬프트 경로에만 적용되고 필터 경로에는 빠집니다.**

공급계약 공시로 재현한 결과 네 앵글 전부 선정 집합이 달랐습니다.

| angle | 프롬프트 | 필터 |
|---|---|---|
| terms | `issue_amt, maturity, counterpart` | `event, maturity, counterpart` |
| decode | `event, issue_amt, contract` | `event, contract, region` |
| context | `event, issue_amt, maturity` | `event, issue_amt, region` |
| reaction | `event, issue_amt, region` | `counterpart, contract, region` |

PR #8(공시 `issue_amt`)부터 있던 버그이고 PR #18 의 research 앵커도 같은 경로를
탑니다. `stock_code` 도 함께 유실돼 근거 숫자 판정에서 빠졌습니다.

### 2. 기간 단위

`· 20일 이동평균 대비: 34.3% 위` 에서 주장의 값은 `34.3` 이고 `20` 은 지표
이름입니다. `facts.derived_values` 는 이미 기간 단위를 값에서 제외하는데
`claims._metadata_numbers` 에만 빠져 있었습니다. `5거래일` 의 `5` 는 공통
화이트리스트에 우연히 있어 통과했고 `20` 만 걸렸습니다.

캐시 197건 재현: **24건 → 0건**.

### 3. 숫자 공유

한 숫자를 여러 claim 이 가지면 주장 하나가 둘로 세어집니다. 실측 197건 중 4건
(장중 고저차와 시가 대비 마감이 둘 다 `29.6%`). `used()` 가 `prefer`(선정된
claim id)를 받아 중복 계수를 막습니다.

## 확인하고 이상 없던 것

추측을 남기지 않으려고 같은 계열을 전수로 봤습니다.

| 점검 | 결과 |
|---|---|
| 결합 사실 줄 ↔ `CLAIM_SPEC` 매핑 | 10종 전부 매핑. 프롬프트 잔류분 없음 |
| 코드가 만든 문장틀 65건 | 필터·근거 검사 **전건 통과** |
| 프롬프트 cap vs 필터 cap | `length` 인자에 페르소나명이 들어가며 양쪽 동일 |

## 판정하지 못한 것

| 리젝 | 건수 | 상태 |
|---|---:|---|
| `flow:결합사실미사용` | 14 | 오탐·정탐 판정 불가 — 실제 본문 필요 |
| `flow:어미단조` | 14 | 설계 의도대로 동작. 조치 불필요 |
| `flow:방향오용` | 10 | 오탐·정탐 판정 불가 — 실제 본문 필요 |

`filter-log` artifact 는 스토리지 호스트(`productionresultssa16.blob.core.windows.net`)가
네트워크 허용 목록에 없어 받지 못했습니다. 허용되면 위 24건을 본문 단위로
판정할 수 있습니다.

## 다음 실행에서 볼 것

세 PR 의 효과는 서로 다른 지표로 나옵니다.

| 지표 | `#121` | 기대 | PR |
|---|---:|---|---|
| `reject_reasons["flow:상대날짜"]` | 60 | 0 | #18 |
| `reject_reasons["flow:선정외주장"]` | 15 | 감소 | #20 |
| `reject_reasons["flow:근거없는수치['20']"]` | 12 | 0 | #20 |
| `by_kind_funnel.research.delivered` | 0 | >0 | #18·#19 |
| `by_kind_funnel.research.attempted` | 35 | 감소 | #19 |
| `by_kind_funnel.policy.delivered` | 0 | >0 | #18 |
| `generation_attempted` | 218 | 감소 | 전부 |
| `llm_usage.calls` | 309 | 감소 | 전부 |

비용 감소는 **추정입니다.** rescue grounding 이 최대 20회 늘어나는 것과
리젝 재생성이 줄어드는 것의 차이라, 실측 전에는 방향만 말할 수 있습니다.

## 실행 조건

테스트 채널 실행은 `@commwrite_bot` 에 새 메시지를 보내 suffix `2744` 를
갱신한 뒤에만 가능합니다. 별도 조치가 없으면 다음 영업일 06:11 정기 실행에
세 PR 이 함께 반영됩니다.
