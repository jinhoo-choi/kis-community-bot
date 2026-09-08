"""종목토론방 고반응 글 구조 분석 — 페르소나 파라미터 조정 근거 수집.

목적: "반응이 좋은 글"과 "반응이 없는 글"의 구조 차이를 실측해
      페르소나의 길이·문장수·어미·질문여부 설정을 추측이 아닌 근거로 바꾼다.

집단 (실측 분포로 정한 임계, data/board_dist.json 근거)
  고반응군 : 추천 20+ & 조회 300+   (표본의 3.2%)
  대조군   : 추천 0~2 & 조회 100+

수집 원칙
  - 본문 원문은 저장하지 않는다. 집계 지표만 남긴다.
  - LLM 을 쓰지 않는다. 길이·문장수·어미·질문여부는 정규식으로 충분하다.
  - 요청 간 간격을 둔다.

주의: 토론방은 UTF-8, 시세 페이지는 euc-kr 이다 (프로브로 확인).
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

# 30종목 x 45페이지로 잡았더니 40분 상한에 걸렸다. 요청당 대기(0.35초) 외에
# 네트워크 왕복이 더 붙어 실제로는 요청당 1초에 가깝다.
# 분포 조사 실측(5종목 x 20페이지 = 2,000건 -> 고반응 63건)을 근거로
# 30종목 x 20페이지면 고반응 약 380건이 나온다. 표본으로 충분하다.
N_STOCKS = 30
N_PAGES = 20          # 종목당 약 400건 = 14일치
SLEEP = 0.25
HOT_UP, HOT_VIEW = 20, 300
COLD_UP, COLD_VIEW = 2, 100
MAX_BODY_FETCH = 250  # 집단별 상세 조회 상한

_SENT = re.compile(r"[.!?…]+\s|[.!?…]+$")
_ENDING = re.compile(r"(습니다|합니다|입니다|네요|는데요|어요|아요|죠|군요|"
                     r"까요|세요|ㅋㅋ|ㅎㅎ|다|요)\s*[.!?…]?\s*$")


def top_stocks(n: int) -> list[tuple[str, str]]:
    out, seen = [], set()
    for page in (1, 2):
        r = requests.get(
            f"https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page={page}",
            headers=H, timeout=20)
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href*='/item/main.naver?code=']"):
            m = re.search(r"code=(\d{6})", a["href"])
            name = a.get_text(strip=True)
            if not m or not name or m.group(1) in seen:
                continue
            if re.search(r"(?<!대)우[A-C]?$", name) or "스팩" in name:
                continue
            seen.add(m.group(1))
            out.append((name, m.group(1)))
            if len(out) >= n:
                return out
        time.sleep(SLEEP)
    return out


def list_page(code: str, page: int) -> list[dict]:
    r = requests.get(f"https://finance.naver.com/item/board.naver?code={code}&page={page}",
                     headers=H, timeout=20)
    if r.status_code != 200:
        return []
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 6:
            continue
        c = [x.get_text(" ", strip=True) for x in tds]
        if not re.match(r"\d{4}\.\d{2}\.\d{2}", c[0]):
            continue
        try:
            views, up, down = int(c[3]), int(c[4]), int(c[5])
        except ValueError:
            continue
        a = tds[1].find("a", href=True)
        rows.append({"code": code, "date": c[0], "title": c[1], "href": a["href"] if a else "",
                     "views": views, "up": up, "down": down})
    return rows


def probe_body_api(code: str, nid: str) -> str:
    """본문 API 주소를 후보로 때려 찾는다.

    본문은 iframe(m.stock.naver.com) 안에서 클라이언트가 따로 부른다.
    __NEXT_DATA__ 에도 없다. 주소를 추측하지 않고 응답으로 확인한다.
    """
    cands = [
        f"https://m.stock.naver.com/api/discussion/domestic/stock/{code}/{nid}",
        f"https://m.stock.naver.com/api/discussion/domestic/{code}/{nid}",
        f"https://m.stock.naver.com/api/discussion/{nid}",
        f"https://m.stock.naver.com/api/stock/{code}/discussion/{nid}",
        f"https://m.stock.naver.com/api/json/discussion/{code}/{nid}",
    ]
    hdr = {**H, "Referer": f"https://m.stock.naver.com/pc/domestic/stock/{code}"
                           f"/discussion/{nid}", "Accept": "application/json"}
    for u in cands:
        try:
            r = requests.get(u, headers=hdr, timeout=15)
            ct = r.headers.get("content-type", "")
            print(f"[probe] {r.status_code} {ct[:24]:24} {u}")
            if r.status_code == 200 and "json" in ct:
                print(f"[probe] 본문 API 확인: {r.text[:200]}")
                return u
        except Exception as e:
            print(f"[probe] 실패 {type(e).__name__} {u}")
        time.sleep(0.3)
    return ""


def fetch_body(href: str) -> str:
    """본문 텍스트. 반환값은 지표 계산에만 쓰고 저장하지 않는다."""
    url = "https://finance.naver.com" + href
    r = requests.get(url, headers=H, timeout=20)
    if r.status_code != 200:
        return ""
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    for sel in ("#body", "div#body", "td.view_se", "div.view_se"):
        el = soup.select_one(sel)
        if el:
            return el.get_text("\n", strip=True)
    return ""


def metrics(title: str, body: str) -> dict:
    """구조 지표만 뽑는다. 원문은 반환하지 않는다."""
    sents = [s for s in _SENT.split(body) if s and s.strip()]
    first = sents[0].strip() if sents else ""
    m = _ENDING.search(body.strip())
    return {
        "title_len": len(title),
        "body_len": len(body),
        "sent_n": len(sents),
        "sent_avg": round(len(body) / len(sents), 1) if sents else 0,
        # 첫 문장이 무엇으로 시작하는가 — 우리 '수치 선두 금지' 규칙의 근거가 된다
        "lead": ("number" if re.match(r"^[\d(]", first)
                 else "question" if first.endswith("?")
                 else "text"),
        "has_number": bool(re.search(r"\d", body)),
        "num_count": len(re.findall(r"\d[\d,.]*", body)),
        "ends_question": body.strip().endswith("?"),
        "ending": m.group(1) if m else "기타",
        "has_url": bool(re.search(r"https?://", body)),
    }


def title_metrics(t: str) -> dict:
    return {
        "len": len(t),
        "lead": ("number" if re.match(r"^[\d(]", t)
                 else "question" if t.rstrip().endswith("?") else "text"),
        "is_question": t.rstrip().endswith("?"),
        "has_number": bool(re.search(r"\d", t)),
        "has_stockname": False,
    }


def summarize_titles(rows: list[dict], label: str) -> dict:
    if not rows:
        return {}
    m = [title_metrics(r["title"]) for r in rows]
    out = {
        "n": len(m),
        "title_len_mean": round(st.mean([x["len"] for x in m]), 1),
        "title_len_median": st.median([x["len"] for x in m]),
        "lead": dict(Counter(x["lead"] for x in m)),
        "question_pct": round(sum(x["is_question"] for x in m) / len(m) * 100, 1),
        "has_number_pct": round(sum(x["has_number"] for x in m) / len(m) * 100, 1),
        "up_mean": round(st.mean([r["up"] for r in rows]), 1),
        "views_mean": round(st.mean([r["views"] for r in rows]), 1),
    }
    print(f"\n=== {label} 제목 지표 (n={out['n']}) ===")
    for k, v in out.items():
        if k != "n":
            print(f"  {k:20} {v}")
    return out


def summarize(rows: list[dict], label: str) -> dict:
    if not rows:
        return {}
    f = lambda k: [r[k] for r in rows]                       # noqa: E731
    out = {
        "n": len(rows),
        "body_len_mean": round(st.mean(f("body_len")), 1),
        "body_len_median": st.median(f("body_len")),
        "sent_n_mean": round(st.mean(f("sent_n")), 2),
        "sent_avg_len": round(st.mean(f("sent_avg")), 1),
        "title_len_mean": round(st.mean(f("title_len")), 1),
        "lead": dict(Counter(f("lead"))),
        "ends_question_pct": round(sum(f("ends_question")) / len(rows) * 100, 1),
        "has_number_pct": round(sum(f("has_number")) / len(rows) * 100, 1),
        "num_count_mean": round(st.mean(f("num_count")), 2),
        "ending_top": dict(Counter(f("ending")).most_common(8)),
        "has_url_pct": round(sum(f("has_url")) / len(rows) * 100, 1),
    }
    print(f"\n=== {label} (n={out['n']}) ===")
    for k, v in out.items():
        if k != "n":
            print(f"  {k:20} {v}")
    return out


def _dump(obj: dict) -> None:
    """중간 결과를 그때그때 파일로 남긴다. 40분 상한에 두 번 잘렸다."""
    try:
        with open("data/board_style.json", "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"[board] 중간 저장 실패: {e}")


def main() -> None:
    stocks = top_stocks(N_STOCKS)
    print(f"[board] 대상 {len(stocks)}종목: {[n for n, _ in stocks]}")

    listed = []
    for i, (name, code) in enumerate(stocks, 1):
        got = []
        for pg in range(1, N_PAGES + 1):
            rows = list_page(code, pg)
            if not rows:
                break
            got += rows
            time.sleep(SLEEP)
        listed += got
        print(f"[board] {i:>2}/{len(stocks)} {name} {len(got)}건")

    hot = [r for r in listed if r["up"] >= HOT_UP and r["views"] >= HOT_VIEW]
    cold = [r for r in listed if r["up"] <= COLD_UP and r["views"] >= COLD_VIEW]

    # 본문 수집이 실패해도 제목 비교는 남는다 (실측: 본문이 SPA 라 전부 빈 값이었다)
    title_res = {"hot_title": summarize_titles(hot, "고반응군"),
                 "cold_title": summarize_titles(
                     sorted(cold, key=lambda r: -r["views"])[:len(hot)], "대조군"),
                 "stage": "title_done", "listed": len(listed)}
    _dump(title_res)

    if hot:
        nid = re.search(r"nid=(\d+)", hot[0]["href"])
        if nid:
            probe_body_api(hot[0]["code"], nid.group(1))
    print(f"\n[board] 목록 {len(listed)}건 → 고반응 {len(hot)} / 대조 {len(cold)}")

    # 대조군은 고반응군과 같은 규모로 맞춘다 (조회 높은 순)
    cold = sorted(cold, key=lambda r: -r["views"])[:min(len(hot), MAX_BODY_FETCH)]
    hot = hot[:MAX_BODY_FETCH]

    result = {}
    for label, group in (("hot", hot), ("cold", cold)):
        met = []
        for j, r in enumerate(group, 1):
            body = fetch_body(r["href"])
            if len(body) >= 10:
                met.append(metrics(r["title"], body))
            time.sleep(SLEEP)
            if j % 50 == 0:
                print(f"[board] {label} 본문 {j}/{len(group)}")
                # 타임아웃돼도 여기까지는 남는다
                _dump({"stage": f"{label}_partial", **{label: summarize(met, label)}})
        result[label] = summarize(met, "고반응군" if label == "hot" else "대조군")

    result.update(title_res)
    result["config"] = {"stocks": len(stocks), "pages": N_PAGES,
                        "hot": f"up>={HOT_UP} & views>={HOT_VIEW}",
                        "cold": f"up<={COLD_UP} & views>={COLD_VIEW}",
                        "listed": len(listed)}
    with open("data/board_style.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("\n[board] data/board_style.json 저장 (집계만, 본문 미저장)")


if __name__ == "__main__":
    main()
