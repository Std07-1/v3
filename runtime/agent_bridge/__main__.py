"""`python -m runtime.agent_bridge` — точка входу процесу smc-agent-bridge (ADR-0090 S1)."""
from runtime.agent_bridge.app import main

raise SystemExit(main())
