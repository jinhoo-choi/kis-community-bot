"""E2E 시뮬레이션.

실제 API를 호출하지 않고 파이프라인 전 구간을 돌린다.
mock 은 '경계'에만 둔다 — 소스 fetch / LLM 프로바이더 / 텔레그램 전송.
그 사이의 gate, tickers, entity, dedup, generator, judge, decide 는
전부 프로덕션 코드가 그대로 실행된다.

시나리오는 실제로 터질 만한 상황을 재현한다.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from src import (tickers, entity, dedup, gate, generator, judge, decide,
                 enrich, telegram_bot, state, crawl)
from src.llm import router
from src.llm.base import GenResult

# ════════════════════════════════════════════════
# 가짜 상장 종목 테이블
# ════════════════════════════════════════════════
FAKE_LISTED = {
    "삼성전자": "005930", "삼성전자우": "005935", "SK하이닉스": "000660",
    "한미반도체": "042700", "한화에어로스페이스": "012450", "대상": "001680",
    "현대차": "005380", "카카오": "035720", "셀트리온": "068270",
    "한국금융지주": "071050", "두산에너빌리티": "034020", "LG에너지솔루션": "373220",
}

# ════════════════════════════════════════════════
# 시나리오 입력 — 각 항목이 어떤 함정을 겨냥하는지 표기
# ════════════════════════════════════════════════
def scenario_items():
    # 실제 파이프라인은 gate.has_substance() 를 통과한 항목만 생성 단계로 보낸다.
    # 픽스처에도 '말할 수 있는 사실'을 넣어야 실제와 같은 경로를 탄다.
    F = lambda t: (f"제목: {t}\n제시 적정가격: 33,000원 (해당 증권사 의견)\n"
                   f"투자의견: 매수 (해당 증권사 의견)\n※ 그 외 수치는 미제공.")
    return [
        # --- 정상 통과 기대 ---
        dict(id="d1", kind="disclosure", stock_code="005930", stock_name="삼성전자",
             title="단일판매·공급계약 체결", facts=F("삼성전자 공급계약"), src="u1"),
        dict(id="d2", kind="disclosure", stock_code="000660", stock_name="SK하이닉스",
             title="영업(잠정)실적 공시", facts=F("SK하이닉스 잠정실적"), src="u2"),
        dict(id="r1", kind="research", stock_code="042700", stock_name="한미반도체",
             title="한미반도체 HBM 장비 수주 점검", facts=F("한미반도체 리포트"), src="u3"),
        dict(id="f1", kind="flow", stock_code="012450", stock_name="한화에어로스페이스",
             title="한화에어로스페이스 전일 4.20% 상승",
             facts="기준일: 20260902\n종가: 812,000원\n등락률: 4.20%\n거래대금: 3,120억원",
             src="u4"),
        dict(id="p1", kind="policy", stock_code=None, stock_name=None,
             title="정부, 반도체 소부장 세제지원 확대 발표",
             facts=F("산업부 반도체 세제지원"), src="u5"),

        # --- [S2] 하드 게이트에서 차단되어야 함 ---
        dict(id="g1", kind="disclosure", stock_code="068270", stock_name="셀트리온",
             title="횡령·배임 혐의 발생", facts=F("횡령"), src="u6"),
        dict(id="g2", kind="disclosure", stock_code="071050", stock_name="한국금융지주",
             title="자기주식 취득 결정", facts=F("자사계열"), src="u7"),
        dict(id="g3", kind="disclosure", stock_code="005380", stock_name="현대차",
             title="[기재정정]주주총회소집결의", facts=F("정정"), src="u8"),
        dict(id="g4", kind="research", stock_code=None, stock_name=None,
             title="대선 관련 정치테마주 급등", facts=F("테마"), src="u9"),

        # --- [S3] 종목 오귀속 — 종목코드 없이 제목 매핑을 태움 ---
        dict(id="e1", kind="research", stock_code=None, stock_name=None,
             title="대상 기업 실적 점검 리포트", facts=F("모호명 단독"), src="u10"),
        dict(id="e2", kind="research", stock_code=None, stock_name=None,
             title="SK하이닉스 실적 전망 상향", facts=F("정상 매핑"), src="u11"),
        dict(id="e3", kind="research", stock_code=None, stock_name=None,
             title="반도체 밸류체인 점검 - 협력사 한미반도체 수혜 전망",
             facts=("공급망 한미반도체, 납품처 확대 수혜 예상\n"
                    "제시 적정가격: 210,000원 (해당 증권사 의견)"), src="u12"),

        # --- [S4] 중복 — d1과 같은 종목·같은 사건이 다른 소스로 ---
        dict(id="dup1", kind="research", stock_code="005930", stock_name="삼성전자",
             title="삼성전자 공급계약 체결 영향 점검", facts=F("중복"), src="u13"),

        # --- [S6] 종목 도배 — 삼성전자 3건 추가 ---
        dict(id="s1", kind="research", stock_code="005930", stock_name="삼성전자",
             title="삼성전자 파운드리 가동률 점검", facts=F("도배1"), src="u14"),
        dict(id="s2", kind="research", stock_code="005930", stock_name="삼성전자",
             title="삼성전자 배당정책 변경 검토", facts=F("도배2"), src="u15"),
        dict(id="s3", kind="flow", stock_code="005930", stock_name="삼성전자",
             title="삼성전자 전일 1.10% 하락",
             facts="기준일: 20260902\n종가: 78,500원\n등락률: -1.10%", src="u16"),

        # --- [S5] 생성물 컴플라이언스 위반 유도 ---
        dict(id="v1", kind="research", stock_code="034020", stock_name="두산에너빌리티",
             title="두산에너빌리티 원전 수주 점검", facts=F("위반유도-1인칭"), src="u17"),
        dict(id="v2", kind="research", stock_code="035720", stock_name="카카오",
             title="카카오 광고매출 회복 점검", facts=F("위반유도-매매권유"), src="u18"),
        dict(id="v3", kind="flow", stock_code="373220", stock_name="LG에너지솔루션",
             title="LG에너지솔루션 전일 2.30% 상승",
             facts="기준일: 20260902\n종가: 412,000원\n등락률: 2.30%", src="u19"),
    ]


# ════════════════════════════════════════════════
# 가짜 LLM 프로바이더
# ════════════════════════════════════════════════
BODIES = {
    # 위반 스크립트 — 정규식 필터가 잡아야 함
    "v1": "두산에너빌리티 원전 수주 소식이 나왔네요. 저는 이거 물렸는데 이번엔 좀 오르려나요. "
          "체코 건 이후로 분위기가 달라진 건 맞습니다. 다만 확정된 수치는 없습니다. 어떻게 보시나요.",
    "v2": "카카오 광고매출 회복 얘기가 나옵니다. 지금 비중확대 하세요. 목표가 6만원 갑니다. "
          "반드시 오릅니다. 지금이 기회입니다. 다들 어떻게 보시나요.",
    # 환각 수치 — facts 에 없는 숫자
    "v3": "LG에너지솔루션이 어제 2.30% 올랐습니다. 거래대금은 8,742억원이었고 외국인이 1,530억 순매수했습니다. "
          "수주잔고도 452조원까지 늘었다고 합니다. 배터리 업황이 돌아서는 걸까요.",
}


class FakeProvider:
    """결정론적 가짜 생성기. 프로덕션 인터페이스를 그대로 구현한다."""

    def __init__(self, name, model="fake-1", retire_on=None):
        self.name = name
        self.model = model
        self.fallbacks = []
        self._retire_on = retire_on or set()   # [S8] 모델 은퇴 시뮬레이션

    def available(self):
        return True

    def generate(self, system, user, temperature=1.0, max_tokens=700):
        iid = _extract_id(user)
        if iid in self._retire_on:
            self.fallbacks.append(f"{self.model}->fake-2")
            self.model = "fake-2"
            self._retire_on = set()

        if iid in BODIES:
            return GenResult(BODIES[iid], self.name, self.model)

        thin = "배경 정보 확보에 실패" in system
        body = (
            f"{_title(user)} 관련 내용이 공시로 나왔습니다.\n"
            f"자세한 수치는 원문에 나와 있지 않습니다. "
            + ("배경이 확인되지 않아 더 적기는 어렵네요. " if thin else
               "업황 흐름과 같이 보면 좋을 것 같습니다. ")
            + "짧게 짚고 넘어갑니다.\n"
              "무엇을 더 확인해봐야 할까요."
        )
        return GenResult(body, self.name, self.model)

    def generate_many(self, jobs, **kw):
        return [self.generate(s, u, **kw) for s, u in jobs]


class FakeJudge:
    """작성자와 다른 프로바이더가 채점하는 상황을 재현."""

    def __init__(self, name, low_ids=None):
        self.name = name
        self.model = "fake-judge"
        self.fallbacks = []
        self._low = low_ids or set()

    def available(self):
        return True

    def generate(self, system, user, **kw):
        iid = _extract_id(user)
        if iid in self._low:                      # [S5] 저품질 판정
            d = dict(factual=2, useful=2, natural=3, compliant=4, fatal=[], reason="내용 공허")
        elif "물렸" in user or "비중확대" in user:  # 정규식이 놓쳤을 경우 대비
            d = dict(factual=3, useful=3, natural=3, compliant=1,
                     fatal=["1인칭 거래경험"], reason="컴플라이언스 위반")
        else:
            d = dict(factual=5, useful=4, natural=4, compliant=5, fatal=[], reason="")
        return GenResult(json.dumps(d, ensure_ascii=False), self.name, self.model)

    def generate_many(self, jobs, **kw):
        return [self.generate(s, u, **kw) for s, u in jobs]


def _extract_id(user_prompt: str) -> str:
    for iid, t in _TITLE_BY_ID.items():
        if t and t in user_prompt:
            return iid
    return ""


def _title(user_prompt: str) -> str:
    for line in user_prompt.splitlines():
        if line.startswith("[제목]"):
            return line.replace("[제목]", "").strip()
    return "해당 항목"


_TITLE_BY_ID = {}
