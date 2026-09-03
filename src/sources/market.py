"""전일 시장 데이터 → 특징주 카드.

pykrx 로 KRX 공식 데이터를 받는다. 여기서 나온 수치는 사실이므로
프롬프트 facts 에 그대로 넣어도 환각 위험이 없다.
"""
from datetime import datetime, timedelta

from config import KST


def _last_trading_day() -> str:
    d = datetime.now(KST) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def fetch(limit: int = 12) -> list[dict]:
    try:
        from pykrx import stock
    except ImportError:
        print("[market] pykrx 미설치 → 스킵")
        return []

    day = _last_trading_day()
    out = []
    try:
        df = stock.get_market_ohlcv(day, market="ALL")
        if df is None or df.empty:
            print(f"[market] {day} 데이터 없음(휴장)")
            return []

        # 거래대금 상위 중 등락률 절댓값이 큰 종목
        df = df[df["거래대금"] > 30_000_000_000]
        df = df.reindex(df["등락률"].abs().sort_values(ascending=False).index)

        for code, row in df.head(limit).iterrows():
            name = stock.get_market_ticker_name(code)
            direction = "상승" if row["등락률"] > 0 else "하락"
            out.append({
                "id": f"flow-{day}-{code}",
                "kind": "flow",
                "stock_code": code,
                "stock_name": name,
                "title": f"{name} 전일 {row['등락률']:.2f}% {direction}",
                "facts": (
                    f"기준일: {day}\n"
                    f"종목: {name} ({code})\n"
                    f"종가: {int(row['종가']):,}원\n"
                    f"등락률: {row['등락률']:.2f}%\n"
                    f"거래대금: {int(row['거래대금'])/1e8:,.0f}억원\n"
                    f"고가/저가: {int(row['고가']):,} / {int(row['저가']):,}\n"
                    f"※ 등락 사유는 데이터에 없음. 원인을 추측해 단정하지 말 것."
                ),
                "src": f"https://finance.naver.com/item/main.naver?code={code}",
            })
    except Exception as e:
        print(f"[market] 실패: {e}")

    print(f"[market] {len(out)}건 수집")
    return out
