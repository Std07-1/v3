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

- Approved baseline: localhost only, single-user workstation deployment.
- Any network exposure, multi-user access, or hosted deployment requires a fresh compliance/security review.
- Commercial, team, hosted, or redistributed use requires separate written permission from FXCM while ForexConnect remains in the stack.

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
