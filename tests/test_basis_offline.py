"""고정된 실측 본문으로 비교 기준 보존을 검사한다. API 호출은 없다."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import filters  # noqa: E402


FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures/basis_regressions.json").read_text(encoding="utf-8"))
OPENING = FIXTURES["opening_start"]
OPENING_FACT = "시가 출발: 전일 종가 대비 8.2% 높게"


def full_check(body):
    return filters.check(body, OPENING["facts"], OPENING["fmt"], OPENING["angle"],
                         OPENING["length"], kind=OPENING["kind"],
                         stock_code=OPENING["stock_code"])


class BasisTests(unittest.TestCase):
    def assert_missing(self, body, facts=OPENING_FACT):
        self.assertTrue(any(e.startswith("비교기준누락")
                            for e in filters._basis_errors(body, facts)), body)

    def test_observed_opening_sentence(self):
        body = "전일 종가 대비 8.2% 높게 시작한 뒤 종가는 9,530원으로 마감했네요."
        self.assertEqual(filters._basis_errors(body, OPENING["facts"]), [])

    def test_observed_full_draft_keeps_fabricated_volume_rejection(self):
        self.assertEqual(full_check(OPENING["body"]), ["근거없는수치['25']"])

    def test_correcting_volume_does_not_bypass_claim_cap(self):
        self.assertEqual(full_check(OPENING["body"].replace("25배", "17.6배")),
                         ["주장과다(6개/5)"])

    def test_grounded_five_claim_draft_passes_full_filter(self):
        self.assertEqual(full_check(OPENING["body"].replace(" 거래량은 20일 평균의 25배!", "")), [])

    def test_both_grounded_opening_directions(self):
        for direction in ("높게", "낮게"):
            for ending in ("시작한 뒤 마감했습니다.", "시작했습니다.", "시작했네요."):
                with self.subTest(direction=direction, ending=ending):
                    body = f"전일 종가 대비 8.2% {direction} {ending}"
                    facts = f"시가 출발: 전일 종가 대비 8.2% {direction}"
                    self.assertEqual(filters._basis_errors(body, facts), [])

    def test_opening_whitespace(self):
        self.assertEqual(filters._basis_errors(
            "전일종가 대비 8.2 % 높게 시작했습니다.", OPENING_FACT), [])

    def test_missing_comparator(self):
        self.assert_missing("8.2% 높게 시작했습니다.")

    def test_wrong_comparator(self):
        self.assert_missing("전주 종가 대비 8.2% 높게 시작했습니다.")
        self.assert_missing("장중 저가 대비 8.2% 높게 시작했습니다.")

    def test_unrelated_start(self):
        self.assert_missing("거래가 시작됐고 전일 종가 대비 8.2% 높았습니다.")
        self.assert_missing("전일 종가 대비 8.2% 높았고 거래를 시작했습니다.")

    def test_wrong_source_direction(self):
        self.assert_missing("전일 종가 대비 8.2% 낮게 시작했습니다.")

    def test_only_explicit_previous_close_source_is_eligible(self):
        self.assert_missing("전일 종가 대비 8.2% 높게 시작했습니다.",
                            "시가 출발: 전주 종가 대비 8.2% 높게")

    def test_only_opening_source_is_eligible(self):
        self.assert_missing("전일 종가 대비 8.2% 높게 시작했습니다.",
                            "시가 대비 마감: 8.2% 높은 수준")

    def test_each_numeric_occurrence_needs_its_own_opening_phrase(self):
        self.assert_missing("전일 종가 대비 8.2% 높게 시작한 뒤 8.2% 높아졌습니다.")
        self.assert_missing("8.2% 높았고 전일 종가 대비 8.2% 높게 시작했습니다.")

    def test_opening_phrase_cannot_cover_another_value(self):
        self.assert_missing("전일 종가 대비 8.2% 높게 시작한 뒤 4.8% 높게 마감했습니다.",
                            OPENING_FACT + "\n시가 대비 마감: 4.8% 높은 수준")

    def test_sentence_boundaries(self):
        for separator in (". ", "\n"):
            with self.subTest(separator=separator):
                self.assert_missing(f"전일 종가 대비{separator}8.2% 높게 시작했습니다.")
                self.assert_missing(f"전일 종가 대비 8.2% 높게{separator}시작했습니다.")

    def test_start_nouns_and_negation_are_not_opening_events(self):
        for suffix in ("시작가입니다.", "시작점입니다.", "시작하지 않았습니다.",
                       "시작한 것은 아닙니다.", "시작했는지는 알 수 없습니다."):
            with self.subTest(suffix=suffix):
                self.assert_missing(f"전일 종가 대비 8.2% 높게 {suffix}")

    def test_existing_opening_vocabulary(self):
        for body in ("시가는 전일 종가 대비 8.2% 높았습니다.",
                     "전일 종가 대비 8.2% 높게 출발했습니다."):
            self.assertEqual(filters._basis_errors(body, OPENING_FACT), [])

    def test_historical_missing_close_basis_remains_rejected(self):
        old = FIXTURES["missing_close_basis"]
        self.assert_missing(old["body"], old["facts"])
        fixed = old["body"].replace("종가는 4.8% 높은", "종가는 시가 대비 4.8% 높은")
        self.assertEqual(filters._basis_errors(fixed, old["facts"]), [])

    def test_other_comparators_attribution_and_dates_are_unchanged(self):
        facts = "기준일: 2026-10-06\n거래량: 20일 평균의 17.6배\n기관 순매수: 169억원"
        self.assert_missing("거래를 시작했고 거래량은 17.6배였습니다.", facts)
        self.assert_missing("169억원 순매수로 시작했습니다.", facts)
        self.assertEqual(filters._basis_errors("10월 7일 거래를 시작했습니다.", facts),
                         ["날짜불일치(10월 7일)"])


if __name__ == "__main__":
    unittest.main()
