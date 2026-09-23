"""Легасі-якір у секундах не повертається (ADR-0095 §3.4, §3.7, слайс S5c).

- AST по `git ls-files core runtime tools app`: рядки-ключі `LEGACY_ANCHOR_KEYS` і імена `RETIRED_ANCHOR_NAMES` у
  рядкових літералах, іменах, атрибутах, імпортах (включно з лінивими всередині функцій), def, аргументах і kwargs.
  Дозволено рівно одне місце — присвоєння цих констант у `core/config_loader.py` (D15.2: одне джерело списку).
- Config з легасі-ключем відмовляє `load_system_config` і `uds._load_cfg` (CONFIG_LEGACY_ANCHOR_KEY з іменем ключа).
- Config репозиторію має `htf_anchor` і не має легасі-ключів.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Set

import pytest

import core.buckets
import core.derive
from core.config_loader import (
    LEGACY_ANCHOR_KEYS,
    RETIRED_ANCHOR_NAMES,
    find_legacy_anchor_keys,
    htf_anchor_rule_resolver,
    load_system_config,
)
from runtime.store.uds import _load_cfg

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCAN_ROOTS = ("core", "runtime", "tools", "app")
_ALLOW_FILE = "core/config_loader.py"
_ALLOW_CONSTANTS = frozenset({"LEGACY_ANCHOR_KEYS", "RETIRED_ANCHOR_NAMES"})


def _forbidden_names() -> FrozenSet[str]:
    return frozenset(key_path.rsplit(".", 1)[-1] for key_path in LEGACY_ANCHOR_KEYS) | frozenset(RETIRED_ANCHOR_NAMES)


_FORBIDDEN = _forbidden_names()
# Ім'я цілим токеном: `htf_anchor_offset_s` і метрика `has_legacy_resolve_anchor_offset_ms` — не легасі-ключі
_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(%s)(?![A-Za-z0-9_])" % "|".join(re.escape(n) for n in sorted(_FORBIDDEN, key=len, reverse=True))
)


def _allowed_node_ids(tree: ast.AST) -> Set[int]:
    """Вузли присвоєння констант-джерел списку (і все всередині них) — єдине дозволене місце."""
    allowed: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in _ALLOW_CONSTANTS for t in node.targets
        ):
            allowed.update(id(n) for n in ast.walk(node))
    return allowed


def _identifiers(node: ast.AST) -> Iterator[str]:
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, ast.Attribute):
        yield node.attr
    elif isinstance(node, ast.alias):
        yield node.name.rsplit(".", 1)[-1]
        if node.asname:
            yield node.asname
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name
    elif isinstance(node, ast.keyword) and node.arg:
        yield node.arg
    elif isinstance(node, ast.arg):
        yield node.arg


def scan_source(source: str, rel_path: str, allow_constants: bool = True) -> List[str]:
    """Порушення у модулі: `шлях:рядок вид токен`. `allow_constants` — вимкнути allowlist (перевірка самого сканера)."""
    tree = ast.parse(source, filename=rel_path)
    allowed = _allowed_node_ids(tree) if allow_constants and rel_path == _ALLOW_FILE else set()
    violations: List[str] = []
    for node in ast.walk(tree):
        if id(node) in allowed:
            continue
        where = "%s:%s" % (rel_path, getattr(node, "lineno", "?"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            violations.extend("%s рядок %s" % (where, m.group(1)) for m in _TOKEN_RE.finditer(node.value))
        violations.extend("%s ім'я %s" % (where, name) for name in _identifiers(node) if name in _FORBIDDEN)
    return violations


def _tracked_python_files() -> List[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", *_SCAN_ROOTS], cwd=str(_REPO_ROOT), capture_output=True, text=True, check=True
    ).stdout
    return [p for p in out.splitlines() if p.endswith(".py")]


def test_no_module_reads_legacy_anchor_keys():
    files = _tracked_python_files()
    assert _ALLOW_FILE in files and len(files) > 100, "git ls-files повернув не той набір: %d" % len(files)
    violations: List[str] = []
    for rel_path in files:
        violations.extend(scan_source((_REPO_ROOT / rel_path).read_text(encoding="utf-8"), rel_path))
    assert violations == [], "легасі-якір у коді (ADR-0095 §3.4):\n" + "\n".join(violations)


def test_scan_list_is_not_vacuous_and_allowlist_is_exactly_the_constants():
    """Без allowlist сканер бачить кожне ім'я у константах config_loader — отже allowlist нічого зайвого не ховає."""
    assert {"day_anchor_offset_s", "day_anchor_offset_s_d1", "binance.d1_anchor_offset_s"} <= set(LEGACY_ANCHOR_KEYS)
    assert {"resolve_anchor_offset_ms", "resolve_cascade_anchor_s"} <= set(RETIRED_ANCHOR_NAMES)
    # Валідатори «членства в alt» (ADR-0095 §3.3) — корінь дефекту, а не лише статичне API
    assert {
        "select_anchor_offset_for_open_ms",
        "_h4_anchor_offsets",
        "_d1_anchor_offsets",
        "anchor_offset_for_tf",
        "_anchor_offset_alts_for_tf",
        "_legal_anchors_ms",
    } <= set(RETIRED_ANCHOR_NAMES)
    source = (_REPO_ROOT / _ALLOW_FILE).read_text(encoding="utf-8")
    unguarded = scan_source(source, _ALLOW_FILE, allow_constants=False)
    assert {v.rsplit(" ", 1)[-1] for v in unguarded} == set(_FORBIDDEN)
    assert scan_source(source, _ALLOW_FILE) == []
    assert scan_source(source, "core/other.py") != []  # allowlist прив'язаний до файлу, а не до імені константи


@pytest.mark.parametrize(
    "snippet",
    [
        "def f():\n    from core.buckets import resolve_anchor_offset_ms\n    return resolve_anchor_offset_ms(1, {})\n",
        "def f(cfg):\n    return cfg.get('day_anchor_offset_s_d1', 0)\n",
        "def f(cfg):\n    return cfg['binance']['d1_anchor_offset_s']\n",
        "import os\nX = os.environ.get('FXCM_DAY_ANCHOR_OFFSET_S', '0')\n",
        "import core.derive as d\nY = d.resolve_cascade_anchor_s(86400)\n",
        "import importlib\nM = getattr(importlib.import_module('core.buckets'), 'resolve_anchor_offset_ms')\n",
        "def g(day_anchor_offset_s=0):\n    return day_anchor_offset_s\n",
        "h(day_anchor_offset_s_alt2=0)\n",
        "K = f'{p}.day_anchor_offset_s_d1_alt'\n",
        "def f(ts):\n    from runtime.store.ssot_jsonl import select_anchor_offset_for_open_ms\n    return ts\n",
        "def _legal_anchors_ms(cfg):\n    return {0}\n",
        "class P:\n    def anchor_offset_for_tf(self, tf_s):\n        return self._anchor_offset_alts_for_tf(tf_s)\n",
    ],
)
def test_scanner_catches_lazy_imports_config_reads_env_and_kwargs(snippet):
    assert scan_source(snippet, "runtime/x.py") != []


def test_scanner_ignores_season_api_and_metric_names():
    snippet = (
        "from core.session_anchor import htf_anchor_offset_s\n"
        "X = htf_anchor_offset_s(14400, 0, 'ny_close_us_dst')\n"
        "m = {'has_legacy_resolve_anchor_offset_ms': False, 'issue': 'legacy_resolve_anchor_offset_ms'}\n"
    )
    assert scan_source(snippet, "tools/x.py") == []


def test_retired_anchor_api_deleted():
    assert not hasattr(core.buckets, "resolve_anchor_offset_ms")
    assert not hasattr(core.derive, "resolve_cascade_anchor_s")


def _repo_cfg() -> Dict[str, Any]:
    return json.loads((_REPO_ROOT / "config.json").read_text(encoding="utf-8"))


def _with_key(cfg: Dict[str, Any], key_path: str, value: Optional[int]) -> Dict[str, Any]:
    node = cfg
    *parents, leaf = key_path.split(".")
    for part in parents:
        node = node.setdefault(part, {})
    node[leaf] = value
    return cfg


@pytest.mark.parametrize("key_path", LEGACY_ANCHOR_KEYS)
@pytest.mark.parametrize("value", [79200, 0, None])
def test_config_legacy_anchor_key_refused(tmp_path, key_path, value):
    """Будь-який легасі-ключ, навіть 0 чи null: старт відмовляє з іменем ключа, а не бере тихий якір."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps(_with_key(_repo_cfg(), key_path, value)), encoding="utf-8")
    expected = r"CONFIG_LEGACY_ANCHOR_KEY key=%s source=" % re.escape(key_path)
    with pytest.raises(ValueError, match=expected):
        load_system_config(str(path))
    for strict in (True, False):
        with pytest.raises(ValueError, match=expected):
            _load_cfg(str(path), strict=strict)


def test_config_several_legacy_keys_all_named(tmp_path):
    cfg = _with_key(_with_key(_repo_cfg(), "day_anchor_offset_s", 79200), "binance.d1_anchor_offset_s", 0)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(ValueError, match="key=day_anchor_offset_s,binance.d1_anchor_offset_s "):
        load_system_config(str(path))


def test_repo_config_has_htf_anchor_no_legacy_keys():
    cfg = load_system_config(str(_REPO_ROOT / "config.json"))
    assert find_legacy_anchor_keys(cfg) == []
    assert find_legacy_anchor_keys(_repo_cfg()) == []
    rule_for_symbol = htf_anchor_rule_resolver(cfg)
    assert {s: rule_for_symbol(s) for s in cfg["symbols"]} == {s: "ny_close_us_dst" for s in cfg["symbols"]}
    assert {rule_for_symbol(s) for s in cfg["binance"]["symbols"]} == {"utc_midnight"}
