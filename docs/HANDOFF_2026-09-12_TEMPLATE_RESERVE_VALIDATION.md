# 문장틀 reserve 구현·실발송 검증 인수인계 (2026-09-12)

## 결론

Claude가 `7c5d241`, `d0647fb`와 비용 절감 제안서 7~9항에 남긴 품질 계약을 코드와
대조한 뒤, Codex가 결정형 flow 문장틀 reserve를 구현했습니다. 검증 가능한 시세 후보가
65건 이상이면 writer와 judge가 모두 중단돼도 **본문 50건을 API 없이 준비**할 수 있습니다.

단, Telegram 장애 중 실제 수신 50건까지 거짓으로 보장하지 않습니다. 전송 성공 응답을 받은
글만 상태에 기록하고, 50 ACK 미만이면 workflow는 실패합니다.

## Claude 회신과 조율 결과

| Claude 조건 | 최종 구현 |
|---|---|
| flow 한정 | `kind=flow`와 종목코드·종목명이 모두 있는 원본만 렌더링 |
| 3~4문장, 70~200자 | 모든 문장틀을 재검증하며 범위 밖이면 reserve에서 제외 |
| 관계값 최소 1개 | 거래량의 20일 평균 배수, 5거래일 누적, 고가 대비 마감 위치만 허용 |
| 문장틀 12종 이상, 같은 틀 3건 이하 | 65건을 만들 수 있도록 22종으로 보정하고 `decide`에서도 3건 상한 재검사 |
| 평시 최대 15건 | 유효 LLM 글 35건 이상이면 LLM 35 + 문장틀 최대 15 |
| 장애 시 부족분 보충 | 유효 LLM 글 35건 미만이면 부족분만 최대 50 |
| 세 페르소나만 사용 | `data_focus`, `fact_note`, `brief_report` |
| 보장 모드 문체 상한 | 문장틀 세 페르소나 각각 최대 17건 |
| LLM 심사 없는 우회 방지 | 임의 boolean을 믿지 않고 facts+본문+template_id로 원문부터 재구성 |
| 담당자 구분 | Telegram 카드에 `생성 경로 : LLM/검증 문장틀` 표시, 복사 본문에는 미포함 |

## 구현

### `3a64a99` — 실발송 문구 보정

5건 실발송에서 다음 의미·문법 문제를 확인했습니다.

- `저가 대비 31.7%가 올랐던 만큼` → `장중 고저 차이는 저가 대비 31.7%`
- `저가 대비 23.4% 상승한 폭` → `장중 고저 차이는 저가 대비 23.4%`
- `고가에서 6.8% 낮은 수준에서 마감` → `고가 대비 6.8% 낮은 수준`
- `20일 평균의 9.5배로 집중` → `20일 평균의 9.5배`
- `낮은 수준에서 마감했습니다` → `낮은 수준이었습니다`

원문 수치는 맞았지만 고저 차이를 종목의 상승폭처럼 읽히게 만들 수 있어, 정제기와 정확한
실발송 문장을 넣은 회귀 테스트를 함께 수정했습니다.

### `4c5da44` — 50건 결정형 reserve

- `src/template_reserve.py`: 22개 문장틀, renderer, 원문 재검증, 65건 builder
- `main.py`: API 호출 전에 reserve를 만들고 dry-run 50건을 확인
- `src/decide.py`: LLM 우선, 정상/보장 모드 분리, 같은 원본 중복 차단
- `src/stats.py`: `template_reserve`, `template_fallback_count`, 페르소나별 준비량 기록
- `src/telegram_bot.py`: 생성 경로 표시
- `tests/test_template_reserve.py`: 수량·다양성·변조 우회·정상/장애 모드·통계 계약 12건

`decide`는 `template_validated=True` 같은 플래그를 보지 않습니다. post의 원본 facts와
`template_id`로 본문을 다시 만들고, 다음 검사를 모두 다시 통과시킵니다.

1. renderer 결과와 본문·페르소나·관계값이 정확히 일치
2. `filters.check`
3. `claims.grounding_errors` — 주장 최대 3개, 원문 밖 숫자 차단
4. `facts.uses_derived` — 결합값과 비교 문맥 동시 사용
5. 문장 수·길이·허용 페르소나·허용 관계값 계약

## 실제 API·Telegram 기준 실행

| 실행 | 결과 | 주요 수치 |
|---|---|---|
| [34693683540](https://github.com/jinhoo-choi/kis-community-bot/actions/runs/34693683540) | 의도대로 전송 차단 | 테스트 채팅 ID·suffix가 없어 5건 준비 뒤 0건 전송 |
| [34693897268](https://github.com/jinhoo-choi/kis-community-bot/actions/runs/34693897268) | 테스트방 ACK 5/5 | 후보 43, stage `[20,14,9]`, 필터 통과 17, 심사 실패 0, 전송 실패 0 |

성공 실행의 실제 계측:

| 항목 | 값 |
|---|---:|
| LLM 호출 | 60회 (Haiku 작성 34, Gemini 작성 9, Sonnet 심사 17) |
| token | 입력 202,676 / 출력 7,396 |
| 검색 보강 | 0회 |
| cache read/write | 0 / 0 |
| token 비용 추정 | `$0.300604` |
| 발송 1건당 | `$0.060121` |
| 후보→발송 수율 | 11.63% |

`by_attempt_type`은 전부 `initial`이어서 필터 탈락 즉시 재작성이 사라졌고,
`grounding_queries=0`이어서 rescue-only 보강도 의도대로 동작했습니다. 최종 5건은 모두
Claude 작성물이었으며, 원문 수치·derived fact·결정형 필터를 다시 적용해 5/5 통과했습니다.

## API 없는 실제 시세 dry-run

`data/market_cache.json`의 실제 234종목에 renderer를 적용했습니다.

| 검증 | 결과 |
|---|---:|
| reserve | 65/65 |
| 원문 재검증 | 65/65 |
| 사용 template_id | 22종 |
| 동일 template_id | 최대 3건 |
| 보장 모드 최종 선택 | 50/50 |
| 문체 분포 | 17 / 17 / 16 |
| 동일 말미 20자 | 최대 2건 |

합성 90종목 회귀도 같은 조건으로 50/50을 반환합니다. 변조 본문에 승인 boolean을 넣은
테스트는 `문장틀검증실패`로 차단됩니다.

## 검증 기준

| 검증 | 결과 |
|---|---:|
| Python 3.11 문법 / workflow YAML | 통과 |
| pyflakes 대상 경고 | 0 |
| 기존 단위 회귀 | 340/340 |
| 문장틀 계약 | 12/12 |
| 설정·프롬프트 감사 | FAIL 0 / WARN 0 |
| Persona×Angle 계약 감사 | 268/268 |
| E2E | 20/20 |

## 남은 운영 검증

1. 이 커밋을 원격에 반영한 뒤 테스트방 5건에서 문장틀 경로·표현·통계를 먼저 확인합니다.
2. 그 결과가 정상일 때 테스트방 50건을 실행해 ACK 50, LLM/template 비율, 비용을 기록합니다.
3. Flash-Lite rejection-only는 Claude가 과거 104건 혼동행렬을 만들고 네 채택 기준을 모두
   충족할 때만 켭니다.
4. 프롬프트 캐싱은 실측에서 cache read/write가 모두 0이었고 현재 system prompt가 항목별
   claim 때문에 달라지므로, 고정 prefix 분리와 1건 warm-up을 별도 실험합니다.
5. Batch는 최대 24시간 처리 계약이 현재 60분 workflow와 충돌하므로 계속 끕니다.

## 롤백

1. 문장틀 reserve 전체: `4c5da44` revert
2. 실발송 문구 정제: `3a64a99` revert

문장틀만 되돌려도 미심사 LLM fail-closed, 성공 ID만 상태 저장, 단계적 생성 등 기존 안전
장치는 유지됩니다.
