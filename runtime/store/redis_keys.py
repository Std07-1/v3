from __future__ import annotations


def symbol_key(symbol: str) -> str:
    return str(symbol).strip().replace("/", "_")
