from __future__ import annotations

import io
import tokenize
from typing import Any, Dict, List, Tuple


def _default_forbidden_modules() -> Tuple[str, ...]:
    return (
        "runtime.ingest",
        "runtime.store.ssot_jsonl",
        "runtime.store.redis_snapshot",
    )


def _join_module(tokens: List[tokenize.TokenInfo]) -> str:
    parts: List[str] = []
    for tok in tokens:
        if tok.type == tokenize.NAME:
            parts.append(tok.string)
        elif tok.type == tokenize.OP and tok.string == ".":
            parts.append(".")
    return "".join(parts)


def _scan_imports(text: str, forbidden_modules: Tuple[str, ...]) -> List[str]:
    hits: List[str] = []
    toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    n = len(toks)
    i = 0
    while i < n:
        tok = toks[i]
        if tok.type == tokenize.NAME and tok.string == "from":
            module_tokens: List[tokenize.TokenInfo] = []
            i += 1
            while i < n:
                t = toks[i]
                if t.type == tokenize.NAME and t.string == "import":
                    break
                module_tokens.append(t)
                i += 1
            module = _join_module(module_tokens)
            for prefix in forbidden_modules:
                if module.startswith(prefix):
                    hits.append(module)
                    break
        elif tok.type == tokenize.NAME and tok.string == "import":
            module_tokens = []
            i += 1
            while i < n:
                t = toks[i]
                if t.type in (tokenize.NEWLINE, tokenize.NL, tokenize.SEMI):
                    break
                module_tokens.append(t)
                i += 1
            module = _join_module(module_tokens)
            for prefix in forbidden_modules:
                if module.startswith(prefix):
                    hits.append(module)
                    break
        i += 1
    return hits


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    file_path = str(inputs.get("file_path", "ui_chart_v3/server.py"))
    forbidden = inputs.get("forbidden_modules")
    if isinstance(forbidden, list) and forbidden:
        forbidden_modules = tuple(str(x) for x in forbidden)
    else:
        forbidden_modules = _default_forbidden_modules()

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as exc:
        return {
            "ok": False,
            "details": f"read_failed:{type(exc).__name__}",
            "metrics": {},
        }

    hits = _scan_imports(text, forbidden_modules)
    ok = len(hits) == 0
    details = "ok" if ok else "forbidden_imports=" + ",".join(hits)
    return {
        "ok": ok,
        "details": details,
        "metrics": {
            "forbidden_count": len(hits),
            "forbidden": hits,
        },
    }
