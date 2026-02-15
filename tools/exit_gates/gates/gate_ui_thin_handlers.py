from __future__ import annotations

import io
import tokenize
from typing import Any, Dict, Iterable, Optional, Set, Tuple


def _default_forbidden_prefixes() -> Tuple[str, ...]:
    return ("_read_jsonl_", "_redis_", "_cache_")


def _scan_handler_functions(
    text: str,
    class_name: str,
    func_names: Iterable[str],
    forbidden_prefixes: Tuple[str, ...],
) -> Tuple[Set[str], int, int]:
    targets = set(func_names)
    forbidden_found: Set[str] = set()
    scanned_tokens = 0
    scanned_names = 0

    indent_level = 0
    class_indent: Optional[int] = None
    in_class = False

    pending_class_name = False
    pending_class_indent = False
    pending_def_name: Optional[str] = None
    pending_def = False
    pending_func_indent = False

    current_func: Optional[str] = None
    func_indent: Optional[int] = None

    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        scanned_tokens += 1
        tok_type = tok.type
        tok_str = tok.string

        if tok_type == tokenize.INDENT:
            indent_level += 1
            if pending_class_indent:
                class_indent = indent_level
                in_class = True
                pending_class_indent = False
            if pending_func_indent and pending_def_name is not None:
                current_func = pending_def_name
                func_indent = indent_level
                pending_func_indent = False
                pending_def_name = None
        elif tok_type == tokenize.DEDENT:
            indent_level = max(0, indent_level - 1)
            if func_indent is not None and indent_level < func_indent:
                current_func = None
                func_indent = None
            if class_indent is not None and indent_level < class_indent:
                in_class = False
                class_indent = None
        elif tok_type == tokenize.NAME:
            if pending_class_name:
                if tok_str == class_name:
                    pending_class_indent = True
                pending_class_name = False
            elif tok_str == "class":
                pending_class_name = True
            elif in_class and tok_str == "def":
                pending_def = True
            elif in_class and pending_def:
                pending_def = False
                if tok_str in targets:
                    pending_def_name = tok_str
                    pending_func_indent = True
            elif current_func is not None and func_indent is not None and indent_level >= func_indent:
                scanned_names += 1
                for prefix in forbidden_prefixes:
                    if tok_str.startswith(prefix):
                        forbidden_found.add(tok_str)
                        break

    return forbidden_found, scanned_tokens, scanned_names


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    file_path = str(inputs.get("file_path", "ui_chart_v3/server.py"))
    class_name = str(inputs.get("class_name", "Handler"))
    func_names = inputs.get("functions", ["do_GET", "_handle_api"])
    if not isinstance(func_names, list):
        func_names = ["do_GET", "_handle_api"]
    prefixes = inputs.get("forbidden_prefixes")
    if isinstance(prefixes, list) and prefixes:
        forbidden_prefixes = tuple(str(x) for x in prefixes)
    else:
        forbidden_prefixes = _default_forbidden_prefixes()

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as exc:
        return {
            "ok": False,
            "details": f"read_failed:{type(exc).__name__}",
            "metrics": {},
        }

    forbidden_found, scanned_tokens, scanned_names = _scan_handler_functions(
        text,
        class_name,
        func_names,
        forbidden_prefixes,
    )

    ok = len(forbidden_found) == 0
    details = "ok" if ok else "forbidden=" + ",".join(sorted(forbidden_found))
    return {
        "ok": ok,
        "details": details,
        "metrics": {
            "scanned_tokens": scanned_tokens,
            "scanned_names": scanned_names,
            "forbidden_count": len(forbidden_found),
            "forbidden": sorted(forbidden_found),
        },
    }
