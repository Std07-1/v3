---
name: God Modules — files exceeding 1500 LOC
description: Files with excessive LOC that violate SoC. Architectural debt candidates for ADR-driven split.
type: project
---

As of 2026-03-24:

| File | LOC | Concern |
|------|-----|---------|
| runtime/store/uds.py | 2419 | Commit, watermark, dedup, coldload, redis priming, preview, tail cache, events, config |
| runtime/ws/ws_server.py | 2018 | HTTP, WS, delta loop, tick relay, SMC, signals, sessions, replay, config, metrics |
| runtime/ingest/polling/m1_poller.py | 1588 | Polling, backfill, recovery, crawl, derive triggers |
| core/smc/engine.py | 1244 | All SMC computation, display filter, snapshot diff |
| app/main.py | 987 | Supervisor, process management, Redis preflight, PID files |

**Why:** Each change to these files has high blast radius and merge conflict probability.

**How to apply:** When a patch touches these files, assess whether the change adds or reduces abstraction levels. If adding: propose split in RFC.
