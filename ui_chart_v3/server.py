#!/usr/bin/env python3
"""
Мінімальний read-only UI чарт для v3 (режим B): читає JSONL з диска і віддає OHLCV через HTTP.

Цілі:
- Без SQL, без метрик, без WebSocket.
- Same-origin: HTML/JS + API в одному процесі.
- Підтримка TF з директорій tf_{tf_s}/part-YYYYMMDD.jsonl.
- Інкрементальні оновлення через простий polling /api/latest.

Запуск:
  python server.py --data-root ./data_v3 --host 0.0.0.0 --port 8089

Використання:
  Відкрити у браузері: http://HOST:PORT/
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import urllib.parse
from collections import deque
from typing import Any


def _safe_int(v: str | None, default: int) -> int:
    try:
        return int(v) if v is not None else default
    except Exception:
        return default


def _sym_dir(symbol: str) -> str:
    return symbol.replace("/", "_")


def _list_symbols(data_root: str) -> list[str]:
    if not os.path.isdir(data_root):
        return []
    out: list[str] = []
    for name in sorted(os.listdir(data_root)):
        p = os.path.join(data_root, name)
        if os.path.isdir(p):
            # Повертаємо у форматі з "/": найпростіша евристика
            # (для XAU_USD -> XAU/USD). Якщо ви маєте інші символи, адаптуйте тут.
            sym = name.replace("_", "/")
            out.append(sym)
    return out


def _list_parts(data_root: str, symbol: str, tf_s: int) -> list[str]:
    d = os.path.join(data_root, _sym_dir(symbol), f"tf_{tf_s}")
    if not os.path.isdir(d):
        return []
    parts = [
        os.path.join(d, x)
        for x in os.listdir(d)
        if x.startswith("part-") and x.endswith(".jsonl")
    ]
    parts.sort()
    return parts


def _read_jsonl_filtered(
    paths: list[str],
    since_open_ms: int | None,
    to_open_ms: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Читає JSONL у хронологічному порядку, повертає останні limit елементів після фільтрації."""
    buf: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue

                    open_ms = obj.get("open_time_ms")
                    if not isinstance(open_ms, int):
                        continue

                    if since_open_ms is not None and open_ms <= since_open_ms:
                        continue
                    if to_open_ms is not None and open_ms > to_open_ms:
                        continue

                    buf.append(obj)
        except FileNotFoundError:
            continue

    out = list(buf)
    out.sort(key=lambda x: x.get("open_time_ms", 0))
    return out


def _bars_to_lwc(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Перетворює канонічний формат у формат Lightweight Charts."""
    out: list[dict[str, Any]] = []
    for b in bars:
        t = b["open_time_ms"] // 1000
        low_val = b.get("low", b.get("l"))
        out.append(
            {
                "time": t,
                "open": float(b["o"]),
                "high": float(b["h"]),
                "low": float(low_val),
                "close": float(b["c"]),
                "volume": float(b.get("v", 0.0)),
                "open_time_ms": int(b["open_time_ms"]),
                "tf_s": int(b["tf_s"]),
                "src": str(b.get("src", "")),
            }
        )
    return out


class Handler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler: / (статичні файли), /api/* (JSON)."""

    server_version = "AiOne_v3_UI/0.1"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if path.startswith("/api/"):
            self._handle_api(parsed)
            return

        # Статичні файли
        super().do_GET()

    def _json(self, code: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _bad(self, msg: str) -> None:
        self._json(400, {"ok": False, "error": msg})

    def _handle_api(self, parsed: urllib.parse.ParseResult) -> None:
        qs = urllib.parse.parse_qs(parsed.query)
        data_root: str = self.server.data_root  # type: ignore[attr-defined]
        config_path: str | None = getattr(self.server, "config_path", None)  # type: ignore[attr-defined]
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/config":
            ui_debug = True
            if config_path and os.path.isfile(config_path):
                try:
                    with open(config_path, encoding="utf-8") as f:
                        cfg = json.load(f)
                    ui_debug = bool(cfg.get("ui_debug", True))
                except Exception:
                    ui_debug = True
            self._json(200, {"ok": True, "ui_debug": ui_debug})
            return

        if path == "/api/symbols":
            self._json(200, {"ok": True, "symbols": _list_symbols(data_root)})
            return

        if path in ("/api/bars", "/api/latest"):
            symbol = (qs.get("symbol", [""])[0] or "").strip()
            tf_s = _safe_int((qs.get("tf_s", ["60"])[0] or "60"), 60)
            limit = _safe_int((qs.get("limit", ["2000"])[0] or "2000"), 2000)

            if not symbol:
                self._bad("missing_symbol")
                return

            parts = _list_parts(data_root, symbol, tf_s)
            if not parts:
                self._json(200, {"ok": True, "bars": [], "note": "no_data"})
                return

            since_open_ms = None
            to_open_ms = None
            if path == "/api/latest":
                a = qs.get("after_open_ms", [None])[0]
                since_open_ms = _safe_int(a, 0) if a is not None else None
            else:
                s = qs.get("since_open_ms", [None])[0]
                t = qs.get("to_open_ms", [None])[0]
                since_open_ms = _safe_int(s, 0) if s is not None else None
                to_open_ms = _safe_int(t, 0) if t is not None else None

            bars = _read_jsonl_filtered(parts, since_open_ms, to_open_ms, limit)
            lwc = _bars_to_lwc(bars)
            self._json(200, {"ok": True, "symbol": symbol, "tf_s": tf_s, "bars": lwc})
            return

        self._bad("unknown_endpoint")

    def end_headers(self) -> None:
        path = self.path or ""
        if not path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    # Вимикаємо логування кожного статичного файлу (за замовчуванням надто шумно)
    def log_message(self, format: str, *args: Any) -> None:
        if self.path.startswith("/api/"):
            super().log_message(format, *args)
        else:
            return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--data-root",
        required=True,
        help="Корінь data_v3 (де лежать SYMBOL/tf_*/part-*.jsonl)",
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8089)
    ap.add_argument(
        "--static-root",
        default="static",
        help="Каталог зі статикою (index.html, app.js)",
    )
    ap.add_argument(
        "--config",
        default=None,
        help="Шлях до config.json (для ui_debug)",
    )
    args = ap.parse_args()

    data_root = os.path.abspath(args.data_root)
    static_root = os.path.abspath(args.static_root)
    default_config = os.path.abspath(os.path.join(static_root, "..", "..", "config.json"))
    config_path = os.path.abspath(args.config) if args.config else default_config
    os.chdir(static_root)

    httpd = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.data_root = data_root  # type: ignore[attr-defined]
    httpd.config_path = config_path  # type: ignore[attr-defined]

    print(f"UI: http://{args.host}:{args.port}/")
    print(f"DATA_ROOT: {httpd.data_root}")  # type: ignore[attr-defined]
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
