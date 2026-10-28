"""把事件列表折成一份快照，也就是协议里那份 JSON。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from . import SCHEMA_VERSION
from .config import Config
from .sources import CallEvent


@dataclass
class BalanceState:
    """余额是异步拉的，单独存一份状态。"""

    ok: bool = False
    cny_micros: int = 0
    at: int = 0


@dataclass
class Snapshot:
    data: dict = field(default_factory=dict)

    def __getitem__(self, key: str):
        return self.data[key]


def _day_start(now: datetime, days_back: int = 0) -> float:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp() - days_back * 86400


def _cost_micros(event: CallEvent, config: Config) -> int:
    price = config.price_for(event.model)
    hit = event.cache_hit_tokens
    miss = event.cache_miss_tokens
    if hit + miss == 0:
        miss = event.input_tokens
    cost = (
        hit / 1_000_000 * price.cache_hit_per_mtok
        + miss / 1_000_000 * price.cache_miss_per_mtok
        + event.output_tokens / 1_000_000 * price.output_per_mtok
    )
    return int(round(cost * 1_000_000))


def _summarise(events: list[CallEvent], config: Config) -> dict:
    calls = len(events)
    errors = sum(1 for e in events if not e.ok)
    hit = sum(e.cache_hit_tokens for e in events)
    miss = sum(e.cache_miss_tokens for e in events)
    split = hit + miss
    return {
        "calls": calls,
        "errors": errors,
        "success_permille": round((calls - errors) * 1000 / calls) if calls else 0,
        "input": sum(e.input_tokens for e in events),
        "output": sum(e.output_tokens for e in events),
        "cache_hit": hit,
        "cache_miss": miss,
        "cache_permille": round(hit * 1000 / split) if split else 0,
        "cost_micros": sum(_cost_micros(e, config) for e in events),
    }


def _error_streak(events: list[CallEvent]) -> int:
    streak = 0
    for event in reversed(events):
        if event.ok:
            break
        streak += 1
    return streak


def _decide_level(
    config: Config,
    cache_permille: int,
    balance: BalanceState,
    streak: int,
    has_data: bool,
) -> tuple[int, str]:
    """等级只在这里算一次，终端不重复判断。"""
    if balance.ok:
        if balance.cny_micros <= 0:
            return 3, "balance_empty"
        if balance.cny_micros < int(config.balance.warn_below_cny * 1_000_000):
            if streak >= config.thresholds.error_streak:
                return 3, "error_streak"
            return 2, "balance_low"
    if streak >= config.thresholds.error_streak:
        return 3, "error_streak"
    if not has_data:
        return 0, "no_data"
    if cache_permille and cache_permille < config.thresholds.cache_bad_permille:
        return 2, "cache_low"
    if cache_permille and cache_permille < config.thresholds.cache_warn_permille:
        return 1, "cache_watch"
    return 0, "ok"


def build(
    events: list[CallEvent],
    config: Config,
    balance: BalanceState | None = None,
    now: float | None = None,
) -> dict:
    """生成协议 v1 快照。"""
    balance = balance or BalanceState()
    moment = datetime.fromtimestamp(now if now is not None else time.time()).astimezone()
    now_ts = int(moment.timestamp())
    today_start = _day_start(moment)
    week_start = _day_start(moment, 6)

    today_events = [e for e in events if e.at >= today_start]
    week_events = [e for e in events if e.at >= week_start]

    today = _summarise(today_events, config)
    week = _summarise(week_events, config)

    last = events[-1] if events else None
    streak = _error_streak(events)
    level, reason = _decide_level(
        config,
        today["cache_permille"],
        balance,
        streak,
        has_data=bool(events),
    )

    return {
        "schema": SCHEMA_VERSION,
        "generated_at": now_ts,
        "utc_offset_minutes": int(moment.utcoffset().total_seconds() // 60),
        "source": config.source.kind,
        "stale_seconds": max(0, now_ts - last.at) if last else 0,
        "calls_today": today["calls"],
        "errors_today": today["errors"],
        "success_permille": today["success_permille"],
        "input_today": today["input"],
        "output_today": today["output"],
        "cache_hit_today": today["cache_hit"],
        "cache_miss_today": today["cache_miss"],
        "cache_hit_permille": today["cache_permille"],
        "cost_today_cny_micros": today["cost_micros"],
        "calls_7d": week["calls"],
        "input_7d": week["input"],
        "output_7d": week["output"],
        "cost_7d_cny_micros": week["cost_micros"],
        "last_at": last.at if last else 0,
        "last_model": last.model if last else "-",
        "last_ok": last.ok if last else True,
        "last_latency_ms": last.latency_ms if last else 0,
        "last_ttft_ms": last.ttft_ms if last else 0,
        "last_cache_hit_permille": last.cache_permille if last else 0,
        "last_cost_cny_micros": _cost_micros(last, config) if last else 0,
        "balance_ok": balance.ok,
        "balance_cny_micros": balance.cny_micros,
        "balance_at": balance.at,
        "spark": [e.cache_permille for e in events[-32:]],
        "error_streak": streak,
        "level": level,
        "level_reason": reason,
    }


def yuan(micros: int) -> str:
    """微元转成人看的字符串。"""
    return f"{micros / 1_000_000:.2f}"


def human_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)
