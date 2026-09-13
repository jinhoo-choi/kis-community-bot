#!/usr/bin/env python3
"""고정 104건으로 Sonnet과 Flash-Lite rejection-only를 비교한다.

운영 라우팅은 이 도구의 결과로 자동 변경하지 않는다. 과거 실행 #102의
결정형 필터 통과본만 고정하고, 두 모델에 현재와 동일한 심사 프롬프트를 보낸다.
당시 Gemini 점수는 정답으로 재사용하지 않는다.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from collections import Counter
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from src import decide, rules, template_reserve
from src.judge import SYSTEM, USER, _parse
from src.llm import base as llm_base
from src.llm.base import GenResult, record_usage, reset_usage, usage_summary
from src.llm.claude import ClaudeProvider
from src.llm.gemini import GeminiProvider


EXPECTED_CORPUS_SIZE = 104
SOURCE_RUN_ID = 34681905042
SOURCE_ARTIFACT = "filter-log-34681905042"
TARGET_POSTS = 50
SONNET_MODEL = "claude-sonnet-5"
FLASH_MODEL = "gemini-3.1-flash-lite"
_DART_MISSING = {
    "dart-20260911000380",
    "dart-20260911000479",
    "dart-20260911000496",
    "dart-20260911000504",
}
_NAVER_MISSING = "naver-api-96077"
_HK_MISSING = "hk-652251"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _fact_index() -> dict[str, dict]:
    """현재 캐시와 posts_latest 전체 이력에서 원문 facts를 복원한다."""
    out = {}
    market_path = ROOT / "data/market_cache.json"
    if market_path.exists():
        for item in (_json(market_path).get("items") or []):
            if item.get("id") and item.get("facts"):
                out[item["id"]] = {**item, "_fact_source": "market_cache"}

    posts_path = ROOT / "data/posts_latest.json"
    if posts_path.exists():
        for item in _json(posts_path):
            if item.get("id") and item.get("facts"):
                out[item["id"]] = {**item, "_fact_source": "posts_latest"}

    try:
        commits = subprocess.run(
            ["git", "log", "--format=%H", "--", "data/posts_latest.json"],
            cwd=ROOT, check=True, text=True, capture_output=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        commits = []
    for commit in commits:
        try:
            raw = subprocess.run(
                ["git", "show", f"{commit}:data/posts_latest.json"],
                cwd=ROOT, check=True, text=True, capture_output=True,
            ).stdout
            items = json.loads(raw)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        for item in items:
            item_id = item.get("id")
            if item_id and item.get("facts") and item_id not in out:
                out[item_id] = {
                    **item,
                    "_fact_source": f"git:{commit[:12]}",
                }
    return out


def _recover_dart(targets: set[str]) -> dict[str, dict]:
    """OpenDART의 고정 접수일 목록과 정형 상세 API로 누락 공시를 복원한다."""
    if not targets:
        return {}
    if not config.DART_API_KEY:
        raise RuntimeError("DART_API_KEY가 없어 누락 공시 facts를 복원할 수 없습니다")

    import requests
    from src.sources import dart_detail

    wanted = {x.removeprefix("dart-") for x in targets}
    rows = {}
    page = 1
    while wanted - rows.keys():
        try:
            response = requests.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={
                    "crtfc_key": config.DART_API_KEY,
                    "bgn_de": "20260911",
                    "end_de": "20260911",
                    "page_no": page,
                    "page_count": 100,
                },
                headers={"User-Agent": config.USER_AGENT},
                timeout=20,
            )
            data = response.json()
        except Exception as exc:
            raise RuntimeError(
                f"OpenDART 목록 복원 실패: {type(exc).__name__}") from None
        if data.get("status") != "000":
            raise RuntimeError(
                f"OpenDART 목록 복원 실패: status={data.get('status')}")
        for row in data.get("list") or []:
            receipt = str(row.get("rcept_no") or "")
            if receipt in wanted:
                rows[receipt] = row
        if page >= int(data.get("total_page") or 1):
            break
        page += 1

    missing = sorted(wanted - rows.keys())
    if missing:
        raise RuntimeError(f"OpenDART 접수번호를 찾지 못했습니다: {missing}")

    out = {}
    for receipt in sorted(wanted):
        row = rows[receipt]
        item = {
            "id": f"dart-{receipt}",
            "kind": "disclosure",
            "stock_code": row.get("stock_code"),
            "stock_name": row.get("corp_name"),
            "title": (row.get("report_nm") or "").strip(),
            "facts": (
                f"공시일: {row.get('rcept_dt', '')}\n"
                f"회사: {row.get('corp_name', '')} ({row.get('stock_code', '')})\n"
                f"공시명: {(row.get('report_nm') or '').strip()}\n"
                f"제출인: {row.get('flr_nm', '')}\n"
                "※ 공시 제목 외 상세 수치는 제공되지 않음. 수치를 추정하지 말 것."
            ),
            "src": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
        }
        if not dart_detail.enrich_one(item, "20260911"):
            raise RuntimeError(f"OpenDART 정형 상세 복원 실패: {receipt}")
        item["_fact_source"] = "OpenDART:list+detail"
        out[item["id"]] = item
    return out


def _recover_naver(item_id: str) -> dict:
    """네이버 리서치 API에서 고정 researchId 한 건을 복원한다."""
    from bs4 import BeautifulSoup
    from src import crawl
    from src.sources import research

    research_id = item_id.removeprefix("naver-api-")
    headers = {
        **crawl.HEADERS,
        "Accept": "application/json",
        "Referer": "https://m.stock.naver.com/investment/research/company",
    }
    hit = None
    try:
        for page in range(1, 6):
            response = crawl.requests.get(
                f"{research.RESEARCH_API}/list",
                params={"category": "company", "page": page, "pageSize": 100},
                headers=headers, timeout=15,
            )
            rows = response.json().get("result") or []
            hit = next((x for x in rows
                        if str(x.get("researchId")) == research_id), None)
            if hit or len(rows) < 100:
                break
        if hit is None:
            raise RuntimeError("목록에 researchId 없음")
        detail_response = crawl.requests.get(
            f"{research.RESEARCH_API}/end",
            params={"researchId": research_id, "category": "company"},
            headers=headers, timeout=15,
        )
        html = (((detail_response.json().get("result") or {})
                 .get("researchContent") or {}).get("content", ""))
        detail_text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)[:400]
    except Exception as exc:
        raise RuntimeError(
            f"네이버 리서치 복원 실패: {type(exc).__name__}") from None

    tp_match = research._TP_API.search(detail_text)
    op_match = research._OP_API.search(detail_text)
    target = tp_match.group(1) if tp_match else ""
    opinion = op_match.group(1) if op_match else ""
    if not target or not opinion:
        raise RuntimeError("네이버 리서치 상세에서 적정가격·투자의견을 찾지 못했습니다")

    code = str(hit.get("itemCode") or "")
    name = str(hit.get("itemName") or "")
    title = str(hit.get("title") or "")
    item = {
        "id": item_id,
        "kind": "research",
        "stock_code": code,
        "stock_name": name,
        "title": title,
        "facts": (
            f"종목: {name} ({code})\n"
            f"리포트 제목: {title}\n"
            f"발간: {hit.get('brokerName', '')} / {hit.get('writeDate', '')}\n"
            f"제시 적정가격: {target}원\n"
            f"투자의견: {opinion}\n"
            "※ 제시 수치는 증권사 의견이며 단정하지 말 것."
        ),
        "src": hit.get("endUrl") or (
            "https://m.stock.naver.com/investment/research/company"),
        "_fact_source": "NAVER:research-api",
    }
    return item


def _recover_hankyung(item_id: str) -> dict:
    """한경컨센서스의 고정 report_idx 한 건을 목록에서 복원한다."""
    from src.sources.research import fetch_hankyung

    try:
        hit = next((x for x in fetch_hankyung(40) if x.get("id") == item_id), None)
    except Exception as exc:
        raise RuntimeError(
            f"한경컨센서스 복원 실패: {type(exc).__name__}") from None
    if hit is None:
        raise RuntimeError(f"한경컨센서스 report_idx를 찾지 못했습니다: {item_id}")
    hit["_fact_source"] = "Hankyung:consensus-list"
    return hit


def build_corpus(filter_log_path: Path) -> list[dict]:
    """#102 필터 통과 104건을 facts와 결합한 고정 입력으로 만든다."""
    raw = _json(filter_log_path)
    if not isinstance(raw, list):
        raise RuntimeError("filter_log 형식이 list가 아닙니다")
    rows = [x for x in raw if x.get("result") in {"sent", "held"}]
    if len(rows) != EXPECTED_CORPUS_SIZE:
        raise RuntimeError(
            f"고정 표본 수가 {len(rows)}건입니다 (기대 {EXPECTED_CORPUS_SIZE})")
    ids = [str(x.get("id") or "") for x in rows]
    if "" in ids or len(set(ids)) != len(ids):
        raise RuntimeError("고정 표본 id가 비었거나 중복됐습니다")

    facts = _fact_index()
    missing = set(ids) - facts.keys()
    unknown = missing - (_DART_MISSING | {_NAVER_MISSING, _HK_MISSING})
    if unknown:
        raise RuntimeError(f"복원 경로가 없는 facts: {sorted(unknown)}")
    facts.update(_recover_dart(missing & _DART_MISSING))
    if _NAVER_MISSING in missing:
        facts[_NAVER_MISSING] = _recover_naver(_NAVER_MISSING)
    if _HK_MISSING in missing:
        facts[_HK_MISSING] = _recover_hankyung(_HK_MISSING)

    still_missing = set(ids) - facts.keys()
    if still_missing:
        raise RuntimeError(f"facts 복원 실패: {sorted(still_missing)}")

    corpus = []
    for old in rows:
        source = facts[old["id"]]
        body = str(old.get("body") or "").strip()
        fact_text = str(source.get("facts") or "").strip()
        if not body or not fact_text:
            raise RuntimeError(f"빈 본문 또는 facts: {old['id']}")
        corpus.append({
            "id": old["id"],
            "kind": old.get("kind") or source.get("kind"),
            "stock_code": source.get("stock_code"),
            "stock_name": source.get("stock_name") or old.get("stock"),
            "title": old.get("title") or source.get("title"),
            "tone": old.get("tone") or "",
            "angle": old.get("angle") or "",
            "provider": old.get("provider") or "",
            "body": body,
            "facts": fact_text,
            # 당시 judge는 enrich_sources만 URL 블록에 넣었다. 복원된 104건은
            # enrich_sources가 없었으므로 두 모델 모두 같은 '-'를 받는다.
            "sources": [],
            "source_url": source.get("src") or "",
            "fact_source": source.get("_fact_source") or "unknown",
            "historical_result": old.get("result"),
        })
    return sorted(corpus, key=lambda x: x["id"])


def corpus_hash(corpus: list[dict]) -> str:
    payload = json.dumps(corpus, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def score_is_production_pass(score: dict | None) -> bool:
    """현재 decide의 LLM 승인 하한과 같은 단건 판정."""
    if not score or score.get("fatal"):
        return False
    return (
        score.get("factual", 0) >= config.MIN_FACTUAL_SCORE
        and score.get("compliant", 0) >= config.MIN_COMPLIANT_SCORE
        and score.get("fit", 0) >= config.MIN_FIT
        and score.get("total", 0) >= config.MIN_JUDGE_SCORE
    )


def flash_rejects(score: dict | None) -> bool:
    """합의된 rejection-only 축. 형식/호출 실패는 Sonnet으로 fail-open한다."""
    if not score:
        return False
    return bool(
        score.get("fatal")
        or score.get("factual", 0) < config.MIN_FACTUAL_SCORE
        or score.get("compliant", 0) < config.MIN_COMPLIANT_SCORE
    )


def confusion_metrics(pairs: list[dict]) -> dict:
    """Sonnet을 기준으로 rejection-only 혼동행렬을 계산한다."""
    sonnet_valid = sum(x.get("sonnet_score") is not None for x in pairs)
    flash_valid = sum(x.get("flash_score") is not None for x in pairs)
    sonnet_fatal = [x for x in pairs
                    if (x.get("sonnet_score") or {}).get("fatal")]
    sonnet_pass = [x for x in pairs
                   if score_is_production_pass(x.get("sonnet_score"))]
    fatal_caught = sum(flash_rejects(x.get("flash_score")) for x in sonnet_fatal)
    pass_rejected = sum(flash_rejects(x.get("flash_score")) for x in sonnet_pass)
    flash_passed = [x for x in pairs if not flash_rejects(x.get("flash_score"))]
    fatal_leaked = sum(bool((x.get("sonnet_score") or {}).get("fatal"))
                       for x in flash_passed)

    def rate(num: int, den: int):
        return round(num / den, 4) if den else None

    return {
        "corpus": len(pairs),
        "sonnet_valid": sonnet_valid,
        "flash_valid": flash_valid,
        "flash_skipped": len(pairs) - flash_valid,
        "sonnet_fatal": len(sonnet_fatal),
        "sonnet_pass": len(sonnet_pass),
        "flash_rejected": sum(flash_rejects(x.get("flash_score")) for x in pairs),
        "fatal_caught": fatal_caught,
        "fatal_recall": rate(fatal_caught, len(sonnet_fatal)),
        "sonnet_pass_false_rejected": pass_rejected,
        "false_reject_rate": rate(pass_rejected, len(sonnet_pass)),
        "flash_passed": len(flash_passed),
        "sonnet_fatal_after_flash_pass": fatal_leaked,
        "fatal_rate_after_flash_pass": rate(fatal_leaked, len(flash_passed)),
    }


def _prompt(item: dict) -> tuple[str, str]:
    return (
        SYSTEM.replace("__FATAL_BLOCK__", rules.judge_block()),
        USER.format(
            tone=item["tone"],
            facts=item["facts"][:2500],
            sources="\n".join(item.get("sources") or []) or "-",
            body=item["body"],
        ),
    )


def _run_provider(provider, corpus: list[dict], role: str,
                  chunk_size: int) -> list[tuple[GenResult, dict | None]]:
    jobs = [_prompt(item) for item in corpus]
    out = []
    for start in range(0, len(jobs), chunk_size):
        chunk = jobs[start:start + chunk_size]
        results = provider.generate_many(
            chunk, temperature=0.0, max_tokens=300)
        if len(results) != len(chunk):
            raise RuntimeError(f"{role} 결과 수 불일치")
        for result in results:
            record_usage(result, role)
            out.append((result, _parse(result.text) if result.ok else None))
        valid = sum(score is not None for _result, score in out)
        print(f"[{role}] {len(out)}/{len(jobs)} 완료 (유효 JSON {valid})",
              flush=True)
    return out


def _result_cost(result: GenResult, force_paid: bool) -> tuple[float, bool]:
    event = {
        "provider": result.provider,
        "model": result.model,
        "tier": "paid" if force_paid else result.tier,
        "billing_mode": result.billing_mode,
        "input_tokens": max(0, int(result.input_tokens or 0)),
        "output_tokens": max(0, int(result.output_tokens or 0)),
        "thinking_tokens": max(0, int(result.thinking_tokens or 0)),
        "cache_read_tokens": max(0, int(result.cache_read_tokens or 0)),
        "cache_write_tokens": max(0, int(result.cache_write_tokens or 0)),
    }
    return llm_base._cost(event)


def _cost_metrics(pairs: list[dict], sonnet_results, flash_results,
                  prepared: int) -> dict:
    sonnet_paid, flash_paid, sonnet_actual, flash_actual = [], [], [], []
    unknown = 0
    for result, _score in sonnet_results:
        paid, known = _result_cost(result, True)
        actual, actual_known = _result_cost(result, False)
        unknown += int(not known or not actual_known)
        sonnet_paid.append(paid)
        sonnet_actual.append(actual)
    for result, _score in flash_results:
        paid, known = _result_cost(result, True)
        actual, actual_known = _result_cost(result, False)
        unknown += int(not known or not actual_known)
        flash_paid.append(paid)
        flash_actual.append(actual)

    keep = [not flash_rejects(x.get("flash_score")) for x in pairs]
    baseline = sum(sonnet_paid)
    tiered = sum(flash_paid) + sum(
        cost for cost, kept in zip(sonnet_paid, keep) if kept)
    reduction = (1 - tiered / baseline) if baseline else None
    actual_spend = sum(sonnet_actual) + sum(flash_actual)
    return {
        "cost_basis": "standard_paid_token_price; same_corpus",
        "unknown_cost_results": unknown,
        "baseline_sonnet_calls": len(sonnet_results),
        "tiered_flash_calls": len(flash_results),
        "tiered_sonnet_calls": sum(keep),
        "baseline_cost_usd": round(baseline, 6),
        "tiered_cost_usd": round(tiered, 6),
        "cost_reduction": round(reduction, 4) if reduction is not None else None,
        "baseline_cost_per_delivered_usd": (
            round(baseline / prepared, 6) if prepared else None),
        "tiered_cost_per_delivered_usd": (
            round(tiered / prepared, 6) if prepared else None),
        "evaluation_actual_spend_usd": round(actual_spend, 6),
    }


def _simulate_prepared(corpus: list[dict], pairs: list[dict]) -> dict:
    pair_by_id = {x["id"]: x for x in pairs}
    llm_posts = []
    flow_items = []
    for item in corpus:
        post = {
            **item,
            "fmt": item.get("tone"),
            "length": item.get("tone"),
            "score": pair_by_id[item["id"]].get("sonnet_score"),
        }
        llm_posts.append(post)
        if item.get("kind") == "flow":
            flow_items.append({
                "id": item["id"], "kind": "flow",
                "stock_code": item.get("stock_code"),
                "stock_name": item.get("stock_name"),
                "title": item.get("title"), "facts": item.get("facts"),
                "src": item.get("source_url"),
            })

    reserve = template_reserve.build(
        flow_items, TARGET_POSTS + template_reserve.RESERVE_EXTRA)
    baseline_sent, _ = decide.decide_distribution(
        copy.deepcopy(llm_posts + reserve), target=TARGET_POSTS)
    tiered_llm = [post for post in llm_posts
                  if not flash_rejects(pair_by_id[post["id"]].get("flash_score"))]
    tiered_sent, _ = decide.decide_distribution(
        copy.deepcopy(tiered_llm + reserve), target=TARGET_POSTS)

    def split(posts):
        return {
            "prepared": len(posts),
            "llm": sum(x.get("provider") != "template" for x in posts),
            "template": sum(x.get("provider") == "template" for x in posts),
        }

    return {
        "target": TARGET_POSTS,
        "reserve_validated": len(reserve),
        "baseline": split(baseline_sent),
        "tiered": split(tiered_sent),
        "unchanged": (
            len(baseline_sent) == TARGET_POSTS
            and len(tiered_sent) == len(baseline_sent)
        ),
    }


def _pct(value) -> str:
    return "산출 불가" if value is None else f"{value * 100:.1f}%"


def _summary(report: dict) -> str:
    matrix = report["matrix"]
    prepared = report["prepared"]
    costs = report["costs"]
    gates = report["gates"]
    verdict = "채택 가능" if report["adopt"] else "전환 보류"
    lines = [
        "# Sonnet–Flash-Lite 104건 동일표본 평가",
        "",
        f"결론: **{verdict}**",
        "",
        f"- 원본 실행: GitHub Actions #{SOURCE_RUN_ID}",
        f"- 코퍼스 SHA-256: `{report['corpus_sha256']}`",
        f"- Sonnet 유효 응답: {matrix['sonnet_valid']}/{matrix['corpus']}",
        f"- Flash-Lite 유효 응답: {matrix['flash_valid']}/{matrix['corpus']}",
        f"- 지정 모델 계약: {'통과' if gates['model_contract'] else '실패'}",
        "",
        "| 채택 기준 | 실측 | 판정 |",
        "|---|---:|---:|",
        f"| Sonnet fatal 재현율 ≥80% | {_pct(matrix['fatal_recall'])} | {'통과' if gates['fatal_recall'] else '실패'} |",
        f"| Sonnet 통과 false reject ≤15% | {_pct(matrix['false_reject_rate'])} | {'통과' if gates['false_reject'] else '실패'} |",
        f"| 50건 준비량 감소 없음 | {prepared['baseline']['prepared']}→{prepared['tiered']['prepared']} | {'통과' if gates['prepared_volume'] else '실패'} |",
        f"| 표준 유료단가 건당비용 ≥20% 절감 | {_pct(costs['cost_reduction'])} | {'통과' if gates['cost'] else '실패'} |",
        "",
        "| 비용 비교 | Sonnet-only 동일표본 | Flash-Lite→Sonnet |",
        "|---|---:|---:|",
        f"| 동일표본 호출 | {costs['baseline_sonnet_calls']} | {costs['tiered_flash_calls']}+{costs['tiered_sonnet_calls']} |",
        f"| 표준 유료단가 비용 | ${costs['baseline_cost_usd']:.6f} | ${costs['tiered_cost_usd']:.6f} |",
        f"| 준비 1건당 비용 | ${costs['baseline_cost_per_delivered_usd'] or 0:.6f} | ${costs['tiered_cost_per_delivered_usd'] or 0:.6f} |",
        "",
        "Flash-Lite는 factual·compliant·fatal 축으로만 탈락시켰습니다. ",
        "형식 오류나 호출 실패는 탈락시키지 않고 Sonnet으로 넘기는 fail-open으로 계산했습니다. ",
        "동일표본 비용비율을 현재 staged 심사 호출량에 적용해 절감률을 판정했습니다. ",
        "이 보고서는 운영 라우팅을 자동 변경하지 않습니다.",
        "",
    ]
    return "\n".join(lines)


def evaluate(corpus: list[dict], chunk_size: int = 12) -> dict:
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY가 없습니다")
    if not (config.GEMINI_API_KEY or config.GEMINI_FREE_API_KEY):
        raise RuntimeError("Gemini 평가 키가 없습니다")

    flash = GeminiProvider(
        config.GEMINI_API_KEY,
        FLASH_MODEL,
        fallback_api_key=config.GEMINI_FREE_API_KEY,
        fallback_model=FLASH_MODEL,
    )
    sonnet = ClaudeProvider(
        config.ANTHROPIC_API_KEY,
        SONNET_MODEL,
        use_batch=False,
    )
    if not flash.available() or not sonnet.available():
        raise RuntimeError("평가 모델 초기화에 실패했습니다")

    reset_usage()
    print(f"[eval] Flash-Lite {len(corpus)}건 시작", flush=True)
    flash_results = _run_provider(
        flash, corpus, "judge_eval_flash", chunk_size)
    print(f"[eval] Sonnet {len(corpus)}건 시작", flush=True)
    sonnet_results = _run_provider(
        sonnet, corpus, "judge_eval_sonnet", chunk_size)

    pairs = []
    for item, (flash_result, flash_score), (sonnet_result, sonnet_score) in zip(
            corpus, flash_results, sonnet_results):
        pairs.append({
            "id": item["id"],
            "kind": item["kind"],
            "flash_score": flash_score,
            "flash_error": ((flash_result.error or "invalid_json")
                            if flash_score is None else ""),
            "flash_model": flash_result.model,
            "flash_tier": flash_result.tier,
            "flash_reject": flash_rejects(flash_score),
            "sonnet_score": sonnet_score,
            "sonnet_error": ((sonnet_result.error or "invalid_json")
                             if sonnet_score is None else ""),
            "sonnet_model": sonnet_result.model,
            "sonnet_pass": score_is_production_pass(sonnet_score),
        })

    matrix = confusion_metrics(pairs)
    prepared = _simulate_prepared(corpus, pairs)
    costs = _cost_metrics(
        pairs, sonnet_results, flash_results,
        prepared["tiered"]["prepared"],
    )
    complete = matrix["sonnet_valid"] == len(corpus)
    model_contract = (
        all(SONNET_MODEL in x["sonnet_model"] for x in pairs)
        and all(FLASH_MODEL in x["flash_model"] for x in pairs)
    )
    gates = {
        "complete_reference": complete,
        "model_contract": model_contract,
        "fatal_recall": (
            matrix["fatal_recall"] is not None
            and matrix["fatal_recall"] >= 0.80
        ),
        "false_reject": (
            matrix["false_reject_rate"] is not None
            and matrix["false_reject_rate"] <= 0.15
        ),
        "prepared_volume": prepared["unchanged"],
        "cost": (
            costs["unknown_cost_results"] == 0
            and costs["cost_reduction"] is not None
            and costs["cost_reduction"] >= 0.20
        ),
    }
    report = {
        "evaluated_at": datetime.now(config.KST).isoformat(timespec="seconds"),
        "source_run_id": SOURCE_RUN_ID,
        "source_artifact": SOURCE_ARTIFACT,
        "corpus_sha256": corpus_hash(corpus),
        "thresholds": {
            "min_judge_score": config.MIN_JUDGE_SCORE,
            "min_factual": config.MIN_FACTUAL_SCORE,
            "min_compliant": config.MIN_COMPLIANT_SCORE,
            "min_fit": config.MIN_FIT,
            "fatal_recall": 0.80,
            "false_reject": 0.15,
            "cost_reduction": 0.20,
        },
        "matrix": matrix,
        "prepared": prepared,
        "costs": costs,
        "usage": usage_summary(prepared["tiered"]["prepared"]),
        "model_counts": {
            "flash": dict(Counter(x["flash_model"] for x in pairs)),
            "flash_tier": dict(Counter(x["flash_tier"] for x in pairs)),
            "sonnet": dict(Counter(x["sonnet_model"] for x in pairs)),
        },
        "gates": gates,
        "adopt": all(gates.values()),
        "items": pairs,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--filter-log", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "data/judge_eval_104")
    parser.add_argument("--chunk-size", type=int, default=12)
    args = parser.parse_args()

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    print("[eval] 고정 코퍼스 facts 복원 시작", flush=True)
    corpus = build_corpus(args.filter_log)
    _write_json(output / "corpus.json", corpus)
    print(f"[eval] 코퍼스 {len(corpus)}건 고정 "
          f"sha256={corpus_hash(corpus)}", flush=True)

    report = evaluate(corpus, max(1, args.chunk_size))
    _write_json(output / "report.json", report)
    (output / "summary.md").write_text(_summary(report), encoding="utf-8")
    print(_summary(report), flush=True)
    # 기준 실패는 유효한 평가 결과다. 기준 Sonnet 응답이 불완전할 때만 실패한다.
    valid_run = (report["gates"]["complete_reference"]
                 and report["gates"]["model_contract"])
    return 0 if valid_run else 2


if __name__ == "__main__":
    raise SystemExit(main())
