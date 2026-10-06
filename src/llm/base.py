"""LLM 프로바이더 공통 인터페이스.

Claude / Gemini 를 동일 시그니처로 호출하기 위한 얇은 어댑터.
generator 는 이 인터페이스만 알고 있으면 되므로 프로바이더 교체·추가가 자유롭다.
"""
from collections import defaultdict
from dataclasses import dataclass, field
import json
import os
import threading
import time


@dataclass
class GenResult:
    text: str
    provider: str
    model: str
    ok: bool = True
    error: str = ""
    sources: list[dict] = field(default_factory=list)
    # 공급자 응답의 실제 usage 를 공통 단위로 정규화한다. input_tokens 는
    # cache read/write 를 포함한 전체 유효 입력이며, 비용 계산 때 각각 분리한다.
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    grounding_queries: int = 0
    tier: str = "paid"
    service_tier: str = "standard"
    billing_mode: str = "standard"
    attempts: int = 1
    # 원장 연결용 식별자. 공급자 message id, 배치 id/custom_id 를 그대로 남긴다.
    request_id: str = ""
    batch_id: str = ""
    custom_id: str = ""
    # estimated: 실제 usage 로 추정 / unconfirmed: 처리·과금 여부를 알 수 없음(0원 합산 금지)
    cost_status: str = "estimated"
    # 호출부가 정한 attempt_type 을 덮어쓸 때만 쓴다(예: 배치 실패분 동기 재시도).
    attempt_type: str = ""


class Provider:
    name = "base"

    def generate(self, system: str, user: str, temperature: float = 1.0,
                 max_tokens: int = 700) -> GenResult:
        raise NotImplementedError

    def generate_many(self, jobs: list[tuple[str, str]], **kw) -> list[GenResult]:
        """[(system, user), ...] → 결과 리스트. 기본은 순차 호출."""
        return [self.generate(s, u, **kw) for s, u in jobs]

    def available(self) -> bool:
        return False

    # 모델 은퇴 감지용. 폴백이 발생하면 여기에 기록되어 run_stats 로 넘어간다.
    fallbacks: list = None


# 2026-09-12 공급자 공식 가격표의 표준 API 단가(USD / MTok).
# 검색 그라운딩은 월간 공유 무료량을 이 프로세스만으로 알 수 없으므로 아래
# token 비용에 합산하지 않고 query 수만 별도로 남긴다.
# https://platform.claude.com/docs/en/about-claude/pricing
# https://ai.google.dev/gemini-api/docs/pricing
_PRICES = (
    ("claude", "claude-haiku-4-5", 1.00, 5.00, 0.10, 1.25),
    ("claude", "claude-sonnet-5", 2.00, 10.00, 0.20, 2.50),
    ("gemini", "gemini-3.5-flash-lite", 0.30, 2.50, 0.03, 0.0),
    ("gemini", "gemini-3.5-flash", 1.50, 9.00, 0.15, 0.0),
    ("gemini", "gemini-3.1-flash-lite", 0.25, 1.50, 0.025, 0.0),
)
_USAGE_EVENTS: list[dict] = []
_USAGE_SEEN: set = set()
_USAGE_LOCK = threading.Lock()
# 이전 실행에서 넘어와 아직 끝나지 않은 배치(batch_id → 요청 수). 비용 미확정.
PENDING_BATCHES: dict = {}
# 실행 식별자. Actions 에서는 run_id, 로컬은 시작 시각.
RUN_ID = [os.environ.get("GITHUB_RUN_ID") or f"local-{int(time.time())}"]


def reset_usage() -> None:
    """같은 프로세스에서 main()을 재실행해도 이전 실행 비용을 섞지 않는다."""
    with _USAGE_LOCK:
        _USAGE_EVENTS.clear()
        _USAGE_SEEN.clear()
        PENDING_BATCHES.clear()


def record_usage(result: GenResult, role: str,
                 attempt_type: str = "initial", job_id: str = "") -> bool:
    """호출 경계에서 결과 1건을 기록한다. 실패 호출도 calls/attempts에 포함한다.

    같은 응답(message id 또는 batch_id+custom_id)을 다시 기록하면 무시하고 False.
    배치 결과를 재조회·재시작 복구해도 비용이 한 번만 합산된다.
    """
    key = result.request_id or (
        f"{result.batch_id}/{result.custom_id}" if result.batch_id else "")
    with _USAGE_LOCK:
        if key and key in _USAGE_SEEN:
            return False
        if key:
            _USAGE_SEEN.add(key)
    event = {
        "run_id": RUN_ID[0],
        "job_id": job_id,
        "request_id": result.request_id,
        "batch_id": result.batch_id,
        "custom_id": result.custom_id,
        "cost_status": result.cost_status,
        "provider": result.provider,
        "model": result.model,
        "tier": result.tier,
        "service_tier": result.service_tier,
        "billing_mode": result.billing_mode,
        "role": role,
        "attempt_type": result.attempt_type or attempt_type,
        # 예열은 본문이 없는 것이 정상 응답이다. 실패로 세지 않는다.
        "ok": bool(result.ok and (result.text or role == "prewarm")),
        "calls": 1,
        "attempts": max(0, int(result.attempts)),
        "input_tokens": max(0, int(result.input_tokens or 0)),
        "output_tokens": max(0, int(result.output_tokens or 0)),
        "thinking_tokens": max(0, int(result.thinking_tokens or 0)),
        "cache_read_tokens": max(0, int(result.cache_read_tokens or 0)),
        "cache_write_tokens": max(0, int(result.cache_write_tokens or 0)),
        "grounding_queries": max(0, int(result.grounding_queries or 0)),
    }
    with _USAGE_LOCK:
        _USAGE_EVENTS.append(event)
    return True


def dump_events(path: str) -> str:
    """건별 원장(run/job/request/batch id 포함). Actions artifact 로 남긴다."""
    with _USAGE_LOCK:
        events = [dict(x) for x in _USAGE_EVENTS]
    for e in events:
        cost, known = _cost(e)
        e["estimated_cost_usd"] = (round(cost, 6) if known
                                   and e["cost_status"] == "estimated" else None)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=1)
    return path


def _rates(provider: str, model: str):
    normalized = (model or "").lower().removeprefix("models/")
    for row in _PRICES:
        if provider == row[0] and row[1] in normalized:
            return row[2:]
    return None


def _cost(event: dict) -> tuple[float, bool]:
    """(토큰 추정비용, 단가 인식 여부). 무료 티어는 명시적으로 0원이다."""
    if event["tier"] == "free":
        return 0.0, True
    rates = _rates(event["provider"], event["model"])
    if rates is None:
        return 0.0, False
    input_rate, output_rate, cache_read_rate, cache_write_rate = rates
    uncached = max(0, event["input_tokens"]
                   - event["cache_read_tokens"] - event["cache_write_tokens"])
    cost = (
        uncached * input_rate
        + event["cache_read_tokens"] * cache_read_rate
        + event["cache_write_tokens"] * cache_write_rate
        + (event["output_tokens"] + event["thinking_tokens"]) * output_rate
    ) / 1_000_000
    if event["billing_mode"] == "batch":
        cost *= 0.5
    # Claude 웹 검색은 토큰과 별도 $10/1,000회(배치도 동일 단가).
    if event["provider"] == "claude":
        cost += event["grounding_queries"] * 0.01
    return cost, True


def usage_summary(delivered: int = 0) -> dict:
    """provider/model/tier/role별 실호출·토큰·비용을 JSON 가능 형태로 집계한다."""
    with _USAGE_LOCK:
        events = [dict(x) for x in _USAGE_EVENTS]

    fields = ("calls", "attempts", "input_tokens", "output_tokens",
              "thinking_tokens", "cache_read_tokens", "cache_write_tokens",
              "grounding_queries")

    def blank():
        return {k: 0 for k in fields} | {
            "failed_calls": 0, "estimated_token_cost_usd": 0.0,
            "unknown_cost_calls": 0,
        }

    total = blank()
    total["uncached_input_tokens"] = 0
    total["unconfirmed_cost_calls"] = 0
    routes = defaultdict(blank)
    attempts = defaultdict(lambda: {"calls": 0, "api_attempts": 0,
                                    "estimated_token_cost_usd": 0.0})
    billing = defaultdict(lambda: {"calls": 0, "estimated_token_cost_usd": 0.0})
    for event in events:
        key = "|".join((event["provider"], event["model"], event["tier"],
                        event["role"]))
        bucket = routes[key]
        for field in fields:
            total[field] += event[field]
            bucket[field] += event[field]
        if not event["ok"]:
            total["failed_calls"] += 1
            bucket["failed_calls"] += 1
        total["uncached_input_tokens"] += max(
            0, event["input_tokens"] - event["cache_read_tokens"]
            - event["cache_write_tokens"])
        att = attempts[event["attempt_type"]]
        att["calls"] += 1
        att["api_attempts"] += event["attempts"]
        bill = billing[event["billing_mode"]]
        bill["calls"] += 1
        # 처리 여부를 모르는 요청은 0원으로 합산하지 않고 따로 센다.
        if event.get("cost_status") == "unconfirmed":
            total["unconfirmed_cost_calls"] += 1
            bucket.setdefault("unconfirmed_cost_calls", 0)
            bucket["unconfirmed_cost_calls"] += 1
            continue
        cost, known = _cost(event)
        if known:
            total["estimated_token_cost_usd"] += cost
            bucket["estimated_token_cost_usd"] += cost
            att["estimated_token_cost_usd"] += cost
            bill["estimated_token_cost_usd"] += cost
        else:
            total["unknown_cost_calls"] += 1
            bucket["unknown_cost_calls"] += 1

    for bucket in [total, *routes.values(), *attempts.values(),
                   *billing.values()]:
        bucket["estimated_token_cost_usd"] = round(
            bucket["estimated_token_cost_usd"], 6)
    total["api_attempts"] = total.pop("attempts")
    for bucket in routes.values():
        bucket["api_attempts"] = bucket.pop("attempts")
    total["cost_per_delivered_usd"] = (
        round(total["estimated_token_cost_usd"] / delivered, 6)
        if delivered else None)
    # Advisory only: crossing the target must never stop the 50-post goal.
    import config
    complete = (total["unconfirmed_cost_calls"] == 0
                and total["unknown_cost_calls"] == 0 and not PENDING_BATCHES)
    return {
        "cost_target_usd": config.API_COST_TARGET_USD,
        "cost_target_mode": "advisory",
        "cost_target_met": (total["estimated_token_cost_usd"] < config.API_COST_TARGET_USD
                            if complete else None),
        "pricing_as_of": "2026-09-12",
        "cost_scope": "token + claude web_search; gemini grounding overage/storage excluded",
        **total,
        # 사용량 기반 추정액이다. 실제 청구액은 콘솔·청구서로만 확인되며 여기서는 미확인.
        "cost_basis": "usage_estimate",
        "billed_cost_usd": None,
        "billing_check": "unverified",
        # 이전 실행에서 넘어와 아직 끝나지 않은 배치 요청 수(비용 미확정, 0원 아님)
        "pending_batch_requests": sum(PENDING_BATCHES.values()),
        "cost_complete": total["unconfirmed_cost_calls"] == 0
                         and total["unknown_cost_calls"] == 0
                         and not PENDING_BATCHES,
        "by_route": dict(sorted(routes.items())),
        "by_attempt_type": dict(sorted(attempts.items())),
        "by_billing_mode": dict(sorted(billing.items())),
    }
