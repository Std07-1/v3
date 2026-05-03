"""Smoke test for ADR-0058 slice 058.1 deployment."""

from runtime.api_v3.auth_validator import app
from fastapi.testclient import TestClient

c = TestClient(app)
r = c.get("/health")
print("VPS_SMOKE_HEALTH", r.status_code, r.json())

r = c.get("/_auth")
print("VPS_SMOKE_AUTH_NO_HEADER", r.status_code, r.json())

r = c.get("/_auth", headers={"X-API-Key": "tk_" + "0" * 64})
print("VPS_SMOKE_AUTH_BAD_TOKEN", r.status_code, r.json())
