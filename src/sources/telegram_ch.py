"""운용사 공식 텔레그램 채널 수집 (t.me/s/ 공개 프리뷰).

전제
  - data/telegram_channels.json 의 verified=true 채널만 수집한다.
    2026-09-04 프로브에서 회사명을 딴 핸들 하나가 채널 판매 스팸이었고
    다른 하나는 비공식 정보공유방이었다. 화이트리스트 없이는 쓸 수 없다.
  - 공개 웹 프리뷰만 읽는다. 개인 계정 로그인(MTProto)은 쓰지 않는다.
  - verified 를 올리기 전 준법감시 검토가 선행되어야 한다.

이 소스가 다른 소스보다 위험한 이유
  DART·거래소·뉴스는 공개 확정 정보다. 운용사 채널은 그렇지 않다.
  그래서 수집 단계에서 아래를 전부 버린다.
    - 타 운용사 자사 ETF 상품 홍보  → 한투 커뮤니티에 올릴 수 없다
    - 외부 언론 기사 링크 재배포     → 저작권
    - 개별 종목 매수·매도 시사       → 투자권유 오인
    - 이미지·파일만 있는 글          → 본문이 없다
  남는 건 시장 코멘트 정도이고, 수율이 낮은 것이 정상이다.
"""
import json
import os
import re
from datetime import datetime, timedelta

from config import KST
from src import crawl

CONFIG = "data/telegram_channels.json"
BASE = "https://t.me/s/{handle}"

# 타 운용사 ETF 상품 홍보 — 자사 커뮤니티에 옮길 수 없다
PRODUCT_RE = re.compile(
    r"\bTIME\b|TIMEFOLIO|TIGER|KODEX|ACE\s|RISE\s|SOL\s|PLUS\s|KOSEF|ARIRANG|HANARO|"
    r"순자산|설정액|보수|분배금|상장\s*예정|신규\s*상장|편입\s*비중|리밸런싱"
)
# 투자권유로 읽힐 수 있는 표현
SOLICIT_RE = re.compile(r"매수|매도|담기|비중\s*확대|추천|주목|유망|기회|수혜주|관련주")
# 외부 기사 재배포
NEWS_LINK_RE = re.compile(r"https?://(n\.news\.naver|news\.|www\.[a-z]+\.co\.kr)")


def _load() -> list[dict]:
    if not os.path.exists(CONFIG):
        return []
    try:
        with open(CONFIG, encoding="utf-8") as f:
            d = json.load(f)
        return [c for c in d.get("channels", []) if c.get("verified")]
    except Exception as e:
        print(f"[tg-src] 설정 로드 실패: {e}")
        return []


def fetch(limit: int = 5) -> list[dict]:
    chans = _load()
    if not chans:
        print("[tg-src] verified 채널 없음 → 스킵 (준법감시 검토 전)")
        crawl.report("telegram_ch", 0, 0, "verified 채널 미등록")
        return []

    cutoff = datetime.now(KST) - timedelta(hours=30)
    out, dropped = [], 0

    for ch in chans:
        if len(out) >= limit:
            break
        soup = crawl.get_soup(BASE.format(handle=ch["handle"]))
        if soup is None:
            continue

        for msg in soup.select("div.tgme_widget_message")[::-1]:
            if len(out) >= limit:
                break
            body_el = msg.select_one("div.tgme_widget_message_text")
            if not body_el:
                dropped += 1
                continue
            text = re.sub(r"\s+", " ", body_el.get_text(" ", strip=True)).strip()

            t = msg.select_one("time")
            when = (t.get("datetime") or "") if t else ""
            try:
                if datetime.fromisoformat(when).astimezone(KST) < cutoff:
                    continue
            except Exception:
                continue

            if (len(text) < 80 or PRODUCT_RE.search(text)
                    or SOLICIT_RE.search(text) or NEWS_LINK_RE.search(text)):
                dropped += 1
                continue

            link = msg.get("data-post", "")
            out.append({
                "id": "tg-" + re.sub(r"\W", "", link)[-24:],
                "kind": "policy",          # 시장 코멘트 → 테마글로 라우팅
                "stock_code": None,
                "stock_name": None,
                "title": text[:60],
                "facts": (
                    f"출처: {ch['company']} 공식 텔레그램 채널\n"
                    f"게시 시각: {when[:16]}\n"
                    f"내용: {text[:600]}\n"
                    "※ 운용사 코멘트다. 특정 상품이나 종목을 언급하지 말 것.\n"
                    "※ 원문을 그대로 옮기지 말고 사실만 짧게 정리할 것."
                ),
                "src": f"https://t.me/{link}" if link else BASE.format(handle=ch["handle"]),
            })
        crawl.sleep_jitter()

    print(f"[tg-src] {len(out)}건 수집 / 필터 제외 {dropped}건")
    crawl.report("telegram_ch", len(out), limit, "채널 구조 변경 또는 필터 과다")
    return out
