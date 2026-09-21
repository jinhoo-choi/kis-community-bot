"""파이프라인 정합성 감사 — 코드가 만든 입력 vs 코드가 만든 검사.

audit_contracts.py 는 전역 규칙 × 페르소나 × Angle 을 본다. 그 범위 밖에서
반복된 실패 계열이 있다.

  코드가 facts 에 넣은 표현을, 코드가 만든 필터·게이트·근거 검사가 위반으로 판정한다

실측 사례 (2026-09-14~15, PR #18~#24)
  #18  facts 의 '시가 출발: 전일 종가 대비 X%' vs 필터의 상대날짜 리젝     60건
  #20  facts 의 '20일 이동평균 대비' vs 근거없는수치['20']                12건
  #20  ANCHOR_TYPES 가 강제한 주장 vs 필터의 선정외주장                    17건
  #24  앵커가 target 을 강제하며 broker 를 밀어냄 vs 출처없는목표주가       3건

전부 사후에 실발송 로그를 읽고서야 찾았다. 이 도구는 같은 계열을 실행 전에
기계적으로 찾는다. 불변식 5개를 검사한다.

  I1  facts 생성부가 만드는 줄은 CLAIM_SPEC 에 매핑되거나, 매핑되지 않으면
      프롬프트에서 제거돼야 한다
  I2  CLAIM_SPEC 의 value 를 그대로 인용한 본문은 어떤 필터에도 걸리지 않아야 한다
  I3  claims.select 는 프롬프트 경로와 필터 경로에서 같은 집합을 내야 한다
  I4  annotate_terms 가 붙이는 사전 문구는 게이트 글감 판정과 claims.build 의
      값 추출 대상에서 빠져야 한다
  I5  ANCHOR_TYPES 로 강제한 주장이, 같은 유형의 필터가 전제하는 주장을
      밀어내지 않아야 한다

FAIL 이면 exit 1. run_tests.sh 에서 호출한다.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                            # noqa: E402
from src import claims, enrich, facts, filters, gate, angles   # noqa: E402

FAIL, WARN = [], []


def fail(inv, msg, detail=""):
    FAIL.append((inv, msg, detail))


def warn(inv, msg, detail=""):
    WARN.append((inv, msg, detail))


# ── 감사 대상 facts ────────────────────────────────────────────
# 실제 캐시가 있으면 그것을 쓴다. 합성 표본은 소스 코드의 facts 템플릿을
# 그대로 옮긴 것이라, 템플릿이 바뀌면 여기도 바꿔야 한다.

def _cached_flow() -> list[dict]:
    try:
        with open("data/market_cache.json", encoding="utf-8") as f:
            return json.load(f)["items"]
    except Exception:
        return []


def _cached_posts() -> list[dict]:
    """직전 실행의 발송분. 실제 facts 로 감사하려고 쓴다.

    보강 텍스트는 이제 저장·적용 시점에 마크다운을 걷어내지만, 이미 나간
    항목의 facts 에는 남아 있다. 지난 실행의 흔적으로 현재 코드를 실패로
    판정하면 감사가 어제를 계속 재판한다. 파이프라인이 지금 하는 것과 같은
    정리를 거쳐서 읽는다.
    """
    try:
        with open("data/posts_latest.json", encoding="utf-8") as f:
            posts = json.load(f)
    except Exception:
        return []
    for p in posts:
        if p.get("facts"):
            p["facts"] = enrich._strip_markup(p["facts"])
    return posts


SYNTHETIC = [
    # dart.py + dart_detail.py
    {"kind": "disclosure", "stock_code": "000660", "stock_name": "A",
     "title": "단일판매ㆍ공급계약체결",
     "facts": "공시일: 20260914\n회사: A (000660)\n공시명: 단일판매ㆍ공급계약체결\n"
              "제출인: A\n※ 공시 제목 외 상세 수치는 제공되지 않음. 수치를 추정하지 말 것.\n"
              "\n[단일판매·공급계약 상세 — 공시 원문]\n- 계약 금액: 220억원\n"
              "- 최근 매출액 대비: 13.77%\n- 계약 상대: B사\n- 계약 종료일: 2027-03-31\n"
              "- 공급 지역: 대한민국\n"
              "※ 위 수치는 공시 원문 값이다. 그대로 쓰되 계산하거나 합산하지 말 것.\n"},
    {"kind": "disclosure", "stock_code": "000661", "stock_name": "C",
     "title": "유상증자결정",
     "facts": "공시일: 20260914\n회사: C (000661)\n공시명: 유상증자결정\n"
              "[유상증자 상세 — 공시 원문]\n- 발행 총액: 200억원\n- 자금 용도: 시설자금\n"
              "- 증자 방식: 제3자배정증자\n- 발행 보통주: 2,000,000주\n"
              "- 전환가액: 12,000원\n- 표면이자율: 2.0%\n"},
    # research.py — 네이버 목록+상세
    {"kind": "research", "stock_code": "090430", "stock_name": "D",
     "title": "실적 개선 지속",
     "facts": "종목: D (090430)\n리포트 제목: 실적 개선 지속\n"
              "발간: 미래에셋증권 / 2026.09.11\n제시 적정가격: 190,000원\n투자의견: Buy\n"
              "리포트 요지: 2027년 매출액 약 7.1조원, 영업이익 2조 1,203억원을 "
              "전망한다. 짐펜트라 미국 매출이 본격화되며 수익성이 개선될 것으로 본다.\n"
              "※ 제시 수치는 증권사 의견이며 단정하지 말 것."},
    # research.py — 한경 컨센서스
    {"kind": "research", "stock_code": "016360", "stock_name": None,
     "title": "코스피 장세 수혜주",
     "facts": "리포트 제목: 코스피 장세 수혜주\n종목코드: 016360\n발간일: 2026.09.10\n"
              "작성: 유안타증권 홍길동\n제시 적정가격: 30,000원 (해당 증권사 의견)\n"
              "투자의견: Buy (해당 증권사 의견)\n"
              "※ 제목 외 본문 수치는 미제공. 위 수치는 증권사 제시치이며 단정하지 말 것."},
    # market.py + facts.py — 수급 줄. 시세 캐시가 #33 이전 코드로 만들어져
    # 감사가 수급 결합 사실을 한 번도 본 적이 없었다. 실측 분포(#133: 비중
    # 중앙 5.57%)에 맞춰 합성한다.
    {"kind": "flow", "stock_code": "000001", "stock_name": "S",
     "facts": "기준일: 2026-09-18\n종목: S (000001)\n종가: 52,000원\n등락률: 12.30%\n"
              "[결합 사실 — 개별 수치만으로는 안 보이는 것]\n"
              "· 외국인 순매수: 62억원 (거래대금 대비 7.3%)\n"
              "· 기관 순매도: 45억원 (거래대금 대비 5.3%)\n"
              "· 등락 크기: 최근 20거래일 평균 등락폭의 4.2배\n"
              "· 고점 대비: 최근 20거래일 최고 종가 대비 12.3% 아래\n"
              "· 갭 되돌림: 상승 출발 후 장중에 전일 종가까지 되돌림\n"},
    # policy.py — RSS
    {"kind": "policy", "stock_code": None, "stock_name": None,
     "title": "정책금융 지원 확대",
     "facts": "보도 시각: 2026-09-14\n출처: 연합뉴스\n제목: 정책금융 지원 확대\n"
              "요지: 정부가 중소기업 대상 정책금융 규모를 1천490억원으로 늘리고 "
              "보증 한도를 상향하는 방안을 발표했다. 적용 시점은 2027년 1월이다.\n"},
]


def targets() -> list[dict]:
    out = list(SYNTHETIC)
    out += _cached_flow()
    out += [p for p in _cached_posts() if p.get("facts")]
    return out


ALL_ANGLES = sorted(angles.ANGLE_PREF.keys()) if hasattr(angles, "ANGLE_PREF") \
    else sorted(claims.ANGLE_PREF.keys())
CAPS = (2, 3, 4, 5)


# ── I1: facts 의 줄이 CLAIM_SPEC 에 매핑되는가 ────────────────
# 매핑되지 않는 줄은 facts_view 가 지우지 못해 프롬프트에 남고, 모델이 인용하면
# 어느 claim 에도 속하지 않아 근거없는수치로 리젝된다.

_NON_CLAIM_PREFIX = re.compile(
    r"^\s*(?:※|\[|기준일|공시일|발행일|발간일|작성일|기사일|보도 시각|게시 시각|"
    r"종목|회사|제출인|출처|용어 설명|종목코드|시점)")


def audit_i1(items):
    pats = [(cid, pat) for cid, _l, pat, _f in claims.CLAIM_SPECS]
    seen = {}
    for it in items:
        for line in (it.get("facts") or "").splitlines():
            if not line.strip() or _NON_CLAIM_PREFIX.match(line):
                continue
            if any(re.search(p, line) for _c, p in pats):
                continue
            if not re.search(r"\d", line):
                continue          # 수치가 없으면 인용해도 근거 문제를 만들지 않는다
            # 미매핑 자체는 문제가 아니다. 날짜·종목코드처럼 허용 목록으로
            # 따로 통과시키는 값이 있다. 실제로 인용했을 때 근거없는수치가
            # 나는 줄만 잡는다.
            body = f"이 건은 {line.strip().lstrip('- ')} 입니다."
            _h, ung = claims.used(body, claims.build(it),
                                  claims._codes(it) | claims._metadata_numbers(it))
            if not ung:
                continue
            key = re.sub(r"[\d,.]+", "N", line.strip())[:60]
            seen.setdefault(key, (it.get("kind"), line.strip(), sorted(ung)))
    for key, (kind, line, ung) in seen.items():
        fail("I1", f"[{kind}] 프롬프트에 남지만 인용하면 근거없는수치 {ung}", line)


# ── I2: claim value 를 그대로 인용한 본문이 필터를 통과하는가 ──

def _quote_sentences(cs: list[dict]) -> str:
    """선정된 claim 을 라벨째 인용한 평범한 본문을 만든다."""
    body = "종목이 움직였습니다. "
    for c in cs:
        body += f"{c['label']}은 {c['value']}였는데요. "
    return body.strip()


def audit_i2(items):
    seen = set()
    for it in items:
        cs = claims.build(it)
        if not cs:
            continue
        _broker = next((x["value"] for x in cs if x["type"] == "broker"), "")
        for c in cs:
            # 목표가·투자의견은 출처 표기가 의무다(filters 출처없는목표주가).
            # 실제 프롬프트도 broker 를 함께 준다(PR #24). 감사 본문도 맞춘다.
            _attr = f"{_broker}가 낸 자료입니다. " if (
                _broker and c["type"] in ("target", "opinion")) else ""
            body = f"종목이 이렇게 움직였네요. {_attr}{c['label']}은 {c['value']}였습니다. " \
                   f"기준은 공시와 시세 자료예요."
            errs = filters.check(body, it.get("facts", ""), None, None, None, None,
                                 False, it.get("kind", ""), it.get("stock_code"))
            # 길이·선두·질문 규칙은 인용 자체와 무관하다
            errs = [e for e in errs
                    if not e.startswith(("너무짧음", "너무김", "수치선두", "선두파손",
                                         "미완성", "질문마무리", "어미단조", "어미반복",
                                         "결합사실미사용", "수치과다", "주장과다",
                                         "선정외주장", "미확인표현"))]
            if errs:
                key = (it.get("kind"), c["type"], tuple(sorted(
                    re.sub(r"\(.*", "", e) for e in errs)))
                if key in seen:
                    continue
                seen.add(key)
                # 문어체는 '원문을 그대로 베꼈을 때' 만 걸린다. 페르소나가 구어체
                # 재작성을 지시하므로 즉시 FAIL 이 아니라 경고로 둔다.
                (warn if errs == ["literary_style"] else fail)(
                    "I2", f"[{it.get('kind')}] claim '{c['type']}' 인용이 필터에 걸림",
                    f"{errs} / {c['label']}: {c['value'][:80]}")


# ── I3: 프롬프트 경로와 필터 경로의 선정이 같은가 ──────────────

def audit_i3(items):
    for it in items:
        thin = {"facts": it.get("facts", ""), "angle": "",
                "kind": it.get("kind", ""), "stock_code": it.get("stock_code")}
        for angle in ALL_ANGLES + [""]:
            for n in CAPS:
                a = [c["type"] for c in claims.select(it, n, angle)]
                b = [c["type"] for c in claims.select(dict(thin, angle=angle), n, angle)]
                if a != b:
                    fail("I3", f"[{it.get('kind')}] 선정 불일치 angle={angle} n={n}",
                         f"프롬프트={a} 필터={b}")
                    return


# ── I4: 용어 설명 줄이 게이트·값 추출을 오염시키는가 ───────────

def audit_i4():
    for word, desc in facts.GLOSSARY:
        for kind in facts.TERM_KINDS:
            # 사전 문구만 있고 실제 글감은 없는 항목
            it = {"kind": kind, "title": f"{word} 관련 공시",
                  "facts": f"공시일: 20260914\n회사: X (000000)\n"
                           f"공시명: {word}\n"
                           f"※ 상세 수치는 제공되지 않음. 수치를 추정하지 말 것.\n"}
            facts.annotate_terms([it])
            if "용어 설명:" not in it["facts"]:
                continue
            # 사전 문구 '때문에' 통과했는지를 본다. 원래도 통과하는 항목이면
            # 오염이 아니다.
            without = {"kind": kind, "facts": "\n".join(
                l for l in it["facts"].splitlines()
                if not l.startswith("용어 설명:"))}
            if gate.has_substance(it) and not gate.has_substance(without):
                fail("I4", f"[{kind}] 용어 설명 때문에 게이트 통과 ('{word}')",
                     it["facts"].splitlines()[-1][:70])
            # 사전 문구 줄만 떼어 돌린다. 공시명에서 나온 event 는 정상이므로
            # 전체 facts 로 비교하면 오탐이 난다.
            gloss_line = [l for l in it["facts"].splitlines()
                          if l.startswith("용어 설명:")]
            for c in claims.build({"kind": kind, "facts": "\n".join(gloss_line)}):
                if c["type"] == "term_def":
                    continue
                fail("I4", f"[{kind}] 사전 문구에서 claim '{c['type']}' 추출 ('{word}')",
                     c["value"][:60])


# ── I5: 앵커가 필터의 전제를 밀어내는가 ────────────────────────
# 필터가 '본문에 X 가 나오면 Y 도 있어야 한다' 고 요구하는데, 앵커 때문에
# Y 가 선정에서 빠지면 프롬프트를 따른 본문이 그대로 리젝된다.
#
# (트리거 claim type, 함께 선정돼야 하는 claim type, 필터 이름)
PREREQ = [("target", "broker", "출처없는목표주가"),
          ("opinion", "broker", "출처없는목표주가")]


def audit_i5(items):
    for it in items:
        kind = it.get("kind", "")
        if kind not in claims.ANCHOR_TYPES:
            continue
        have = {c["type"] for c in claims.build(it)}
        for trigger, needed, rule in PREREQ:
            if needed not in have:
                continue
            for angle in ALL_ANGLES + [""]:
                for n in CAPS:
                    sel = {c["type"] for c in claims.select(it, n, angle)}
                    if trigger in sel and needed not in sel:
                        fail("I5",
                             f"[{kind}] 앵커가 '{needed}' 를 밀어냄 → {rule} 리젝",
                             f"angle={angle} n={n} 선정={sorted(sel)}")
                        return


# ── I6: 주장이 다른 라벨의 줄 한가운데에서 잡히는가 ─────────
# 반복된 실패 계열이다(세 번):
#   #27  '리포트 요지:' 가 policy 의 '요지' 로 잡힘
#   #34  리서치 요지의 '고점 대비 약 50% 하락' 이 flow 의 drawdown 으로 잡힘
# 정상 주장은 자기 라벨로 시작하는 줄의 앞머리에서 잡힌다. 줄 중간에서
# 잡히면 남의 산문을 자기 주장으로 가져간 것이다.

def audit_i6(items):
    seen = set()
    for it in items:
        facts = claims._claimable(it.get("facts", ""))
        for cid, _label, pat, _fmt in claims.CLAIM_SPECS:
            for m in re.finditer(pat, facts):
                ls = facts.rfind("\n", 0, m.start()) + 1
                le = facts.find("\n", m.start())
                line = facts[ls: le if le != -1 else len(facts)]
                lead = len(line) - len(line.lstrip(" ·-\t"))
                if m.start() - ls <= lead + 1:
                    continue
                key = (it.get("kind"), cid, line.split(":")[0][:14])
                if key in seen:
                    continue
                seen.add(key)
                fail("I6", f"[{it.get('kind')}] '{cid}' 가 다른 줄 중간에서 잡힘",
                     line.strip()[:80])
                break


# ── I7: 소스가 내는 길이가 게이트 하한을 넘는가 ───────────────
# #127: policy GIST_MAX(110) < 게이트 하한(120) 이라 요지가 구조적으로 통과 불가.
# #132: 머리말 제거로 요지가 짧아져 하한(90) 아래로 떨어짐.
# 한쪽만 바꾸면 반대쪽이 조용히 깨진다.

def audit_i7():
    from src.sources import policy as _pol
    if gate._POLICY_MIN_DESC >= _pol.GIST_MAX:
        fail("I7", "정책 게이트 하한 ≥ 요지 상한 — 구조적으로 통과 불가",
             f"게이트 {gate._POLICY_MIN_DESC} / 요지 상한 {_pol.GIST_MAX}")


# ── I8: 수집은 되는데 생성 대상이 0 인 유형이 있는가 ──────────
# #132: 정책 수집 12건 → 생성 대상 0건. dry-run 리포트를 보기 전까지 몰랐다.
# 가장 최근 실행 기록에서 이런 유형을 찾는다.

def audit_i8():
    for path, pat in (("data/dryrun_report.txt",
                       r"생성 대상 \d+건 (\{[^}]*\})"),):
        try:
            txt = open(path, encoding="utf-8").read()
        except Exception:
            continue
        m = re.findall(pat, txt)
        if not m:
            continue
        import ast
        gen = ast.literal_eval(m[-1])
        col = dict(re.findall(r"\[crawl\] (\w+) (\d+)건", txt))
        src_of = {"policy": "policy_rss", "research": "naver_research",
                  "disclosure": "dart"}
        for kind, src in src_of.items():
            got = int(col.get(src, 0))
            if got >= 5 and gen.get(kind, 0) == 0:
                fail("I8", f"[{kind}] 수집 {got}건인데 생성 대상 0건",
                     f"{path} 최근 실행")


def main():
    items = targets()
    print(f"=== 파이프라인 정합성 감사 — 대상 {len(items)}건 "
          f"(합성 {len(SYNTHETIC)} + 캐시 {len(items) - len(SYNTHETIC)}) ===")
    audit_i1(items)
    audit_i2(items)
    audit_i3(items)
    audit_i4()
    audit_i5(items)
    audit_i6(items)
    audit_i7()
    audit_i8()

    for tag, lst in (("FAIL", FAIL), ("WARN", WARN)):
        for inv, msg, detail in lst:
            print(f"  {tag} [{inv}] {msg}")
            if detail:
                print(f"       └ {detail}")
    print(f"FAIL {len(FAIL)} / WARN {len(WARN)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
