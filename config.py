"""전역 설정. 시크릿은 전부 환경변수(GitHub Secrets)에서만 읽는다."""
import os
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# --- Secrets (GitHub Actions Secrets 로 주입) ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
DART_API_KEY      = os.environ.get("DART_API_KEY", "")
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")

# 테스트 발송 채널. 운영 단톡방에는 사람이 있어 반복 테스트를 보낼 수 없다.
# 수동 실행(workflow_dispatch)은 이쪽으로, 정기 cron 은 운영 채널로 보낸다.
TELEGRAM_TEST_CHAT_ID = os.environ.get("TELEGRAM_TEST_CHAT_ID", "")

# 임시 경로: 시크릿 등록 없이 뒤 4자리로 테스트 채널을 찾는다.
# getUpdates 는 24시간만 보관하므로 상시 운영에는 쓸 수 없다.
# TELEGRAM_TEST_CHAT_ID 를 등록하면 이 경로는 쓰이지 않는다.
TELEGRAM_TEST_CHAT_SUFFIX = os.environ.get("TELEGRAM_TEST_CHAT_SUFFIX", "")

# 반복 테스트용. 같은 날 여러 번 돌리면 dedup 이력에 막혀 1~2건만 나온다.
# 이력을 무시하고 생성하되, 상태 저장도 하지 않는다(운영 이력을 오염시키지 않는다).
IGNORE_SEEN = os.environ.get("IGNORE_SEEN", "0") == "1"
TEST_MODE = os.environ.get("TEST_MODE", "0") == "1"


def target_chat() -> tuple[str, bool]:
    """(chat_id, is_test). 테스트 모드인데 테스트 채널이 없으면 발송하지 않는다.
    실수로 운영 채널에 테스트 50건을 쏘는 사고를 구조적으로 막는다."""
    if TEST_MODE:
        return TELEGRAM_TEST_CHAT_ID, True
    return TELEGRAM_CHAT_ID, False

# --- 발송 목표 ---
# TARGET_POSTS 는 '최종 발송' 기준이다. 생성 목표가 아니다.
# 이전에는 생성 목표여서 50 을 줘도 실제 발송은 1~3건이었다.
TARGET_POSTS = int(os.environ.get("TARGET_POSTS", "50"))

# 발송 1건을 만들려면 몇 건을 생성해야 하는가. 실측 누적으로 갱신한다.
# 09-06 기준 생성 대비 발송 33%(3/9), 정규식 통과 41% -> 생성 대상 대비 약 14%.
YIELD = float(os.environ.get("PIPELINE_YIELD", "0.14"))
OVERGEN_RATE = max(1.5, 1.0 / max(YIELD, 0.02))

# 50건 기준 슬롯 배분.
# 리포트는 목표가·투자의견이 있는 건만 쓸 수 있어(한경 위주) 물량이 적다.
# 억지로 채우면 "본문 읽어보세요" 수준의 글이 나오므로 비중을 줄이고
# 정형 수치가 확실한 공시·특징주로 옮긴다.
# flow 는 구조적으로 커뮤니티 적합성을 넘지 못한다 (실측: fit 2/5 로 5건 보류,
# 사유가 전부 "데이터 나열에 그침"). 이미 그 종목을 보고 있는 사람에게
# 종가·등락률·거래대금은 새 정보가 아니다 — 그 사람은 이미 시세를 봤다.
# 주장 수를 줄이면 나열은 사라지지만 남는 게 없다. 두 요구가 반대로 당긴다.
# 해석할 거리가 있는 공시로 물량을 옮긴다.
# 슬롯은 '공급 가능량'에 맞춰야 한다. 하루치 공급은 이렇게 정해져 있다:
#   DART 주요공시 16건 / 리서치 10건 / 정책 6건 / 조회공시 2~3건 (실측)
# 이 셋은 아무리 슬롯을 열어도 그 이상 나오지 않는다.
# 유일하게 탄력적인 소스가 특징주다 — 등락률 상·하위까지 보면 수십 건이 된다.
# 발송 50건이 목표라면 flow 를 주력으로 삼는 수밖에 없다.
_BASE_QUOTA = {
    "disclosure": 14,       # DART 공시 — 공급 한계 16건
    "research":    7,       # 증권사 리포트 (적정가격·투자의견 있는 건만)
    "flow":       22,       # 특징주 — 유일하게 탄력적인 소스
    "policy":      4,       # 정책/거시 — 공급 한계 6건
    "poll":        3,       # 토론 발제
}

# TARGET_POSTS 를 줄이면 슬롯도 비례 축소한다.
# 축소하지 않으면 소량 실행 시 disclosure 하나가 정원을 다 먹어
# 다른 톤·유형을 확인할 수 없다. 각 슬롯 최소 1건은 보장한다.
# 슬롯별 하루 공급 상한 (실측). 이보다 크게 잡아도 그만큼 들어오지 않는다.
# 상한을 두지 않으면 TARGET=50 일 때 생성 대상이 357건으로 부풀어
# LLM 호출 714회가 되고 워크플로가 타임아웃된다.
# flow 는 배포 상한이 19건인데 183건을 생성하고 있었다 (LLM 호출 낭비).
# 배포 상한의 3배까지만 만든다.
SUPPLY_CAP = {"disclosure": 20, "research": 12, "flow": 60, "policy": 12, "poll": 10}

_need = TARGET_POSTS / max(YIELD, 0.02)          # 발송 목표를 위한 생성 필요량
_scale = _need / sum(_BASE_QUOTA.values())
SLOT_QUOTA = {k: min(SUPPLY_CAP[k], max(1, round(v * _scale)))
              for k, v in _BASE_QUOTA.items()}

# 최종 배포 구성비. SLOT_QUOTA(생성 대상)와 별개다.
# 실측: TARGET=50 실행에서 배포 50건 중 flow 가 35건(70%)을 차지했다.
# decide 가 per_kind_cap 으로 SLOT_QUOTA(flow 120)를 쓰는 바람에
# flow 혼자 정원을 채울 수 있었기 때문이다. 배포 상한은 공급량이 아니라
# '독자가 읽을 만한 구성'으로 정해야 한다.
_MIX = {"disclosure": 12, "research": 12, "flow": 20, "policy": 6, "poll": 2}
DIST_CAP = {k: max(1, round(v * TARGET_POSTS / sum(_MIX.values())))
            for k, v in _MIX.items()}
DIST_CAP["theme"] = DIST_CAP["policy"]

# 목표에 못 미치는 이유가 필터인지 공급인지 즉시 판별할 수 있어야 한다.
EXPECTED_SENT = sum(SLOT_QUOTA.values()) * YIELD

# --- 모델 (멀티 프로바이더) ---
# Claude: 대량 저비용 haiku / 문체 품질 우선 sonnet
# 모델은 은퇴한다 (예: Gemini 2.0 계열 2026-06-01 종료).
# 단일 문자열로 박아두면 그날 파이프라인이 통째로 죽으므로 candidate list 로 관리하고
# 첫 호출 실패 시 다음 후보로 자동 폴백한다. 폴백 발생은 run_stats 에 기록된다.
CLAUDE_CANDIDATES = [
    os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
    "claude-sonnet-5",
]
GEMINI_CANDIDATES = [
    os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
    "gemini-flash-latest",
    "gemini-3.1-flash-lite",
]

CLAUDE_MODEL       = CLAUDE_CANDIDATES[0]
CLAUDE_JUDGE_MODEL = os.environ.get("CLAUDE_JUDGE_MODEL", "claude-haiku-4-5-20251001")
# Batch API 는 비동기 대량 작업용이다. 소량 실행에서는 큐 대기가 길어
# 5건 생성에 20분 넘게 걸리고 Actions 타임아웃(45분)에 근접한다(실측).
# 정식 50건 운영에서는 비용 이점이 크므로 켜고, 소량 테스트에서는 끈다.
USE_BATCH = (os.environ.get("USE_BATCH", "auto") == "1") or (
    os.environ.get("USE_BATCH", "auto") == "auto" and TARGET_POSTS >= 30)

# Gemini: 3.5-flash 가 GA 주력(=gemini-flash-latest), 3.1-flash-lite 는 저비용
GEMINI_MODEL        = GEMINI_CANDIDATES[0]
GEMINI_ENRICH_MODEL = os.environ.get("GEMINI_ENRICH_MODEL", "gemini-3.5-flash")
GEMINI_JUDGE_MODEL  = os.environ.get("GEMINI_JUDGE_MODEL", "gemini-3.1-flash-lite")

# 작성 물량 배분 (프로바이더가 하나만 살아있으면 자동으로 몰아준다)
# 실측 평균 심사점수: claude 16.4 / gemini 4.0.
# Gemini 가 체크리스트를 본문으로 출력하는 등 지시 준수가 약해 비중을 낮춘다.
# 0 으로 두지 않는 이유는 문체 지문 분산 효과가 있고, 폴백 경로도 살려두기 위해서다.
WRITER_RATIO = {"claude": 8, "gemini": 2}

TEMPERATURE = float(os.environ.get("TEMPERATURE", "1.0"))

# 페르소나 설계 전환 스위치. 되돌릴 수 있게 둔다.
#   v1 = Voice 4 x Angle 11 x Format 6 x Length 3  (축 분해형)
#   v2 = Persona 10 x Angle 11                     (캐릭터 통합형)
# 어느 쪽이 나은지는 실측으로 판단한다. run_stats 에 mode 가 기록된다.
# 기본값 v2. 같은 소재·같은 조건 비교에서 v2 가 우세했다(2026-09-04):
#   배포 6 vs 5 / 다양성 5·4종 vs 3·3종 / 심사 16.7 vs 16.0
#   특히 v1 은 길이 미달 4건, 어미반복 2건 — Length 를 별도 축으로 두면
#   다른 축과 계속 충돌한다. v2 는 길이가 페르소나 안에 있어 1건뿐이었다.
# v1 은 롤백용으로 유지한다.

# 슬롯별 temperature 차등.
# 수치가 본문에 직접 등장하는 슬롯은 낮춰 숫자 창작을 억제하고,
# 서술 위주 슬롯은 높게 유지해 문체 다양성을 지킨다.
TEMPERATURE_BY_KIND = {
    "flow":       0.4,   # 종가·등락률·거래대금이 그대로 들어감
    "disclosure": 0.6,
    "research":   0.8,
    "policy":     1.0,
    "poll":       1.0,
    "theme":      1.0,
}

# 도배 방지 상한. 50건 중 한 종목에 5건이 몰리면 커뮤니티에서 바로 티가 난다.
MAX_PER_STOCK = int(os.environ.get("MAX_PER_STOCK", "2"))

# 교차 심사
ENABLE_ENRICH = os.environ.get("ENABLE_ENRICH", "1") == "1"
# enrich 는 Gemini 검색 그라운딩이라 항목당 단가가 높다.
# 실측: 2일간 1,376회 호출로 유료 청구 5만원이 발생했다. 폭주 방지용 상한.
ENRICH_MAX = int(os.environ.get("ENRICH_MAX", "60"))

ENABLE_JUDGE  = os.environ.get("ENABLE_JUDGE", "1") == "1"
MIN_JUDGE_SCORE = int(os.environ.get("MIN_JUDGE_SCORE", "14"))   # 20점 환산
# community_fit 하한. 심사가 '이 종목을 보는 사람이 새로 얻는 게 없다'고 판정했는데
# 총점만 넘어 배포되던 문제(실측: 5건 전건 fit 2~3점인데 전건 배포)를 막는다.
MIN_FIT = int(os.environ.get("MIN_FIT", "3"))

# --- 크롤링 매너 ---
REQUEST_DELAY = 1.2         # 초
USER_AGENT = "kis-community-bot/1.0 (internal content pipeline)"

# --- 경로 ---
STATE_PATH = "data/state.json"
OUTPUT_PATH = "data/posts_latest.json"

FOOTER = "AI 생성 · 출처 {src}\n※ 투자 판단과 그 책임은 본인에게 있습니다."
