"""
tests/test_adr0042_delta_parity.py — ADR-0042 P2: delta frame metadata contract.

DF-2: delta frame with complete bar MUST include zone_grades, bias_map,
momentum_map, pd_state when SmcRunner provides them.

Перевіряє що SmcRunner protocol має всі потрібні method signatures
і що runtime/ws/ws_server.py delta loop викликає metadata getters
коли _any_complete=True.

pytest tests/test_adr0042_delta_parity.py -v
"""

from __future__ import annotations

import ast
import textwrap

import pytest


# ── DF-2 Contract: SmcRunnerLike has required methods ─────────────


REQUIRED_METADATA_METHODS = [
    "get_zone_grades",
    "get_bias_map",
    "get_momentum_map",
    "get_pd_state",
    "get_signals",
]


def test_smc_runner_protocol_has_metadata_methods():
    """DF-2: SmcRunnerLike protocol declares all metadata getter methods."""
    from runtime.ws.ws_server import SmcRunnerLike
    import inspect

    members = {name for name, _ in inspect.getmembers(SmcRunnerLike)}
    for method in REQUIRED_METADATA_METHODS:
        assert method in members, (
            f"SmcRunnerLike protocol missing {method} — ADR-0042 DF-2 violation"
        )


def test_smc_runner_impl_has_metadata_methods():
    """DF-2: SmcRunner implementation provides all metadata getters."""
    from runtime.smc.smc_runner import SmcRunner
    for method in REQUIRED_METADATA_METHODS:
        assert hasattr(SmcRunner, method), (
            f"SmcRunner missing {method} — ADR-0042 DF-2 violation"
        )
        assert callable(getattr(SmcRunner, method)), (
            f"SmcRunner.{method} not callable"
        )


# ── DF-2 AST gate: delta loop calls metadata getters ─────────────


def _parse_ws_server_ast():
    """Parse ws_server.py AST and find the global delta loop function."""
    import pathlib

    path = pathlib.Path("runtime/ws/ws_server.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    return tree, source


def _find_function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _collect_attr_calls(node: ast.AST) -> set[str]:
    """Collect all `obj.method(...)` call names from a function body."""
    calls = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            calls.add(child.func.attr)
    return calls


def test_delta_loop_calls_zone_grades():
    """AST: delta loop must call get_zone_grades (ADR-0042 DF-2)."""
    tree, _ = _parse_ws_server_ast()
    fn = _find_function(tree, "_global_delta_loop")
    assert fn is not None, "_global_delta_loop not found in ws_server.py"
    calls = _collect_attr_calls(fn)
    assert "get_zone_grades" in calls, (
        "ADR-0042 DF-2: _global_delta_loop must call get_zone_grades"
    )


def test_delta_loop_calls_bias_map():
    """AST: delta loop must call get_bias_map (ADR-0042 DF-2)."""
    tree, _ = _parse_ws_server_ast()
    fn = _find_function(tree, "_global_delta_loop")
    assert fn is not None
    calls = _collect_attr_calls(fn)
    assert "get_bias_map" in calls, (
        "ADR-0042 DF-2: _global_delta_loop must call get_bias_map"
    )


def test_delta_loop_calls_pd_state():
    """AST: delta loop must call get_pd_state (ADR-0042 DF-2)."""
    tree, _ = _parse_ws_server_ast()
    fn = _find_function(tree, "_global_delta_loop")
    assert fn is not None
    calls = _collect_attr_calls(fn)
    assert "get_pd_state" in calls, (
        "ADR-0042 DF-2: _global_delta_loop must call get_pd_state"
    )


def test_delta_loop_calls_momentum_map():
    """AST: delta loop must call get_momentum_map (ADR-0042 DF-2)."""
    tree, _ = _parse_ws_server_ast()
    fn = _find_function(tree, "_global_delta_loop")
    assert fn is not None
    calls = _collect_attr_calls(fn)
    assert "get_momentum_map" in calls, (
        "ADR-0042 DF-2: _global_delta_loop must call get_momentum_map"
    )


def test_delta_loop_calls_signals():
    """AST: delta loop must call get_signals (ADR-0042 DF-2)."""
    tree, _ = _parse_ws_server_ast()
    fn = _find_function(tree, "_global_delta_loop")
    assert fn is not None
    calls = _collect_attr_calls(fn)
    assert "get_signals" in calls, (
        "ADR-0042 DF-2: _global_delta_loop must call get_signals"
    )
