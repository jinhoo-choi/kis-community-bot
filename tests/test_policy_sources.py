"""종료된 RSS 대체·공식 요약·날짜·뉴스 폴백 회귀. 외부 호출 없음."""
import html
import sys
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import KST  # noqa: E402
from src.sources import policy  # noqa: E402

NOW = datetime(2026, 10, 13, 22, 0, tzinfo=KST)
TITLE = "정부, 반도체 산업 지원 확대 방안 발표"
GIST = "정부는 반도체 산업의 연구개발 지원을 확대하는 방안을 발표했다. 지원 대상과 신청 절차는 보도자료에 기재된 범위에서 확인해야 한다."
LINK = "http://mofe.go.kr/nw/nes/detailNesDtaView.do?searchBbsId=MOSFBBS_000000000028&menuNo=4010100&searchNttId=MOSF_000000000079619"


def rss(items):
    return ("<rss><channel>" + "".join(
        "<item>" + "".join(f"<{k}>{escape(v)}</{k}>" for k, v in item.items())
        + "</item>" for item in items) + "</channel></rss>").encode()


def item(**changes):
    return {"title": TITLE, "link": LINK,
            "pubDate": "2026-10-13 15:32:05.0", **changes}


def response(content):
    return SimpleNamespace(content=content, raise_for_status=lambda: None)


def excerpt_page(text=GIST):
    return ("<html><nav>누리집 전체 메뉴</nav><input id='nttCn' value='"
            + html.escape("<p>" + text + "</p><p>※ 자세한 내용은 첨부파일을 확인</p>", quote=True)
            + "'></html>").encode()


class PolicySources(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.output = StringIO()
        self.stack.enter_context(redirect_stdout(self.output))
        fresh = policy._fresh_published_at
        self.stack.enter_context(patch.object(policy, "_fresh_published_at",
                                              side_effect=lambda raw: fresh(raw, NOW)))
        self.stack.enter_context(patch.object(policy.crawl, "sleep_jitter"))
        self.report = self.stack.enter_context(patch.object(policy.crawl, "report"))

    def read_official(self, data, page=None, budget=4):
        get = self.stack.enter_context(patch.object(policy.requests, "get", side_effect=[
            response(data), response(page or excerpt_page()),
        ]))
        out = []
        okay = policy._read("재정경제부", policy.OFFICIAL_FEEDS[0][1], out, 4, True,
                            official=True, detail_budget=[budget])
        return okay, out, get

    def test_live_schema_without_description_uses_official_summary(self):
        okay, out, get = self.read_official(rss([item()]))
        self.assertTrue(okay)
        self.assertEqual(len(out), 1)
        self.assertIn(GIST, out[0]["facts"])
        self.assertIn("공식 보도자료", out[0]["facts"])
        self.assertNotIn("누리집 전체 메뉴", out[0]["facts"])
        self.assertNotIn("첨부파일을 확인", out[0]["facts"])
        self.assertTrue(out[0]["src"].startswith("https://mofe.go.kr/"))
        self.assertNotIn("menuNo", get.call_args.args[0])

    def test_description_avoids_additional_http_request(self):
        _, out, get = self.read_official(rss([item(description=GIST)]))
        self.assertEqual(len(out), 1)
        get.assert_called_once()

    def test_missing_summary_does_not_invent_facts(self):
        _, out, _ = self.read_official(rss([item()]), "<html><main>일반 메뉴</main></html>".encode())
        self.assertEqual(out, [])
        self.assertIn("요지미확인 1", self.output.getvalue())

    def test_stale_and_undated_items_never_fetch_summary(self):
        data = rss([item(pubDate="2026-09-01 00:00:00.0"), item(pubDate="")])
        _, out, get = self.read_official(data)
        self.assertEqual(out, [])
        get.assert_called_once()

    def test_unrelated_title_never_fetches_summary(self):
        _, out, get = self.read_official(rss([item(title="오늘의 날씨 소식 안내")]))
        self.assertEqual(out, [])
        get.assert_called_once()

    def test_external_and_malformed_official_links_are_rejected(self):
        data = rss([item(link="https://example.invalid/view"),
                    item(link="https://mofe.go.kr:invalid/nw/nes/detailNesDtaView.do"),
                    item(link=LINK.replace("000000000028", "000000000032"))])
        _, out, get = self.read_official(data)
        self.assertEqual(out, [])
        get.assert_called_once()

    def test_summary_requests_share_a_bounded_budget(self):
        budget = [2]
        get = self.stack.enter_context(patch.object(policy.requests, "get", side_effect=[
            response(rss([item() for _ in range(6)])),
            response(b"<html/>"), response(b"<html/>"),
        ]))
        out = []
        policy._read("재정경제부", policy.OFFICIAL_FEEDS[0][1], out, 4, True,
                     official=True, detail_budget=budget)
        self.assertEqual(budget, [0])
        self.assertEqual(get.call_count, 3)
        self.assertEqual(out, [])

    def test_non_rss_response_is_a_source_failure(self):
        okay, out, _ = self.read_official(b"<html><body>error</body></html>")
        self.assertFalse(okay)
        self.assertEqual(out, [])

    def test_official_failure_falls_back_to_existing_news(self):
        def get(url, **kwargs):
            if url == policy.OFFICIAL_FEEDS[0][1]:
                raise policy.requests.HTTPError("404 fixture")
            return response(rss([item(description=GIST,
                                      link="https://www.yna.co.kr/view/fixture")]))
        self.stack.enter_context(patch.object(policy.requests, "get", side_effect=get))
        out = policy.fetch(limit=1)
        self.assertEqual(len(out), 1)
        self.assertIn("언론 보도", out[0]["facts"])
        self.assertIn("공식 0/1피드", self.output.getvalue())

    def test_official_and_news_sources_both_fit_total_limit(self):
        def get(url, **kwargs):
            if url == policy.OFFICIAL_FEEDS[0][1]:
                return response(rss([item(description=GIST) for _ in range(8)]))
            return response(rss([item(description=GIST,
                                      link="https://www.yna.co.kr/view/fixture")]))
        self.stack.enter_context(patch.object(policy.requests, "get", side_effect=get))
        out = policy.fetch(limit=5)
        self.assertEqual(len(out), 5)
        self.assertEqual(sum("공식 보도자료" in x["facts"] for x in out), 4)
        self.assertEqual(sum("언론 보도" in x["facts"] for x in out), 1)

    def test_retired_endpoints_are_never_requested(self):
        get = self.stack.enter_context(patch.object(policy.requests, "get",
                                                   return_value=response(rss([]))))
        policy.fetch()
        self.assertTrue(all("korea.kr/rss/" not in call.args[0]
                            for call in get.call_args_list))
        self.assertTrue(any("site:fsc.go.kr" in call.args[0] for call in get.call_args_list))
        self.assertTrue(any("site:motir.go.kr" in call.args[0] for call in get.call_args_list))

    def test_zero_limit_performs_no_network_requests(self):
        get = self.stack.enter_context(patch.object(policy.requests, "get"))
        self.assertEqual(policy.fetch(limit=0), [])
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
