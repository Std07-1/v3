# runtime/smc/__init__.py
# I/O обгортка SMC Engine (ADR-0024 §3.4).
# Pure logic (core/smc/) ← I/O wrapper (runtime/smc/) ← ws_server
from runtime.smc.smc_runner import SmcRunner

__all__ = ["SmcRunner"]
