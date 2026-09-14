"""CLI: `python -m tools.repair.first_tick_m1 <fetch|plan|apply|verify|rollback> ...` (ADR-0096 §3.3 B). Python 3.7.

Модуль фази імпортується ЛИШЕ після вибору фази: fetch іде в .venv37 (Python 3.7) і не має права навіть
імпортувати plan/apply/verify з їхніми платформними залежностями.
"""

from __future__ import annotations

import importlib
import sys
from typing import List, Optional

PHASES = ("fetch", "plan", "apply", "verify", "rollback")
USAGE = "usage: python -m tools.repair.first_tick_m1 {%s} [--help] ..." % ",".join(PHASES)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in PHASES:
        print(USAGE, file=sys.stderr)
        return 2
    phase_module = importlib.import_module("tools.repair.first_tick_m1.%s" % args[0])
    return int(phase_module.main(args[1:]))


if __name__ == "__main__":
    sys.exit(main())
