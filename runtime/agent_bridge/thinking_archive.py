"""runtime/agent_bridge/thinking_archive.py — rotation-aware reader thinking-архіву клієнта.

Перенесено з runtime/ws/ws_server.py (ADR-0090 S1, move-only): файли
`v3_thinking_archive*.jsonl` пише зовнішній клієнт у agent_bridge.data_dir (ADR-018 rotation).
Pure: (data_dir, limit, offset) → (page, total); I5 — биті рядки/зниклі файли = warning, не crash.
"""
from __future__ import annotations

import glob
import json
import logging
import os

_log = logging.getLogger("agent_bridge.thinking")

_THINKING_LIVE_FILE = "v3_thinking_archive.jsonl"
_THINKING_ROTATED_GLOB = "v3_thinking_archive_*.jsonl"


def _list_thinking_files(data_dir: str) -> list[str]:
    """Newest-first archive file list: live head, then rotated by name desc.

    Writer (trader-v3 ThinkingArchive) appends to the live file and, on 5 MB,
    renames it to ``v3_thinking_archive_<YYYYMMDD_HHMMSS>.jsonl``. The live
    file is therefore always freshest; rotated files carry a UTC strftime
    suffix that sorts lexicographically == chronologically, so descending name
    order == newest-first. The live file leads unconditionally.
    """
    files: list[str] = []
    live = os.path.join(data_dir, _THINKING_LIVE_FILE)
    if os.path.exists(live):
        files.append(live)
    rotated = glob.glob(os.path.join(data_dir, _THINKING_ROTATED_GLOB))
    rotated.sort(key=os.path.basename, reverse=True)
    files.extend(rotated)
    return files


def read_thinking_records(
    data_dir: str, limit: int, offset: int
) -> tuple[list, int]:
    """Read the thinking archive across live + rotated files, newest-first.

    Returns ``(page, total)`` where ``page`` is the ``offset:offset+limit``
    slice in newest-first order (same wire order the single-file reader used
    via ``entries.reverse()``), and ``total`` counts every non-empty record
    across all files.

    Lazy: page records are JSON-decoded only until ``offset+limit`` are
    collected; further files are line-counted for ``total`` without decoding
    their entries. I5 degraded-but-loud: a corrupt JSON line on the page is
    skipped with a warning (never crashes); a file that vanished between glob
    and open (rotation/cleanup race) is skipped.
    """
    want = offset + limit
    page: list = []
    total = 0
    for fpath in _list_thinking_files(data_dir):
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                lines = [ln.strip() for ln in fh]
        except OSError as exc:
            # File disappeared between glob and open, or unreadable -> skip loud.
            _log.warning("THINKING_READ_SKIP_FILE: %s (%s)", fpath, exc)
            continue
        lines = [ln for ln in lines if ln]
        total += len(lines)
        if len(page) >= want:
            # Already have the full page; only need remaining files' counts.
            continue
        # Within a file the writer appends oldest->newest; reverse for
        # newest-first, then decode only what the page still needs.
        for ln in reversed(lines):
            if len(page) >= want:
                break
            try:
                page.append(json.loads(ln))
            except (json.JSONDecodeError, ValueError):
                _log.warning("THINKING_READ_BAD_JSON: %s", fpath)
                continue
    return page[offset : offset + limit], total
