from __future__ import annotations


def symbol_key(symbol: str) -> str:
    return str(symbol).strip().replace("/", "_")


def preview_curr_key(ns: str, symbol: str, tf_s: int) -> str:
    return f"{ns}:preview:curr:{symbol_key(symbol)}:{int(tf_s)}"


def preview_tail_key(ns: str, symbol: str, tf_s: int) -> str:
    return f"{ns}:preview:tail:{symbol_key(symbol)}:{int(tf_s)}"


def preview_updates_seq_key(ns: str, symbol: str, tf_s: int) -> str:
    return f"{ns}:preview:updates:{symbol_key(symbol)}:{int(tf_s)}:seq"


def preview_updates_list_key(ns: str, symbol: str, tf_s: int) -> str:
    return f"{ns}:preview:updates:{symbol_key(symbol)}:{int(tf_s)}:list"
