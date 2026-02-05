from __future__ import annotations

import logging
from typing import Callable, Optional


def run_with_shutdown(run_fn: Callable[[], None], cleanup_fn: Optional[Callable[[], None]]) -> Optional[int]:
    try:
        run_fn()
    except KeyboardInterrupt:
        logging.info("Зупинено користувачем (KeyboardInterrupt) у main.")
        return 0
    finally:
        if cleanup_fn is not None:
            cleanup_fn()
    return None
