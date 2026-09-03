# KIS Community Post Bot

한국투자증권 앱 커뮤니티 활성화를 위한 **AI 게시글 생성·배포 파이프라인**.
전일자 공시/리포트/수급/정책을 수집해 하루 50건의 게시글 초안을 만들고,
텔레그램으로 직원에게 배포한다. **게시는 사람이 직접 하며, 게시글에는 'AI 작성' 뱃지를 부착한다.**

## 흐름
수집 → 종목매핑 → 중복제거 → Claude 생성 → 자동검수 → 텔레그램 배포 → 상태저장

## 소스
| 슬롯 | 소스 | 종목코드 |
|---|---|---|
| disclosure(20) | DART OpenAPI | 자동 |
| research(12) | 네이버 금융 리서치 / 한경컨센서스 | 자동 / 제목매핑 |
| flow(10) | pykrx (KRX 전일 시세·거래대금) | 자동 |
| policy(5) | 정책브리핑·기재부·금융위·산업부 RSS | 없음(테마) |
| poll(3) | 위에서 파생 | 혼합 |

## 톤 5종
`pro`(전문) `calm`(진중) `light`(장난) `buddy`(친근) `skeptic`(회의)
→ 전부 3인칭. 1인칭 투자 경험 서술은 필터에서 자동 리젝된다.

## 세팅
1. `pip install -r requirements.txt`
2. GitHub Secrets 등록: `ANTHROPIC_API_KEY`, `DART_API_KEY`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`
3. 로컬 테스트: `python main.py --dry-run` (수집만, API 미호출)
4. 스케줄: `.github/workflows/daily.yml` — UTC 21:00 = KST 06:00

## 자동 검수 (src/filters.py)
매매권유 / 목표가단정 / 1인칭경험 / 단정예측 / 마크다운·이모지 / 길이 /
**원문에 없는 숫자(환각)** → 리젝 후 1회 재생성

## 주의
- 리포트 원문(PDF)은 저장·재배포하지 않는다. 제목·메타 + 원문 링크만 사용.
- 모든 게시글 하단에 출처와 `투자 판단과 그 책임은 본인에게 있습니다` 문구가 붙는다.
- 운영 전 준법감시 템플릿 사전승인 권장.
