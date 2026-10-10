"""공식 HTML 실측 fixture·RSS 중복·장애 폴백. 네트워크/LLM/발송 없음."""
import json
import pathlib
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from xml.sax.saxutils import escape

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import KST
from src import gate
from src.sources import policy

FIXTURE = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                      "policy_official_20261010.json").read_text())


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 9, 11, 30, tzinfo=KST)


def response(url, content):
    return SimpleNamespace(url=url, content=content.encode(), raise_for_status=lambda: None)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(policy, "datetime", Clock)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.rows = policy._motir_rows(FIXTURE["list_html"].encode())

    def get(self, url, **kwargs):
        self.assertEqual(kwargs["timeout"], 12)
        if url == policy.MOTIR_LIST:
            return response(url, FIXTURE["list_html"])
        if url in FIXTURE["details"]:
            return response(url, FIXTURE["details"][url])
        return response(url, "<rss><channel/></rss>")

    def collect(self, limit=8):
        out = []
        with patch.object(policy.requests, "get", side_effect=self.get) as request:
            policy._read_motir(out, limit)
        return out, request.call_count

    def test_actual_list_metadata(self):
        self.assertEqual(len(self.rows), 10)
        self.assertEqual(self.rows[0], (
            "북아프리카 핵심 거점 이집트와 산업·통상 협력 가속화", "2026-10-08",
            "https://www.motir.go.kr/kor/article/ATCL3f49a5a8c/172275/view"))
        self.assertTrue(all("?" not in u for _, _, u in self.rows))

    def test_actual_nested_body_and_publication_match(self):
        title, day, url = self.rows[0]
        body = policy._motir_detail(FIXTURE["details"][url].encode(), title, day)
        self.assertIn("양해각서(MOU)를 체결", body)
        self.assertIn("협상 개시를 공식 선언", body)
        self.assertFalse(body.startswith(title))

    def test_mismatch_or_empty_detail_rejected(self):
        title, day, url = self.rows[0]
        html = FIXTURE["details"][url].encode()
        for t, d, raw in [(title, "2026-10-09", html), ("다른 제목", day, html),
                          (title, day, b""), (title, day, b"<html>Access Denied</html>")]:
            with self.subTest(title=t, day=d):
                self.assertEqual(policy._motir_detail(raw, t, d), "")

    def test_wrong_board_external_and_ambiguous_date_rejected(self):
        html = FIXTURE["list_html"]
        for raw in [html.replace("/kor/article/ATCL3f49a5a8c/", "https://evil.test/"),
                    html.replace("ATCL3f49a5a8c", "other"),
                    html.replace("2026-10-08", "알 수 없음").replace("2026-10-07", "알 수 없음")
                    .replace("2026-10-06", "알 수 없음")]:
            self.assertEqual(policy._motir_rows(raw.encode()), [])

    def test_bounded_requests_and_substantive_output(self):
        out, calls = self.collect()
        self.assertEqual((len(out), calls), (3, 4))
        for item in out:
            self.assertTrue(gate.has_substance(item))
            self.assertIn("보도일: 2026-10-08", item["facts"])
            self.assertNotIn("00:00", item["facts"])
            self.assertLessEqual(len(item["facts"].split("요지: ")[1].splitlines()[0]), policy.GIST_MAX)

    def test_capacity_before_detail_requests(self):
        out, calls = self.collect(1)
        self.assertEqual((len(out), calls), (1, 2))
        with patch.object(policy.requests, "get") as request:
            with patch.object(policy.crawl, "report"):
                self.assertEqual(policy.fetch(0), [])
            request.assert_not_called()

    def test_stale_missing_future_date_no_detail_call(self):
        html = FIXTURE["list_html"].replace("2026-10-08", "2026-10-12")
        with patch.object(policy.requests, "get", return_value=response(policy.MOTIR_LIST, html)) as request:
            out = []
            policy._read_motir(out, 8)
        self.assertEqual(out, [])
        self.assertEqual(request.call_count, 1)

    def test_tomorrow_date_never_borrows_rss_clock_skew(self):
        class LateClock(Clock):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 9, 23, 30, tzinfo=KST)
        html = FIXTURE["list_html"].replace("2026-10-08", "2026-10-10")
        with patch.object(policy, "datetime", LateClock), \
                patch.object(policy.requests, "get", return_value=response(policy.MOTIR_LIST, html)) as request:
            out = []
            policy._read_motir(out, 8)
        self.assertEqual(out, [])
        self.assertEqual(request.call_count, 1)

    def test_empty_or_redirected_list_is_not_success(self):
        for url, html in [(policy.MOTIR_LIST, ""), ("https://other/", FIXTURE["list_html"])]:
            with patch.object(policy.requests, "get", return_value=response(url, html)) as request:
                out = []
                policy._read_motir(out, 8)
            self.assertEqual(out, [])
            self.assertEqual(request.call_count, 1)

    def test_title_duplicate_rss_does_not_use_slot(self):
        out, _ = self.collect()
        rss = ("<rss><channel><item><title>" + escape(out[0]["title"] + " - 정책브리핑") +
               "</title><pubDate>Thu, 08 Oct 2026 01:00:00 GMT</pubDate>"
               "<link>https://news.google.com/rss/articles/example</link>"
               "<description>정부는 조선산업 투자 지원 확대 방안을 발표했다.</description>"
               "</item></channel></rss>")
        with patch.object(policy.requests, "get", return_value=response(policy.GOOGLE_NEWS, rss)):
            self.assertTrue(policy._read("정책브리핑(구글뉴스)", policy.GOOGLE_NEWS, out, 8, True))
        self.assertEqual(len(out), 3)
        self.assertTrue(out[0]["src"].startswith("https://www.motir.go.kr/"))

    def test_similar_title_url_and_id_duplicates(self):
        out, _ = self.collect()
        self.assertFalse(policy._append_unique({**out[0], "id": "different", "src": "https://other/1"}, out))
        self.assertFalse(policy._append_unique({**out[0], "id": "different", "title": "다른 제목"}, out))
        self.assertFalse(policy._append_unique({**out[0], "id": "different", "src": "https://other/2",
                                               "title": out[0]["title"] + " 발표"}, out))
        self.assertTrue(policy._append_unique({**out[0], "id": "new", "src": "https://other/3",
                                              "title": "정부 반도체 세제지원 확대 발표"}, out))

    def test_detail_failure_redirect_and_empty_fail_closed(self):
        for mode in ("empty", "redirect", "error"):
            def get(url, **kwargs):
                if url == policy.MOTIR_LIST:
                    return self.get(url, **kwargs)
                if mode == "error":
                    raise policy.requests.Timeout("fixture")
                return response("https://other/" if mode == "redirect" else url, "")
            with self.subTest(mode=mode), patch.object(policy.requests, "get", side_effect=get) as request:
                out = []
                policy._read_motir(out, 8)
                self.assertEqual(out, [])
                self.assertEqual(request.call_count, 4)

    def test_official_failure_keeps_existing_rss_fallback(self):
        def get(url, **kwargs):
            if url == policy.MOTIR_LIST:
                raise policy.requests.Timeout("fixture")
            if url == policy.FEEDS[0][1]:
                return response(url, "<rss><item><title>정부 반도체 세제지원 확대 발표</title>"
                                "<pubDate>Thu, 08 Oct 2026 01:00:00 GMT</pubDate>"
                                "<link>https://www.yna.co.kr/view/example</link>"
                                "<description>정부는 반도체 투자 관련 세제 지원 범위 확대를 발표했다.</description>"
                                "</item></rss>")
            return self.get(url, **kwargs)
        with patch.object(policy.requests, "get", side_effect=get), \
                patch.object(policy.crawl, "sleep_jitter"), patch.object(policy.crawl, "report"):
            out = policy.fetch(8)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["src"].startswith("https://www.yna.co.kr/"))

    def test_discontinued_rss_never_called(self):
        with patch.object(policy.requests, "get", side_effect=self.get) as request, \
                patch.object(policy.crawl, "sleep_jitter"), patch.object(policy.crawl, "report"):
            policy.fetch(8)
        self.assertFalse(any("korea.kr/rss/" in call.args[0] for call in request.call_args_list))
        self.assertEqual(policy.OPTIONAL_FEEDS, [("정책브리핑(구글뉴스)", policy.GOOGLE_NEWS)])


if __name__ == "__main__":
    unittest.main()
