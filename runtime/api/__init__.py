"""runtime.api — security primitives for HTTP API (ADR-0052 S7/S8).

Modules:
    auth        Bearer-token validation + HMAC response signing.
    rate_limit  Redis-backed per-identity token bucket (10 msg/min default).
    audit       Immutable event stream → Redis XADD (replay protection).

All modules ship feature-flag OFF by default (``enabled=False``); callers opt-in
via config. Security primitives are fail-open with *loud* warnings (I7
degraded-but-loud): a Redis outage must never block a legitimate request, but
every degradation emits an explicit log line and audit event.
"""
