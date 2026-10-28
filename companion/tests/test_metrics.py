"""指标计算的单元测试。"""

from __future__ import annotations

import time
from datetime import datetime

from dsh_usage.config import Config, Price, SourceConfig, Thresholds
from dsh_usage.metrics import BalanceState, build
from dsh_usage.sources import CallEvent, extract_events, parse_time


def _cfg() -> Config:
    return Config(
        source=SourceConfig(kind="jsonl", path="/tmp"),
        pricing={"default": Price(0.10, 3.00, 9.00)},
        thresholds=Thresholds(cache_warn_permille=950, cache_bad_permille=800, error_streak=3),
    )


def _event(now: int, hit: int = 9000, miss: int = 1000, ok: bool = True) -> CallEvent:
    return CallEvent(
        at=now,
        model="deepseek-v4-flash",
        ok=ok,
        input_tokens=hit + miss,
        output_tokens=1000,
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
        latency_ms=1200,
    )


def test_cache_permille() -> None:
    assert _event(0, 9000, 1000).cache_permille == 900
    assert _event(0, 1000, 0).cache_permille == 1000
    assert CallEvent(at=0).cache_permille == 0


def test_build_today_totals() -> None:
    now = int(time.time())
    events = [_event(now - 60), _event(now - 120), _event(now - 180, ok=False)]
    snapshot = build(events, _cfg(), BalanceState(), now=now)
    assert snapshot["calls_today"] == 3
    assert snapshot["errors_today"] == 1
    assert snapshot["success_permille"] == 667
    assert snapshot["cache_hit_permille"] == 900
    assert snapshot["schema"] == 1


def test_cost_uses_cache_split() -> None:
    now = int(time.time())
    event = _event(now, hit=1_000_000, miss=0)
    event = CallEvent(
        at=now, model="deepseek-v4-flash", input_tokens=1_000_000,
        output_tokens=0, cache_hit_tokens=1_000_000, cache_miss_tokens=0,
    )
    snapshot = build([event], _cfg(), BalanceState(), now=now)
    # 1M 命中 token × 0.10 元/M = 0.10 元
    assert snapshot["cost_today_cny_micros"] == 100_000


def test_level_cache_low() -> None:
    now = int(time.time())
    snapshot = build([_event(now, 700, 300)], _cfg(), BalanceState(), now=now)
    assert snapshot["level"] == 2
    assert snapshot["level_reason"] == "cache_low"


def test_level_balance_empty() -> None:
    now = int(time.time())
    snapshot = build([_event(now)], _cfg(), BalanceState(ok=True, cny_micros=0), now=now)
    assert snapshot["level"] == 3
    assert snapshot["level_reason"] == "balance_empty"


def test_level_error_streak() -> None:
    now = int(time.time())
    events = [_event(now - i, ok=False) for i in range(3)]
    snapshot = build(events, _cfg(), BalanceState(), now=now)
    assert snapshot["level"] == 3
    assert snapshot["level_reason"] == "error_streak"


def test_spark_is_capped() -> None:
    now = int(time.time())
    events = [_event(now - i * 60) for i in range(50)]
    snapshot = build(events, _cfg(), BalanceState(), now=now)
    assert len(snapshot["spark"]) == 32


def test_extract_from_nested_payload() -> None:
    payload = {
        "type": "turn.end",
        "createdAt": "2026-10-28T10:00:00Z",
        "payload": {
            "model": "deepseek-v4-pro",
            "usage": {
                "prompt_tokens": 12000,
                "completion_tokens": 800,
                "prompt_cache_hit_tokens": 11000,
                "prompt_cache_miss_tokens": 1000,
            },
        },
    }
    events = extract_events(payload)
    assert len(events) == 1
    event = events[0]
    assert event.model == "deepseek-v4-pro"
    assert event.input_tokens == 12000
    assert event.output_tokens == 800
    assert event.cache_hit_tokens == 11000
    assert event.cache_permille == 917


def test_extract_ignores_empty_usage() -> None:
    payload = {"usage": {"input_tokens": 0, "output_tokens": 0}}
    assert extract_events(payload) == []


def test_parse_time_variants() -> None:
    assert parse_time(1793268000) == 1793268000
    assert parse_time(1793268000000) == 1793268000
    assert parse_time("1793268000") == 1793268000
    assert parse_time("2026-10-28T10:00:00Z") is not None
    assert parse_time("不是时间") is None
    assert parse_time(None) is None


def test_day_boundary_excludes_yesterday() -> None:
    now = int(datetime.now().timestamp())
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    events = [
        _event(int(today_start) - 3600),
        _event(int(today_start) + 60),
    ]
    snapshot = build(events, _cfg(), BalanceState(), now=now)
    assert snapshot["calls_today"] == 1
    assert snapshot["calls_7d"] == 2
