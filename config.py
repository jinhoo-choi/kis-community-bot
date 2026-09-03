"""전역 설정. 시크릿은 전부 환경변수(GitHub Secrets)에서만 읽는다."""
import os
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# --- Secrets (GitHub Actions Secrets 로 주입) ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DART_API_KEY      = os.environ.get("DART_API_KEY", "")
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")

# --- 생성 목표 ---
TARGET_POSTS = 50
OVERGEN_RATE = 1.3          # 필터 리젝 대비 130% 생성

SLOT_QUOTA = {              # 슬롯별 목표 건수 (합 = TARGET_POSTS)
    "disclosure": 20,       # DART 공시
    "research":   12,       # 증권사 리포트
    "flow":       10,       # 특징주/수급
    "policy":      5,       # 정책/거시
    "poll":        3,       # 토론 발제
}

# --- 모델 ---
# 대량 저비용: haiku / 문체 품질 우선: sonnet
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
MODEL_FALLBACK = "claude-sonnet-5"
USE_BATCH = os.environ.get("USE_BATCH", "1") == "1"

# --- 크롤링 매너 ---
REQUEST_DELAY = 1.2         # 초
USER_AGENT = "kis-community-bot/1.0 (internal content pipeline)"

# --- 경로 ---
STATE_PATH = "data/state.json"
OUTPUT_PATH = "data/posts_latest.json"

FOOTER = "AI 생성 · 출처 {src}\n※ 투자 판단과 그 책임은 본인에게 있습니다."
