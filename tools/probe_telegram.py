"""텔레그램 채널 프로브.

t.me/s/{handle} 웹 프리뷰가 실제로 읽히는지, 그 채널이 정말 그 회사 것인지 확인한다.
핸들은 절대 추측해서 쓰지 않는다. 프로브가 확인해 준 것만 화이트리스트에 넣는다.

주의(중요):
  - 공개 채널의 웹 프리뷰만 본다. 개인 계정 로그인(MTProto)은 쓰지 않는다.
  - 비공식 재배포방·리딩방은 대상이 아니다. 회사가 직접 운영하는 채널만.
  - 실제 수집 여부는 준법감시 검토 후에 결정한다. 이 스크립트는 '가능한지' 확인만 한다.
"""
import json
import re
import sys

import requests
from bs4 import BeautifulSoup

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
     "Accept-Language": "ko-KR,ko;q=0.9"}

# (회사, 핸들, 출처)
#   confirmed : 언론 보도 등으로 확인된 핸들
#   candidate : 미확인. 프로브 결과로 판정한다. 결과가 회사와 무관하면 버린다.
CANDIDATES = [
    ("타임폴리오자산운용", "activeetf", "confirmed(시사저널e 2025-02)"),
    ("미래에셋 TIGER ETF", "tigeretf", "candidate"),
    ("삼성 KODEX ETF", "kodexetf", "candidate"),
    ("한국투자신탁운용 ACE", "aceetf", "candidate"),
    ("KB자산운용 RISE", "riseetf", "candidate"),
    ("신한자산운용 SOL", "soletf", "candidate"),
    ("한화자산운용 PLUS", "plusetf", "candidate"),
]

OUT = []


def log(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    OUT.append(line)


def probe(handle: str) -> dict:
    url = f"https://t.me/s/{handle}"
    r = requests.get(url, headers=H, timeout=15, allow_redirects=True)
    info = {"handle": handle, "status": r.status_code, "url": r.url}

    if r.status_code != 200:
        return info

    soup = BeautifulSoup(r.text, "html.parser")

    # 프리뷰가 꺼져 있으면 t.me/{handle} 안내 페이지로 넘어간다
    if "/s/" not in r.url:
        info["preview"] = False
        return info

    title = soup.select_one("div.tgme_channel_info_header_title")
    desc = soup.select_one("div.tgme_channel_info_description")
    counters = {c.select_one("span.counter_type").get_text(strip=True):
                c.select_one("span.counter_value").get_text(strip=True)
                for c in soup.select("div.tgme_channel_info_counter")
                if c.select_one("span.counter_type")}

    msgs = soup.select("div.tgme_widget_message")
    times = [t.get("datetime", "")[:16] for t in soup.select("time.time")]

    info.update({
        "preview": True,
        "title": title.get_text(strip=True) if title else "",
        "desc": (desc.get_text(" ", strip=True)[:80] if desc else ""),
        "counters": counters,
        "posts_on_page": len(msgs),
        "latest": max(times) if times else "",
    })

    # 본문 샘플 (수집 가능성 판단용, 저장하지 않는다)
    if msgs:
        body = msgs[-1].select_one("div.tgme_widget_message_text")
        info["sample"] = re.sub(r"\s+", " ", body.get_text(" ", strip=True))[:100] if body else ""
    return info


def main():
    log("=" * 66)
    log("텔레그램 공개 채널 프리뷰 접근성 프로브")
    log("=" * 66)

    results = []
    for company, handle, note in CANDIDATES:
        try:
            info = probe(handle)
        except Exception as e:
            info = {"handle": handle, "status": f"ERR {type(e).__name__}"}
        info["company"] = company
        info["note"] = note
        results.append(info)

        ok = info.get("preview")
        log(f"\n[{company}] @{handle}  ({note})")
        log(f"  HTTP {info.get('status')}  preview={ok}")
        if ok:
            log(f"  제목   : {info.get('title')}")
            log(f"  소개   : {info.get('desc')}")
            log(f"  구독/글: {info.get('counters')}")
            log(f"  최근글 : {info.get('latest')}  (페이지 내 {info.get('posts_on_page')}건)")
            log(f"  샘플   : {info.get('sample')}")
            log("  ※ 제목·소개가 해당 회사와 일치하는지 사람이 반드시 확인할 것")

    log("\n" + "=" * 66)
    live = [r for r in results if r.get("preview")]
    log(f"프리뷰 접근 가능: {len(live)}/{len(results)}")
    log("접근 가능해도 자동 수집 여부는 별개다. 준법감시 검토 후 결정한다.")

    with open("data/telegram_probe.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    with open("data/telegram_probe.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
