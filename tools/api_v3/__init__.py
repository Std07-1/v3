"""ADR-0058 slice 058.4 — token operational tooling.

CLI scripts for token lifecycle:
    - issue_token   — generate token + SETEX in Redis
    - list_tokens   — read-only audit (SCAN + JSON parse)
    - revoke_token  — DEL (instant)
    - extend_token  — EXPIRE (renewal shortcut, see ADR §3.4 Option B)

All scripts share `_common.get_redis()` for env-driven Redis config.
Reuse `runtime.api_v3.token_store` for token format/key SSOT.
"""
