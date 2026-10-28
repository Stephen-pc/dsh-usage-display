"""HTTP 服务：一份 JSON 喂所有终端。"""

from __future__ import annotations

import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__
from .balance import fetch as fetch_balance
from .config import Config
from .metrics import BalanceState, build
from .notify import Notifier
from .sources import CallEvent, append_event, discover_logs, load_events, parse_file

WEB_DIR = Path(__file__).parent / "web"
SIDECAR_NAME = "events.jsonl"


class UsageState:
    """缓存一份快照，后台线程负责刷新，请求线程只读。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.lock = threading.Lock()
        self.events: list[CallEvent] = []
        self.snapshot: dict = {}
        self.balance = BalanceState()
        self.notifier = Notifier(config)
        self._fingerprint: tuple = ()
        self._last_balance = 0.0
        self.last_notified: str | None = None

    def _scan_fingerprint(self) -> tuple:
        root = Path(self.config.source.path)
        if root.is_file():
            return ((str(root), root.stat().st_mtime),)
        marks = []
        for path in discover_logs(root):
            try:
                marks.append((str(path), path.stat().st_mtime))
            except OSError:
                continue
        return tuple(marks)

    def refresh(self, force: bool = False) -> dict:
        fingerprint = self._scan_fingerprint()
        if force or fingerprint != self._fingerprint:
            self._fingerprint = fingerprint
            self.events = load_events(self.config.source.kind, self.config.source.path)

        if self.config.balance.enabled:
            now = time.time()
            if force or (now - self._last_balance) >= self.config.balance.interval_seconds:
                self.balance = fetch_balance(self.config.balance.api_key, self.balance)
                self._last_balance = now

        snapshot = build(self.events, self.config, self.balance)
        with self.lock:
            self.snapshot = snapshot
        reason = self.notifier.maybe_notify(snapshot)
        if reason:
            self.last_notified = reason
        return snapshot

    def read(self) -> dict:
        with self.lock:
            return dict(self.snapshot)

    def add_record(self, payload: dict[str, Any]) -> None:
        event = CallEvent(
            at=int(payload.get("ts") or payload.get("at") or time.time()),
            model=str(payload.get("model") or "-"),
            ok=bool(payload.get("ok", True)),
            input_tokens=int(payload.get("input_tokens", 0) or 0),
            output_tokens=int(payload.get("output_tokens", 0) or 0),
            cache_hit_tokens=int(payload.get("cache_hit_tokens", 0) or 0),
            cache_miss_tokens=int(payload.get("cache_miss_tokens", 0) or 0),
            latency_ms=int(payload.get("latency_ms", 0) or 0),
            ttft_ms=int(payload.get("ttft_ms", 0) or 0),
        )
        sidecar = Path(self.config.source.path) / SIDECAR_NAME
        append_event(sidecar, event)
        self._fingerprint = ()


def _make_handler(state: UsageState, config: Config):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"dsh-usage/{__version__}"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            if config.host not in {"127.0.0.1", "localhost"}:
                super().log_message(fmt, *args)

        def _authorised(self) -> bool:
            if not config.token:
                return True
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer ") and header[7:].strip() == config.token:
                return True
            return False

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]

            if path in {"/", "/index.html"}:
                index = WEB_DIR / "index.html"
                if index.is_file():
                    self._send(200, index.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._json(500, {"error": "web/index.html 缺失"})
                return

            if path == "/api/health":
                self._json(200, {"ok": True, "version": __version__})
                return

            if path == "/api/usage":
                if not self._authorised():
                    self._json(401, {"error": "unauthorised"})
                    return
                snapshot = state.read() or state.refresh(force=True)
                self._json(200, snapshot)
                return

            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path != "/api/record":
                self._json(404, {"error": "not found"})
                return
            if not self._authorised():
                self._json(401, {"error": "unauthorised"})
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0 or length > 64 * 1024:
                self._json(400, {"error": "bad body"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json(400, {"error": "invalid json"})
                return
            if not isinstance(payload, dict):
                self._json(400, {"error": "expect object"})
                return
            state.add_record(payload)
            self._json(200, {"ok": True})

    return Handler


def _poller(state: UsageState, interval: int, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            state.refresh()
        except Exception as error:  # noqa: BLE001 - 后台线程不能死
            print(f"[refresh] 出错：{error}")
        stop.wait(interval)


def serve(config: Config, refresh_seconds: int = 5) -> None:
    """起服务，阻塞运行。"""
    state = UsageState(config)
    state.refresh(force=True)

    stop = threading.Event()
    thread = threading.Thread(
        target=_poller, args=(state, refresh_seconds, stop), daemon=True
    )
    thread.start()

    server = ThreadingHTTPServer((config.host, config.port), _make_handler(state, config))
    shown = config.host if config.host != "0.0.0.0" else "<本机局域网 IP>"
    print(f"dsh-usage {__version__} 已启动")
    print(f"  看板   http://{shown}:{config.port}/")
    print(f"  接口   http://{shown}:{config.port}/api/usage")
    print(f"  数据源 {config.source.kind} → {config.source.path}")
    print(f"  鉴权   {'Bearer token' if config.token else '未开启（仅本机建议）'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n收工")
    finally:
        stop.set()
        server.server_close()


def snapshot_once(config: Config) -> dict:
    events = load_events(config.source.kind, config.source.path)
    balance = BalanceState()
    if config.balance.enabled and config.balance.api_key:
        balance = fetch_balance(config.balance.api_key, balance)
    return build(events, config, balance)
