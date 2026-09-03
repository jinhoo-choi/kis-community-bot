"""3단계 — 교차 심사.

filters.py 의 정규식은 명백한 위반만 잡는다.
"AI 티가 난다", "내용이 공허하다" 같은 건 규칙으로 못 잡으므로 모델이 채점한다.

핵심: 작성자와 다른 프로바이더가 심사한다.
      같은 모델이 자기 글을 채점하면 점수가 후하게 나온다(self-preference bias).
"""
import concurrent.futures as cf
import json
import re

from src import rules
from src.llm.router import judges, cross_judge_for

SYSTEM = """당신은 증권사 커뮤니티 게시글의 품질 심사자입니다.
게시글에는 'AI 작성' 뱃지가 붙으므로 사람인 척할 필요는 없습니다.
다만 읽을 가치가 있어야 합니다.

[평가 항목] 각 1~5점
1. factual   : 제시된 사실관계에만 근거했는가. 없는 수치·전망을 지어내지 않았는가
2. useful    : 읽는 사람에게 새로운 정보나 생각할 거리를 주는가. 제목만 바꿔 쓴 수준은 아닌가
3. natural   : 문장이 자연스럽고 지정된 톤에 맞는가. 상투적인 AI 문투가 아닌가
4. compliant : 매매 권유, 목표주가 단정, 1인칭 투자 경험 서술이 없는가

[치명적 위반] 하나라도 해당하면 fatal 에 담습니다
__FATAL_BLOCK__

[출력] JSON 만. 설명 금지.
{"factual":n,"useful":n,"natural":n,"compliant":n,"fatal":["..."],"reason":"20자 이내"}"""

USER = """[톤] {tone}
[제공된 사실관계]
{facts}

[심사 대상 게시글]
{body}"""


def _parse(txt: str) -> dict | None:
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group())
        for k in ("factual", "useful", "natural", "compliant"):
            d[k] = int(d.get(k, 0))
        d["fatal"] = d.get("fatal") or []
        d["total"] = d["factual"] + d["useful"] + d["natural"] + d["compliant"]
        return d
    except Exception:
        return None


def _one(post: dict) -> dict:
    jname = cross_judge_for(post.get("provider", ""))
    if not jname:
        post["score"] = None
        return post

    j = judges()[jname]
    r = j.generate(
        # ※ SYSTEM 에 JSON 리터럴이 있어 .format() 을 쓰면 KeyError 로 죽는다. replace 고정.
        SYSTEM.replace("__FATAL_BLOCK__", rules.judge_block()),
        USER.format(tone=post["tone"], facts=post["facts"][:2500], body=post["body"]),
        temperature=0.0,
        max_tokens=300,
    )
    d = _parse(r.text)
    post["score"] = d
    post["judged_by"] = jname
    return post


def judge_all(posts: list[dict], workers: int = 6) -> list[dict]:
    if len(judges()) < 2:
        print("[judge] 프로바이더가 1개뿐 → 교차 심사 스킵")
        return posts
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_one, posts))
