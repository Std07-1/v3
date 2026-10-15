"""`python -m runtime.alerts` — точка входу процесу smc-alerts (ADR-0114)."""

from runtime.alerts.service import main

raise SystemExit(main())
