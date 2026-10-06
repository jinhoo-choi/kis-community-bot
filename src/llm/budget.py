"""Atomic pre-request API cost reservations. USD estimates, never an invoice guarantee.

Reserve a cold-cache request plus its entire output limit before network submission.
Responses with missing usage or uncertain billing retain their reservation. The JSON
checkpoint is written before submission so same-run restarts cannot spend it twice.
"""
import json
import math
import os
import threading
from decimal import Decimal, ROUND_CEILING

from src.llm import base

_LOCK = threading.RLock()
_STATE = None
_PATH = None


class BudgetDenied(RuntimeError):
    pass


def _micros(value):
    return int((Decimal(str(value)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


def _save():
    if not _PATH:
        return
    os.makedirs(os.path.dirname(_PATH) or ".", exist_ok=True)
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_STATE, f, ensure_ascii=False, indent=1, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, _PATH)


def start(limit_usd=0.29, path=None, run_id=None):
    """Initialize a production run; None explicitly disables only offline harnesses."""
    global _STATE, _PATH
    with _LOCK:
        _PATH = path
        if limit_usd is None:
            _STATE = None
            return
        limit = _micros(limit_usd)
        if not 0 < limit <= 290000:
            raise ValueError("API budget must be greater than zero and at most $0.29")
        run_id = run_id or base.RUN_ID[0]
        old = None
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    old = json.load(f)
                required = {"run_id", "limit", "spent", "reserved", "next", "blocked",
                            "denied", "overruns"}
                if not isinstance(old, dict) or not required <= old.keys():
                    raise ValueError("invalid budget checkpoint schema")
                numbers = ("limit", "spent", "next", "denied", "overruns")
                if (any(not isinstance(old[k], int) or isinstance(old[k], bool)
                        or old[k] < 0 for k in numbers)
                        or not 0 < old["limit"] <= 290000
                        or not isinstance(old["run_id"], str) or not old["run_id"]
                        or not isinstance(old["blocked"], str)
                        or not isinstance(old.get("accounted", []), list)
                        or not all(isinstance(x, str) for x in old.get("accounted", []))
                        or not isinstance(old["reserved"], dict)
                        or any(not isinstance(k, str) or not isinstance(v, int)
                               or isinstance(v, bool) or v < 0
                               for k, v in old["reserved"].items())):
                    raise ValueError("invalid budget checkpoint fields")
            except Exception as exc:
                raise BudgetDenied("budget checkpoint unreadable") from exc
        _STATE = {"run_id": run_id, "limit": limit, "spent": 0, "reserved": {},
                  "next": 0, "blocked": "", "denied": 0, "overruns": 0,
                  "accounted": []}
        if old and old.get("run_id") == run_id:
            _STATE = old
            _STATE["limit"] = min(limit, int(old["limit"]))
            if old.get("reserved"):
                _STATE["blocked"] = "unresolved request from same-run restart"
        elif old and old.get("reserved"):
            # Preserve the evidence across every later run. Replacing it with an
            # empty reservation map would let the third run forget the uncertainty.
            _STATE["reserved"] = {f"legacy-{k}": v for k, v in old["reserved"].items()}
            _STATE["blocked"] = "unresolved request from previous run; reconcile first"
        if int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")) > 1:
            # Hosted reruns can start from an old checkout after a killed runner;
            # a local atomic file is not proof that all remote charges survived.
            _STATE["blocked"] = "workflow rerun requires cost reconciliation"
        _save()


def active():
    return _STATE is not None


def stopped():
    with _LOCK:
        return bool(_STATE and _STATE["blocked"])


def block(reason):
    with _LOCK:
        if _STATE is not None:
            _STATE["blocked"] = str(reason)
            _save()


def reserve(model, input_tokens, max_output_tokens, cache_multiplier=1.25):
    """Count endpoint estimates get max(10%, 256) padding; no cache-hit discount."""
    with _LOCK:
        if _STATE is None:
            return None
        rates = base._rates("claude", model)
        if not rates or model not in ("claude-haiku-4-5-20251001", "claude-sonnet-5"):
            block("unpriced or unapproved model")
            raise BudgetDenied(_STATE["blocked"])
        if not isinstance(input_tokens, int) or isinstance(input_tokens, bool) or input_tokens <= 0:
            block("invalid input token estimate")
            raise BudgetDenied(_STATE["blocked"])
        if not isinstance(max_output_tokens, int) or max_output_tokens < 0:
            block("invalid output token limit")
            raise BudgetDenied(_STATE["blocked"])
        padded = input_tokens + max(256, math.ceil(input_tokens * 0.1))
        # microdollars: MTok dollar rates × tokens; rounded upward once.
        amount = int((Decimal(padded) * Decimal(str(rates[0]))
                      * Decimal(str(max(1.25, cache_multiplier)))
                      + Decimal(max_output_tokens) * Decimal(str(rates[1])))
                     .to_integral_value(rounding=ROUND_CEILING))
        projected = _STATE["spent"] + sum(_STATE["reserved"].values()) + amount
        if _STATE["blocked"] or base.PENDING_BATCHES or projected > _STATE["limit"]:
            _STATE["denied"] += 1
            # Insufficient headroom with concurrent requests is a denial, not a
            # permanent stop: settlement can release room for the next stage.
            if base.PENDING_BATCHES:
                _STATE["blocked"] = "unresolved legacy batch"
            _save()
            raise BudgetDenied(_STATE["blocked"] or "insufficient API budget headroom")
        _STATE["next"] += 1
        token = str(_STATE["next"])
        _STATE["reserved"][token] = amount
        try:
            _save()
        except Exception:
            _STATE["blocked"] = "budget checkpoint write failed"
            raise BudgetDenied(_STATE["blocked"])
        return token


def settle(token, result):
    with _LOCK:
        if token is None or _STATE is None:
            return
        amount = _STATE["reserved"].get(token)
        if amount is None:
            return
        event = vars(result)
        cost, known = base._cost(event)
        if (not known or result.cost_status != "estimated" or result.input_tokens <= 0
                or result.service_tier != "standard"):
            block("response billing or usage unconfirmed")
            return
        actual = _micros(cost)
        _STATE["reserved"].pop(token)
        _STATE["spent"] += actual
        key = result.request_id or f"{result.batch_id}/{result.custom_id}"
        if key and key != "/":
            _STATE.setdefault("accounted", []).append(key)
        if actual > amount:
            _STATE["overruns"] += 1
            _STATE["blocked"] = "actual usage exceeded padded reservation"
        _save()


def account_recovered(result):
    """Reconciled prior-batch charges count before any new paid admission."""
    with _LOCK:
        if _STATE is None:
            return
        key = result.request_id or f"{result.batch_id}/{result.custom_id}"
        if key in _STATE.setdefault("accounted", []):
            return
        cost, known = base._cost(vars(result))
        if (not known or result.cost_status != "estimated" or result.input_tokens <= 0
                or result.service_tier != "standard"):
            _STATE["reserved"]["recovered-" + key] = _STATE["limit"]
            block("recovered batch usage unconfirmed")
            return
        _STATE["accounted"].append(key)
        _STATE["spent"] += _micros(cost)
        if _STATE["spent"] + sum(_STATE["reserved"].values()) > _STATE["limit"]:
            block("recovered liabilities exhaust API budget")
        _save()


def fail(token, known_unbilled=False):
    with _LOCK:
        if token is None or _STATE is None:
            return
        if known_unbilled:
            _STATE["reserved"].pop(token, None)
        else:
            _STATE["blocked"] = "request outcome unconfirmed"
        _save()


def summary():
    with _LOCK:
        if _STATE is None:
            return {"enabled": False}
        reserved = sum(_STATE["reserved"].values())
        return {"enabled": True, "limit_usd": _STATE["limit"] / 1e6,
                "estimated_settled_usd": _STATE["spent"] / 1e6,
                "outstanding_reserved_usd": reserved / 1e6,
                "projected_total_usd": (_STATE["spent"] + reserved) / 1e6,
                "denied_requests": _STATE["denied"], "overruns": _STATE["overruns"],
                "stop_reason": _STATE["blocked"], "invoice_verified": False}
