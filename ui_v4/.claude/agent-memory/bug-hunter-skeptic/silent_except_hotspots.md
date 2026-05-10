---
name: Silent Exception Hotspots in Runtime
description: Locations where except Exception swallows errors without adequate logging. C5 violation pattern.
type: project
---

Critical silent swallows (as of 2026-03-24):

1. **runtime/ws/ws_server.py:873-874** — Redis viewer_count init. `except Exception: pass`. If Redis down at start, viewer_count never written, tick_preview stays idle. NO LOG.
2. **runtime/ws/ws_server.py:891-892** — Redis viewer_count write in loop. `except Exception: pass`. Same pattern, less critical since it retries.
3. **env_profile.py:60-61** — .env file read_text(). `except Exception: pass`. Misreports keys_count=0 with loaded=True.
4. **tools/exit_gates/gates/gate_preview_not_on_disk.py:57-58** — `_iter_tail_lines()` `except Exception: return []`. Silently returns empty on any file I/O error.

Less critical (logged at debug level — may be invisible in production):
- ws_server.py:1123, 1129, 1135, 1141, 1192, 1207 — delta loop SMC errors logged at DEBUG level. In production with INFO threshold, these are invisible.

**Why:** Silent except in data pipeline = silent data loss. The ws_server viewer_count one is the most dangerous because it causes cascading degradation (idle tick_preview).

**How to apply:** When reviewing except blocks in runtime/, verify: (1) specific exception type, (2) WARNING or higher log level, (3) metric increment. Debug-level in data path = effectively silent.
