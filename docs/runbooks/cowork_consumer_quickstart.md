# Cowork Consumer Quickstart — Public `/api/v3/*` Read-Only API

> **Status**: Active (post slice 058.5, 2026-05-04)
> **Audience**: cowork bot / external publication agents that need SMC platform output
> **Companion ADRs**: [ADR-0058](../adr/0058-public-readonly-api-auth.md) · [SECURITY.md](../../SECURITY.md)

---

## 1. Base URL & auth

```
Base:   https://aione-smc.com/api/v3/
Header: X-API-Key: tk_<64 hex>            # required on every request
```

Token is provisioned by ops via `tools/api_v3/issue_token.py` and delivered out-of-band
(NEVER commit it). Default TTL = 90 days; ops will rotate before expiry with a 7-day
overlap window where both old and new tokens accept traffic.

Missing or wrong key → `401` envelope:

```json
{ "kind": "error", "schema_version": "v3.0", "server_ts": "...",
  "disclaimer": "...",
  "data": { "code": "missing_api_key" | "invalid_api_key", "message": "..." } }
```

---

## 2. Envelope contract (all endpoints)

```jsonc
{
  "schema_version": "v3.0",     // bump = breaking change → consumer must adapt
  "kind": "<endpoint name>",    // e.g. "signals_latest", "bias_latest"
  "server_ts": "2026-05-04T18:42:11Z",
  "disclaimer": "Educational/research data only. Not financial advice. ...",
  "data":  { ... }              // OR
  "items": [ ... ],             // (list endpoints only)
  "total": 42                   // (list endpoints only)
}
```

**Hard rule**: consumer **MUST surface the `disclaimer` text** in every published output
(channel post, digest, alert, etc.). See ADR-0058 §10 / X28 invariant.

---

## 3. The five endpoints

| Endpoint | Method | Purpose | Key params |
|---|---|---|---|
| `/api/v3/signals/latest` | GET | Most recent active signals | `limit≤100` (def 10), `source=tda_cascade\|smc_narrative\|all` |
| `/api/v3/signals/journal` | GET | Per-day signal journal | `date=YYYY-MM-DD` (≤90d back), `symbol=`, `source=` |
| `/api/v3/bias/latest` | GET | Multi-TF directional bias | `symbol=XAU/USD` (req) |
| `/api/v3/narrative/snapshot` | GET | Active scenario / context | `symbol=` (req), `tf=900` (def, M15 = 900) |
| `/api/v3/macro/context` | GET | Session, killzone, market phase | (none) |

Full param/response shapes: see [docs/contracts.md](../contracts.md) and the
schemas embedded in [`runtime/api_v3/endpoints.py`](../../runtime/api_v3/endpoints.py).

### 3.1 Quick `curl` smoke test

```bash
TOKEN="tk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
curl -s -H "X-API-Key: $TOKEN" \
  "https://aione-smc.com/api/v3/signals/latest?limit=5&source=tda_cascade" \
  | jq '.kind, .total, .items[0]'
```

---

## 4. Rate limits & quotas

| Limit | Value | Action when exceeded |
|---|---|---|
| Requests / IP / sec | **2 r/s** (burst 30) | nginx returns `503` immediately |
| Concurrent connections / IP | **20** | nginx `503` |
| `?limit` hard cap | **100** | API returns `400 limit_too_large` envelope |
| `?date` lookback | **90 days** | API returns `400 date_too_old` envelope |

**Recommended client behaviour**:

- Cache idempotent GETs for 30 s (`Cache-Control: no-store` on responses is set for
  privacy, not freshness — server-side data is updated on the order of seconds anyway).
- Retry on `429`/`503` with exponential backoff (start 2 s, cap 30 s, jitter ±20%).
- Fail-loud (alert ops) on `schema_version` mismatch. Do NOT silently coerce.
- Alert ops on **any** `5xx` other than `503 limit_exceeded`.

---

## 5. Error codes (envelope `data.code`)

| Code | HTTP | Meaning |
|---|---|---|
| `missing_api_key` | 401 | No `X-API-Key` header |
| `invalid_api_key` | 401 | Token unknown or expired |
| `auth_backend_unavailable` | 503 | Token store (Redis) down — retry |
| `auth_unconfigured` | 503 | Server misconfigured — alert ops |
| `bad_param` | 400 | Param shape wrong (e.g. malformed date) |
| `limit_too_large` | 400 | `?limit > 100` |
| `date_too_old` | 400 | `?date` further back than 90 days |
| `source_invalid` | 400 | `?source` not in `{tda_cascade, smc_narrative, all}` |
| `not_found` | 404 | Unknown `/api/v3/*` path |
| `data_unavailable` | 503 | Upstream SMC runner not warmed up — retry |

---

## 6. Consumer obligations (X28 — anti-redrive)

The platform is **the SSOT** for all SMC-derived values. Consumer **MUST NOT** re-derive
any of:

- `headline`, `bias`, `grade`, `confidence`
- `entry`, `sl`, `tp`, `target_desc`, `r_r`
- `market_phase`, `session`, `killzone`
- Zone classifications (OB / FVG / liquidity / IFVG / Breaker)

Consumer **MAY**:

- Format / translate / shorten for human readability
- Aggregate multiple signals into a single post (preserving each signal's grade & disclaimer)
- Add its own metadata (publishing channel, post ID, etc.)
- Filter by grade / source / symbol

Violation = X28 in the platform constitution. Spotted = consumer access revoked.

---

## 7. Operational contacts & changes

- **Token rotation**: ops emails consumer 7 days before expiry with new token + cutover date
- **Schema bump** (v3.0 → v4.0): minimum 30-day notice, parallel-serve both versions for 14 days
- **Endpoint deprecation**: `Sunset` HTTP header set 60 days in advance; consumer alerts on header presence
- **Outage / degraded data**: `degraded[]` field appears in response; consumer should pause publication when present

For incidents or token issues: ping ops in the dedicated ops channel.

---

## 8. Audit trail

Every request is logged to `data_v3/_audit/api_v3_access-YYYY-MM-DD.jsonl` on the
server with: timestamp, consumer name (resolved from token), hashed IP (per-day salt,
GDPR-friendly), method, path, query, status, latency. Retention: 90 days. Token
values are NEVER persisted. See ADR-0058 §3.5 / F-S3-002.
