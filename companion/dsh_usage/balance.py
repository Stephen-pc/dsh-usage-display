"""账户余额。这是唯一需要出网取的数据。

DeepSeek 的余额接口要 API Key，所以默认关闭，配好 key 才启用。
取不到就保持上一次的值，不会让看板变空。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .metrics import BalanceState

BALANCE_URL = "https://api.deepseek.com/user/balance"
TIMEOUT_SECONDS = 8


def _to_micros(value: object) -> int:
    try:
        return int(round(float(str(value)) * 1_000_000))
    except (TypeError, ValueError):
        return 0


def fetch(api_key: str, previous: BalanceState | None = None) -> BalanceState:
    """请求余额。失败时返回上一次的状态，只把 ok 置为 False。"""
    if not api_key:
        return previous or BalanceState(ok=False)

    request = urllib.request.Request(
        BALANCE_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "dsh-usage/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        if previous:
            return BalanceState(ok=False, cny_micros=previous.cny_micros, at=previous.at)
        return BalanceState(ok=False)

    infos = payload.get("balance_infos") or []
    chosen = None
    for info in infos:
        if str(info.get("currency", "")).upper() == "CNY":
            chosen = info
            break
    if chosen is None and infos:
        chosen = infos[0]

    if chosen is None:
        if previous:
            return BalanceState(ok=False, cny_micros=previous.cny_micros, at=previous.at)
        return BalanceState(ok=False)

    micros = _to_micros(chosen.get("total_balance", 0))
    available = bool(payload.get("is_available", True))
    return BalanceState(ok=available, cny_micros=micros, at=int(time.time()))
