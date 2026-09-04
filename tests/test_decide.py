"""배포 판정 테스트. main() 이 아니라 decide.py 의 실제 함수를 호출한다."""
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
                       "dry", "short_note", "reaction")
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
    ok.append(run("정상 글은 통과", not _f2.check(_good, "302,500 21.73 2,790"),
                  str(_f2.check(_good, "302,500 21.73 2,790"))))

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
    ok.append(run("Format 7종", len(_F) == 7, str(len(_F))))
    ok.append(run("Angle 8종", len(_ang.ANGLES) == 8, str(len(_ang.ANGLES))))
    ok.append(run("3축 조합 200가지 이상",
                  len(_V) * len(_ang.ANGLES) * len(_F) >= 200,
                  str(len(_V) * len(_ang.ANGLES) * len(_F))))
    # 가중치 방식이므로 슬롯별로 모든 Voice/Format 이 확률 > 0 이어야 한다
    ok.append(run("금지 조합 없음(가중치 방식)",
                  all(len(v) == 4 for v in _VW.values()) and
                  all(len(f) == 7 for f in _FW.values())))

    _it = {"kind": "disclosure", "stock_code": "005930",
           "facts": "발행 총액: 200억원\n전환가액: 2,396원\n만기: 2031-09-11\n"
                    "운영자금: 100억원\n매출 대비 18%"}
    _used = set()
    _c = [_pick(_it, {}, _used) for _ in range(6)]
    ok.append(run("같은 실행 내 축 반복 억제",
                  len({x[1] for x in _c}) >= 4, str([x[1] for x in _c])))

    # Angle 은 사실관계가 허용하는 것만
    ok.append(run("데이터에 없는 Angle 미생성",
                  "reaction" not in _ang.available(_it), str(_ang.available(_it))))
    ok.append(run("uncertainty 단독은 앵글 없음",
                  _ang.available({"facts": "상세 수치는 공개되지 않음"}) == []))

    # 길이·문장수는 Format 에 귀속 (Global '최소 5문장' 과 충돌하던 문제)
    ok.append(run("short_note 문장수 지정", _F["short_note"]["sentences"] == "3~4문장"))
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
    ok.append(run("short_note 질문마무리 리젝",
                  any("질문마무리금지" in e for e in _f2.check(_qb, "", "short_note"))))
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

    print(f"\n{sum(ok)}/{len(ok)} passed")
    sys.exit(0 if all(ok) else 1)

if __name__ == "__main__":
    main()
