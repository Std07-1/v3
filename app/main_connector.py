from __future__ import annotations

import logging

from app.composition import ConfigError, build_connector
from app.lifecycle import run_with_shutdown


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def main() -> int:
    setup_logging(verbose=False)
    logging.info("Запуск PollingConnectorB")
    try:
        runner, cleanup_fn = build_connector("config.json")
    except ConfigError as exc:
        if exc.stage == "load":
            logging.exception("Не вдалось завантажити config.json")
        else:
            logging.exception("Невірна конфігурація")
        return 2
    except Exception:
        logging.exception("Не вдалось ініціалізувати FxcmHistoryProvider або запустити engine")
        return 3

    try:
        result = run_with_shutdown(getattr(runner, "run_forever"), cleanup_fn)
        if result is not None:
            return result
    except Exception:
        logging.exception("Помилка в роботі engine")
        return 1

    logging.info("Завершення роботи (main)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
