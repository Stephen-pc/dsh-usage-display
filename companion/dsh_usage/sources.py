"""数据源：从 dsh 会话日志里把每一次模型调用的用量挖出来。

dsh 的会话日志格式还在预览期变动，所以这里不写死 schema，而是用一层宽容探测：
递归遍历 JSON 树，凡是长得像 usage 的字典就抽出来，字段名用别名表匹配。
抽不到的字段一律记 0，绝不因为少一个键就中断。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

LOG_SUFFIXES = (".jsonl", ".jsonl.zst", ".json", ".jsonl.zstd")
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "dist", "build", ".pnpm"}

_INPUT_KEYS = (
    "input_tokens",
    "prompt_tokens",
    "inputTokens",
    "promptTokens",
    "input_token_count",
    "prompt_token_count",
    "input",
)
_OUTPUT_KEYS = (
    "output_tokens",
    "completion_tokens",
    "outputTokens",
    "completionTokens",
    "output_token_count",
    "completion_token_count",
    "output",
)
_HIT_KEYS = (
    "prompt_cache_hit_tokens",
    "promptCacheHitTokens",
    "cache_hit_tokens",
    "cacheHitTokens",
    "cached_tokens",
    "cacheReadTokens",
    "cache_read_input_tokens",
    "cache_read_tokens",
    "cache_hit",
)
_MISS_KEYS = (
    "prompt_cache_miss_tokens",
    "promptCacheMissTokens",
    "cache_miss_tokens",
    "cacheMissTokens",
    "cache_creation_input_tokens",
    "cacheCreationTokens",
    "cache_write_tokens",
    "cache_miss",
)
_MODEL_KEYS = ("model", "modelId", "model_id", "modelName", "model_name")
_TIME_KEYS = (
    "timestamp",
    "time",
    "at",
    "ts",
    "createdAt",
    "created_at",
    "startedAt",
    "started_at",
    "occurredAt",
)
_OK_KEYS = ("ok", "success", "succeeded")
_ERROR_KEYS = ("error", "errorMessage", "error_message", "failure")
_STATUS_KEYS = ("status", "statusCode", "status_code", "code")
_LATENCY_KEYS = ("latency_ms", "latencyMs", "duration_ms", "durationMs", "elapsed_ms", "elapsedMs", "latency")
_TTFT_KEYS = ("ttft_ms", "ttftMs", "first_token_ms", "firstTokenMs", "time_to_first_token_ms")


@dataclass(frozen=True)
class CallEvent:
    """一次模型调用的用量记录。"""

    at: int
    model: str = "-"
    ok: bool = True
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    latency_ms: int = 0
    ttft_ms: int = 0

    @property
    def billed_input(self) -> int:
        """计费用的输入 token。有缓存拆分时用 hit+miss，否则退回 input。"""
        split = self.cache_hit_tokens + self.cache_miss_tokens
        return split if split > 0 else self.input_tokens

    @property
    def cache_permille(self) -> int:
        split = self.cache_hit_tokens + self.cache_miss_tokens
        if split <= 0:
            return 0
        return round(self.cache_hit_tokens * 1000 / split)

    @property
    def dedupe_key(self) -> tuple:
        return (
            self.at,
            self.model,
            self.input_tokens,
            self.output_tokens,
            self.cache_hit_tokens,
            self.cache_miss_tokens,
        )


def _first_int(node: dict, keys: Iterable[str]) -> int | None:
    for key in keys:
        if key in node:
            value = node[key]
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str) and value.strip().lstrip("-").isdigit():
                return int(value.strip())
    return None


def _first_str(node: dict, keys: Iterable[str]) -> str | None:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_time(value: Any) -> int | None:
    """把各种时间写法归一到 Unix 秒。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e12:  # 毫秒
            number /= 1000.0
        if number > 1e9:
            return int(number)
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return parse_time(int(text))
        try:
            cleaned = text.replace("Z", "+00:00")
            moment = datetime.fromisoformat(cleaned)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return int(moment.timestamp())
        except ValueError:
            return None
    return None


def _looks_like_usage(node: dict) -> bool:
    if _first_int(node, _INPUT_KEYS) is not None:
        return True
    if _first_int(node, _OUTPUT_KEYS) is not None:
        return True
    return _first_int(node, _HIT_KEYS) is not None or _first_int(node, _MISS_KEYS) is not None


def _walk(node: Any, ancestors: list[dict], found: list[tuple[dict, list[dict]]]) -> None:
    if isinstance(node, dict):
        if _looks_like_usage(node):
            found.append((node, list(ancestors)))
        for value in node.values():
            if isinstance(value, (dict, list)):
                _walk(value, ancestors + [node], found)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                _walk(item, ancestors, found)


def _resolve_status(usage: dict, ancestors: list[dict]) -> tuple[bool, bool]:
    """返回 (是否成功, 是否明确拿到状态)。"""
    chain = [usage] + list(reversed(ancestors))
    for node in chain:
        for key in _OK_KEYS:
            if isinstance(node.get(key), bool):
                return bool(node[key]), True
        for key in _ERROR_KEYS:
            if key in node and node[key] not in (None, "", 0, False):
                return False, True
        for key in _STATUS_KEYS:
            value = node.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and 100 <= value <= 599:
                return 200 <= value < 300, True
            if isinstance(value, str):
                lowered = value.lower()
                if lowered in {"ok", "success", "succeeded", "completed", "done"}:
                    return True, True
                if lowered in {"error", "failed", "failure", "cancelled", "aborted"}:
                    return False, True
    return True, False


def extract_events(payload: Any, fallback_time: int | None = None) -> list[CallEvent]:
    """从一个 JSON 对象里抽出所有用量事件。"""
    found: list[tuple[dict, list[dict]]] = []
    _walk(payload, [], found)

    events: list[CallEvent] = []
    for usage, ancestors in found:
        hit = _first_int(usage, _HIT_KEYS) or 0
        miss = _first_int(usage, _MISS_KEYS) or 0
        input_tokens = _first_int(usage, _INPUT_KEYS) or 0
        output_tokens = _first_int(usage, _OUTPUT_KEYS) or 0
        if input_tokens == 0 and output_tokens == 0 and hit == 0 and miss == 0:
            continue

        chain = [usage] + list(reversed(ancestors))
        at = None
        for node in chain:
            for key in _TIME_KEYS:
                if key in node:
                    at = parse_time(node[key])
                    if at:
                        break
            if at:
                break
        at = at or fallback_time or int(time.time())

        model = "-"
        for node in chain:
            name = _first_str(node, _MODEL_KEYS)
            if name:
                model = name
                break

        latency = 0
        for node in chain:
            value = _first_int(node, _LATENCY_KEYS)
            if value:
                latency = value if value < 600_000 else value // 1000
                break

        ttft = 0
        for node in chain:
            value = _first_int(node, _TTFT_KEYS)
            if value:
                ttft = value
                break

        ok, _ = _resolve_status(usage, ancestors)
        events.append(
            CallEvent(
                at=at,
                model=model,
                ok=ok,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_hit_tokens=hit,
                cache_miss_tokens=miss,
                latency_ms=latency,
                ttft_ms=ttft,
            )
        )
    return events


def _read_zstd(path: Path) -> Iterator[str]:
    try:
        import zstandard  # type: ignore
    except ImportError:
        print(
            f"[source] 跳过 {path.name}：需要 pip install zstandard 才能读压缩日志",
            file=sys.stderr,
        )
        return
    with path.open("rb") as raw:
        reader = zstandard.ZstdDecompressor().stream_reader(raw)
        text = reader.read().decode("utf-8", errors="replace")
    yield from text.splitlines()


def iter_lines(path: Path) -> Iterator[str]:
    if path.suffix in {".zst", ".zstd"}:
        yield from _read_zstd(path)
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        yield from handle


def parse_file(path: Path) -> list[CallEvent]:
    """解析一个文件，坏行跳过。"""
    events: list[CallEvent] = []
    fallback = int(path.stat().st_mtime) if path.exists() else int(time.time())
    for line in iter_lines(path):
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        events.extend(extract_events(payload, fallback_time=fallback))
    return events


def discover_logs(root: Path) -> list[Path]:
    """在目录下找会话日志，跳过 node_modules 这类深坑。"""
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    hits: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.endswith(LOG_SUFFIXES):
                hits.append(Path(dirpath) / name)
    hits.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
    return hits


def load_events(kind: str, path: str) -> list[CallEvent]:
    """按配置读取事件列表，已按时间去重排序。"""
    if kind == "stdin":
        return _load_stdin()
    root = Path(path)
    events: list[CallEvent] = []
    for file_path in discover_logs(root):
        events.extend(parse_file(file_path))
    return sorted(_dedupe(events), key=lambda e: e.at)


def _load_stdin() -> list[CallEvent]:
    events: list[CallEvent] = []
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        events.extend(extract_events(payload))
    return sorted(_dedupe(events), key=lambda e: e.at)


def _dedupe(events: list[CallEvent]) -> list[CallEvent]:
    """同一次调用可能被 request 与 response 各记一条，按内容指纹去重。"""
    seen: set[tuple] = set()
    unique: list[CallEvent] = []
    for event in events:
        if event.dedupe_key in seen:
            continue
        seen.add(event.dedupe_key)
        unique.append(event)
    return unique


def append_event(path: Path, event: CallEvent) -> None:
    """往 JSONL 里追加一条记录，给 dsh 插件或 hook 直接调用。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": event.at,
        "model": event.model,
        "ok": event.ok,
        "usage": {
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
            "prompt_cache_hit_tokens": event.cache_hit_tokens,
            "prompt_cache_miss_tokens": event.cache_miss_tokens,
        },
        "latency_ms": event.latency_ms,
        "ttft_ms": event.ttft_ms,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def probe(root: Path, limit: int = 5) -> None:
    """扫一遍日志，打印探测到的事件，用来确认字段能不能对上。"""
    files = discover_logs(root)
    print(f"在 {root} 找到 {len(files)} 个候选日志文件")
    for file_path in files[-limit:]:
        events = parse_file(file_path)
        print(f"\n{file_path}  ->  {len(events)} 条事件")
        for event in events[-3:]:
            stamp = datetime.fromtimestamp(event.at).strftime("%m-%d %H:%M:%S")
            print(
                f"  {stamp}  {event.model:<24} in={event.input_tokens:<7} "
                f"out={event.output_tokens:<6} hit={event.cache_hit_tokens:<7} "
                f"miss={event.cache_miss_tokens:<6} 命中={event.cache_permille}‰"
            )
    if not files:
        print("没找到日志。先用 dsh 跑一轮对话，或用 dsh-usage record 手动灌几条。")


_SAFE_MODEL = re.compile(r"[^A-Za-z0-9._\-]+")


def safe_model_name(model: str) -> str:
    return _SAFE_MODEL.sub("-", model).strip("-") or "-"
