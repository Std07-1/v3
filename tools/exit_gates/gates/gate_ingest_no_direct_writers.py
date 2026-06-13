from __future__ import annotations

import io
import os
import tokenize
from typing import Any, Dict, List, Tuple


def _default_forbidden_modules() -> Tuple[str, ...]:
    return (
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


def _scan_imports(
    text: str, forbidden_modules: Tuple[str, ...]
) -> List[Tuple[str, List[str]]]:
    """Return (module, imported_symbols) per import of a forbidden module.

    `from M import a, b`  → (M, ['a', 'b']);  plain `import M` → (M, []).
    Symbols let the caller allow read-only helpers — the gate forbids direct
    *writers*, not the mere presence of the module name.
    """
    hits: List[Tuple[str, List[str]]] = []
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
            # collect imported symbols after `import` until end-of-statement;
            # drop the alias name in `x as y` (keep the original x).
            symbols: List[str] = []
            skip_alias = False
            i += 1  # step past the `import` token
            while i < n:
                t = toks[i]
                if t.type in (tokenize.NEWLINE, tokenize.NL, tokenize.SEMI):
                    break
                if t.type == tokenize.NAME:
                    if t.string == "as":
                        skip_alias = True
                    elif skip_alias:
                        skip_alias = False
                    else:
                        symbols.append(t.string)
                i += 1
            for prefix in forbidden_modules:
                if module.startswith(prefix):
                    hits.append((module, symbols))
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
                    hits.append((module, []))
                    break
        i += 1
    return hits


def _iter_py_files(root_dir: str) -> List[str]:
    out: List[str] = []
    for base, _dirs, files in os.walk(root_dir):
        for name in files:
            if not name.endswith(".py"):
                continue
            out.append(os.path.join(base, name))
    return out


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    root_dir = str(inputs.get("root_dir", "runtime/ingest"))
    forbidden = inputs.get("forbidden_modules")
    if isinstance(forbidden, list) and forbidden:
        forbidden_modules = tuple(str(x) for x in forbidden)
    else:
        forbidden_modules = _default_forbidden_modules()
    # Read-only helpers explicitly allowed: the gate forbids direct *writers*,
    # so importing a pure reader from an otherwise-forbidden module is fine.
    allow = inputs.get("allow_symbols")
    allow_symbols = (
        frozenset(str(x) for x in allow) if isinstance(allow, list) else frozenset()
    )

    hits: List[str] = []
    scanned = 0
    for path in _iter_py_files(root_dir):
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as exc:
            return {
                "ok": False,
                "details": f"read_failed:{type(exc).__name__}",
                "metrics": {},
            }
        scanned += 1
        for module, symbols in _scan_imports(text, forbidden_modules):
            # OK only if EVERY imported symbol is an allow-listed reader.
            # Bare `import M` (symbols == []) is never excused — it exposes
            # the whole module including its writers.
            if symbols and all(s in allow_symbols for s in symbols):
                continue
            hits.append(f"{path}:{module}")

    ok = len(hits) == 0
    details = "ok" if ok else "forbidden_imports=" + ",".join(hits)
    return {
        "ok": ok,
        "details": details,
        "metrics": {
            "files_scanned": scanned,
            "forbidden_count": len(hits),
            "forbidden": hits,
        },
    }
