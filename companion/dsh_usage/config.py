"""配置加载：TOML 文件打底，环境变量覆盖。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".dsh-usage" / "config.toml"

SAMPLE_CONFIG = """\
# dsh-usage 配置。放在 ~/.dsh-usage/config.toml，或启动时 --config 指定。
host = "0.0.0.0"
port = 8787
token = "change-me"

[source]
# dsh-jsonl: 直接读 dsh 会话日志；jsonl: 读任意 append-only 的 JSONL；stdin: 从标准输入收
kind = "dsh-jsonl"
path = "~/.dsh"

[balance]
enabled = true
api_key = ""
interval_seconds = 300
warn_below_cny = 5.0

[notify]
enabled = false
provider = "bark"
bark_url = ""
ntfy_url = ""
cooldown_seconds = 600

[thresholds]
cache_warn_permille = 950
cache_bad_permille = 800
error_streak = 3

[pricing.default]
cache_hit_per_mtok = 0.10
cache_miss_per_mtok = 3.00
output_per_mtok = 9.00
"""


@dataclass(frozen=True)
class Price:
    """每百万 token 的人民币单价。"""

    cache_hit_per_mtok: float = 0.10
    cache_miss_per_mtok: float = 3.00
    output_per_mtok: float = 9.00


@dataclass(frozen=True)
class SourceConfig:
    kind: str = "dsh-jsonl"
    path: str = "~/.dsh"


@dataclass(frozen=True)
class BalanceConfig:
    enabled: bool = True
    api_key: str = ""
    interval_seconds: int = 300
    warn_below_cny: float = 5.0


@dataclass(frozen=True)
class NotifyConfig:
    enabled: bool = False
    provider: str = "bark"
    bark_url: str = ""
    ntfy_url: str = ""
    cooldown_seconds: int = 600


@dataclass(frozen=True)
class Thresholds:
    cache_warn_permille: int = 950
    cache_bad_permille: int = 800
    error_streak: int = 3


@dataclass(frozen=True)
class Config:
    host: str = "127.0.0.1"
    port: int = 8787
    token: str = ""
    source: SourceConfig = field(default_factory=SourceConfig)
    balance: BalanceConfig = field(default_factory=BalanceConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    thresholds: Thresholds = field(default_factory=Thresholds)
    pricing: dict[str, Price] = field(default_factory=dict)

    def price_for(self, model: str) -> Price:
        """按模型名找价格，找不到退回 default。"""
        if model in self.pricing:
            return self.pricing[model]
        lowered = model.lower()
        for name, price in self.pricing.items():
            if name != "default" and name.lower() in lowered:
                return price
        return self.pricing.get("default", Price())


def _expand(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(str(value)))


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load(path: str | os.PathLike[str] | None = None) -> Config:
    """读配置。文件不存在时全部走默认值。"""
    config_path = Path(_expand(str(path))) if path else DEFAULT_CONFIG_PATH
    raw: dict = {}
    if config_path.is_file():
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)

    source = raw.get("source", {}) or {}
    balance = raw.get("balance", {}) or {}
    notify = raw.get("notify", {}) or {}
    thresholds = raw.get("thresholds", {}) or {}
    pricing_raw = raw.get("pricing", {}) or {}

    pricing: dict[str, Price] = {}
    for name, entry in pricing_raw.items():
        if isinstance(entry, dict):
            pricing[name] = Price(
                cache_hit_per_mtok=float(entry.get("cache_hit_per_mtok", 0.10)),
                cache_miss_per_mtok=float(entry.get("cache_miss_per_mtok", 3.00)),
                output_per_mtok=float(entry.get("output_per_mtok", 9.00)),
            )
    if "default" not in pricing:
        pricing["default"] = Price()

    api_key = str(balance.get("api_key", "") or "") or os.environ.get(
        "DEEPSEEK_API_KEY", ""
    )

    return Config(
        host=os.environ.get("DSH_USAGE_HOST", str(raw.get("host", "127.0.0.1"))),
        port=int(os.environ.get("DSH_USAGE_PORT", raw.get("port", 8787))),
        token=os.environ.get("DSH_USAGE_TOKEN", str(raw.get("token", ""))),
        source=SourceConfig(
            kind=os.environ.get("DSH_USAGE_SOURCE", str(source.get("kind", "dsh-jsonl"))),
            path=_expand(str(source.get("path", "~/.dsh"))),
        ),
        balance=BalanceConfig(
            enabled=_as_bool(balance.get("enabled", True)),
            api_key=api_key,
            interval_seconds=int(balance.get("interval_seconds", 300)),
            warn_below_cny=float(balance.get("warn_below_cny", 5.0)),
        ),
        notify=NotifyConfig(
            enabled=_as_bool(notify.get("enabled", False)),
            provider=str(notify.get("provider", "bark")),
            bark_url=str(notify.get("bark_url", "") or ""),
            ntfy_url=str(notify.get("ntfy_url", "") or ""),
            cooldown_seconds=int(notify.get("cooldown_seconds", 600)),
        ),
        thresholds=Thresholds(
            cache_warn_permille=int(thresholds.get("cache_warn_permille", 950)),
            cache_bad_permille=int(thresholds.get("cache_bad_permille", 800)),
            error_streak=int(thresholds.get("error_streak", 3)),
        ),
        pricing=pricing,
    )


def write_sample(path: str | os.PathLike[str] | None = None) -> Path:
    """把示例配置写到磁盘，已存在则不动。"""
    target = Path(_expand(str(path))) if path else DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(SAMPLE_CONFIG, encoding="utf-8")
    return target
