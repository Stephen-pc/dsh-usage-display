"""阈值推送：只在真的出事时才响。

不是每次调用都推一条——那样手腕会被震麻。只推三种：命中率跌破线、余额不够、
连续失败。同一个原因在冷却期内不重复推。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import Config

TIMEOUT_SECONDS = 6

_REASON_TEXT = {
    "cache_low": ("缓存命中率掉了", "命中率 {cache}‰，低于 {line}‰"),
    "balance_low": ("余额不多了", "还剩 ¥{balance}"),
    "balance_empty": ("余额用完了", "还剩 ¥{balance}"),
    "error_streak": ("连续失败", "已经连续失败 {streak} 次"),
}


def _compose(snapshot: dict, reason: str, config: Config) -> tuple[str, str]:
    title, template = _REASON_TEXT.get(reason, ("dsh 用量异常", reason))
    body = template.format(
        cache=snapshot.get("cache_hit_permille", 0),
        line=config.thresholds.cache_bad_permille,
        balance=snapshot.get("balance_cny_micros", 0) / 1_000_000,
        streak=snapshot.get("error_streak", 0),
    )
    tail = (
        f"今日 {snapshot.get('calls_today', 0)} 次调用 · "
        f"命中 {snapshot.get('cache_hit_permille', 0)}‰ · "
        f"花费 ¥{snapshot.get('cost_today_cny_micros', 0) / 1_000_000:.2f}"
    )
    return title, f"{body}\n{tail}"


def _send_bark(url: str, title: str, body: str) -> bool:
    base = url.rstrip("/")
    endpoint = f"{base}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}"
    request = urllib.request.Request(
        endpoint + "?group=dsh-usage&level=timeSensitive",
        headers={"User-Agent": "dsh-usage/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return False


def _send_ntfy(url: str, title: str, body: str) -> bool:
    payload = json.dumps({"title": title, "message": body, "priority": 4}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "dsh-usage/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return False


class Notifier:
    """带冷却的推送器。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._last_sent: dict[str, float] = {}

    def _cooled(self, reason: str) -> bool:
        last = self._last_sent.get(reason, 0.0)
        return (time.time() - last) >= self.config.notify.cooldown_seconds

    def maybe_notify(self, snapshot: dict) -> str | None:
        """按快照的等级决定要不要推，返回推送的原因。"""
        cfg = self.config.notify
        if not cfg.enabled:
            return None

        reason = str(snapshot.get("level_reason", ""))
        level = int(snapshot.get("level", 0))
        if level < 2 or reason not in _REASON_TEXT:
            return None
        if not self._cooled(reason):
            return None

        title, body = _compose(snapshot, reason, self.config)
        if cfg.provider == "ntfy" and cfg.ntfy_url:
            ok = _send_ntfy(cfg.ntfy_url, title, body)
        elif cfg.provider == "bark" and cfg.bark_url:
            ok = _send_bark(cfg.bark_url, title, body)
        else:
            return None

        if ok:
            self._last_sent[reason] = time.time()
            return reason
        return None
