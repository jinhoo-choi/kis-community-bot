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
TARGET_POSTS = 50
OVERGEN_RATE = 1.5          # 정규식 리젝 + 심사 탈락 대비 150% 생성

SLOT_QUOTA = {              # 슬롯별 목표 건수 (합 = TARGET_POSTS)
    "disclosure": 20,       # DART 공시
    "research":   12,       # 증권사 리포트
    "flow":       10,       # 특징주/수급
    "policy":      5,       # 정책/거시
    "poll":        3,       # 토론 발제
}

# --- 모델 (멀티 프로바이더) ---
# Claude: 대량 저비용 haiku / 문체 품질 우선 sonnet
CLAUDE_MODEL       = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
CLAUDE_JUDGE_MODEL = os.environ.get("CLAUDE_JUDGE_MODEL", "claude-haiku-4-5-20251001")
USE_BATCH = os.environ.get("USE_BATCH", "1") == "1"

# Gemini: 3.5-flash 가 GA 주력(=gemini-flash-latest), 3.1-flash-lite 는 저비용
GEMINI_MODEL        = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_ENRICH_MODEL = os.environ.get("GEMINI_ENRICH_MODEL", "gemini-3.5-flash")
GEMINI_JUDGE_MODEL  = os.environ.get("GEMINI_JUDGE_MODEL", "gemini-3.1-flash-lite")

# 작성 물량 배분 (프로바이더가 하나만 살아있으면 자동으로 몰아준다)
WRITER_RATIO = {"claude": 5, "gemini": 5}

TEMPERATURE = float(os.environ.get("TEMPERATURE", "1.0"))

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
