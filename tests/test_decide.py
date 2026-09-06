"""배포 판정 테스트. main() 이 아니라 decide.py 의 실제 함수를 호출한다."""
import pathlib
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.decide import decide_distribution, temperature_for
from src.gate import is_hard_excluded
from src import rules, entity, dedup

def P(i, code="005930", total=18, fatal=None, kind="disclosure"):
    return {"id": f"p{i}", "stock_code": code, "kind": kind, "provider": "claude",
            "score": {"total": total, "fatal": fatal or []}}

def run(name, cond, detail=""):
    print(("  OK  " if cond else "  FAIL") + f"  {name}" + (f"  ({detail})" if detail else ""))
    return cond

def main():
    ok = []
    from src import filters as _f2

    # 1) 종목 상한: 같은 종목 5건 → 2건만
    sent, held = decide_distribution([P(i) for i in range(5)], target=50, per_stock=2)
    ok.append(run("종목상한 2건 적용", len(sent) == 2 and len(held) == 3))

    # 2) 치명 위반은 점수와 무관하게 보류
    sent, held = decide_distribution([P(1, total=20, fatal=["환각수치"])], per_stock=2)
    ok.append(run("fatal 즉시 보류", len(sent) == 0 and "fatal" in held[0]["hold_reason"]))

    # 3) 저점수 컷
    sent, _ = decide_distribution([P(1, total=9)], min_score=14)
    ok.append(run("저점수 컷", len(sent) == 0))

    # 4) 정렬 우선: 고점수가 상한을 먼저 차지
    posts = [P(1, total=12), P(2, total=20), P(3, total=15)]
    sent, _ = decide_distribution(posts, per_stock=1, min_score=10)
    ok.append(run("고점수 우선 선점", sent[0]["score"]["total"] == 20))

    # 5) 테마글은 종목 상한에서 제외
    posts = [P(i, code=None, kind="policy") for i in range(4)]
    sent, _ = decide_distribution(posts, per_stock=1, per_kind_cap={"policy": 4})
    ok.append(run("테마글 종목상한 면제", len(sent) == 4))

    # 6) 유형 상한
    posts = [P(i, code=f"00000{i}") for i in range(9)]
    sent, _ = decide_distribution(posts, per_kind_cap={"disclosure": 3})
    ok.append(run("유형상한 적용", len(sent) == 3))

    # 7) 게이트: 자사 계열 배제
    ok.append(run("이해상충 차단", is_hard_excluded(
        {"title": "실적 발표", "stock_code": "071050"})[0]))

    # 8) 게이트: 법적 사안 배제
    ok.append(run("횡령 공시 차단", is_hard_excluded(
        {"title": "횡령·배임 혐의 발생", "stock_code": "005930"})[0]))

    # 9) 게이트: 정상 공시는 통과
    ok.append(run("정상 공시 통과", not is_hard_excluded(
        {"title": "단일판매·공급계약 체결", "stock_code": "005930"})[0]))

    # 10) 게이트: 정정공시 노이즈 배제
    ok.append(run("정정신고 차단", is_hard_excluded(
        {"title": "[기재정정]주주총회소집결의", "stock_code": "005930"})[0]))

    # 11) temperature 차등
    ok.append(run("flow 저온 / policy 고온",
                  temperature_for({"kind": "flow"}) < temperature_for({"kind": "policy"})))

    # 12) 규칙 단일 소스: 작성/심사 프롬프트가 같은 개수에서 파생
    ok.append(run("규칙 단일소스 연결",
                  len(rules.writer_block().splitlines()) == len(rules.RULES)
                  and len(rules.judge_block()) > 0))


    # ── 귀속 검증 (인사이트봇 2026-08-02 사례 이식)
    ok.append(run("모호명 단독 거부", not entity.verify_attribution(
        "대상", "대상 기업 실적 점검 리포트")[0]))
    ok.append(run("모호명+주체신호 인정", entity.verify_attribution(
        "대상", "대상, 3분기 영업이익 증가")[0]))
    ok.append(run("일반 종목명 인정", entity.verify_attribution(
        "한미반도체", "한미반도체 공급계약 체결")[0]))
    ok.append(run("제목 미등장 거부", not entity.verify_attribution(
        "한미반도체", "반도체 업황 점검", "한미반도체 언급")[0]))

    # ── 부수 언급 (fail-open 확인)
    ok.append(run("부수언급 드롭", entity.is_incidental(
        "한미반도체", "SK하이닉스 실적 점검", "밸류체인 한미반도체, 협력사 수혜")))
    ok.append(run("주체는 fail-open 유지", not entity.is_incidental(
        "한미반도체", "한미반도체 공급계약", "한미반도체 계약 체결")))

    # ── 다축 dedup
    seen = {}
    a = {"id": "dart-1", "stock_code": "005930", "title": "유상증자 결정"}
    b = {"id": "naver-9", "stock_code": "005930", "title": "삼성전자 유상증자 영향 점검"}
    dedup.mark(a, seen, "2026-09-03")
    ok.append(run("다른 소스 같은 사건 중복 제거", dedup.is_dup(b, seen)[0]))
    c = {"id": "dart-2", "stock_code": "005930", "title": "자기주식 취득 결정"}
    ok.append(run("다른 사건은 통과", not dedup.is_dup(c, seen)[0]))
    ok.append(run("dedup 키 조회·저장 일치",
                  set(dedup.keys(a)) & set(seen.keys()) == set(dedup.keys(a))))


    # ── 프롬프트 빌드가 예외 없이 되는지 (JSON 리터럴 + format 충돌 회귀)
    from src.personas import build_messages
    from src.judge import SYSTEM as JSYS
    try:
        build_messages({"kind": "flow", "title": "t", "facts": "f",
                        "stock_name": "삼성전자", "thin_facts": True},
                       "dry", "lead_number", "reaction", "short")
        JSYS.replace("__FATAL_BLOCK__", rules.judge_block())
        built = True
    except Exception as e:
        built = False
        print("     ", e)
    ok.append(run("프롬프트 빌드 무예외", built))
    ok.append(run("judge 템플릿에 미치환 필드 없음",
                  "__FATAL_BLOCK__" not in JSYS.replace("__FATAL_BLOCK__", "x")))


    # ── 한경 제목 파싱 회귀 (2026-09-03 실데이터)
    from src.sources.research import _undouble, _strip_code
    def hk(t):
        return _strip_code(_undouble(t))
    ok.append(run("한경 완전2배중복 정규화",
        hk("롯데지주(004990) 노이즈보다 다가올 호황에 조명롯데지주(004990) 노이즈보다 다가올 호황에 조명")
        == ("004990", "롯데지주 노이즈보다 다가올 호황에 조명")))
    ok.append(run("한경 잘린중복 정규화",
        hk("코리아써키트(007810) 시간을 주시면, 더 강해져 돌아옵니다코리아써키트")
        == ("007810", "코리아써키트 시간을 주시면, 더 강해져 돌아옵니다")))
    ok.append(run("한경 코드없는 제목 통과",
        hk("반도체 업황 점검") == ("", "반도체 업황 점검")))


    # ── 한경 제목 파싱 (2026-09-03 실측 HTML 기반 회귀)
    from src.sources.research import _undouble, _strip_code
    hk = [
        ("롯데지주(004990) 노이즈보다 다가올 호황에 조명롯데지주(004990) 노이즈보다 다가올 호황에 조명",
         "004990", "롯데지주 노이즈보다 다가올 호황에 조명"),
        ("코리아써키트(007810) 시간을 주시면, 더 강해져 돌아옵니다코리아써키트",
         "007810", "코리아써키트 시간을 주시면, 더 강해져 돌아옵니다"),
        ("산일전기(062040) 과도한 저평가 영역", "062040", "산일전기 과도한 저평가 영역"),
        ("반도체 업황 점검", "", "반도체 업황 점검"),
    ]
    for raw, ecode, etitle in hk:
        c, t = _strip_code(_undouble(raw))
        ok.append(run(f"한경 파싱 {ecode or 'no-code'}", c == ecode and t == etitle, t))


    # ── 2026-09-03 실측 오탐 회귀 (연합뉴스 RSS 정치·인사·헤드라인 유입)
    from src.sources.policy import is_relevant
    bad = [
        "추미애 1차 추경서 예산 누락분 보강했어야…도의회도 책임",
        "총학생회장단 만난 박홍근, 청년 성장단계별 종합투자 추진",
        "김석봉 씨티 부사장, 모건스탠리 韓IB 공동대표로 선임",
        "[연합뉴스 이시각 헤드라인] 18:00",
        "머니톡스 외국인 소문의 진실, 다음 파티가 열리기 전 해야 할",
    ]
    for t in bad:
        r, why = is_relevant(t, "")
        ok.append(run(f"뉴스 오탐 차단: {t[:14]}", not r, why))

    good = [
        ("산업부·코트라, 미국 첨단기업 4곳 28조원 투자유치", "산업통상자원부는 투자유치를 발표했다"),
        ("정부, 반도체 소부장 세제지원 확대 시행", "기획재정부 세제 개편안"),
    ]
    for t, d in good:
        r, why = is_relevant(t, d)
        ok.append(run(f"정책기사 통과: {t[:14]}", r, why))


    # ── KIND 상장목록 파싱 (2026-09-03 실측 구조 회귀)
    #    실구조: 회사명 | 시장구분 | 종목코드 | 업종 | ...  (종목코드는 td[2])
    import re as _re
    from bs4 import BeautifulSoup as _BS
    from src import tickers as _T
    _sample = ("<table><tr><th>회사명</th><th>시장구분</th><th>종목코드</th></tr>"
               "<tr><td>스카이랩스</td><td>\n 코스닥 \n</td>"
               "<td style=\"mso-number-format:'@';\">386380</td></tr>"
               "<tr><td>KODEX 인버스</td><td>유가증권</td><td>114800</td></tr>"
               "<tr><td>삼성전자</td><td>유가증권</td><td>005930</td></tr></table>")
    _tbl = {}
    for _tr in _BS(_sample, "html.parser").find_all("tr"):
        _tds = _tr.find_all("td")
        if len(_tds) < 3:
            continue
        _n, _c = _tds[0].get_text(strip=True), _tds[2].get_text(strip=True)
        if _re.fullmatch(r"\d{6}", _c) and _n and not _T._EXCLUDE_NAME.search(_n):
            _tbl[_n] = _c
    ok.append(run("KIND 종목코드 컬럼(td[2])", _tbl.get("삼성전자") == "005930", str(_tbl)))
    ok.append(run("KIND 시장구분 오인 안함", "코스닥" not in _tbl.values()))
    ok.append(run("KIND ETF 제외", "KODEX 인버스" not in _tbl))


    # ── 복사 영역 분리 (고지 문구는 앱이 자동 표기하므로 <pre> 밖이어야 함)
    import src.telegram_bot as _tg
    _card = _tg.card({
        "stock_name": "산일전기", "stock_code": "062040", "kind": "research",
        "tone": "pro", "board": "stock", "provider": "claude",
        "score": {"total": 18}, "src": "https://example.com/r",
        "body": "본문 첫 줄.\n본문 둘째 줄.\n다들 어떻게 보시나요.",
    })
    _pre = _card[_card.index("<pre>"):_card.index("</pre>")]
    _pre = _pre[_pre.index(">", _pre.index("<code")) + 1:].replace("</code>", "")
    ok.append(run("복사영역에 본문만", _pre.strip().endswith("보시나요.")))
    ok.append(run("복사영역에 AI생성 표기 없음", "AI 생성" not in _pre))
    ok.append(run("복사영역에 투자책임 문구 없음", "투자 판단" not in _pre))
    ok.append(run("복사영역에 출처 URL 없음", "http" not in _pre))
    ok.append(run("카드에 고지문구 없음", "AI 생성" not in _card and "투자 판단" not in _card))
    ok.append(run("카드에 원문링크 없음", "<a href" not in _card))
    ok.append(run("복사영역 끝이 본문", _pre.strip().endswith("보시나요.")))


    # ── 담당자 배정 (중복 게시 방지)
    import json as _j, os as _os
    from src import assign as _as
    _bak = None
    if _os.path.exists(_as.PATH):
        _bak = open(_as.PATH, encoding="utf-8").read()
    _os.makedirs("data", exist_ok=True)
    _j.dump({"members": ["A", "B", "C"]}, open(_as.PATH, "w", encoding="utf-8"))
    _ps = [{"id": f"a{i}", "stock_code": c} for i, c in
           enumerate(["005930", "005930", "000660", "042700", None, None])]
    _as.assign(_ps)
    _by = {p["id"]: p["assignee"] for p in _ps}
    ok.append(run("전건 배정됨", all(_by.values())))
    ok.append(run("같은 종목은 같은 담당자", _by["a0"] == _by["a1"], f"{_by['a0']}/{_by['a1']}"))
    from collections import Counter as _C
    _load = _C(_by.values())
    ok.append(run("균등 분배(편차 1 이하)", max(_load.values()) - min(_load.values()) <= 1, str(dict(_load))))
    _j.dump({"members": []}, open(_as.PATH, "w", encoding="utf-8"))
    _as.assign(_ps)
    ok.append(run("명단 없으면 미지정", all(p["assignee"] == "" for p in _ps)))
    if _bak is not None:
        open(_as.PATH, "w", encoding="utf-8").write(_bak)

    # ── 길이 기준 (50~300자)
    from src import filters as _f
    ok.append(run("50자 미만 리젝", any("너무짧음" in e for e in _f.check("짧은 글." * 3, ""))))
    ok.append(run("60자 통과", not any("너무짧" in e or "너무김" in e
                                       for e in _f.check("가" * 60, ""))))
    ok.append(run("300자 초과 리젝", any("너무김" in e for e in _f.check("가" * 350, ""))))


    # ── 카드 구조 (잘림·복사 회귀)
    _long = {"stock_name": "삼성전자", "stock_code": "005930", "kind": "disclosure",
             "tone": "pro", "board": "stock", "assignee": "김선임",
             "src": "https://dart.fss.or.kr/x", "body": "가" * 5000}
    _c = _tg.card(_long, 3, 5)
    ok.append(run("카드 태그 미절단", _c.count("<pre>") == 1 and _c.count("</pre>") == 1))
    ok.append(run("본문만 절단", len(_c) < 4096, f"{len(_c)}자"))
    ok.append(run("복사블록 language 지정", 'class="language-' in _c))
    _lines = _c.splitlines()
    ok.append(run("1행 카테고리", _lines[0].startswith("카테고리 : ")))
    ok.append(run("2행 담당", _lines[1] == "담당 : 김선임"))
    ok.append(run("3행부터 복사블록", _lines[2].startswith("<pre><code")))
    ok.append(run("종목건은 종목명+코드 표기", "삼성전자 (005930)" in _lines[0], _lines[0]))
    _t = _tg.card({"kind": "policy", "assignee": "이책임", "body": "가" * 60})
    ok.append(run("테마건은 카테고리만", _t.splitlines()[0] == "카테고리 : 정책", _t.splitlines()[0]))

    # 방향 오용 (2026-09-04 실측: +23.74% 상승 건에 '낙폭')
    _ff = "등락률: 23.74%\n종가: 307,500원"
    ok.append(run("방향오용 차단", any("방향오용" in e for e in
                  _f2.check("로보티즈가 올랐네요. 이 정도 낙폭이면 뭔가 있을 법한데 확인이 안 되네요. "
                            "아시는 분 계신가요. 저도 궁금하네요.", _ff))))
    ok.append(run("provider/심사점수 미노출", "심사" not in _c and "claude" not in _c))


    # ── 2026-09-04 실제 배포분 회귀
    #    심사 19/20 를 받고 배포됐지만 담당자·임원 관점에서 게시 불가였던 글들.
    #    같은 유형이 다시 통과하면 실패한다.
    _bad = [
        ("신문체", "에이프로젠이 자회사의 유상증자를 결정했다. 9월 3일 공시된 주요사항보고서에 "
                   "따르면 제3자배정 방식으로 진행된다. 상세 수치는 아직 공개되지 않았다."),
        ("외부안내", "리포트 전체 내용이 궁금하다면 신한투자증권에 직접 문의하는 것이 필요합니다. "
                     "이 종목에 대해 다른 증권사의 평가는 어떤 상태인가요. 확인이 필요해 보입니다."),
        ("타사폄하", "메리츠증권의 의견도 결국 하나의 해석일 뿐이다. 실적 반등이 지속될지는 "
                     "별개입니다. 호재도 수치 없이는 그림의 떡 아닐까요. 판단은 각자의 몫입니다."),
        ("당신지칭", "세경하이테크가 실적 반등 평가를 받고 있습니다. 당신의 판단은 이 분석과 "
                     "다른가요. 정보선행자인지 후발주자인지 생각해볼 필요가 있습니다."),
        ("교과서", "유상증자는 일반적으로 사업 확장이나 부채 감소, 운영자금 확보 등의 목적으로 "
                   "실행됩니다. 이번 건도 그중 하나로 보입니다. 어떻게 보시나요."),
        ("자기소개", "안녕하세요, AI 작성 도우미입니다. 대전시가 지식재산 진흥 최우수기관 표창을 "
                     "받았다고 하네요. 중소기업 지원 성과가 반영된 결과로 보입니다. 어떠신가요."),
    ]
    for _name, _body in _bad:
        _e = _f2.check(_body, "")
        ok.append(run(f"실배포 회귀 차단: {_name}", bool(_e), str(_e)[:50]))

    _good = ("로보티즈가 어제 21.73% 올랐네요. 종가는 302,500원이고 거래대금도 2,790억원이었습니다. "
             "이 정도 폭이면 뭔가 있었을 것 같은데 원인은 확인이 안 되네요. "
             "공시는 따로 없었던 것 같고요. 혹시 배경 아시는 분 계신가요.")
    _good2 = _good.replace("기록했습니다", "였습니다")
    ok.append(run("정상 글은 통과", not _f2.check(_good2, "302,500 21.73 2,790"),
                  str(_f2.check(_good2, "302,500 21.73 2,790"))))
    from src.generator import clean as _cl
    ok.append(run("상투어 치환('기록했습니다')",
                  "이었습니다" in _cl("거래대금은 942억원을 기록했습니다.")))
    ok.append(run("상투어 리젝(치환불가)", any("news_cliche" in e for e in
                  _f2.check("남은 과제입니다. " * 8, ""))))
    ok.append(run("주체없는 평가 차단", any("unsourced_eval" in e for e in
                  _f2.check("수익성이 개선되는 중이라는 평가네요. " * 3, ""))))

    # 글감 부족 게이트
    from src.gate import has_substance as _hs
    ok.append(run("제목만 있는 항목 차단",
                  not _hs({"facts": "리포트 제목: Never Stop Rising\n※ 본문 수치는 미제공."})))
    ok.append(run("수치 있는 항목 통과",
                  _hs({"facts": "종가: 302,500원\n등락률: 21.73%"})))


    # ── 문체 다양성 (실측: 5건이 전부 같은 구조로 수렴)
    from src.personas import VOICES as _V, FORMATS as _F, VOICE_W as _VW, FORMAT_W as _FW
    from src.generator import pick_style as _pick
    from src import angles as _ang
    ok.append(run("Voice 4종", len(_V) == 4, str(len(_V))))
    from src.personas import LENGTHS as _L
    ok.append(run("Format 6종(길이 분리)", len(_F) == 6, str(len(_F))))
    ok.append(run("Length 축 신설", len(_L) == 3, str(len(_L))))
    ok.append(run("short_note 제거", "short_note" not in _F))
    ok.append(run("Angle 11종", len(_ang.ANGLES) == 11, str(len(_ang.ANGLES))))
    ok.append(run("4축 조합 700가지 이상",
                  len(_V) * len(_ang.ANGLES) * len(_F) * len(_L) >= 700,
                  str(len(_V) * len(_ang.ANGLES) * len(_F) * len(_L))))
    ok.append(run("금지 조합 없음(가중치 방식)",
                  all(len(v) == 4 for v in _VW.values()) and
                  all(len(f) == 6 for f in _FW.values())))

    _it = {"kind": "disclosure", "stock_code": "005930",
           "facts": "발행 총액: 200억원\n전환가액: 2,396원\n만기: 2031-09-11\n"
                    "운영자금: 100억원\n매출 대비 18%"}
    # 억제는 금지가 아니라 확률 조정이므로 소수 시행으로는 판정할 수 없다.
    # 난수를 고정하고 충분히 뽑아 '전 후보 커버 + 한쪽 쏠림 없음'으로 본다.
    import random as _rnd
    _rnd.seed(20260904)
    _avail = set(_ang.available(_it))
    _draws = [_pick(_it, {}, set())[1] for _ in range(60)]
    _seen_a = set(_draws)
    ok.append(run("가능한 Angle 전부 등장", _avail <= _seen_a,
                  f"{sorted(_avail - _seen_a)} 미등장"))
    from collections import Counter as _C2
    _top = _C2(_draws).most_common(1)[0][1] / len(_draws)
    ok.append(run("한 Angle 쏠림 없음(50% 미만)", _top < 0.5, f"{_top:.0%}"))
    _used = set()
    _c6 = [_pick(_it, {}, _used) for _ in range(6)]
    ok.append(run("같은 실행 내 억제 동작", len({x[1] for x in _c6}) >= 3,
                  str([x[1] for x in _c6])))

    # Angle 은 사실관계가 허용하는 것만
    ok.append(run("데이터에 없는 Angle 미생성",
                  "reaction" not in _ang.available(_it), str(_ang.available(_it))))
    ok.append(run("uncertainty 단독은 앵글 없음",
                  _ang.available({"facts": "상세 수치는 공개되지 않음"}) == []))

    # 길이·문장수는 Format 에 귀속 (Global '최소 5문장' 과 충돌하던 문제)
    ok.append(run("Length spec 존재", "3문장" in _L["short"]["spec"]))
    from src.personas import SYSTEM_PROMPT as _SP
    ok.append(run("Global 최소문장수 제거", "최소 5문장" not in _SP))
    ok.append(run("'확인되지 않았다' 강제 제거", '"확인되지 않았다"고 적을 것' not in _SP))

    # ── DART 상세 보강
    from src.gate import has_substance as _hs2
    ok.append(run("DART 상세는 글감 인정",
                  _hs2({"facts": "제목: 유상증자결정\n\n[유상증자 결정 상세 — DART 정형 데이터]\n"
                                 "- 발행 보통주: 1,000,000주"})))
    from src.sources.dart_detail import _fmt as _dfmt
    ok.append(run("억원 단위 변환", _dfmt("12345678900", "원") == "123억원", _dfmt("12345678900", "원")))
    ok.append(run("빈값은 빈 문자열", _dfmt("-", "원") == ""))


    # ── 구조별 질문 마무리 금지 (실측: 프롬프트만으로는 4건 전부 물음표로 끝남)
    _qb = "디케이티가 어제 올랐네요. 거래대금도 늘었는데요. 사유는 확인이 안 됩니다. 배경이 뭐라고 보시나요?"
    ok.append(run("fact_read 질문마무리 리젝",
                  any("질문마무리금지" in e for e in _f2.check(_qb, "", "fact_read"))))
    ok.append(run("open_question 은 허용",
                  not any("질문마무리금지" in e for e in _f2.check(_qb, "", "open_question"))))

    # ── 리포트 글감 기준 (제목만 있으면 차단)
    ok.append(run("리포트 제목만 차단", not _hs2(
        {"kind": "research", "facts": "리포트 제목: 하이 앤 드라이\n발간: 대신증권"})))
    ok.append(run("리포트 배경보강만으로는 불충분", not _hs2(
        {"kind": "research", "facts": "리포트 제목: 하이 앤 드라이\n\n"
                                      "[검색으로 확인된 배경]\n- 팬오션은 벌크선사"})))
    ok.append(run("적정가격 있으면 통과", _hs2(
        {"kind": "research", "facts": "리포트 제목: 실적 반등\n제시 적정가격: 33,000원"})))


    # ── 축 편중 억제 (실측: 5건 중 short_note 3, context 3)
    from src.generator import PENALTY as _PEN
    ok.append(run("억제 계수 0.5 미만", _PEN < 0.5, str(_PEN)))
    _it2 = {"kind": "disclosure", "stock_code": "005930",
            "facts": "회사: 삼성전자 (005930)\n공시명: 전환사채 발행 결정\n"
                     "발행 총액: 200억원\n전환가액: 2,396원\n표면이자율: 0%\n"
                     "만기: 2031-09-11\n운영자금: 100억원\n시설자금: 100억원\n"
                     "매출 대비 18%\n증자 방식: 제3자배정\n상장 예정일: 2026-10-01\n"
                     "제출인: 삼성전자 대표이사\n자금 용도: 반도체 생산설비 증설\n"
                     "납입일: 2026-09-20\n전환청구 개시일: 2027-09-11\n"
                     "사채 종류: 무기명식 이권부 무보증 사모 전환사채\n"
                     "[검색으로 확인된 배경]\n- 반도체 제조업을 영위하는 기업\n"
                     "- 메모리와 파운드리 사업을 함께 운영"}
    import collections as _co
    _u = set(); _cf = _co.Counter()
    for _ in range(12):
        _cf[_pick(_it2, {}, _u)[2]] += 1
    ok.append(run("Format 4종 이상 등장", len(_cf) >= 4, str(dict(_cf))))
    # context 는 배경 블록 존재가 아니라 업종 서술이 있을 때만
    ok.append(run("배경블록만으로 context 미채택", "context" not in _ang.available(
        {"facts": "발행 총액: 200억원\n\n[검색으로 확인된 배경]\n- 코스닥 상장사"})))
    ok.append(run("업종 서술 있으면 context 채택", "context" in _ang.available(
        {"facts": "발행 총액: 200억원\n- 바이오시밀러 기업으로 의약품 제조업 영위"})))


    # ── 미확인 표현 fatal (uncertainty 앵글에서만 허용)
    _mb = "로보티즈가 어제 크게 올랐습니다. 거래대금도 늘었습니다. 다만 구체적인 상승 배경은 확인되지 않았습니다."
    ok.append(run("미확인표현 fatal", any("미확인표현" in e for e in
                  _f2.check(_mb, "등락률: 20.4%", "fact_read", "reaction"))))
    ok.append(run("uncertainty 앵글은 허용", not any("미확인표현" in e for e in
                  _f2.check(_mb, "등락률: 20.4%", "fact_read", "uncertainty"))))
    ok.append(run("공개되지않음도 탐지",
                  bool(_ang.MISSING_RE.search("상세 수치는 아직 공개되지 않았으니"))))

    # ── Angle eligibility (쿠콘 사례: 데이터 없는데 context 선택)
    _thin = {"facts": "등락률: 18.2%\n종가: 41,300원\n거래대금: 312억원"}
    ok.append(run("빈약한 특징주에 context 미채택",
                  "context" not in _ang.available(_thin), str(_ang.available(_thin))))
    _rich = dict(_thin); _rich["facts"] += "\n20일 평균 거래대금 대비: 4.2배\n최근 5거래일 누적: +31.20%"
    ok.append(run("지표 보강 시 compare 채택",
                  "compare" in _ang.available(_rich), str(_ang.available(_rich))))

    # ── Angle 이 생성 계약을 담고 있는가
    ok.append(run("Angle 계약에 첫문장 규칙", "첫 문장" in _ang.contract("reaction")))
    ok.append(run("lead_number 구조 존재", "lead_number" in _F))


    # ── Length 연동 길이 기준 (실측: 고정 50~300 과 어긋나 4건 과잉 리젝)
    _len_cases = [("short", 34, True), ("short", 90, False),
                  ("medium", 150, False), ("long", 260, False), ("long", 370, True)]
    for _ln, _n, _should in _len_cases:
        _e = [x for x in _f2.check("가" * _n, "", "fact_read", "reaction", _ln)
              if "너무" in x]
        ok.append(run(f"길이 {_ln}/{_n}자 {'리젝' if _should else '통과'}",
                      bool(_e) == _should, str(_e)))
    ok.append(run("Length spec 에 하한 명시", "이상" in _L["short"]["spec"]))
    ok.append(run("프롬프트 상한 < 필터 상한", _L["long"]["max"] > 270))


    # ── 텔레그램 운용사 채널 (화이트리스트 + 필터)
    from src.sources import telegram_ch as _tg2
    ok.append(run("verified 아니면 미수집", len(_tg2._load()) == 0))
    ok.append(run("타사 상품 홍보 차단", is_hard_excluded(
        {"title": "TIGER 미국나스닥 ETF 순자산총액 1조 돌파", "facts": ""})[0]))
    ok.append(run("상품명 필터", bool(_tg2.PRODUCT_RE.search("TIME 글로벌AI ETF 순자산"))))
    ok.append(run("기사 재배포 필터", bool(_tg2.NEWS_LINK_RE.search("https://n.news.naver.com/x"))))
    ok.append(run("시장 코멘트는 통과", not any(r.search(
        "미 증시는 다우 +1.18%로 마감했습니다. 연준 위원 발언에 금리가 안정되며 "
        "위험자산 선호가 회복된 모습입니다. 오늘 국내 증시도 이를 반영할 것으로 보입니다.")
        for r in (_tg2.PRODUCT_RE, _tg2.SOLICIT_RE, _tg2.NEWS_LINK_RE))))


    # ── 거래소 조회공시 (특징주 '왜 올랐는지' 공백을 메우는 유일한 확정 정보)
    from src.sources.kind_inquiry import _stance as _st, attach_to_flow as _att
    ok.append(run("미확정 답변 해석", "미확정" in _st("풍문 또는 보도에 대한 해명(미확정)")))
    ok.append(run("부인 답변 해석", "부인" in _st("풍문 또는 보도에 대한 해명(부인)")))
    _iq_facts = ("거래소 조회공시\n종목: SK하이닉스 (000660)\n"
                 "공시 제목: 풍문 또는 보도에 대한 해명(미확정)\n"
                 "답변 성격: 회사는 '미확정'이라고 답변")
    ok.append(run("조회공시는 글감 인정", _hs2({"kind": "disclosure", "facts": _iq_facts})))
    ok.append(run("inquiry 앵글 채택", "inquiry" in _ang.available({"facts": _iq_facts})))
    _fl = [{"stock_code": "000660", "facts": "등락률: 8.2%"}]
    _n = _att(_fl, [{"stock_code": "000660", "title": "풍문 또는 보도에 대한 해명(미확정)"}])
    ok.append(run("특징주에 조회공시 연결", _n == 1 and "조회공시" in _fl[0]["facts"]))
    ok.append(run("무관 종목엔 미연결",
                  _att([{"stock_code": "005930", "facts": "x"}], []) == 0))


    # ── AI 티 제거 규칙
    _f_ai = "등락률: 20.32%\n종가: 299,000원\n거래대금: 2,990억원"
    ok.append(run("상투 마무리 차단", any("stock_ending" in e for e in _f2.check(
        "로보티즈가 올랐습니다. 거래대금도 늘었습니다. 추가 공시를 지켜봐야 할 것 같습니다.",
        _f_ai, "fact_read", "reaction", "short"))))
    ok.append(run("완충표현 남발 차단", any("완충표현" in e for e in _f2.check(
        "오른 것 같습니다. 거래도 는 것으로 보입니다. 배경이 있는 듯합니다. 흐름이 이어질 것 같습니다.",
        _f_ai, "fact_read", "reaction", "short"))))
    ok.append(run("어미 반복 차단", any("어미반복" in e for e in _f2.check(
        "올랐네요. 늘었네요. 컸네요. 많았네요. 재밌네요.",
        _f_ai, "fact_read", "reaction", "short"))))
    # grounding 도입으로 '숫자 개수' 대신 '인용한 주장 수' 로 판정한다
    _f_many = ("등락률: 20.32%\n종가: 299,000원\n거래대금: 2,990억원\n"
               "거래량: 20일 평균의 5.8배\n5거래일 누적 등락률: +34.10%\n"
               "장중 고저 차이: 저가 대비 21.5%")
    ok.append(run("주장 과다 차단", any("주장과다" in e or "수치과다" in e for e in _f2.check(
        "20.32% 상승에 299,000원 마감. 거래대금 2,990억원, 20일 평균 5.8배, "
        "5거래일 34.10%, 장중 고저 21.5%였습니다.",
        _f_many, "fact_read", "reaction", "quick_memo"))))
    ok.append(run("근거없는 수치 차단", any("근거없는수치" in e for e in _f2.check(
        "거래대금은 8,742억원이었습니다.", _f_many, "fact_read", "reaction", "quick_memo"))))
    ok.append(run("한 주장의 복수 숫자는 1개로", not any("주장과다" in e for e in _f2.check(
        "1 대 1.8702948. 우성이 우성유통을 흡수합병하기로 결정했습니다.",
        "합병 비율: 1 대 1.8702948", "fact_read", "ratio", "quick_memo"))))
    ok.append(run("자연스러운 글 통과", not _f2.check(
        "20.32% 상승. 로보티즈 종가는 299,000원입니다. 거래대금은 2,990억원이었습니다.",
        _f_ai, "fact_read", "reaction", "short")))

    # ── Voice 가 계약을 담고 있는가 (라벨이면 문체가 안 바뀐다)
    ok.append(run("Voice 계약에 어미 규칙", "종결어미" in _V["dry"]["desc"]))
    ok.append(run("Voice 계약에 문장 규칙", "5어절" in _V["light"]["desc"]))


    # ── 재생성 힌트 (같은 프롬프트로 재시도하면 같은 실수를 반복한다)
    from src.generator import _hint as _h
    _hh = _h(["수치과다(6개/medium)", "방향오용(낙폭)"])
    ok.append(run("힌트에 수치 지적", "숫자" in _hh))
    ok.append(run("힌트에 방향 지적", "부호" in _hh))
    ok.append(run("힌트 중복 제거", _h(["수치과다(5개)", "수치과다(6개)"]).count("\n") == 0))
    from src.personas import build_messages as _bm
    _sys, _ = _bm({"kind": "flow", "title": "t", "facts": "등락률: 20.32%",
                   "retry_hint": "- 숫자를 줄이세요."}, "calm", "fact_read", "reaction", "medium")
    ok.append(run("힌트가 프롬프트에 주입", "직전 시도에서" in _sys))

    # calm 계약이 방향 오용을 유도하지 않는가
    ok.append(run("calm 계약에 방향 규칙", "낙폭" in _V["calm"]["desc"]))
    ok.append(run("전역 규칙에 방향 어휘", "등락 방향 어휘" in _SP))


    # ── 페르소나 v2 (캐릭터 통합형) — 되돌릴 수 있게 v1 과 공존
    from src.personas_v2 import PERSONAS as _P2, SLOT_W as _SW2
    from src import personas as _PM
    ok.append(run("v2 페르소나 10종", len(_P2) == 10, str(len(_P2))))
    ok.append(run("v2 슬롯 가중치 전건 10종",
                  all(len(v) == 10 for v in _SW2.values())))
    ok.append(run("v2 조합 110가지", len(_P2) * len(_ang.ANGLES) == 110))
    ok.append(run("페르소나마다 길이·숫자상한 보유",
                  all({"min", "max", "num_cap", "no_question", "sentences"} <= set(v)
                      for v in _P2.values())))
    # 모드 공통 접근자가 v1/v2 를 모두 흡수하는가
    ok.append(run("접근자 v1 호환", _PM.len_bounds("short") == (40, 160)))
    ok.append(run("접근자 v2 호환", _PM.len_bounds("quick_memo") == (35, 120)))
    from src import personas_v2 as _P2v
    # num_cap 은 claim_cap 에서 파생된다. 주장 하나가 숫자 둘을 데려오므로
    # claim_cap 보다 작으면 모순이다 (실측: 수치과다 6건 중 5건이 이 불일치)
    ok.append(run("숫자상한 ≥ 주장상한",
                  all(_PM.num_cap(k) > _P2v.claim_cap(k) for k in _P2v.PERSONAS)))
    ok.append(run("접근자 숫자상한", _PM.num_cap("quick_memo") == 4))
    ok.append(run("v2 슬롯 가중치 0 허용(정책×수치중심)", _SW2["policy"]["data_focus"] == 0))
    _s2, _ = _PM.build_messages_v2({"kind": "flow", "title": "t", "facts": "등락률: 20.32%"},
                                   "brief_report", "reaction")
    ok.append(run("v2 프롬프트 미치환 없음",
                  not any(x in _s2 for x in ("{persona_name}", "{angle_desc}",
                                             "{rule_block}", "{num_cap}"))))
    ok.append(run("v2 프롬프트에 공통규칙 주입", "1인칭" in _s2 and "당신" in _s2))


    # ── 테스트 채널 분리 (운영 단톡방에 테스트 50건을 쏘는 사고 방지)
    import importlib, os as _os2, config as _cfg
    _bak_env = {k: _os2.environ.get(k) for k in
                ("TEST_MODE", "TELEGRAM_CHAT_ID", "TELEGRAM_TEST_CHAT_ID")}
    _os2.environ.update({"TEST_MODE": "1", "TELEGRAM_CHAT_ID": "-100main",
                         "TELEGRAM_TEST_CHAT_ID": ""})
    importlib.reload(_cfg)
    ok.append(run("테스트채널 미등록 시 발송 차단", _cfg.target_chat() == ("", True)))
    _os2.environ["TELEGRAM_TEST_CHAT_ID"] = "123test"
    importlib.reload(_cfg)
    ok.append(run("테스트 모드는 테스트채널로", _cfg.target_chat() == ("123test", True)))
    _os2.environ["TEST_MODE"] = "0"
    importlib.reload(_cfg)
    ok.append(run("운영 모드는 운영채널로", _cfg.target_chat() == ("-100main", False)))
    for _k, _v in _bak_env.items():
        if _v is None:
            _os2.environ.pop(_k, None)
        else:
            _os2.environ[_k] = _v
    importlib.reload(_cfg)


    # ── 테마글 종목 배정 (커뮤니티에 종목방만 존재)
    from src import theme_map as _tm, tickers as _tk
    _bak_listed = _tk.listed
    _tk.listed = lambda: {"삼성전자": "005930", "SK하이닉스": "000660",
                          "KB금융": "105560", "CJ제일제당": "097950",
                          "두산에너빌리티": "034020", "NAVER": "035420"}
    _cases = [("정부, 반도체 소부장 세제지원 확대", {"삼성전자", "SK하이닉스"}),
              ("강원도-신한은행 서민 금융지원 협약", {"KB금융"}),
              ("원전 수출 지원 방안", {"두산에너빌리티"})]
    for _t, _expect in _cases:
        _i = {"kind": "policy", "title": _t, "facts": "요지: 내용"}
        _tm.assign(_i)
        ok.append(run(f"섹터 매칭: {_t[:12]}", _i.get("stock_name") in _expect,
                      str(_i.get("stock_name"))))
    _i2 = {"kind": "policy", "title": "무관한 제목", "facts": "요지: 내용"}
    _tm.assign(_i2)
    ok.append(run("매칭 실패 시 대형주 폴백", bool(_i2.get("stock_code"))))
    ok.append(run("본문 종목언급 금지 지시 주입", "종목명이나 종목코드를" in _i2["facts"]))
    ok.append(run("이미 종목 있으면 미배정",
                  not _tm.assign({"kind": "policy", "stock_code": "005930", "facts": "x"})))
    ok.append(run("테마글 본문 종목언급 리젝", any("테마글종목언급" in e for e in _f2.check(
        "삼성전자 수혜가 예상됩니다. 반도체 세제지원이 확대됩니다. 적용 시점은 내년입니다.",
        "x", "fact_note", "context", "fact_note", "삼성전자"))))
    _tk.listed = _bak_listed


    # ── Positive Claim Grammar (규칙 확장의 대안)
    from src import claims as _cl2, facts as _fx
    _it3 = {"kind": "flow", "facts": "종가: 8,600원\n등락률: 12.41%\n"
                                     "거래대금: 942억원\n거래량: 20일 평균의 3.2배"}
    _cs = _cl2.build(_it3)
    ok.append(run("claim 추출", len(_cs) == 4, str([c["type"] for c in _cs])))
    ok.append(run("claim 블록 생성", "이번 글에 쓸 사실" in _cl2.block(_it3)))
    ok.append(run("미선정 주장은 블록에 없음",
                  "3.2배" not in _cl2.block(_it3, 3, "reaction")))
    ok.append(run("금지 claim type 명시", "등락의 원인" in _cl2.block(_it3)))
    from src.personas import build_messages_v2 as _bm2
    _s3, _ = _bm2(_it3, "quick_memo", "reaction")
    ok.append(run("프롬프트에 claim 주입", "등락률: 12.41%" in _s3))
    # 지시는 데이터에 진다 — 안 쓸 수치는 [사실관계]에서도 지워야 한다 (실측 오탐 6건)
    _s4, _u4 = _bm2(_it3, "quick_memo", "reaction")
    _keep = {c["value"].split()[0] for c in _cl2.select(_it3, 3, "reaction")}
    ok.append(run("미선정 수치는 사실관계에도 없음",
                  "3.2배" not in _u4, _u4[-160:]))
    ok.append(run("종목·기준일 줄은 보존", "12.41%" in _u4))
    for _bad in ["기대감이 반영된 것으로 보입니다.",
                 "반도체 업황 수혜가 예상됩니다.",
                 "수익 구조를 안정화하려는 전략으로 보입니다."]:
        ok.append(run(f"범위이탈 차단: {_bad[:10]}",
                      any("claim_out_of_scope" in e
                          for e in _f2.check(_bad * 4, "x", "fact_note", "reaction",
                                             "fact_note"))))
    # Fact 계열화 / contrast_pair
    ok.append(run("계열당 최대2 절충", _fx.count(_it3) == 3, str(_fx.count(_it3))))
    _many = {"facts": "등락률: 1%\n종가: 1원\n거래대금: 1억원\n장중 고저 차이: 저가 대비 1%"}
    ok.append(run("같은 계열 4슬롯도 2로 계산", _fx.count(_many) == 2, str(_fx.count(_many))))
    _ct = _fx.contrast_pairs({"facts": "등락률: 12.41%\n마감 위치: 장중 고가 대비 8.2% 낮은 수준"})
    ok.append(run("contrast_pair 생성", len(_ct) == 1, str(_ct)))
    ok.append(run("호환 그래프 timeline×ratio 차단",
                  not __import__("src.personas_v2", fromlist=["x"]).compatible(
                      "timeline_note", "ratio")))


    # ── 입력 정합성 / 사용 개수 명시 (claim grammar 실측 반영)
    from src.generator import clean as _cl3
    ok.append(run("깨진 문자 제거",
                  "ꤼ" not in _cl3("이 정도 규모라면 ꤼ 의미 있는 움직임입니다.")))
    ok.append(run("한글 본문은 보존",
                  "움직임입니다" in _cl3("이 정도 규모라면 ꤼ 의미 있는 움직임입니다.")))
    ok.append(run("선정된 주장만 제시", len(_cl2.select(_it3, 3)) == 3
                  and len(_cl2.select(_it3, 2)) == 2))
    ok.append(run("메뉴 제공 안 함", "골라" not in _cl2.block(_it3, 2)
                  and "이 중" not in _cl2.block(_it3, 2)))
    ok.append(run("선정 결과 재현 가능",
                  _cl2.select(_it3, 3, "reaction") == _cl2.select(_it3, 3, "reaction")))


    # ── 입력 정합성 / 소진 방지
    from src.generator import clean as _cl3
    ok.append(run("깨진 문자 제거",
                  "ꤼ" not in _cl3("규모라면 ꤼ 의미 있는 움직임입니다.")))
    ok.append(run("한글·기호는 보존",
                  _cl3("종가는 8,600원(+12.41%)이었습니다.") ==
                  "종가는 8,600원(+12.41%)이었습니다."))



    # ── 입력 정합성 / 사용 개수 명시
    ok.append(run("깨진 문자 제거",
                  "ꤼ" not in _cl("이 정도 규모라면 ꤼ 의미 있는 움직임입니다.")))
    ok.append(run("한글·숫자는 보존",
                  "12.41%" in _cl("12.41% 상승했습니다.")))



    # ── 주장 상한을 문장 수에 연동 (num_cap 은 숫자 개수, claim_cap 은 주장 수)
    from src.personas_v2 import claim_cap as _cc
    ok.append(run("claim_cap 문장수 연동", _cc("quick_memo") == 3 and _cc("fact_note") == 5,
                  f"quick {_cc('quick_memo')} / fact {_cc('fact_note')}"))
    ok.append(run("claim_cap 상한 5", max(_cc(k) for k in _P2) <= 5))
    _fq = ("종가: 22,500원\n등락률: 29.91%\n거래대금: 225억원\n"
           "거래량: 20일 평균의 6.0배\n장중 고저 차이: 저가 대비 28.6%")
    _bq = ("225억원 거래대금 가운데 29.91% 올랐네요. 종가 22,500원이었고 "
           "거래량은 20일 평균의 6.0배였습니다. 장중 저가 대비 28.6%까지 움직였는데요.")
    ok.append(run("짧은 페르소나는 주장과다 리젝",
                  any("주장과다" in e for e in
                      _f2.check(_bq, _fq, "quick_memo", "reaction", "quick_memo"))))
    ok.append(run("긴 페르소나는 통과",
                  not any("주장과다" in e for e in
                          _f2.check(_bq, _fq, "fact_note", "reaction", "fact_note"))))
    # 등락률 이상치
    import re as _re3
    from src import facts as _fx2
    ok.append(run("가격제한폭 초과 차단", bool(_fx2.sanity_errors({"pct": 135.0}))))
    ok.append(run("상한가는 통과", not _fx2.sanity_errors({"pct": 29.94})))
    ok.append(run("종가 불일치 차단",
                  bool(_fx2.sanity_errors({"pct": 5.0, "close": 1000, "close_hist": 10000}))))
    # 종목코드는 근거없는수치가 아니다 (실측: 엔에프씨(265740) 리젝)
    _itc = {"facts": "종목: 엔에프씨 (265740)\n등락률: 5.20%\n종가: 12,000원",
            "stock_code": "265740"}
    ok.append(run("종목코드 오탐 없음",
                  not _cl2.grounding_errors("엔에프씨(265740)가 5.20% 올랐어요.", _itc, 4)))


    # ── 로그 키 마스킹 (퍼블릭 레포에 API 키가 커밋된 사고 회귀)
    import io as _io, sys as _sys2, importlib as _il
    import config as _cfg2, main as _mn
    _bak_key = _cfg2.DART_API_KEY
    _cfg2.DART_API_KEY = "cf0792ba00cb2113a33030aa508679f3b719b346"
    _buf, _old = _io.StringIO(), _sys2.stdout
    _mn._install_log_mask()
    _sys2.stdout._s = _buf
    print("url?crtfc_key=" + _cfg2.DART_API_KEY + "&x=1")
    _sys2.stdout = _old
    _out = _buf.getvalue()
    ok.append(run("로그에서 키 마스킹", _cfg2.DART_API_KEY not in _out, _out.strip()[:60]))
    _cfg2.DART_API_KEY = _bak_key

    # DART 요청 실패가 파이프라인을 죽이지 않는가
    _dsrc = pathlib.Path("src/sources/dart.py").read_text(encoding="utf-8")
    ok.append(run("DART 요청 예외 처리", "except Exception as e:" in _dsrc))
    ok.append(run("예외 메시지에 URL 미출력", "type(e).__name__" in _dsrc))

    # ── 절단 방어 / 조회공시 역방향 연결
    ok.append(run("미완성 본문 리젝",
                  any("미완성" in e for e in
                      _f2.check("통관 특별 지원에 나선다고 밝혔", "기준일: x",
                                "", "", "quick_memo"))))
    ok.append(run("정상 종결은 통과",
                  not any("미완성" in e for e in
                          _f2.check("통관 특별 지원에 나선다고 밝혔습니다.", "기준일: x",
                                    "", "", "quick_memo"))))
    from src.sources import kind_inquiry as _ki, market as _mk2
    _orig = _mk2._add_history
    _mk2._add_history = lambda r: r.update({"close_hist": 71000, "prev_close": 62000})
    _q = [{"stock_code": "005930", "facts": "답변 성격: 회사는 미확정이라고 답변"}]
    ok.append(run("조회공시→시세 연결", _ki.enrich_with_market(_q, []) == 1
                  and "등락률: 14.52%" in _q[0]["facts"]))
    _mk2._add_history = lambda r: r.update({"close_hist": 150000, "prev_close": 62000})
    ok.append(run("가격제한폭 위반은 연결 안 함",
                  _ki.enrich_with_market([{"stock_code": "000660", "facts": "x"}], []) == 0))
    _mk2._add_history = _orig
    _cfg = __import__("config")
    ok.append(run("슬롯이 공급 상한을 넘지 않음",
                  all(_cfg.SLOT_QUOTA[k] <= _cfg.SUPPLY_CAP[k] for k in _cfg.SLOT_QUOTA)))
    ok.append(run("기대 발송 산출됨", _cfg.EXPECTED_SENT > 0))

    from src.sources import dart_detail as _dd
    ok.append(run("무수치 공시유형 차단",
                  all(_dd.NO_DETAIL_API.search(t) for t in
                      ["자기주식처분결과보고서", "유상증자또는사채등의발행결과"])))
    ok.append(run("해지결정은 차단하지 않음",     # 프로브로 정형 API 실재 확인
                  not _dd.NO_DETAIL_API.search("자기주식취득신탁계약 해지결정")))
    ok.append(run("정상 공시는 통과",
                  not any(_dd.NO_DETAIL_API.search(t) for t in
                          ["자기주식취득 결정", "전환사채권 발행결정", "회사합병 결정"])))
    # 신탁계약은 '자기주식취득' 패턴에도 걸린다 — 순서가 뒤집히면 조용히 0건이 된다
    def _ep(t):
        import re as _r
        return next((e for p_, e, _l, _f in _dd.ENDPOINTS if _r.search(p_, t)), None)
    ok.append(run("신탁 해지가 취득보다 먼저 매칭",
                  _ep("주요사항보고서(자기주식취득신탁계약해지결정)") == "tsstkAqTrctrCcDecsn"))
    ok.append(run("신탁 체결이 취득보다 먼저 매칭",
                  _ep("주요사항보고서(자기주식취득신탁계약체결결정)") == "tsstkAqTrctrCnsDecsn"))
    ok.append(run("일반 취득은 그대로",
                  _ep("주요사항보고서(자기주식취득결정)") == "tsstkAqDecsn"))
    # 공급계약은 정형 API 가 없어 원문 표를 읽는다 (프로브: 후보 3종 전부 101)
    _rows = {"판매ㆍ공급계약내용": "CLT Interface Board",
             "계약금액총액(원)": "9,686,300,000",
             "최근매출액(원)": "66,026,746,277",
             "매출액대비(%)": "14.7",
             "계약상대방": "삼성전자",
             "판매ㆍ공급지역": "대한민국",
             "종료일": "2026-12-31"}
    _orig_doc = _dd._doc_rows
    _dd._doc_rows = lambda r: _rows
    _ci = {"id": "dart-20260904900736", "facts": "공시일: 20260904",
           "title": "단일판매ㆍ공급계약체결", "stock_code": "092870"}
    _got = _dd.enrich_one(_ci, "20260904")
    ok.append(run("공급계약 원문 보강", _got and _ci.get("dart_detail") == "document"))
    ok.append(run("계약금액 억원 변환", "97억원" in _ci["facts"]))
    ok.append(run("매출액 대비 추출", "14.7%" in _ci["facts"]))
    ok.append(run("계약상대 추출", "삼성전자" in _ci["facts"]))
    _dd._doc_rows = lambda r: {}
    ok.append(run("표 비면 보강 안 함",
                  not _dd.enrich_one({"id": "dart-1", "facts": "x",
                                      "title": "단일판매ㆍ공급계약체결"}, "20260904")))
    _dd._doc_rows = _orig_doc
    ok.append(run("타법인 양수 매칭",
                  _ep("주요사항보고서(타법인주식및출자증권양수결정)")
                  == "otcprStkInvscrInhDecsn"))
    # 원문 값의 줄바꿈이 한 줄 형식을 깨뜨린다
    ok.append(run("줄바꿈 접힘", "\n" not in _dd._fmt("토지 및 건물\n경기도 성남시", "")))

    # 함수 안 재import 가 모듈 전역을 가려 UnboundLocalError 를 냈다 (실측: 워크플로 실패).
    # 유닛테스트로는 안 잡힌다 — 네트워크 함수라 호출되지 않기 때문이다. 정적으로 잡는다.
    import ast as _ast
    _shadow = []
    for _f in pathlib.Path("src").rglob("*.py"):
        _t = _ast.parse(_f.read_text(encoding="utf-8"))
        _top = {a.asname or a.name.split(".")[0] for n in _t.body
                if isinstance(n, (_ast.Import, _ast.ImportFrom)) for a in n.names}
        for _fn in [n for n in _ast.walk(_t) if isinstance(n, _ast.FunctionDef)]:
            for _n in _ast.walk(_fn):
                if isinstance(_n, (_ast.Import, _ast.ImportFrom)):
                    for _a in _n.names:
                        if (_a.asname or _a.name.split(".")[0]) in _top:
                            _shadow.append(f"{_f}:{_n.lineno}")
    ok.append(run("함수 내 재import 로 전역 가림 없음", not _shadow, str(_shadow[:3])))

    # 공급계약 원문 파서 (프로브로 확인한 실제 표 구조)
    from src.sources import dart_contract as _dc
    _html = ("<table>"
             "<tr><td>2. 계약내역</td><td>조건부 계약여부</td><td>미해당</td></tr>"
             "<tr><td>계약금액 총액(원)</td><td>662,348,400</td></tr>"
             "<tr><td>최근 매출액(원)</td><td>3,386,486,863</td></tr>"
             "<tr><td>매출액 대비(%)</td><td>19.56</td></tr>"
             "<tr><td>3. 계약상대방</td><td>-</td></tr>"
             "<tr><td>- 최근 매출액(원)</td><td>-</td></tr>"
             "<tr><td>4. 판매ㆍ공급지역</td><td>미국</td></tr></table>")
    _c = _dc._cells(_html)
    ok.append(run("셀이 3개인 행도 라벨 정확",
                  _c.get("조건부 계약여부") == "미해당"))
    ok.append(run("계약금액 추출", _c.get("계약금액 총액(원)") == "662,348,400"))
    ok.append(run("억원 반올림 안 함", _dc._won("662,348,400") == "6.6억원"))
    ok.append(run("계약상대방 매출액과 혼동 없음",
                  next((v for l, v in _c.items()
                        if "최근 매출액" in l and not l.startswith("-")), "")
                  == "3,386,486,863"))
    ok.append(run("공급계약 제목만 처리",
                  bool(_dc.TITLE_RE.search("[기재정정]단일판매ㆍ공급계약체결"))
                  and not _dc.TITLE_RE.search("주요사항보고서(유상증자결정)")))

    # 보강 필드를 claim 에 등록하지 않아 '근거없는수치'로 리젝됐다 (실측 50건 회차)
    _cf = ("공시명: 단일판매ㆍ공급계약체결\n- 계약 내용: CLT Interface Board\n"
           "- 계약 금액: 97억원\n- 최근 매출액 대비: 14.7%\n- 계약 상대: 삼성전자")
    ok.append(run("공급계약 수치가 근거로 인정됨",
                  not _cl2.grounding_errors(
                      "삼성전자와 97억원 규모 공급계약입니다. 최근 매출액 대비 14.7%입니다.",
                      {"facts": _cf}, 4)))
    _af = "공시명: 타법인주식취득\n- 양수 금액: 823억원\n- 자산총액 대비: 11.39%"
    ok.append(run("양수 수치가 근거로 인정됨",
                  not _cl2.grounding_errors(
                      "823억원에 취득했고 자산총액 대비 11.39%입니다.", {"facts": _af}, 4)))
    # quick_memo 는 표본 9건 전멸. 공시 외 슬롯에서 뽑히면 안 된다.
    from src import generator as _g2
    _fl = {"kind": "flow", "stock_code": "005930", "facts":
           "종가: 12,000원\n등락률: 5.20%\n거래대금: 300억원\n"
           "거래량: 20일 평균의 3.2배\n5거래일 누적 등락률: +8.10%"}
    _picks = {_g2.pick_style(_fl, {}, set())[0] for _ in range(30)}
    ok.append(run("flow 에서 quick_memo 미선택", "quick_memo" not in _picks, str(_picks)))

    print(f"\n{sum(ok)}/{len(ok)} passed")
    sys.exit(0 if all(ok) else 1)

if __name__ == "__main__":
    main()
