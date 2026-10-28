"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

from . import __version__
from . import config as config_module
from .config import Config
from .metrics import human_tokens, yuan
from .sources import CallEvent, append_event, probe
from .server import serve, snapshot_once

SIDECAR_NAME = "events.jsonl"


def _config(args: argparse.Namespace) -> Config:
    return config_module.load(args.config)


def _cmd_serve(args: argparse.Namespace) -> int:
    serve(_config(args), refresh_seconds=args.refresh)
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    snapshot = snapshot_once(_config(args))
    if args.pretty:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(snapshot, ensure_ascii=False))
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    cfg = _config(args)
    probe(Path(cfg.source.path), limit=args.limit)
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    cfg = _config(args)
    hit = args.cache_hit
    miss = args.cache_miss
    if hit is None and miss is None and args.input_tokens:
        hit = 0
        miss = args.input_tokens
    event = CallEvent(
        at=int(time.time()),
        model=args.model,
        ok=not args.fail,
        input_tokens=args.input_tokens or 0,
        output_tokens=args.output_tokens or 0,
        cache_hit_tokens=hit or 0,
        cache_miss_tokens=miss or 0,
        latency_ms=args.latency or 0,
        ttft_ms=args.ttft or 0,
    )
    sidecar = Path(cfg.source.path) / SIDECAR_NAME
    append_event(sidecar, event)
    print(f"已记录 → {sidecar}")
    print(
        f"  输入 {event.input_tokens} · 输出 {event.output_tokens} · "
        f"命中 {event.cache_hit_tokens} · 未命中 {event.cache_miss_tokens} · "
        f"命中率 {event.cache_permille}‰"
    )
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    target = Path(config_module._expand(args.config)) if args.config else config_module.DEFAULT_CONFIG_PATH
    if target.exists() and not args.force:
        print(f"配置已存在：{target}（加 --force 覆盖）")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(config_module.SAMPLE_CONFIG, encoding="utf-8")
    print(f"已写入示例配置：{target}")
    return 0


def _cmd_mock(args: argparse.Namespace) -> int:
    cfg = _config(args)
    sidecar = Path(cfg.source.path) / SIDECAR_NAME
    now = int(time.time())
    models = ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash"]
    hit_ratio = 0.97
    for index in range(args.count):
        hit_ratio = min(0.995, max(0.62, hit_ratio + random.uniform(-0.04, 0.035)))
        input_tokens = random.randint(4000, 26000)
        hit = int(input_tokens * hit_ratio)
        event = CallEvent(
            at=now - (args.count - index) * 90,
            model=random.choice(models),
            ok=random.random() > 0.03,
            input_tokens=input_tokens,
            output_tokens=random.randint(120, 1400),
            cache_hit_tokens=hit,
            cache_miss_tokens=input_tokens - hit,
            latency_ms=random.randint(900, 4200),
            ttft_ms=random.randint(180, 900),
        )
        append_event(sidecar, event)
    print(f"已生成 {args.count} 条模拟事件 → {sidecar}")
    snapshot = snapshot_once(cfg)
    print(
        f"今日 {snapshot['calls_today']} 次 · 命中 {snapshot['cache_hit_permille']}‰ · "
        f"花费 ¥{yuan(snapshot['cost_today_cny_micros'])} · "
        f"token {human_tokens(snapshot['input_today'])} in / "
        f"{human_tokens(snapshot['output_today'])} out · 等级 {snapshot['level']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dsh-usage",
        description="把 DeepSeek Harness 的用量数据变成任何设备都能读的一份 JSON。",
    )
    parser.add_argument("--version", action="version", version=f"dsh-usage {__version__}")
    parser.add_argument("--config", help="配置文件路径，默认 ~/.dsh-usage/config.toml")
    sub = parser.add_subparsers(dest="command")

    serve_parser = sub.add_parser("serve", help="启动 HTTP 服务（默认命令）")
    serve_parser.add_argument("--refresh", type=int, default=5, help="刷新间隔秒数")
    serve_parser.set_defaults(func=_cmd_serve)

    snapshot_parser = sub.add_parser("snapshot", help="打印一次快照 JSON")
    snapshot_parser.add_argument("--pretty", action="store_true", help="格式化输出")
    snapshot_parser.set_defaults(func=_cmd_snapshot)

    probe_parser = sub.add_parser("probe", help="扫描日志，看字段能不能对上")
    probe_parser.add_argument("--limit", type=int, default=5, help="最多看几个文件")
    probe_parser.set_defaults(func=_cmd_probe)

    record_parser = sub.add_parser("record", help="手动追加一条用量记录")
    record_parser.add_argument("--input", dest="input_tokens", type=int, default=0)
    record_parser.add_argument("--output", dest="output_tokens", type=int, default=0)
    record_parser.add_argument("--cache-hit", dest="cache_hit", type=int, default=None)
    record_parser.add_argument("--cache-miss", dest="cache_miss", type=int, default=None)
    record_parser.add_argument("--model", default="deepseek-v4-flash")
    record_parser.add_argument("--latency", type=int, default=0)
    record_parser.add_argument("--ttft", type=int, default=0)
    record_parser.add_argument("--fail", action="store_true", help="记为失败")
    record_parser.set_defaults(func=_cmd_record)

    init_parser = sub.add_parser("init", help="写一份示例配置")
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(func=_cmd_init)

    mock_parser = sub.add_parser("mock", help="灌模拟数据，方便先看看效果")
    mock_parser.add_argument("--count", type=int, default=40)
    mock_parser.set_defaults(func=_cmd_mock)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = parser.parse_args((argv or []) + ["serve"])
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
