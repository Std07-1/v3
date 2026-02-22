from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_PREVIEW_TF_ALLOWLIST = {60, 180}


def _load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _preview_tf_allowlist_from_cfg(cfg: Dict[str, Any]) -> List[int]:
    raw = cfg.get("tf_preview_allowlist_s")
    out: List[int] = []
    if isinstance(raw, list):
        for item in raw:
            try:
                tf_s = int(item)
            except Exception:
                continue
            if tf_s > 0:
                out.append(tf_s)
    if out:
        return out
    return sorted(DEFAULT_PREVIEW_TF_ALLOWLIST)


def _iter_tail_lines(path: str, max_lines: int) -> Iterable[bytes]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            buf = b""
            chunk = 8192
            lines: List[bytes] = []
            while pos > 0 and len(lines) < max_lines:
                step = min(chunk, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + buf
                while b"\n" in buf:
                    idx = buf.rfind(b"\n")
                    line = buf[idx + 1 :]
                    buf = buf[:idx]
                    if line:
                        lines.append(line)
                        if len(lines) >= max_lines:
                            break
            if buf and len(lines) < max_lines:
                lines.append(buf)
            for line in lines:
                yield line
    except Exception:
        return []


def _list_symbols(data_root: str) -> List[str]:
    if not os.path.isdir(data_root):
        return []
    out: List[str] = []
    for name in os.listdir(data_root):
        p = os.path.join(data_root, name)
        if os.path.isdir(p):
            out.append(name)
    return sorted(out)


def _latest_part_file(symbol_dir: str, tf_s: int) -> Optional[str]:
    tf_dir = os.path.join(symbol_dir, f"tf_{tf_s}")
    if not os.path.isdir(tf_dir):
        return None
    parts = [
        os.path.join(tf_dir, name)
        for name in os.listdir(tf_dir)
        if name.startswith("part-") and name.endswith(".jsonl")
    ]
    if not parts:
        return None
    parts.sort()
    return parts[-1]


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    config_path = str(inputs.get("config_path", "config.json"))
    tail_lines = int(inputs.get("tail_lines", 200))
    cfg = _load_config(config_path)
    data_root = str(inputs.get("data_root") or cfg.get("data_root") or "")
    if not data_root:
        return {"ok": False, "details": "data_root_missing", "metrics": {}}

    tf_list = inputs.get("tf_s")
    if isinstance(tf_list, list):
        tfs = []
        for item in tf_list:
            try:
                tf_s = int(item)
            except Exception:
                continue
            if tf_s > 0:
                tfs.append(tf_s)
    else:
        tfs = _preview_tf_allowlist_from_cfg(cfg)

    violations: List[str] = []
    scanned = 0
    for sym in _list_symbols(data_root):
        sym_dir = os.path.join(data_root, sym)
        for tf_s in tfs:
            last_part = _latest_part_file(sym_dir, tf_s)
            if not last_part:
                continue
            saw_any = False
            violation_found = False
            for raw in _iter_tail_lines(last_part, max_lines=tail_lines):
                scanned += 1
                saw_any = True
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                src = obj.get("src")
                is_preview = obj.get("is_preview")
                if isinstance(src, str) and src.startswith("preview"):
                    violations.append(f"{sym}/tf_{tf_s}:{src}")
                    violation_found = True
                    break
                if is_preview is True:
                    violations.append(f"{sym}/tf_{tf_s}:is_preview=true")
                    violation_found = True
                    break

    ok = not violations
    details = "ok" if ok else "found=" + ";".join(violations[:10])
    return {
        "ok": ok,
        "details": details,
        "metrics": {"scanned_lines": scanned, "violations": len(violations)},
    }


def main() -> int:
    result = run_gate({})
    if not result.get("ok"):
        details = str(result.get("details", ""))
        if details.startswith("found="):
            details = details[len("found=") :]
        print("EXIT_GATE_FAIL: preview не має потрапляти у SSOT JSONL")
        for item in details.split(";"):
            if item:
                print(" - " + item)
        return 2
    print("EXIT_GATE_OK: preview не знайдено у SSOT JSONL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
