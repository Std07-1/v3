"""ADR-0090 §3.0 п.1: платформа (ws_server) не має маршрутів клієнта — консоль = окремий процес."""
from __future__ import annotations

from pathlib import Path


def test_ws_server_has_no_agent_surface():
    """ADR-0090 §3.0 п.1: платформа не має маршрутів archi|agent; консоль = окремий процес."""
    src = Path("runtime/ws/ws_server.py").read_text(encoding="utf-8")
    for needle in ("/api/archi", '"/api/agent/', "_archi_auth", "wake_cards"):
        assert needle not in src, needle
