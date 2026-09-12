"""배포 판정 테스트. main() 이 아니라 decide.py 의 실제 함수를 호출한다."""
import pathlib
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.decide import decide_distribution, temperature_for
from src.gate import is_hard_excluded
from src import rules, entity, dedup
from src.judge import _parse as parse_judge

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

    _fact_low = P("fact-low", total=20)
    _fact_low["score"].update(factual=3, compliant=5)
    sent, held = decide_distribution([_fact_low])
    ok.append(run("총점과 무관하게 사실성 하한 적용",
                  not sent and held[0]["hold_reason"].startswith("사실성")))
    _comp_low = P("comp-low", total=20)
    _comp_low["score"].update(factual=5, compliant=3)
    sent, held = decide_distribution([_comp_low])
    ok.append(run("총점과 무관하게 준법성 하한 적용",
                  not sent and held[0]["hold_reason"].startswith("준법성")))

    # 심사 장애는 정규식 통과 여부와 무관하게 fail-closed 한다.
    unjudged = P("unjudged")
    unjudged["score"] = None
    unjudged["judge_error"] = "quota"
    sent, held = decide_distribution([unjudged])
    ok.append(run("미심사 글 배포 차단",
                  not sent and held[0]["hold_reason"].startswith("심사실패:")))
    ok.append(run("심사 점수 범위 검증",
                  parse_judge('{"factual":9,"useful":5,"natural":5,"compliant":5,'
                              '"gain":5,"fit":5,"fatal":[],"reason":""}') is None))
    ok.append(run("정상 심사 JSON 파싱",
                  parse_judge('{"factual":5,"useful":4,"natural":4,"compliant":5,'
                              '"gain":4,"fit":3,"fatal":[],"reason":""}') is not None))

    # 4) 정렬 우선: 고점수가 상한을 먼저 차지
    posts = [P(1, total=12), P(2, total=20), P(3, total=15)]
    sent, _ = decide_distribution(posts, per_stock=1, min_score=10)
    ok.append(run("고점수 우선 선점", sent[0]["score"]["total"] == 20))

    # 5) 테마글은 종목 상한에서 제외
    posts = [P(i, code=None, kind="policy") for i in range(4)]
    sent, _ = decide_distribution(posts, per_stock=1, per_kind_cap={"policy": 4})
    ok.append(run("테마글 종목상한 면제", len(sent) == 4))

    # 6) 유형 상한 — 목표를 채운 뒤에는 상한이 지켜진다
    posts = [P(i, code=f"00000{i}") for i in range(9)]
    sent, _ = decide_distribution(posts, per_kind_cap={"disclosure": 3}, target=3)
    ok.append(run("유형상한 적용", len(sent) == 3))

    # 6b) 목표에 못 미치면 유형상한을 풀어 메운다.
    # 공시/리포트/정책은 품질이 좋지만 공급이 적고, flow 는 그 반대다.
    # 좋은 것부터 채우고 모자란 만큼만 flow 로 메운다.
    posts = [P(i, code=f"00000{i}") for i in range(9)]
    sent, _ = decide_distribution(posts, per_kind_cap={"disclosure": 3}, target=8)
    ok.append(run("목표 미달 시 상한 완화", len(sent) == 8, str(len(sent))))

    # 6c) 우선순위: 같은 점수면 공시가 flow 보다 먼저 들어간다
    posts = ([P(f"f{i}", code=f"10000{i}", kind="flow", total=20) for i in range(5)]
             + [P(f"d{i}", code=f"20000{i}", kind="disclosure", total=15)
                for i in range(3)])
    sent, _ = decide_distribution(
        posts, per_kind_cap={"flow": 5, "disclosure": 5}, target=3)
    ok.append(run("공시 우선 배치",
                  all(p["kind"] == "disclosure" for p in sent),
                  str([p["kind"] for p in sent])))

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
    dated_seen = {}
    dated_a = {**a, "id": "dart-20260903123456", "facts": "공시일: 20260903"}
    dated_b = {**b, "id": "naver-10", "facts": "발간: 증권사 / 2026.09.04"}
    dedup.mark(dated_a, dated_seen, "2026-09-03")
    ok.append(run("다른 날짜의 같은 사건유형 통과",
                  not dedup.is_dup(dated_b, dated_seen)[0]))
    same_title = [
        {"id": "same-a", "stock_code": "005930", "title": "3분기 실적 발표"},
        {"id": "same-b", "stock_code": "000660", "title": "3분기 실적 발표"},
    ]
    scoped, _ = dedup.filter_new(same_title, {})
    ok.append(run("다른 종목의 동일 제목 통과", len(scoped) == 2))


    # ── 프롬프트 빌드가 예외 없이 되는지 (JSON 리터럴 + format 충돌 회귀)
    from src.personas import build_messages_v2
    from src.judge import SYSTEM as JSYS
    try:
        build_messages_v2({"kind": "flow", "title": "t", "facts": "f",
                           "stock_name": "삼성전자", "thin_facts": True},
                          "fact_note", "reaction")
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
    from src.sources.policy import is_relevant, _fresh_published_at
    from datetime import datetime as _policy_datetime
    from config import KST as _POLICY_KST
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
    _corp_news = "다이나믹솔루션, 파마앤바이오 신기술조합 주식 160억 취득"
    _corp_ok, _corp_why = is_relevant(_corp_news, "바이오 기업 투자 결정")
    ok.append(run("개별기업 기사는 정책 슬롯 차단",
                  not _corp_ok and _corp_why == "개별기업사건", _corp_why))
    _policy_now = _policy_datetime(2026, 9, 12, 12, 0, tzinfo=_POLICY_KST)
    ok.append(run("최근 RSS 발행시각 통과",
                  _fresh_published_at("Sat, 12 Sep 2026 01:00:00 +0000",
                                      _policy_now) is not None))
    ok.append(run("48시간 지난 RSS 제외",
                  _fresh_published_at("Thu, 10 Sep 2026 01:00:00 +0000",
                                      _policy_now) is None))
    ok.append(run("발행시각 없는 RSS 제외",
                  _fresh_published_at("", _policy_now) is None))
    _policy_monday = _policy_datetime(2026, 9, 14, 6, 11, tzinfo=_POLICY_KST)
    ok.append(run("월요일 정책 RSS 금요일부터 포함",
                  _fresh_published_at("Fri, 11 Sep 2026 00:00:00 +0000",
                                      _policy_monday) is not None))
    from src.sources import dart as _dart_date
    ok.append(run("월요일 DART 금~일 조회",
                  _dart_date._range(_policy_monday) == ("20260911", "20260913")))
    from src.sources import market as _market_date
    ok.append(run("월요일 장전 시세는 금요일 기준",
                  _market_date._last_trading_day(_policy_monday) == "2026-09-11"))


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
    # 종목이 있으면 3행에 앱 종목방 딥링크가 붙는다
    # URL 전문은 카드에 넣지 않는다. 버튼으로만 전달한다.
    ok.append(run("카드에 URL 전문 없음", "openData=" not in _c))
    from src.telegram_bot import buttons as _btn
    _b = _btn({"stock_code": "005930", "stock_name": "삼성전자"})
    ok.append(run("인라인 버튼 생성",
                  bool(_b) and "openData=005930" in
                  _b["inline_keyboard"][0][0]["url"]))
    ok.append(run("종목 없으면 버튼 없음", _btn({"kind": "policy"}) is None))

    # 부분 전송 시 성공한 항목만 반환해야 state/dedup 에 기록할 수 있다.
    class _Resp:
        def __init__(self, success):
            self.ok = success
            self.status_code = 200 if success else 500
            self.text = "ok" if success else "fail"

    _old_token, _old_post, _old_sleep = _tg.TELEGRAM_TOKEN, _tg._post, _tg.time.sleep
    _tg.TELEGRAM_TOKEN = "test"
    _responses = iter([_Resp(True), _Resp(False)])
    _tg._post = lambda *_a, **_k: next(_responses)
    _tg.time.sleep = lambda *_a: None
    _delivery = [{"id": "ok", "kind": "policy", "body": "가" * 60},
                 {"id": "fail", "kind": "policy", "body": "나" * 60}]
    _delivered = _tg.send_all(_delivery)
    ok.append(run("부분 전송은 성공 글만 반환",
                  [p["id"] for p in _delivered] == ["ok"]))
    _tg.TELEGRAM_TOKEN, _tg._post, _tg.time.sleep = _old_token, _old_post, _old_sleep

    _summary_payload = []
    _tg.TELEGRAM_TOKEN = "test"
    _tg._post = lambda _method, payload: _summary_payload.append(payload)
    _tg.send_summary([], 0, target=50)
    ok.append(run("목표 기준 요약 0/50 표기",
                  bool(_summary_payload)
                  and "배포 미달" in _summary_payload[0]["text"]
                  and "0/50건" in _summary_payload[0]["text"]))
    _tg.TELEGRAM_TOKEN, _tg._post = _old_token, _old_post

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
             "고가 대비로는 조금 밀린 자리에서 마감했고요. 혹시 배경 아시는 분 계신가요.")
    _good2 = _good.replace("기록했습니다", "였습니다")
    ok.append(run("정상 글은 통과", not _f2.check(_good2, "302,500 21.73 2,790"),
                  str(_f2.check(_good2, "302,500 21.73 2,790"))))
    from src.generator import clean as _cl
    ok.append(run("상투어 치환('기록했습니다')",
                  "이었습니다" in _cl("거래대금은 942억원을 기록했습니다.")))
    ok.append(run("R&D 기호 보존", "R&D" in _cl("사옥 및 R&D 센터로 활용합니다.")))
    ok.append(run("하락 부호·방향 중복 정리",
                  "-4.66% 내렸" not in _cl("LG가 -4.66% 내렸어요.")
                  and "4.66% 내렸" in _cl("LG가 -4.66% 내렸어요.")))
    ok.append(run("종가 뒤 음수 등락률 문장 정리",
                  _cl("제주반도체는 75,200원에 -4.57% 마감했습니다.") ==
                  "제주반도체는 4.57% 내려 75,200원에 마감했습니다."))
    ok.append(run("장중 고저 범위 비문 정리",
                  "고저 차이는 저가 대비 26.6%였네요" in
                  _cl("장중에는 저가 대비 26.6% 범위에서 움직였네요.")))
    ok.append(run("수급 순위 중복 표현 정리",
                  "외국인 수급은 순매도 상위 5위" in
                  _cl("외국인 순매매 수급 순위는 순매도 상위 5위였습니다.")))
    ok.append(run("누적 등락률 부호를 방향어로 정리",
                  "10.15% 상승했네요" in
                  _cl("5거래일 누적 등락률은 +10.15%였습니다.")))
    ok.append(run("장중 오르내림 표현을 고저 차이로 정리",
                  "고저 차이는 저가 대비 32.2%였습니다" in
                  _cl("장중에는 저가 대비 32.2% 오르내렸습니다.")))
    ok.append(run("장중 변동성 표현을 고저 차이로 정리",
                  "고저 차이는 저가 대비 31.1%였고" in
                  _cl("장중 저가 대비 31.1%까지 오르며 변동성을 보였는데,")))
    ok.append(run("흐름 연결어미 마무리 정리",
                  _cl("5거래일 누적으로는 16.09% 상승한 흐름인데요.").endswith("흐름입니다.")))
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
    from src.generator import pick_style as _pick
    from src import angles as _ang
    from src.personas_v2 import PERSONAS as _P2, SLOT_W as _SW
    ok.append(run("페르소나 10종", len(_P2) == 10, str(len(_P2))))
    ok.append(run("Angle 11종", len(_ang.ANGLES) == 11, str(len(_ang.ANGLES))))
    ok.append(run("Persona x Angle 100가지 이상",
                  len(_P2) * len(_ang.ANGLES) >= 100,
                  str(len(_P2) * len(_ang.ANGLES))))
    ok.append(run("슬롯 전건 페르소나 가중치 존재",
                  all(w and sum(w.values()) > 0 for w in _SW.values())))

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

    # 페르소나를 먼저 뽑은 뒤 호환 Angle이 없다고 원 후보로 돌아가면 COMPAT을 우회한다.
    from src import generator as _gen_style
    _old_weighted_compat = _gen_style._weighted
    def _prefer_careful(weights, _penalize):
        return ("careful_note" if weights.get("careful_note", 0) > 0
                else next(k for k, v in weights.items() if v > 0))
    _gen_style._weighted = _prefer_careful
    _amount_only = {"kind": "disclosure", "stock_code": "000001",
                    "facts": "공시명: 유상증자 결정\n발행 총액: 200억원\n운영자금: 100억원"}
    _compat_pick = _gen_style.pick_style(_amount_only, {}, set())
    _gen_style._weighted = _old_weighted_compat
    ok.append(run("런타임 Persona × Angle 호환 강제",
                  _gen_style.P.v2.compatible(_compat_pick[0], _compat_pick[1]),
                  f"{_compat_pick[0]}×{_compat_pick[1]}"))

    # 길이·문장수는 Format 에 귀속 (Global '최소 5문장' 과 충돌하던 문제)
    from src.personas_v2 import SYSTEM_PROMPT as _SP, PERSONAS as _PD2
    ok.append(run("페르소나 문장수 지시 존재",
                  all(p["sentences"] for p in _PD2.values())))
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


    # ── 페르소나별 질문 마무리 금지 (실측: 프롬프트만으로는 4건 전부 물음표로 끝남)
    _qb = "디케이티가 어제 올랐네요. 거래대금도 늘었는데요. 사유는 확인이 안 됩니다. 배경이 뭐라고 보시나요?"
    ok.append(run("fact_note 질문마무리 리젝",
                  any("질문마무리금지" in e for e in _f2.check(_qb, "", "fact_note"))))
    ok.append(run("open_talk 은 허용",
                  not any("질문마무리금지" in e for e in _f2.check(_qb, "", "open_talk"))))

    # ── 리포트 글감 기준 (제목만 있으면 차단)
    ok.append(run("리포트 제목만 차단", not _hs2(
        {"kind": "research", "facts": "리포트 제목: 하이 앤 드라이\n발간: 대신증권"})))
    ok.append(run("리포트 배경보강만으로는 불충분", not _hs2(
        {"kind": "research", "facts": "리포트 제목: 하이 앤 드라이\n\n"
                                      "[검색으로 확인된 배경]\n- 팬오션은 벌크선사"})))
    ok.append(run("적정가격 있으면 통과", _hs2(
        {"kind": "research", "facts": "리포트 제목: 실적 반등\n제시 적정가격: 33,000원"})))
    ok.append(run("적정가격 리포트에 안전한 Angle 존재",
                  "terms" in _ang.available(
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


    # ── Length 연동 길이 기준 (실측: 고정 50~300 과 어긋나 4건 과잉 리젝)
    _len_cases = [("short", 34, True), ("short", 90, False),
                  ("medium", 150, False), ("long", 260, False), ("long", 370, True)]
    for _ln, _n, _should in _len_cases:
        _e = [x for x in _f2.check("가" * _n, "", "fact_read", "reaction", _ln)
              if "너무" in x]
        ok.append(run(f"길이 {_ln}/{_n}자 {'리젝' if _should else '통과'}",
                      bool(_e) == _should, str(_e)))
    ok.append(run("페르소나 상한이 필터 상한 이내",
                  max(p["max"] for p in _PD2.values()) <= 330))


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
    from src import facts as _facts3
    _derived_facts = ("등락률: 3.20%\n" + _facts3.DERIVED_HEADER
                      + "\n· 거래량: 20일 평균의 3.2배")
    ok.append(run("같은 숫자만 쓴 결합사실 우회 차단",
                  not _facts3.uses_derived("등락률은 3.2%였습니다.", _derived_facts)))
    ok.append(run("결합값과 관계를 함께 쓰면 통과",
                  _facts3.uses_derived("거래량은 20일 평균의 3.2배였습니다.", _derived_facts)))
    ok.append(run("숫자 1개 글도 결합사실 검사",
                  any("결합사실미사용" in e for e in _f2.check(
                      "삼성전자의 등락률은 3.2%였습니다. 확인된 수치만 정리한 내용입니다.",
                      _derived_facts, "fact_note", "reaction", "short"))))
    ok.append(run("한 주장의 복수 숫자는 1개로", not any("주장과다" in e for e in _f2.check(
        "1 대 1.8702948. 우성이 우성유통을 흡수합병하기로 결정했습니다.",
        "합병 비율: 1 대 1.8702948", "fact_read", "ratio", "quick_memo"))))
    # 격식체만 3문장이면 어미단조로 걸린다 (종토방 실측: '~습니다' 1.7%)
    ok.append(run("자연스러운 글 통과", not _f2.check(
        "로보티즈가 어제 20.32% 올랐습니다. 종가는 299,000원인데요. "
        "거래대금은 2,990억원으로 고가 대비 소폭 밀린 자리에서 마감했습니다.",
        _f_ai, "fact_read", "reaction", "short")))
    ok.append(run("격식체 일변도 리젝", any("어미단조" in e for e in _f2.check(
        "로보티즈가 어제 20.32% 올랐습니다. 종가는 299,000원입니다. "
        "거래대금은 2,990억원이었습니다.",
        _f_ai, "fact_read", "reaction", "short"))))
    # 실측: 50건 중 22건이 수치로 시작해 종목명이 끝까지 안 나왔다
    ok.append(run("수치 선두 리젝", any("수치선두" in e for e in _f2.check(
        "20.32% 상승. 로보티즈 종가는 299,000원입니다.",
        _f_ai, "fact_read", "reaction", "short"))))
    ok.append(run("선두 파손 리젝", any("선두파손" in e for e in _f2.check(
        "6.4배습니다. 어제 로보티즈 거래량이 20일 평균의 6.4배였습니다.",
        _f_ai, "fact_read", "reaction", "short"))))

    # ── Voice 가 계약을 담고 있는가 (라벨이면 문체가 안 바뀐다)
    from src.personas_v2 import PERSONAS as _PD
    ok.append(run("페르소나 계약에 어미 규칙",
                  any("종결어미" in p["desc"] for p in _PD.values())))


    # ── 재생성 힌트 (같은 프롬프트로 재시도하면 같은 실수를 반복한다)
    from src.generator import _hint as _h
    _hh = _h(["수치과다(6개/medium)", "방향오용(낙폭)"])
    ok.append(run("힌트에 수치 지적", "숫자" in _hh))
    ok.append(run("힌트에 방향 지적", "부호" in _hh))
    ok.append(run("힌트 중복 제거", _h(["수치과다(5개)", "수치과다(6개)"]).count("\n") == 0))
    from src.personas import build_messages_v2 as _bm
    _sys, _ = _bm({"kind": "flow", "title": "t", "facts": "등락률: 20.32%",
                   "retry_hint": "- 숫자를 줄이세요."}, "fact_note", "reaction")
    ok.append(run("힌트가 프롬프트에 주입", "직전 시도에서" in _sys))

    # 방향 오용 규칙은 전역(SYSTEM_PROMPT)에만 둔다 — 페르소나마다 적으면 어긋난다
    ok.append(run("전역 규칙에 방향 어휘", "등락 방향 어휘" in _SP))


    # ── 페르소나 v2 (캐릭터 통합형)
    from src.personas_v2 import PERSONAS as _P2, SLOT_W as _SW2
    from src import personas as _PM
    ok.append(run("v2 페르소나 10종", len(_P2) == 10, str(len(_P2))))
    ok.append(run("v2 슬롯 가중치 전건 10종",
                  all(len(v) == 10 for v in _SW2.values())))
    ok.append(run("v2 조합 110가지", len(_P2) * len(_ang.ANGLES) == 110))
    ok.append(run("페르소나마다 길이·숫자상한 보유",
                  all({"min", "max", "num_cap", "no_question", "sentences"} <= set(v)
                      for v in _P2.values())))
    # 공통 접근자가 페르소나 스펙을 정확히 흡수하는가
    ok.append(run("접근자 길이 반영", _PM.len_bounds("quick_memo") == (55, 120)))
    ok.append(run("미등록 페르소나는 기본값", _PM.len_bounds("없는페르소나") == (50, 300)))
    from src import personas_v2 as _P2v
    # num_cap 은 claim_cap 에서 파생된다. 주장 하나가 숫자 둘을 데려오므로
    # claim_cap 보다 작으면 모순이다 (실측: 수치과다 6건 중 5건이 이 불일치)
    ok.append(run("숫자상한 ≥ 주장상한",
                  all(_PM.num_cap(k) > _P2v.claim_cap(k) for k in _P2v.PERSONAS)))
    ok.append(run("접근자 숫자상한", _PM.num_cap("quick_memo") == 4))
    ok.append(run("v2 슬롯 가중치 0 허용(정책×수치중심)", _SW2["policy"]["data_focus"] == 0))
    ok.append(run("짧은메모는 공시 슬롯만 활성",
                  _SW2["disclosure"]["quick_memo"] > 0
                  and all(_SW2[k]["quick_memo"] == 0 for k in
                          ("research", "flow", "policy", "poll", "theme"))))
    ok.append(run("짧은메모 숫자 지시 충돌 없음",
                  "숫자는 가장 눈에 띄는 하나만" not in _P2["quick_memo"]["desc"]))
    _poll_styles = [p for p, w in _SW2["poll"].items() if w > 0]
    ok.append(run("투표 슬롯은 질문형 페르소나만 허용",
                  _poll_styles == ["open_talk"]
                  and all(not _P2[p]["no_question"] for p in _poll_styles)))
    ok.append(run("투표 본문 질문 마무리 필수",
                  any("질문마무리필수" in e for e in _f2.check(
                      "정부가 반도체 지원안을 발표했습니다. 적용 범위를 정리했습니다.",
                      "제목: 정부 반도체 지원안", "open_talk", "context", "open_talk",
                      require_question=True))))
    _flow_facts = ("기준일: 2026-09-11\n종가: 4,115원\n등락률: 13.05%\n"
                   "거래대금: 500억원\n거래량: 20일 평균의 1.7배")
    ok.append(run("시세 상대날짜 차단",
                  any("상대날짜" in e for e in _f2.check(
                      "빛과전자가 어제 4,115원으로 마감했습니다. "
                      "거래량은 20일 평균의 1.7배였네요.",
                      _flow_facts, "fact_note", "ratio", "fact_note"))))
    ok.append(run("근거 없는 원인 질문 차단",
                  "unsupported_cause_question" in _f2.check(
                      "빛과전자가 13.05% 올랐습니다. 거래량은 20일 평균의 1.7배였는데요. "
                      "어떤 수급 요인이 작용했다고 생각하시나요?",
                      _flow_facts, "open_talk", "ratio", "open_talk", require_question=True)))
    ok.append(run("추세·수급 평가 차단",
                  "claim_out_of_scope" in _f2.check(
                      "가온전선이 11.87% 올랐습니다. 상승 추세지만 수급은 우호적이지 않네요.",
                      "등락률: 11.87%", "two_view", "reaction", "two_view")))
    ok.append(run("명사+수치 문장 파편 차단",
                  any("선두파손(명사+수치)" in e for e in _f2.check(
                      "다이나믹솔루션 160억원. 주식 취득 관련 보도입니다. 자세한 내용입니다.",
                      "양수 금액: 160억원", "brief_report", "amount", "brief_report"))))
    ok.append(run("근거 없는 용어해설 차단",
                  "용어근거없음" in _f2.check(
                      "엔투텍이 전환사채 발행을 결정했습니다. "
                      "전환사채는 주식으로 바뀌는 채권입니다. 전환가액은 1,413원이에요.",
                      "공시명: 전환사채 발행 결정\n전환가액: 1,413원",
                      "term_guide", "decode", "term_guide")))
    ok.append(run("결정 공시 완료형 차단",
                  "결정공시완료형" in _f2.check(
                      "루멘스가 유상증자를 결정했습니다. 보통주 3,571,428주를 발행했습니다.",
                      "공시명: 유상증자 결정\n발행 보통주: 3,571,428주",
                      "fact_note", "amount", "fact_note")))
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
              ("원전 수출 지원 방안", {"두산에너빌리티"})]
    for _t, _expect in _cases:
        _i = {"kind": "policy", "title": _t, "facts": "요지: 내용"}
        _tm.assign(_i)
        ok.append(run(f"섹터 매칭: {_t[:12]}", _i.get("stock_name") in _expect,
                      str(_i.get("stock_name"))))
    _i2 = {"kind": "policy", "title": "무관한 제목", "facts": "요지: 내용"}
    _tm.assign(_i2)
    ok.append(run("매칭 실패 시 임의 대형주 배정 금지",
                  not _i2.get("stock_code") and _i2.get("no_stock_fit")))
    import main as _main_theme
    _map_blocked = []
    ok.append(run("게시판 없는 글은 생성 전 제외",
                  not _main_theme._drop_no_board([_i2], _map_blocked)
                  and _map_blocked == [("?", "tier5:게시판없음")]))
    ok.append(run("섹터 매칭 글에 종목언급 금지 지시 주입",
                  "종목명이나 종목코드를" in _i["facts"]))
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
                 "수익 구조를 안정화하려는 전략으로 보입니다.",
                 "마감 무렵 일부 차익실현이 나온 형태입니다.",
                 "개별 거래일 움직임은 들쭉날쭉했습니다."]:
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

    # 기준일이 정확히 같은 확정 캐시는 시각·주말과 무관하게 먼저 써야
    # 전종목 API 조회를 피한다.
    _old_cache_load = _mk2._cache_load
    _old_after_close = _mk2._after_close
    _old_last_day = _mk2._last_trading_day
    _old_get_soup = _mk2.crawl.get_soup
    _mk2._cache_load = lambda _day, max_age_days=0: [
        {"id": f"cached-{i}", "kind": "flow"} for i in range(100)]
    _mk2._after_close = lambda now=None: True
    _mk2._last_trading_day = lambda: "2026-09-11"
    _network_called = [False]
    def _unexpected_market_network(*_a, **_k):
        _network_called[0] = True
        return None
    _mk2.crawl.get_soup = _unexpected_market_network
    _cached_result = _mk2.fetch(50)
    ok.append(run("기준일 일치 시세 캐시 우선 사용",
                  len(_cached_result) == 50 and not _network_called[0]))
    _mk2._cache_load = _old_cache_load
    _mk2._after_close = _old_after_close
    _mk2._last_trading_day = _old_last_day
    _mk2.crawl.get_soup = _old_get_soup

    import tempfile as _market_tmp, json as _market_json
    _old_market_cache = _mk2._CACHE
    with _market_tmp.TemporaryDirectory() as _market_td:
        _mk2._CACHE = _market_td + "/market.json"
        pathlib.Path(_mk2._CACHE).write_text(_market_json.dumps({
            "day": "2026-09-11", "items": [{"id": "friday"}]
        }), encoding="utf-8")
        ok.append(run("휴장일 최근 확정 캐시 허용",
                      bool(_mk2._cache_load("2026-09-14", max_age_days=4))))
        ok.append(run("오래된 시세 캐시 거부",
                      not _mk2._cache_load("2026-09-20", max_age_days=4)))
        pathlib.Path(_mk2._CACHE).write_text(_market_json.dumps({
            "day": "2026-09-11", "items": [{
                "id": "zero", "facts": "종가: 100원\n· 마감 위치: 장중 고가 대비 0.0% 낮은 수준\n· 거래량: 20일 평균의 2.0배"
            }]
        }), encoding="utf-8")
        _fixed_cache = _mk2._cache_load("2026-09-11")
        ok.append(run("이전 시세 캐시의 0.0% 마감위치 제거",
                      "0.0% 낮은 수준" not in _fixed_cache[0]["facts"]
                      and "2.0배" in _fixed_cache[0]["facts"]))
    _mk2._CACHE = _old_market_cache

    # 정확한 기준일 일별시세를 만들 수 있으면 오래된 캐시보다 우선해야 한다.
    _old_market = {n: getattr(_mk2, n) for n in
                   ("_cache_load", "_after_close", "_last_trading_day",
                    "_rank_from_daily", "flow_ranks", "_add_history",
                    "_add_flow", "_cache_save")}
    _old_soup = _mk2.crawl.get_soup
    _mk2._cache_load = lambda _day, max_age_days=0: (
        [] if max_age_days == 0 else [{"id": "stale", "kind": "flow"}])
    _mk2._after_close = lambda now=None: False
    _mk2._last_trading_day = lambda now=None: "2026-09-11"
    _mk2.crawl.get_soup = lambda *_a, **_k: None
    _mk2._rank_from_daily = lambda _day, _limit: [{
        "code": "005930", "name": "삼성전자", "market": "KOSPI",
        "close": 1000.0, "pct": 5.0, "eok": 300.0, "vol_x": 2.0,
        "day_used": "20260911",
    }]
    _mk2.flow_ranks = lambda: {}
    _mk2._add_history = lambda _r: None
    _mk2._add_flow = lambda _r: None
    _mk2._cache_save = lambda *_a, **_k: None
    _exact_market = _mk2.fetch(1)
    ok.append(run("정확한 시세가 오래된 캐시보다 우선",
                  len(_exact_market) == 1
                  and _exact_market[0]["id"].startswith("flow-2026-09-11")))
    for _n, _v in _old_market.items():
        setattr(_mk2, _n, _v)
    _mk2.crawl.get_soup = _old_soup

    _cfg = __import__("config")
    ok.append(run("슬롯이 공급 상한을 넘지 않음",
                  all(_cfg.SLOT_QUOTA[k] <= _cfg.SUPPLY_CAP[k] for k in _cfg.SLOT_QUOTA)))
    ok.append(run("기대 발송 산출됨", _cfg.EXPECTED_SENT > 0))
    ok.append(run("수집 상한이 생성 상한을 충족",
                  all(_cfg.COLLECT_CAP[k] >= _cfg.GEN_CAP[k] for k in _cfg.GEN_CAP)))
    ok.append(run("기본 설정 목표 공급 가능",
                  _cfg.EXPECTED_SENT >= _cfg.TARGET_POSTS))

    import main as _pipeline
    _mix = ([{"id": f"d{i}", "kind": "disclosure"} for i in range(8)]
            + [{"id": f"f{i}", "kind": "flow"} for i in range(30)]
            + [{"id": f"r{i}", "kind": "research"} for i in range(8)])
    _ordered = _pipeline._stage_order(_mix)
    ok.append(run("단계 생성 순서가 후보를 보존",
                  {x["id"] for x in _ordered} == {x["id"] for x in _mix}))
    ok.append(run("첫 생성 묶음 유형 혼합",
                  len({x["kind"] for x in _ordered[:12]}) >= 2))
    _rescue_mix = ([{"id": f"rd{i}", "kind": "disclosure"} for i in range(8)]
                   + [{"id": f"rr{i}", "kind": "research"} for i in range(3)]
                   + [{"id": f"rp{i}", "kind": "policy"} for i in range(3)])
    _rescued = _pipeline._balanced_rescue(_rescue_mix, 5)
    ok.append(run("보강 rescue가 5건 상한과 유형 균형 유지",
                  len(_rescued) == 5
                  and len({x["kind"] for x in _rescued}) >= 2
                  and sum(x["kind"] == "disclosure" for x in _rescued) < 5))
    ok.append(run("보강 rescue 기본 묶음은 5건",
                  _cfg.ENRICH_RESCUE_CHUNK == 5))
    ok.append(run("50건 목표 첫 생성 묶음은 60건",
                  _cfg.TARGET_POSTS != 50 or _cfg.GEN_STAGE_SIZE == 60))
    ok.append(run("후속 생성량은 실수율 기반 10~60건",
                  _pipeline._next_stage_size(200, 5, 100, 50) == 10
                  and _pipeline._next_stage_size(200, 40, 100, 10) == 60
                  and _pipeline._next_stage_size(7, 40, 100, 10) == 7
                  and _pipeline._next_stage_size(200, 0, 100, 10) == 0))

    from src import stats as _stats
    _stat_candidates = [{"id": f"c{i}", "kind": "flow"} for i in range(4)]
    _stat_attempted = _stat_candidates[:2]
    _stat_attempted[0]["_selected_persona"] = "brief_report"
    _stat_attempted[0]["_selected_angle"] = "reaction"
    _stat_attempted[1]["_selected_persona"] = "fact_note"
    _stat_attempted[1]["_selected_angle"] = "compare"
    _stat_generated = [
        {"id": "c0", "kind": "flow", "provider": "claude",
         "tone": "brief_report", "angle": "reaction",
         "score": {"total": 16, "fit": 3}},
        {"id": "c1", "kind": "flow", "provider": "claude",
         "tone": "fact_note", "angle": "compare", "score": None},
    ]
    _stat_sent = [_stat_generated[0]]
    _stat_row = _stats.summarize(
        _stat_candidates, [], 0, _stat_generated, _stat_sent,
        [_stat_generated[1]], [], generation_candidates=_stat_candidates,
        generation_attempted=_stat_attempted, generation_stages=[2],
        delivery_attempted=_stat_sent)
    ok.append(run("단계 생성 절감량 계측",
                  _stat_row["generation_items_avoided"] == 2
                  and _stat_row["generation_attempted"] == 2
                  and _stat_row["generation_stages"] == [2]))
    ok.append(run("심사·전송 실패 계측",
                  _stat_row["judge_failed"] == 1
                  and _stat_row["delivery_failed"] == 0))
    ok.append(run("유형별 퍼널 계측",
                  _stat_row["by_kind_funnel"]["flow"]["delivered"] == 1))
    ok.append(run("페르소나별 퍼널 계측",
                  _stat_row["by_persona"]["brief_report"]["attempted"] == 1
                  and _stat_row["by_persona"]["brief_report"]["delivered"] == 1
                  and _stat_row["by_persona"]["fact_note"]["held"] == 1))

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
    _piic_fields = next(f for p_, e, _l, f in _dd.ENDPOINTS if e == "piicDecsn")
    ok.append(run("DART ssl_at 오매핑 제거",
                  all(k != "ssl_at" for k, _l, _u in _piic_fields)))
    _rows_by_receipt = [{"rcept_no": "20260901000001", "bd_fta": "1"},
                        {"rcept_no": "20260901000002", "bd_fta": "2"}]
    ok.append(run("DART 상세 접수번호 정확 매칭",
                  _dd._row_for_receipt(_rows_by_receipt, "20260901000001")["bd_fta"] == "1"))
    ok.append(run("DART 상세 불일치 시 사용 안 함",
                  _dd._row_for_receipt(_rows_by_receipt, "20260901000003") is None))
    _dart_source = pathlib.Path("src/sources/dart.py").read_text(encoding="utf-8")
    ok.append(run("DART 시장구분 전체 조회", '"corp_cls"' not in _dart_source))
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
    ok.append(run("DART 고정소수 합병비율 정리",
                  _dd._fmt("1.0000000 : 0.0000000", "") == "1 : 0"))

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

    # 관문 기준 역산 (계측 결과: 게이트 51% / 정규식 41% / 심사 33%)
    from src import gate as _gt, personas as _pn
    from src.personas_v2 import PERSONAS as _PV, claim_cap as _cc
    ok.append(run("정정 공시 일괄차단 해제",
                  not _gt.is_hard_excluded(
                      {"title": "[기재정정]주요사항보고서(유상증자결정)"})[0]))
    ok.append(run("num_cap 이 claim_cap 이상",
                  all(_pn.num_cap(p_) > _cc(p_) for p_ in _PV)))
    ok.append(run("미선정 비수치 사실도 프롬프트에서 제거",
                  "공시명:" not in _cl2.facts_view(
                      {"facts": "공시명: 공급계약\n등락률: 5.20%"}, 1, "reaction")))
    _selected_item = {"facts": "등락률: 5.20%\n종가: 12,000원", "angle": "reaction"}
    ok.append(run("미선정 주장 수치 사용 차단",
                  any("선정외주장" in e for e in
                      _cl2.grounding_errors("종가는 12,000원입니다.", _selected_item, 1))))
    ok.append(run("평범한 관계값은 글감에서 제외",
                  not _facts3.evaluate({"vol_x": 1.0, "high": 10010, "low": 10000,
                                        "close": 10005, "ret5": 1.0})))
    ok.append(run("고가 동일 마감에 0.0% 낮음 미생성",
                  not any("마감 위치" in x for x in
                          _facts3.evaluate({"high": 13000, "low": 10000,
                                            "close": 13000, "pct": 29.9}))))
    ok.append(run("유의미한 관계값은 글감으로 인정",
                  bool(_facts3.evaluate({"vol_x": 2.0, "high": 10600, "low": 10000,
                                         "close": 10550, "ret5": 8.0}))))

    # ── 프로바이더 장애·보강 캐시
    from src.llm import gemini as _gm
    ok.append(run("Gemini 일시 429는 전체 차단하지 않음",
                  not _gm._is_permanent_quota("429 RESOURCE_EXHAUSTED rate limit")))
    ok.append(run("Gemini 결제 오류만 전체 차단",
                  _gm._is_permanent_quota("insufficient credit balance; check billing")))
    from types import SimpleNamespace as _NS
    _web = _NS(web=_NS(uri="https://example.com/a", title="근거"))
    _resp = _NS(candidates=[_NS(grounding_metadata=_NS(grounding_chunks=[_web, _web]))])
    ok.append(run("검색 근거 URL 추출·중복제거",
                  _gm._grounding_sources(_resp) ==
                  [{"title": "근거", "url": "https://example.com/a"}]))
    _usage_resp = _NS(
        text="ok", model_version="gemini-3.5-flash",
        usage_metadata=_NS(prompt_token_count=120, candidates_token_count=30,
                           thoughts_token_count=7, cached_content_token_count=20,
                           service_tier="STANDARD"),
        candidates=[_NS(grounding_metadata=_NS(
            grounding_chunks=[], web_search_queries=["검색1", "검색2"]))],
    )
    _usage_gr = _gm._response_result(
        _usage_resp, "gemini", "fallback", "paid", True, attempts=2)
    ok.append(run("Gemini 실제 token·검색 query usage 추출",
                  _usage_gr.input_tokens == 120
                  and _usage_gr.output_tokens == 30
                  and _usage_gr.thinking_tokens == 7
                  and _usage_gr.cache_read_tokens == 20
                  and _usage_gr.grounding_queries == 2
                  and _usage_gr.attempts == 2))
    from google.genai import types as _gtypes
    _gcfg_provider = _gm.GeminiProvider.__new__(_gm.GeminiProvider)
    _gcfg_provider._types = _gtypes
    _gcfg_provider.grounding = False
    _gcfg = _gcfg_provider._config("s", 0.4, 700, "gemini-3.5-flash")
    ok.append(run("Gemini 3.5 사고수준 minimal",
                  _gcfg.thinking_config.thinking_level == _gtypes.ThinkingLevel.MINIMAL))
    class _GeminiModels:
        def __init__(self, error=""):
            self.error = error
        def generate_content(self, **_kw):
            if self.error:
                raise RuntimeError(self.error)
            return _NS(text="ok", candidates=[])
    _gm._PAID_QUOTA_DISABLED.clear()
    _gm._FREE_QUOTA_DISABLED.clear()
    _gm._PAID_TRANSPORT_DISABLED.clear()
    _gm._FREE_TRANSPORT_DISABLED.clear()
    _gp = _gm.GeminiProvider.__new__(_gm.GeminiProvider)
    _gp.model, _gp.fallback_model, _gp.grounding = "paid", "free", False
    _gp._paid_client = _NS(models=_GeminiModels("prepayment credits are depleted"))
    _gp._free_client = _NS(models=_GeminiModels())
    _gp._types = None
    _gp._config = lambda *_a, **_k: None
    _old_interval = _cfg2.GEMINI_FREE_MIN_INTERVAL
    _cfg2.GEMINI_FREE_MIN_INTERVAL = 0
    _gr = _gp.generate("s", "u")
    ok.append(run("Gemini 유료 소진 시 무료 프로젝트 폴백",
                  _gr.ok and _gr.model == "free"
                  and _gm._PAID_QUOTA_DISABLED.is_set()))
    _cfg2.GEMINI_FREE_MIN_INTERVAL = _old_interval
    _gm._PAID_QUOTA_DISABLED.clear()
    _gm._FREE_QUOTA_DISABLED.clear()
    _gm._PAID_TRANSPORT_DISABLED.clear()
    _gm._FREE_TRANSPORT_DISABLED.clear()

    _gp2 = _gm.GeminiProvider.__new__(_gm.GeminiProvider)
    _gp2.model, _gp2.fallback_model, _gp2.grounding = "paid", "free", False
    _gp2._paid_client = _NS(models=_GeminiModels("ReadTimeout: timed out"))
    _gp2._free_client = _NS(models=_GeminiModels())
    _gp2._types = None
    _gp2._config = lambda *_a, **_k: None
    _gr2 = _gp2.generate("s", "u")
    ok.append(run("Gemini 시간초과 시 회로 차단·무료 폴백",
                  _gr2.ok and _gr2.model == "free"
                  and _gm._PAID_TRANSPORT_DISABLED.is_set()))
    _gm._PAID_TRANSPORT_DISABLED.clear()
    _gm._FREE_TRANSPORT_DISABLED.clear()

    import tempfile as _tmp, json as _json
    from src import enrich as _en
    from src.llm.base import GenResult as _GR
    _old_path, _old_enricher = _en.CACHE_PATH, _en.enricher
    with _tmp.TemporaryDirectory() as _td:
        _en.CACHE_PATH = _td + "/cache.json"

        _now = __import__("time").time()
        with open(_en.CACHE_PATH, "w", encoding="utf-8") as _f:
            _json.dump({
                "ok-live": {"ts": _now - 2 * 86400, "status": "ok",
                            "text": "확인된 배경 사실입니다.",
                            "sources": ["https://example.com/source"]},
                "none-old": {"ts": _now - 2 * 86400, "status": "none",
                             "text": "", "sources": []},
                "ok-old": {"ts": _now - 8 * 86400, "status": "ok",
                           "text": "오래된 사실입니다.",
                           "sources": ["https://example.com/old"]},
            }, _f, ensure_ascii=False)
        _live_cache = _en._load_cache()
        ok.append(run("보강 성공 7일·NONE 1일 TTL 분리",
                      "ok-live" in _live_cache
                      and "none-old" not in _live_cache
                      and "ok-old" not in _live_cache))
        _cached_item = {"id": "ok-live", "facts": "기존 사실"}
        _cached_counts = _en.apply_cached([_cached_item])
        ok.append(run("보강 캐시는 외부 호출 없이 선행 적용",
                      _cached_counts == {"ok": 1, "none": 0}
                      and _cached_item.get("enriched") is True
                      and "확인된 배경 사실입니다." in _cached_item["facts"]))

        class _BrokenEnricher:
            def available(self): return True
            def generate(self, *_a, **_k):
                return _GR("", "gemini", "fake", ok=False, error="timeout")

        _en.enricher = lambda: _BrokenEnricher()
        _en.enrich_all([{"id": "err", "facts": "x"}], workers=1)
        _saved = _json.load(open(_en.CACHE_PATH, encoding="utf-8"))
        ok.append(run("보강 오류는 NONE으로 캐시하지 않음", "err" not in _saved))

        _probe_calls = [0]
        class _ProbeBroken:
            def available(self): return True
            def generate(self, *_a, **_k):
                _probe_calls[0] += 1
                return _GR("검색 도구 없는 답", "gemini", "fake", sources=[])
        _en.enricher = lambda: _ProbeBroken()
        _probe_items = [{"id": f"probe-{i}", "facts": "x"} for i in range(7)]
        _probe_out = _en.enrich_all(_probe_items, workers=2)
        ok.append(run("보강 첫 묶음 전멸 시 후속 호출 차단",
                      _probe_calls[0] == 2
                      and all(x.get("thin_facts") for x in _probe_out)))

        class _NoneEnricher:
            def available(self): return True
            def generate(self, *_a, **_k): return _GR("NONE", "gemini", "fake")

        _en.enricher = lambda: _NoneEnricher()
        _en.enrich_all([{"id": "none", "facts": "x"}], workers=1)
        _saved = _json.load(open(_en.CACHE_PATH, encoding="utf-8"))
        ok.append(run("확인된 배경 없음만 NONE 캐시", _saved["none"]["status"] == "none"))
    _en.CACHE_PATH, _en.enricher = _old_path, _old_enricher

    from src.llm import claude as _claude_mod
    from src.llm.claude import ClaudeProvider as _CP
    _claude_msg = _NS(
        content=[_NS(type="text", text="ok")], model="claude-haiku-4-5-20251001",
        usage=_NS(input_tokens=100, output_tokens=25,
                  cache_read_input_tokens=50, cache_creation_input_tokens=20,
                  service_tier="standard"),
    )
    _claude_gr = _claude_mod._message_result(
        _claude_msg, "claude", "fallback")
    ok.append(run("Claude 실제 cache 포함 token usage 추출",
                  _claude_gr.input_tokens == 170
                  and _claude_gr.output_tokens == 25
                  and _claude_gr.cache_read_tokens == 50
                  and _claude_gr.cache_write_tokens == 20))
    import threading as _th
    _cp = _CP("", "fake", use_batch=False)
    _barrier, _threads = _th.Barrier(2), set()
    def _parallel_generate(*_a, **_k):
        _threads.add(_th.get_ident())
        _barrier.wait(timeout=1)
        return _GR("ok", "claude", "fake")
    _cp.generate = _parallel_generate
    _cp.generate_many([("s", "u")] * 4)
    ok.append(run("Claude 동기 호출 제한 병렬화", len(_threads) >= 2))

    from src.llm import base as _usage_base
    _usage_base.reset_usage()
    _usage_base.record_usage(_GR(
        "ok", "claude", "claude-haiku-4-5-20251001",
        input_tokens=2000, output_tokens=1000,
        cache_read_tokens=500, cache_write_tokens=500, attempts=2), "write")
    _usage_base.record_usage(_GR(
        "ok", "gemini", "gemini-3.1-flash-lite",
        input_tokens=5000, output_tokens=500, tier="free"), "judge")
    _usage_base.record_usage(_GR(
        "", "other", "unknown", ok=False, attempts=0),
        "write", "provider_reallocation")
    _usage_sum = _usage_base.usage_summary(delivered=2)
    ok.append(run("역할·모델·티어별 호출 비용 계측",
                  _usage_sum["calls"] == 3
                  and _usage_sum["api_attempts"] == 3
                  and _usage_sum["unknown_cost_calls"] == 1
                  and _usage_sum["estimated_token_cost_usd"] == 0.006675
                  and _usage_sum["cost_per_delivered_usd"] == 0.003338
                  and "claude|claude-haiku-4-5-20251001|paid|write"
                  in _usage_sum["by_route"]))
    _usage_base.reset_usage()

    # Gemini 유료·무료 티어가 모두 막혀도 동일 Haiku 자기심사는 하지 않는다.
    # Sonnet 5는 temperature를 거부하므로 sampling parameter 없이 호출해야 한다.
    _sonnet = _CP.__new__(_CP)
    _sonnet.model, _sonnet._no_temp = "claude-sonnet-5", False
    _sent_kw = {}
    class _ClaudeMessages:
        def create(self, **kw):
            _sent_kw.update(kw)
            return _NS(content=[])
    _sonnet._client = _NS(messages=_ClaudeMessages())
    _sonnet._create("s", "u", 0.0, 300)
    ok.append(run("Claude Sonnet 심사는 temperature 없이 실행",
                  "temperature" not in _sent_kw
                  and _sent_kw.get("thinking") == {"type": "disabled"}))

    from src import judge as _judge2
    _old_judges, _old_cross = _judge2.judges, _judge2.cross_judge_for
    _valid_score = ('{"factual":5,"useful":4,"natural":4,"compliant":5,'
                    '"gain":4,"fit":3,"fatal":[],"reason":"정상"}')
    class _FakeJudge:
        def __init__(self, result): self.result = result
        def available(self): return True
        def generate(self, *_a, **_k): return self.result
    _judge_pool = {
        "gemini": _FakeJudge(_GR("", "gemini", "paid", ok=False,
                                         error="billing")),
        "claude_backup": _FakeJudge(_GR(_valid_score, "claude", "sonnet")),
    }
    _judge2.judges = lambda: _judge_pool
    _judge2.cross_judge_for = lambda _writer: "gemini"
    _judged = _judge2._one({"provider": "claude", "tone": "fact_note",
                            "facts": "종가: 1,000원", "body": "검증 가능한 본문입니다."})
    ok.append(run("Gemini 심사 실패 시 Sonnet 교차모델 재심사",
                  _judged.get("score") is not None
                  and _judged.get("judged_by") == "claude_backup"))
    _judge2.judges, _judge2.cross_judge_for = _old_judges, _old_cross

    from src.llm import router as _router2
    _old_router_judges = _router2.judges
    _router2.judges = lambda: {
        "claude": _FakeJudge(_GR(_valid_score, "claude", "haiku")),
        "gemini": _FakeJudge(_GR(_valid_score, "gemini", "flash")),
        "claude_backup": _FakeJudge(_GR(_valid_score, "claude", "sonnet")),
    }
    ok.append(run("Claude 작성물은 Sonnet 교차모델 우선 심사",
                  _router2.cross_judge_for("claude") == "claude_backup"))
    _router2.judges = _old_router_judges

    _g2._DEGRADED_WRITERS.clear()
    _g2._QUALITY_FALLBACKS.clear()
    ok.append(run("작성자 품질 0/10이면 후속 회로 차단",
                  _g2._record_writer_quality("gemini", 10, 0)
                  and "gemini" in _g2._DEGRADED_WRITERS))
    _g2._DEGRADED_WRITERS.clear()
    _g2._QUALITY_FALLBACKS.clear()

    # 정규식 탈락 직후 재작성하면 아직 안 쓴 원본 후보보다 두 번째 호출을 먼저 쓴다.
    _old_gen_parts = (_g2.pick_style, _g2.router.split_by_ratio,
                      _g2.router.writers, _g2._run, _g2.filters.check)
    _old_rejected = list(_g2.REJECTED)
    _g2.REJECTED.clear()
    _gen_calls = []
    class _AvailableWriter:
        def available(self): return True
    _g2.pick_style = lambda *_a, **_k: (
        "fact_note", "reaction", "fact_note", "fact_note")
    _g2.router.split_by_ratio = lambda items: {"claude": items}
    _g2.router.writers = lambda: {
        "claude": _AvailableWriter(), "gemini": _AvailableWriter()}
    def _fake_run(name, items, tones, fmts=None, angs=None, lens=None,
                  attempt_type="initial"):
        _gen_calls.append((attempt_type, name, len(items)))
        body = "bad" if attempt_type == "initial" else "good"
        return [{**it, "body": body, "provider": name, "model": "fake",
                 "tone": tones[i], "fmt": (fmts or ["fact_read"])[i],
                 "angle": (angs or [""])[i], "length": (lens or ["medium"])[i]}
                for i, it in enumerate(items)]
    _g2._run = _fake_run
    _g2.filters.check = lambda body, *_a, **_k: ["리젝"] if body == "bad" else []
    _fresh_item = {"id": "fresh-first", "kind": "disclosure", "facts": "수치: 1"}
    _first_pass = _g2.generate([_fresh_item], {})
    ok.append(run("정규식 리젝 즉시 재작성 금지",
                  not _first_pass
                  and [x[0] for x in _gen_calls] == ["initial"]
                  and len(_g2.REJECTED) == 1))
    _late_retry = _g2.retry_rejected()
    ok.append(run("원본 후보 소진 뒤 한 번만 재작성",
                  len(_late_retry) == 1
                  and _gen_calls[-1][:2] == ("filter_rewrite", "gemini")
                  and not _g2.retry_rejected()))
    (_g2.pick_style, _g2.router.split_by_ratio, _g2.router.writers,
     _g2._run, _g2.filters.check) = _old_gen_parts
    _g2.REJECTED[:] = _old_rejected

    # 슬롯을 키우면 기대치가 따라 올라 전 소스가 오탐 경보를 냈다
    # (실측: market 기대 857 vs 실제 62 — 수집이 아니라 기준이 망가진 것)
    from src import crawl as _cw
    _cw._health.clear()
    _cw.report("market", 62, 857)
    _cw.report("kind_inquiry", 2, 35)
    ok.append(run("정상 공급을 경보하지 않음",
                  not any(v["degraded"] for v in _cw.health().values())))
    _cw._health.clear()
    _cw.report("market", 5, 857)
    ok.append(run("진짜 수집 실패는 경보", _cw.health()["market"]["degraded"]))
    _cw._health.clear()

    # 방향 예외는 상승·하락 양쪽 대칭이어야 한다 (실측: 하락일 5건 오탐)
    from src.filters import _direction_errors as _de
    ok.append(run("하락일 5거래일 상승 언급 통과",
                  not _de("3.2% 하락했습니다. 5거래일 누적으로는 12.4% 올랐는데요.",
                          "등락률: -3.20%")))
    ok.append(run("상승일 5거래일 하락 언급 통과",
                  not _de("3.2% 상승했습니다. 5거래일 누적으로는 12.4% 내렸는데요.",
                          "등락률: 3.20%")))
    ok.append(run("진짜 방향오용은 잡음",
                  bool(_de("로보티즈가 올랐네요. 이 정도 낙폭이면 뭔가 있는데요.",
                           "등락률: 12.44%"))))
    _dated_facts = "기준일: 2026-09-11\n등락률: -4.57%\n종가: 75,200원"
    ok.append(run("기준일 있으면 오늘 표현 차단",
                  any("상대날짜(오늘)" in e for e in
                      _f2.check("제주반도체가 오늘 4.57% 내렸습니다. 종가는 75,200원입니다.",
                                _dated_facts, "brief_report", "reaction", "brief_report"))))
    ok.append(run("종가를 출발가처럼 쓴 문장 차단",
                  any("종가표현오류" in e for e in
                      _f2.check("라온시큐어가 9,380원에서 6.59% 올랐습니다.",
                                "종가: 9,380원\n등락률: 6.59%",
                                "brief_report", "reaction", "brief_report"))))
    ok.append(run("입력에 없는 기간 계산 차단",
                  any("기간계산근거없음" in e for e in
                      _f2.check("3월에 신청한 지 약 6개월 만에 승인됐습니다.",
                                "신청일: 2026-03-10\n승인일: 2026-09-11",
                                "brief_report", "duration", "brief_report"))))
    ok.append(run("연결어미로 끝난 글 차단",
                  any("연결어미" in e for e in
                      _f2.check("종가는 14,850원이었고 거래량도 늘었는데요.",
                                "종가: 14,850원", "brief_report", "reaction",
                                "brief_report"))))
    ok.append(run("명사형 인데요 마무리도 차단",
                  any("연결어미" in e for e in
                      _f2.check("지난 5거래일은 상승한 흐름인데요.",
                                "5거래일 누적 등락률: +16.09%",
                                "careful_note", "compare", "careful_note"))))

    from src import claims as _claims2
    _date_item = {"facts": "보도 시각: 2026-09-11 15:57 KST\n요지: 정부가 지원안을 발표했습니다.",
                  "angle": "context"}
    ok.append(run("근거 있는 날짜 메타데이터 숫자 허용",
                  not any("근거없는수치" in e for e in
                          _claims2.grounding_errors(
                              "정부가 9월 11일 지원안을 발표했습니다.", _date_item, 3))))
    ok.append(run("날짜와 무관한 미확인 숫자는 계속 차단",
                  any("근거없는수치" in e for e in
                      _claims2.grounding_errors(
                          "정부가 9월 11일 84% 지원안을 발표했습니다.", _date_item, 3))))

    _old_weighted = _g2._weighted
    _g2._weighted = lambda weights, _penalize: next(k for k, v in weights.items() if v > 0)
    _policy_style = _g2.pick_style({
        "id": "policy-rich", "kind": "policy", "stock_code": "",
        "facts": "요지: " + ("확정된 정책 내용을 구체적으로 설명합니다. " * 4),
    }, {}, set())
    _g2._weighted = _old_weighted
    ok.append(run("장문 정책 요지는 사실정리 페르소나 허용",
                  _policy_style[0] == "fact_note"))

    # flow 절대 상한: 2차 배분에서도 목표의 60% 를 넘지 못한다 (실측 46/50 재발 방지)
    _flow = [P(f"f{i}", code=f"{i:06d}", total=18, kind="flow") for i in range(60)]
    for i, _p in enumerate(_flow):
        # 말미중복 상한에 걸리지 않도록 마무리 20자를 전부 다르게 둔다
        _p["body"] = f"{i}번 종목 종가와 거래대금을 정리한 기록 {i:04d}-{i*7:05d} 입니다."
    sent, held = decide_distribution(_flow, target=50, per_stock=2)
    # 공급이 목표보다 적으면 3차 해제로도 채울 수 없고, 절대상한은 그대로 남는다
    sent_h, _ = decide_distribution(_flow[:20], target=40, per_stock=2,
                                    hard_kind_cap={"flow": 12})
    ok.append(run("공급이 목표보다 적으면 있는 만큼만 배포",
                  len(sent_h) == 20, f"{len(sent_h)}건"))
    # 3차: flow 밖에 없으면 절대상한을 풀어서라도 50건을 채운다 (사용자 최우선 조건)
    ok.append(run("목표 미달 시 절대상한 해제로 50건 확보", len(sent) == 50,
                  f"flow {sum(1 for x in sent if x['kind'] == 'flow')}건"))
    # 비-flow 공급이 있으면 절대상한이 지켜져야 한다
    _mix = [P(f"d{i}", code=f"1{i:05d}", total=19, kind="disclosure") for i in range(25)] + \
           [P(f"g{i}", code=f"2{i:05d}", total=17, kind="flow") for i in range(40)]
    for i, _p in enumerate(_mix):
        _p["body"] = f"{i}번 항목을 정리한 기록 {i:04d}-{i*11:05d} 입니다."
    sent2, _ = decide_distribution(_mix, target=50, per_stock=2)
    ok.append(run("비-flow 공급 충분하면 절대상한 유지",
                  sum(1 for x in sent2 if x["kind"] == "flow") <= 30,
                  f"flow {sum(1 for x in sent2 if x['kind'] == 'flow')}건 / 총 {len(sent2)}건"))

    from src import facts as _facts2
    _terms = [{"kind": "disclosure", "title": "삼성전자 전환사채 발행 결정", "facts": "발행 총액: 1,000억원"},
              {"kind": "flow", "title": "공매도 상위 종목", "facts": "등락률: +3.1%"}]
    _n_term = _facts2.annotate_terms(_terms)
    ok.append(run("공시에 용어 설명 주입", _n_term == 1 and "용어 설명:" in _terms[0]["facts"]))
    ok.append(run("특징주에는 용어 설명 미주입", "용어 설명:" not in _terms[1]["facts"]))
    ok.append(run("주입된 용어로 term_word 슬롯 성립",
                  "term_word" in _facts2.slots(_terms[0])))
    _facts2.annotate_terms(_terms)
    ok.append(run("용어 설명 중복 주입 없음",
                  _terms[0]["facts"].count("용어 설명:") == 1))

    print(f"\n{sum(ok)}/{len(ok)} passed")
    sys.exit(0 if all(ok) else 1)

if __name__ == "__main__":
    main()
