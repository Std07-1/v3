# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| v3.x (current) | ✅ |
| v2.x and older | ❌ |

## Reporting a Vulnerability

If you discover a security vulnerability in Trading Platform v3, please report it responsibly:

1. **Email**: Contact the owner directly (see LICENSE for contact info).
2. **Do NOT** open a public GitHub issue for security vulnerabilities.
3. **Include**:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

## Response Timeline

- **Acknowledgment**: within 48 hours
- **Assessment**: within 7 days
- **Fix (S0/S1)**: within 14 days
- **Fix (S2/S3)**: within 30 days

## Security Design Principles

- All services bind to `127.0.0.1` (localhost only)
- Secrets stored in `.env` (gitignored), never committed
- Credentials are never logged
- SSOT JSONL writes protected against path traversal
- WebSocket messages have size limits (64 KB)
- User input is sanitized before logging (no log injection)

## Deployment Boundary

- **Default baseline**: all services bind to `127.0.0.1`, single-user workstation deployment.
- **Sanctioned public surface (ADR-0058)**: a single read-only HTTP API is exposed via Cloudflare → nginx → loopback `aiohttp` (`runtime/api_v3/endpoints.py`). Five `GET /api/v3/*` endpoints, every request requires `X-API-Key`.
  - Token format: `tk_{64 hex}` (32 bytes from `secrets.token_bytes`), stored in Redis with `SETEX` TTL (default 90 days).
  - Token issuance/revocation tooling: `tools/api_v3/{issue_token,list_tokens,revoke_token}.py`. Runbook: [docs/runbooks/api_v3_tokens.md](docs/runbooks/api_v3_tokens.md).
  - All other ports (`ws_server` raw `:8000`, Redis `:6379`, internal supervisor) remain bound to `127.0.0.1`. UFW only permits Cloudflare egress IPs on `:80/:443`.
- Any further network exposure, multi-user access, or hosted deployment requires a fresh compliance/security review (R_COMPLIANCE).
- Commercial, team, hosted, or redistributed use requires separate written permission from FXCM while ForexConnect remains in the stack.

## Public API Threat Model (ADR-0058)

OWASP API Security Top 10 (2023) coverage for `/api/v3/*`:

| OWASP | Mitigation | Where |
|-------|------------|-------|
| **A01 Broken Access** | `X-API-Key` validated on every request via `TokenStore.lookup`; unknown/expired token → `401`; scope mismatch → `401`; fail-closed on Redis error → `503` | [runtime/api_v3/endpoints.py](runtime/api_v3/endpoints.py) `_validate_token` |
| **A02 Auth Failures** | Token entropy = `secrets.token_bytes(32).hex()` (256-bit, CSPRNG); Redis `SETEX` TTL (no in-memory cache that survives revocation); revocation = `DEL` is instant | [runtime/api_v3/token_store.py](runtime/api_v3/token_store.py) |
| **A03 Property Exposure** | Endpoints emit canonical backend shapes verbatim; no `?include_internal` flag is honored; sensitive fields opt-in only (currently none) | endpoints.py |
| **A04 Resource Consumption** | `?limit` hard cap = 100; `?date` rejected if older than 90 days; per-IP rate limit `2 r/s` burst 30 (= 120 r/min); per-IP `limit_conn` 20 | endpoints.py + `tools/smc-nginx-v4-hardened.conf` |
| **A05 Function-level Auth** | nginx `limit_except GET HEAD OPTIONS { deny all; }` zero-trusts non-read methods at the front door; no `POST/PUT/DELETE` handlers exist in `endpoints.py` | nginx vhost |
| **A07 ID/Auth Failures** | Token store TTL is authoritative; no nginx `auth_request` cache (avoids stale-after-revoke); Cloudflare "Always HTTPS" enforces TLS browser→edge | TokenStore + CF |
| **A08 Misconfiguration** | Empty `Access-Control-Allow-Origin ""` blocks browser cross-origin reads; `X-Content-Type-Options nosniff`; `Referrer-Policy no-referrer`; `Cache-Control no-store`; structured 404 catch-all (`F-S2-004`) instead of leaking 500 stack traces | nginx vhost + endpoints.py `_handle_v3_not_found` |
| **A09 Logging** | Structured logs (`api_v3_auth_reject token_prefix=…`); only first 8 chars of token logged (full token never logged); nginx access logs do not record request headers; rate-limit denials are logged by nginx with consumer IP. **Audit JSONL** at `data_v3/_audit/api_v3_access-YYYY-MM-DD.jsonl` (F-S3-002): per-request `{ts, consumer, ip_hash, method, path, query, status, latency_ms}`. IP hashed with daily-rotating salt (no cross-day correlation, GDPR-friendly); 90-day retention enforced on registration. Token values never persisted. | endpoints.py `audit_middleware` + nginx |
| **A10 SSRF** | Not applicable — endpoints accept no URL/host parameters; all data sources are local filesystem or in-process state | n/a |

### Public API Disclaimer

Every `/api/v3/*` JSON envelope includes a top-level `disclaimer` field:
> "Educational/research data only. Not financial advice. No recommendation to buy or sell any instrument."

Consumers MUST surface this disclaimer (or an equivalent) to end users. Stripping it violates the API contract.

## Automated Enforcement

- CI workflow: `.github/workflows/ci.yml`
- Static governance gates: `python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.ci.json`
- Python dependency scan: `pip-audit -r requirements.txt`
- Python SAST baseline: `bandit -q -r app core runtime tools`
- Frontend dependency scan: `npm audit --audit-level=high --omit=dev`
- Dependency drift automation: `.github/dependabot.yml`

## Out of Scope

- Vulnerabilities in third-party dependencies (report to upstream)
- Issues requiring physical access to the host machine
- Social engineering attacks
