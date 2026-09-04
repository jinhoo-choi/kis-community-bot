"""담당자 배정.

배포 카드에 담당자를 찍지 않으면 누가 어느 글을 올릴지 몰라
같은 글이 여러 명에 의해 중복 게시된다. 중복 게시는 커뮤니티에서 가장 티가 나는
실패라 배정은 선택 사항이 아니다.

담당자 명단은 data/assignees.json 에서 읽는다. 코드 수정 없이 명단만 바꾸면 된다.

    {
      "members": ["김OO", "이OO", "박OO"],
      "notes": "빈 배열이면 '담당 미지정'으로 표기된다"
    }

배정 규칙
  - 정렬된 게시글에 라운드로빈. 인원수로 균등 분배된다.
  - 같은 종목의 글은 같은 담당자에게 몰아준다.
    한 사람이 한 종목방에 연속으로 올리는 편이, 여러 명이 같은 방에 흩어져
    올리는 것보다 자연스럽고 중복 위험도 낮다.
  - 명단이 비어 있으면 배정하지 않고 '미지정'으로 표기한다.
"""
import json
import os
from collections import Counter

PATH = "data/assignees.json"


def load() -> list[str]:
    if not os.path.exists(PATH):
        return []
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
        return [str(m).strip() for m in (d.get("members") or []) if str(m).strip()]
    except Exception as e:
        print(f"[assign] 명단 로드 실패: {e}")
        return []


def assign(posts: list[dict]) -> list[dict]:
    members = load()
    if not members:
        for p in posts:
            p["assignee"] = ""
        print("[assign] 담당자 명단 없음 → 전건 '미지정'")
        return posts

    by_stock: dict[str, str] = {}
    load_count = Counter()
    idx = 0

    # 종목 있는 글을 먼저 배정해야 종목 단위 묶음이 깨지지 않는다
    ordered = sorted(posts, key=lambda p: (not p.get("stock_code"), p.get("id", "")))

    for p in ordered:
        code = p.get("stock_code")
        if code and code in by_stock:
            who = by_stock[code]
        else:
            # 현재 가장 적게 맡은 사람 우선, 동률이면 라운드로빈
            who = min(members, key=lambda m: (load_count[m], members.index(m)))
            idx += 1
            if code:
                by_stock[code] = who
        p["assignee"] = who
        load_count[who] += 1

    print(f"[assign] {dict(load_count)}")
    return posts


def summary(posts: list[dict]) -> str:
    c = Counter(p.get("assignee") or "미지정" for p in posts)
    return " / ".join(f"{k} {v}건" for k, v in c.most_common())
