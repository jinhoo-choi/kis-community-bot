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

# --- 생성 목표 ---
TARGET_POSTS = int(os.environ.get("TARGET_POSTS", "50"))
# 정규식 리젝 + 심사 탈락 대비 초과 생성.
# 소량 실행은 후보가 몇 건뿐이라 한두 건만 리젝돼도 0건이 된다. 배율을 올린다.
OVERGEN_RATE = 1.5 if TARGET_POSTS >= 30 else 3.0

# 50건 기준 슬롯 배분.
# 리포트는 목표가·투자의견이 있는 건만 쓸 수 있어(한경 위주) 물량이 적다.
# 억지로 채우면 "본문 읽어보세요" 수준의 글이 나오므로 비중을 줄이고
# 정형 수치가 확실한 공시·특징주로 옮긴다.
_BASE_QUOTA = {
    "disclosure": 22,       # DART 공시 (정형 API 로 수치 확보)
    "research":    7,       # 증권사 리포트 (적정가격·투자의견 있는 건만)
    "flow":       14,       # 특징주/수급 (종가·등락률·거래대금)
    "policy":      4,       # 정책/거시
    "poll":        3,       # 토론 발제
}

# TARGET_POSTS 를 줄이면 슬롯도 비례 축소한다.
# 축소하지 않으면 소량 실행 시 disclosure 하나가 정원을 다 먹어
# 다른 톤·유형을 확인할 수 없다. 각 슬롯 최소 1건은 보장한다.
_scale = TARGET_POSTS / sum(_BASE_QUOTA.values())
SLOT_QUOTA = {k: max(1, round(v * _scale)) for k, v in _BASE_QUOTA.items()}

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
ENABLE_JUDGE  = os.environ.get("ENABLE_JUDGE", "1") == "1"
MIN_JUDGE_SCORE = int(os.environ.get("MIN_JUDGE_SCORE", "14"))   # 20점 만점

# --- 크롤링 매너 ---
REQUEST_DELAY = 1.2         # 초
USER_AGENT = "kis-community-bot/1.0 (internal content pipeline)"

# --- 경로 ---
STATE_PATH = "data/state.json"
OUTPUT_PATH = "data/posts_latest.json"

FOOTER = "AI 생성 · 출처 {src}\n※ 투자 판단과 그 책임은 본인에게 있습니다."
