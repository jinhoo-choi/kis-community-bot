"""종목토론방 추천 수 분포 조사 — 수집 임계값을 정하기 위한 사전 측정.

"추천 5개 이상"이 유효한 표본을 만드는지는 분포를 봐야 안다.
표본이 0에 수렴하면 본수집 설계 자체가 무의미하다.

본문은 저장하지 않는다. 제목 길이·반응 수치 등 집계 지표만 남긴다.
토론방은 시세 페이지와 달리 UTF-8 이다 (프로브로 확인).
"""
import json
import re
import statistics as st
import sys
import time
from collections import Counter

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, ".")

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
N_STOCKS = 5
N_PAGES = 20


def top_stocks(n: int) -> list[tuple[str, str]]:
    r = requests.get(
        "https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page=1",
        headers=H, timeout=20)
    r.encoding = "euc-kr"                      # 시세 페이지는 euc-kr
    soup = BeautifulSoup(r.text, "html.parser")
    seen, out = set(), []
    for a in soup.select("a[href*='/item/main.naver?code=']"):
        m = re.search(r"code=(\d{6})", a["href"])
        name = a.get_text(strip=True)
        if not m or not name or m.group(1) in seen:
            continue
        if name.endswith("우") or "스팩" in name:   # 우선주·스팩 제외
            continue
        seen.add(m.group(1))
        out.append((name, m.group(1)))
        if len(out) >= n:
            break
    return out


def fetch_page(code: str, page: int) -> list[dict]:
    url = f"https://finance.naver.com/item/board.naver?code={code}&page={page}"
    r = requests.get(url, headers=H, timeout=20)
    if r.status_code != 200:
        return []
    r.encoding = "utf-8"                       # 토론방은 UTF-8 (프로브 확인)
    soup = BeautifulSoup(r.text, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 6:
            continue
        cells = [c.get_text(" ", strip=True) for c in tds]
        if not re.match(r"\d{4}\.\d{2}\.\d{2}", cells[0]):
            continue
        try:
            views, up, down = int(cells[3]), int(cells[4]), int(cells[5])
        except ValueError:
            continue
        rows.append({"date": cells[0], "title_len": len(cells[1]),
                     "title": cells[1], "views": views, "up": up, "down": down})
    return rows


def main() -> None:
    stocks = top_stocks(N_STOCKS)
    print(f"[board] 대상 {[n for n, _ in stocks]}")
    all_rows, per_stock = [], {}
    for name, code in stocks:
        rows = []
        for pg in range(1, N_PAGES + 1):
            got = fetch_page(code, pg)
            if not got:
                break
            rows += got
            time.sleep(0.4)
        per_stock[name] = len(rows)
        all_rows += rows
        print(f"[board] {name} {len(rows)}건")

    ups = sorted(r["up"] for r in all_rows)
    n = len(ups)
    print(f"\n[board] 총 {n}건 / 종목별 {per_stock}")
    if not n:
        return
    print(f"[board] 날짜 범위 {all_rows[-1]['date']} ~ {all_rows[0]['date']}")

    print("\n=== 추천 수 분포 ===")
    print(f"  0추천 {sum(1 for u in ups if u == 0)}건 "
          f"({sum(1 for u in ups if u == 0) / n:.1%})")
    for thr in (1, 2, 3, 5, 10, 20, 50):
        c = sum(1 for u in ups if u >= thr)
        print(f"  추천 {thr:>2}+ : {c:>5}건 ({c / n:6.2%})")
    print(f"  평균 {st.mean(ups):.2f} / 중앙 {st.median(ups)} / 최대 {ups[-1]}")
    for q in (0.90, 0.95, 0.99):
        print(f"  상위 {1 - q:.0%} 경계: 추천 {ups[int(n * q)]}")

    print("\n=== 조회 수 분포 ===")
    vs = sorted(r["views"] for r in all_rows)
    print(f"  평균 {st.mean(vs):.0f} / 중앙 {st.median(vs)} / 최대 {vs[-1]}")

    print("\n=== 반응 상위 글의 제목 길이 ===")
    top = [r for r in all_rows if r["up"] >= max(3, ups[int(n * 0.99)])]
    if top:
        tl = [r["title_len"] for r in top]
        print(f"  상위 {len(top)}건 제목 길이 평균 {st.mean(tl):.1f}자 "
              f"(전체 평균 {st.mean([r['title_len'] for r in all_rows]):.1f}자)")
        print("  표본 제목(구조 참고용, 저장 안 함):")
        for r in sorted(top, key=lambda x: -x["up"])[:8]:
            print(f"    추천{r['up']:>3} 조회{r['views']:>6}  {r['title'][:40]}")

    # 집계만 파일로 남긴다. 본문·제목은 저장하지 않는다.
    out = {
        "sampled": n,
        "per_stock": per_stock,
        "date_from": all_rows[-1]["date"], "date_to": all_rows[0]["date"],
        "up_hist": dict(Counter(min(u, 20) for u in ups)),
        "up_thresholds": {str(t): sum(1 for u in ups if u >= t)
                          for t in (1, 2, 3, 5, 10, 20, 50)},
        "up_mean": round(st.mean(ups), 2),
        "views_mean": round(st.mean(vs), 1),
    }
    with open("data/board_dist.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n[board] data/board_dist.json 저장 (집계만)")


if __name__ == "__main__":
    main()
