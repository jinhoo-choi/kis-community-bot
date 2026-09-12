"""Google Gemini 프로바이더.

모델(2026년 기준):
  gemini-3.5-flash        : GA(2026-05-19). gemini-flash-latest 가 가리키는 모델
  gemini-3.1-flash-lite   : GA(2026-05-07). 속도·비용 최적화 → 심사(judge)용
  gemini-3-pro-preview    : 최고 추론 품질

참고: 2026년 6월부터 Interactions API 가 권장 인터페이스이나,
기존 generateContent 는 레거시로 분류되었을 뿐 계속 완전히 지원되므로
안정성을 위해 여기서는 generateContent(SDK)를 사용한다.
문서: https://ai.google.dev/gemini-api/docs/models
"""
import concurrent.futures as cf
import threading
import time

import config
from src.llm.base import Provider, GenResult

# 모델이 은퇴하면 단일 문자열은 그날 파이프라인을 죽인다.
# 404/not_found/deprecated 계열 오류에서만 다음 후보로 승격한다.
_RETIRED = ("not_found", "404", "deprecated", "does not exist",
            "is not supported", "NOT_FOUND", "unsupported")
_PERMANENT_QUOTA = ("billing", "prepayment", "credit balance", "insufficient credit",
                    "quota has been exhausted", "payment required")
_RETRYABLE_LIMIT = ("429", "resource_exhausted", "rate limit", "too many requests")
_PAID_QUOTA_DISABLED = threading.Event()
_FREE_QUOTA_DISABLED = threading.Event()
_PAID_TRANSPORT_DISABLED = threading.Event()
_FREE_TRANSPORT_DISABLED = threading.Event()
_FREE_RATE_LOCK = threading.Lock()
_FREE_NEXT_CALL = 0.0


def _grounding_sources(response) -> list[dict]:
    """SDK 응답의 Google 검색 근거 URL을 중복 없이 보존한다."""
    out, seen = [], set()
    for cand in getattr(response, "candidates", None) or []:
        meta = getattr(cand, "grounding_metadata", None)
        for chunk in getattr(meta, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", "") if web else ""
            if uri and uri not in seen:
                seen.add(uri)
                out.append({"title": getattr(web, "title", "") or "", "url": uri})
    return out[:10]


def _ival(obj, name: str) -> int:
    try:
        return int(getattr(obj, name, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _grounding_query_count(response) -> int:
    """한 요청이 실제로 만든 Google Search query 수를 합산한다."""
    return sum(len(getattr(getattr(cand, "grounding_metadata", None),
                           "web_search_queries", None) or [])
               for cand in (getattr(response, "candidates", None) or []))


def _response_result(response, provider: str, fallback_model: str, tier: str,
                     grounding: bool, attempts: int) -> GenResult:
    usage = getattr(response, "usage_metadata", None)
    model = str(getattr(response, "model_version", "") or fallback_model)
    return GenResult(
        (response.text or "").strip(), provider, model,
        ok=bool(response.text),
        sources=_grounding_sources(response) if grounding else [],
        input_tokens=_ival(usage, "prompt_token_count"),
        output_tokens=_ival(usage, "candidates_token_count"),
        thinking_tokens=_ival(usage, "thoughts_token_count"),
        cache_read_tokens=_ival(usage, "cached_content_token_count"),
        grounding_queries=_grounding_query_count(response) if grounding else 0,
        tier=tier,
        service_tier=str(getattr(usage, "service_tier", "standard") or "standard"),
        attempts=attempts,
    )


def _is_permanent_quota(message: str) -> bool:
    low = message.lower()
    return any(k in low for k in _PERMANENT_QUOTA)


def _is_transport_failure(message: str) -> bool:
    """이번 실행에서 같은 엔드포인트를 계속 기다려도 의미 없는 장애."""
    low = message.lower()
    return any(k in low for k in (
        "timeout", "timed out", "readtimeout", "connecttimeout",
        "connection reset", "connection error", "service unavailable",
        "502", "503", "504",
    ))


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key: str, model: str, grounding: bool = False,
                 fallback_api_key: str = "", fallback_model: str = ""):
        self.model = model
        self.fallback_model = fallback_model or model
        self.grounding = grounding
        self._paid_client = None
        self._free_client = None
        self._types = None
        if api_key or fallback_api_key:
            from google import genai
            from google.genai import types
            self._types = types
            # SDK 기본값은 요청 제한시간이 없다. 실제 운영에서 검색 그라운딩
            # 호출이 55분 넘게 열린 채 남아 Actions 60분 제한을 소진했다.
            http_options = types.HttpOptions(
                timeout=int(config.GEMINI_HTTP_TIMEOUT_SEC * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            )
            if api_key:
                self._paid_client = genai.Client(
                    api_key=api_key, http_options=http_options)
            # 무료 티어는 Google 검색 그라운딩을 지원하지 않는다.
            if fallback_api_key and not grounding:
                self._free_client = genai.Client(
                    api_key=fallback_api_key, http_options=http_options)

    def available(self) -> bool:
        return self._active() is not None

    def _active(self):
        if (self._paid_client is not None
                and not _PAID_QUOTA_DISABLED.is_set()
                and not _PAID_TRANSPORT_DISABLED.is_set()):
            return self._paid_client, self.model, "paid"
        if (self._free_client is not None
                and not _FREE_QUOTA_DISABLED.is_set()
                and not _FREE_TRANSPORT_DISABLED.is_set()):
            return self._free_client, self.fallback_model, "free"
        return None

    @staticmethod
    def _wait_free_slot() -> None:
        """무료 프로젝트의 호출을 직렬화해 기본 12RPM 이하로 유지한다."""
        global _FREE_NEXT_CALL
        with _FREE_RATE_LOCK:
            now = time.monotonic()
            if _FREE_NEXT_CALL > now:
                time.sleep(_FREE_NEXT_CALL - now)
            _FREE_NEXT_CALL = time.monotonic() + config.GEMINI_FREE_MIN_INTERVAL

    def _promote(self, err: str) -> bool:
        """모델 은퇴로 보이면 다음 후보로 교체. 교체했으면 True."""
        if not any(k in err for k in _RETIRED):
            return False
        cands = config.GEMINI_CANDIDATES
        try:
            nxt = cands[cands.index(self.model) + 1]
        except (ValueError, IndexError):
            return False
        print(f"[{self.name}] 모델 은퇴 감지: {self.model} -> {nxt}")
        self.fallbacks = (self.fallbacks or []) + [f"{self.model}->{nxt}"]
        self.model = nxt
        return True

    def _config(self, system, temperature, max_tokens, model=""):
        t = self._types
        kw = dict(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        # Gemini 3 계열은 사고가 기본 ON이고 max_output_tokens가 사고 토큰까지
        # 포함한다. 운영 실측에서 700 토큰을 사고에 소진해 본문이 20~40자에서
        # 잘렸다. 3.5 Flash는 minimal, latest/그 외는 호환성이 넓은 low를 쓴다.
        level = (t.ThinkingLevel.MINIMAL if "3.5-flash" in (model or "")
                 else t.ThinkingLevel.LOW)
        kw["thinking_config"] = t.ThinkingConfig(thinking_level=level)
        if self.grounding:
            # Google 검색 그라운딩 — 사실 보강 단계에서만 켠다
            kw["tools"] = [t.Tool(google_search=t.GoogleSearch())]
        return t.GenerateContentConfig(**kw)

    def generate(self, system, user, temperature=1.0, max_tokens=700) -> GenResult:
        active = self._active()
        if active is None:
            return GenResult("", self.name, self.model, ok=False,
                             error="Gemini unavailable after quota/billing error",
                             attempts=0)
        client, active_model, tier = active
        for attempt in range(3):
            try:
                if tier == "free":
                    self._wait_free_slot()
                r = client.models.generate_content(
                    model=active_model,
                    contents=user,
                    config=self._config(system, temperature, max_tokens, active_model),
                )
                return _response_result(r, self.name, active_model, tier,
                                        self.grounding, attempt + 1)
            except Exception as e:
                msg = str(e)
                if tier == "paid" and self._promote(msg):
                    result = self.generate(system, user, temperature, max_tokens)
                    result.attempts += attempt + 1
                    return result
                if _is_permanent_quota(msg):
                    disabled = _FREE_QUOTA_DISABLED if tier == "free" else _PAID_QUOTA_DISABLED
                    if not disabled.is_set():
                        print(f"[gemini] {tier} billing/quota 오류 → 해당 티어 후속 호출 중단")
                    disabled.set()
                    fallback = self._active()
                    if tier == "paid" and fallback is not None:
                        note = f"{active_model}->{fallback[1]}(free)"
                        if note not in (self.fallbacks or []):
                            self.fallbacks = (self.fallbacks or []) + [note]
                        print(f"[gemini] 유료 티어 중단 → 무료 프로젝트 {fallback[1]} 폴백")
                        result = self.generate(system, user, temperature, max_tokens)
                        result.attempts += attempt + 1
                        return result
                    return GenResult("", self.name, active_model, ok=False,
                                     error=msg[:200], tier=tier,
                                     attempts=attempt + 1)
                if _is_transport_failure(msg):
                    disabled = (_FREE_TRANSPORT_DISABLED if tier == "free"
                                else _PAID_TRANSPORT_DISABLED)
                    if not disabled.is_set():
                        print(f"[gemini] {tier} 연결/시간초과 오류 → "
                              "이번 실행 후속 호출 중단")
                    disabled.set()
                    fallback = self._active()
                    if tier == "paid" and fallback is not None:
                        note = f"{active_model}->{fallback[1]}(free)"
                        if note not in (self.fallbacks or []):
                            self.fallbacks = (self.fallbacks or []) + [note]
                        print(f"[gemini] 유료 티어 연결 실패 → 무료 프로젝트 "
                              f"{fallback[1]} 폴백")
                        result = self.generate(system, user, temperature, max_tokens)
                        result.attempts += attempt + 1
                        return result
                    return GenResult("", self.name, active_model, ok=False,
                                     error=msg[:200], tier=tier,
                                     attempts=attempt + 1)
                retryable = any(k in msg.lower() for k in _RETRYABLE_LIMIT)
                if retryable and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return GenResult("", self.name, active_model, ok=False,
                                 error=msg[:200], tier=tier,
                                 attempts=attempt + 1)

    def generate_many(self, jobs, temperature=1.0, max_tokens=700,
                      workers: int = 6) -> list[GenResult]:
        """Gemini 는 배치 대신 소규모 병렬 호출. RPM 초과를 피해 workers 를 낮게 유지."""
        out = [None] * len(jobs)
        active = self._active()
        if active and active[2] == "free":
            workers = 1
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            fut = {
                ex.submit(self.generate, s, u, temperature, max_tokens): i
                for i, (s, u) in enumerate(jobs)
            }
            for f in cf.as_completed(fut):
                out[fut[f]] = f.result()
        return out
