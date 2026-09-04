"""Voice 4종을 같은 소재로 하나씩 생성해 비교한다.

축이 실제로 문체를 바꾸는지는 조합을 통제하지 않으면 알 수 없다.
Angle/Format/Length 를 고정하고 Voice 만 바꿔 나란히 뽑는다.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import filters
from src.llm import router
from src.personas import VOICES, build_messages

ITEM = {
    "kind": "flow", "stock_code": "108490", "stock_name": "로보티즈",
    "title": "로보티즈 전일 20.32% 상승",
    "facts": ("시장: KOSDAQ\n종목: 로보티즈 (108490)\n종가: 299,000원\n"
              "등락률: 20.32%\n거래대금: 2,990억원\n"
              "20일 평균 거래량 대비: 5.8배\n최근 5거래일 누적: +34.10%\n"
              "장중 고저: 305,000원 / 251,000원\n시가 대비 종가: +12.40%\n"
              "※ 등락 사유는 데이터에 없음. 원인을 추측해 단정하지 말 것."),
    "src": "https://finance.naver.com/item/main.naver?code=108490",
}

FIXED = dict(angle="reaction", fmt="fact_read", length="medium")
OUT = []


def log(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    OUT.append(line)


def main():
    p = router.writers().get("claude") or list(router.writers().values())[0]
    log("=" * 66)
    log(f"Voice 비교  (Angle={FIXED['angle']} / Format={FIXED['fmt']} / Length={FIXED['length']} 고정)")
    log(f"소재: {ITEM['title']}")
    log("=" * 66)

    for vid, v in VOICES.items():
        system, user = build_messages(ITEM, vid, FIXED["fmt"], FIXED["angle"], FIXED["length"])
        r = p.generate(system, user, temperature=1.0)
        body = (r.text or "").strip()
        errs = filters.check(body, ITEM["facts"], FIXED["fmt"], FIXED["angle"], FIXED["length"])
        log(f"\n[{vid}] {v['name']}  ({len(body)}자)  필터: {errs or 'OK'}")
        log(body or f"(생성 실패: {r.error})")

    os.makedirs("data", exist_ok=True)
    with open("data/voice_sample.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))


if __name__ == "__main__":
    main()
