"""ADR-0058 — Public read-only API sidecar (slice 058.1).

Layer: runtime (I/O — Redis token lookup, FastAPI HTTP surface).
Invariants:
- I0: depends on `core/` and external libs only; no `tools/`, no `ui/` imports.
- I1: read-only. No POST/PUT/DELETE handlers, no Redis writes, no UDS writes.
- I5: degraded-but-loud — Redis unavailable → 503 + structured log; never silent allow.

Modules:
- `token_store` — Redis-backed token lookup (pure logic + thin Redis call).
- `auth_validator` — FastAPI app for nginx `auth_request` (slice 058.1 scope).
"""
