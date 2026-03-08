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

## Out of Scope

- Vulnerabilities in third-party dependencies (report to upstream)
- Issues requiring physical access to the host machine
- Social engineering attacks
