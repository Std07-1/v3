#!/usr/bin/env python3
"""
Смоук-перевірки UI чарту v3.

Перевірки:
- інваріанти статичних файлів (CSS висоти, підключення адаптера)
- інваріанти адаптера (wheel capture + passive false)
- опційно API (symbols/bars) якщо задано base-url
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_index_html(static_root):
    path = os.path.join(static_root, "index.html")
    require(os.path.isfile(path), f"index.html не знайдено: {path}")
    text = read_text(path)

    require("chart_adapter_lite.js" in text, "index.html: немає chart_adapter_lite.js")
    require("/app.js" in text, "index.html: немає app.js")

    css_html_body = re.search(r"html\s*,\s*body\s*\{[^}]*height\s*:\s*100%", text, re.S)
    require(css_html_body, "index.html: немає height: 100% для html/body")

    css_chart = re.search(r"#chart\s*\{[^}]*height\s*:\s*100%", text, re.S)
    require(css_chart, "index.html: немає height: 100% для #chart")


def check_adapter(static_root):
    path = os.path.join(static_root, "chart_adapter_lite.js")
    require(os.path.isfile(path), f"chart_adapter_lite.js не знайдено: {path}")
    text = read_text(path)

    wheel_opts = re.search(r"WHEEL_OPTIONS\s*=\s*\{[^}]*passive\s*:\s*false[^}]*capture\s*:\s*true", text, re.S)
    require(wheel_opts, "chart_adapter_lite.js: WHEEL_OPTIONS без passive:false + capture:true")

    wheel_hook = "container.addEventListener(\"wheel\", handleWheel, WHEEL_OPTIONS)" in text
    require(wheel_hook, "chart_adapter_lite.js: wheel listener без WHEEL_OPTIONS")

    volume_series = "addHistogramSeries" in text
    require(volume_series, "chart_adapter_lite.js: не знайдено addHistogramSeries (обсяги)")


def api_get(url):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} для {url}")
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def check_api(base_url):
    data = api_get(base_url + "/api/symbols")
    require(data.get("ok") is True, "api/symbols: ok != true")
    symbols = data.get("symbols") or []
    if not symbols:
        print("API: symbols порожній — пропускаю bars")
        return

    symbol = symbols[0]
    bars = api_get(base_url + f"/api/bars?symbol={symbol}&tf_s=60&limit=5")
    require(bars.get("ok") is True, "api/bars: ok != true")
    items = bars.get("bars") or []
    if not items:
        print("API: bars порожній — пропускаю перевірку полів")
        return

    last_time = None
    for bar in items:
        for key in ("time", "open", "high", "low", "close"):
            require(key in bar, f"api/bars: бракує поля {key}")
        if last_time is not None:
            require(bar["time"] >= last_time, "api/bars: час не відсортовано")
        last_time = bar["time"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--static-root", default=os.path.join("ui_chart_v3", "static"))
    ap.add_argument("--base-url", default="")
    args = ap.parse_args()

    check_index_html(args.static_root)
    check_adapter(args.static_root)

    if args.base_url:
        check_api(args.base_url.rstrip("/"))

    print("smoke_ui_v3: ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as e:
        print(f"smoke_ui_v3: api error: {e}")
        raise SystemExit(2)
    except Exception as e:
        print(f"smoke_ui_v3: fail: {e}")
        raise SystemExit(1)
