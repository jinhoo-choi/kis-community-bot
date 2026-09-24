"""국내 증시 영업일 판단.

담당자가 출근하지 않는 날에는 글이 나가면 안 된다(운영 요청).
08:00 실행 시점에는 당일 장이 열릴지 시장 데이터로 알 수 없다 — 개장 전이라
'시세 기준일이 전진했는가' 같은 관찰 방식으로는 휴장일과 영업일이 구분되지 않는다.
실측 2026-09-24(추석 연휴 목요일): 기준일은 09-23 으로 정상 전진했다.

그래서 휴장일은 목록으로 관리한다. KRX 가 연초에 공표하는 휴장일을
data/market_holidays.txt 에 한 줄에 하나씩(YYYY-MM-DD) 적는다.
'#' 로 시작하는 줄과 빈 줄은 무시한다.
주말은 cron(월~금)이 이미 제외하지만, 수동 실행 대비로 여기서도 막는다.
"""
import os
from datetime import datetime

from config import KST

PATH = "data/market_holidays.txt"


def today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def holidays() -> set:
    try:
        with open(PATH, encoding="utf-8") as f:
            return {l.strip() for l in f
                    if l.strip() and not l.lstrip().startswith("#")}
    except FileNotFoundError:
        return set()


def is_holiday(day: str = None) -> bool:
    d = day or today()
    if datetime.strptime(d, "%Y-%m-%d").weekday() >= 5:
        return True
    return d in holidays()
