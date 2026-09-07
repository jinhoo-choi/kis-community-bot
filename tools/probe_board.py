"""네이버 종목토론방 구조 확인 — 반응 지표와 페이지네이션.

목적: 시총 상위 종목의 토론방에서 반응(공감·댓글)이 높은 글의 **구조 지표**를
      뽑아 페르소나 파라미터(길이·문장수·어미·첫문장 유형)를 실증으로 조정한다.
      본문은 저장하지 않는다. 집계만 남긴다.

URL·컬럼·파라미터명은 추측하지 않는다. 응답을 덤프해 근거를 만든다.
과거 KIND 에서 td[2]를 td[1]로 잡아 2743종목 중 3개만 파싱된 적이 있다.
"""
import re
import sys

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, ".")

OUT = []
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}


def log(s: str = "") -> None:
    print(s)
    OUT.append(s)


def probe_market_sum() -> list[tuple[str, str]]:
    """시총 상위 목록에서 종목코드를 얻는다."""
    log("\n── 1. 시총 상위 ──")
    url = "https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page=1"
    r = requests.get(url, headers=H, timeout=20)
    r.encoding = "euc-kr"
    log(f"  HTTP {r.status_code} / {len(r.text):,}bytes")
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for a in soup.select("a[href*='/item/main.naver?code=']"):
        m = re.search(r"code=(\d{6})", a["href"])
        if m and a.get_text(strip=True):
            out.append((a.get_text(strip=True), m.group(1)))
    # 중복 제거, 순서 유지
    seen, uniq = set(), []
    for n, c in out:
        if c not in seen:
            seen.add(c)
            uniq.append((n, c))
    log(f"  종목 {len(uniq)}개 추출 / 앞 5개: {uniq[:5]}")
    return uniq


def probe_board(code: str, name: str) -> None:
    """토론방 목록의 컬럼 구조와 반응 지표 위치를 확인한다."""
    log(f"\n── 2. 토론방 [{name} {code}] ──")
    url = f"https://finance.naver.com/item/board.naver?code={code}&page=1"
    r = requests.get(url, headers=H, timeout=20)
    log(f"  {url}")
    log(f"  HTTP {r.status_code} / {len(r.content):,}bytes")
    # DART 원문이 meta 는 euc-kr 인데 실제는 UTF-8 이었던 전례가 있다.
    # 선언을 믿지 말고 둘 다 디코딩해 눈으로 고른다.
    for enc in ("euc-kr", "utf-8"):
        try:
            head = r.content[:4000].decode(enc, errors="replace")
        except Exception as e:
            log(f"  [{enc}] 디코딩 실패 {e}")
            continue
        m = re.search(r"charset=([\w-]+)", head, re.I)
        log(f"  [{enc}] meta charset={m.group(1) if m else '?'} "
            f"표본={head[head.find('<title>'):head.find('<title>') + 60]!r}")
    r.encoding = "euc-kr"
    if r.status_code != 200:
        return
    soup = BeautifulSoup(r.text, "html.parser")

    frames = [f.get("src") for f in soup.find_all(["frame", "iframe"]) if f.get("src")]
    if frames:
        log(f"  프레임: {frames[:5]}")

    for ti, table in enumerate(soup.find_all("table")[:5]):
        th = [t.get_text(strip=True) for t in table.find_all("th")]
        th = [x for x in th if x]
        if not th:
            continue
        log(f"  표{ti} 헤더({len(th)}): {th[:12]}")
        shown = 0
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            cells = [c.get_text(" ", strip=True) for c in tds]
            log(f"    행({len(cells)}): {cells[:10]}")
            # 제목 셀 안의 댓글수 표기 방식 확인
            a = tr.find("a", href=True)
            if a:
                log(f"      제목링크: {a.get_text(' ', strip=True)[:60]}")
                log(f"      href: {a['href'][:110]}")
                # 댓글 수는 별도 컬럼이 없다. 제목 옆 표기인지 확인한다.
                log(f"      제목셀 HTML: {str(tds[1])[:260]}")
            shown += 1
            if shown >= 3:
                break

    # 페이지네이션 파라미터 확인 (30일치를 긁으려면 마지막 페이지를 알아야 한다)
    pg = [a["href"] for a in soup.select("a[href*='page=']")][:6]
    log(f"  페이지 링크 표본: {pg}")


def main() -> None:
    stocks = probe_market_sum()
    for name, code in stocks[:2]:
        probe_board(code, name)
    with open("data/board_probe.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("\n[probe] data/board_probe.txt 저장")


if __name__ == "__main__":
    main()
