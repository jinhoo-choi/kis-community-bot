"""중복 게시 방지 + 톤 반복 방지용 상태. 레포에 커밋되어 실행 간 유지된다."""
import json
import os
from datetime import datetime, timedelta

from config import STATE_PATH, KST

DEFAULT = {"seen": {}, "recent_tone": {}}


def load() -> dict:
    if not os.path.exists(STATE_PATH):
        return dict(DEFAULT)
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            s = json.load(f)
        return {**DEFAULT, **s}
    except Exception:
        return dict(DEFAULT)


def prune(s: dict, days: int = 7) -> dict:
    cut = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    s["seen"] = {k: v for k, v in s["seen"].items() if v >= cut}
    return s


def is_new(s: dict, item_id: str) -> bool:
    return item_id not in s["seen"]


def mark(s: dict, posts: list[dict]):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    for p in posts:
        s["seen"][p["id"]] = today
        key = p.get("stock_code") or "_theme"
        hist = s["recent_tone"].setdefault(key, [])
        hist.append(f"{p['tone']}:{p.get('angle', '')}:{p.get('fmt', '')}")
        s["recent_tone"][key] = hist[-3:]      # 최근 3개만 유지


def save(s: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
