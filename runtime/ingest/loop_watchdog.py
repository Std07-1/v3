"""Watchdog головного циклу для процесів із блокуючими нативними викликами.

ADR-0054 §3.6 п.3. Інцидент 06.09.2026: ``broker_sidecar`` завис у синхронному
``PriceHistoryCommunicator.get_history`` (futex без таймауту) на 5.5 год; кооперативний
SIGTERM-обробник не виконався, бо головний потік не повертався у байткод; після
``supervisorctl restart`` процес пережив зупинку сиротою з живими FXCM-сесіями.

Нативний C-виклик неможливо перервати ні сигналом, ні прапорцем — Python виконує
обробники лише між байткодами. Єдиний дієвий засіб — окремий потік, який бачить, що
головний цикл не рухається, і завершує процес цілком; supervisor/``app.main`` піднімають
чистий. Тому тут два примітиви:

* :class:`LoopWatchdog` — головний цикл позначає вхід/вихід у блокуючий виклик; демон-потік
  раз на ``poll_s`` перевіряє, чи виклик не триває довше ``timeout_s``.
* :func:`arm_exit_timer` — SIGTERM-обробник озброює таймер: якщо цикл не вийшов сам за
  ``grace_s``, процес завершується примусово. Межа чесності: обробник сигналу виконується
  лише в головному потоці між байткодами, тож для потоку, який УЖЕ завис у нативному
  виклику, таймер не буде озброєно взагалі — той кейс закривають LoopWatchdog і зовнішня
  ескалація ``app.main._terminate`` (TERM → wait → SIGKILL). Grace тримати меншим за
  supervisor ``stopwaitsecs``.

Обидва потоки-наглядачі потребують GIL, тобто спрацьовують лише якщо завислий нативний
виклик його відпустив. Для інциденту 06.09 це доведено (SDK-потік relay-їв тіки всі
5.5 год зависання); якщо колись виклик зависне З утриманим GIL — єдина дієва рейка
= ``app.main`` (SIGKILL дитині), і саме тому вона первинна, а не цей модуль.

Модуль працює під Python 3.7 (``.venv37``, ADR-0016) — без walrus, без generics-у-рантаймі.
Чистий: жодного I/O, годинник і exit-функція інжектуються для тестів.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional, Tuple

# EX_TEMPFAIL (sysexits.h): «тимчасовий збій, повторіть» — app.main/supervisor перезапускають.
EXIT_CODE_WATCHDOG_HANG = 75
# SIGTERM, який процес не зміг обробити сам: 128 + 15, як його трактує shell.
EXIT_CODE_SIGTERM_FORCED = 143


class LoopWatchdog:
    """Слідкує, чи головний цикл не застряг усередині одного блокуючого виклику."""

    def __init__(self, timeout_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s має бути > 0, отримано %r" % (timeout_s,))
        self._timeout_s = float(timeout_s)
        self._clock = clock
        self._lock = threading.Lock()
        self._label = None  # type: Optional[str]
        self._since = 0.0

    def enter(self, label: str) -> None:
        """Головний цикл входить у блокуючий виклик ``label`` (login, fetch_m1 …)."""
        with self._lock:
            self._label = label
            self._since = self._clock()

    def leave(self) -> None:
        """Блокуючий виклик повернувся — цикл живий."""
        with self._lock:
            self._label = None

    def in_flight(self) -> bool:
        with self._lock:
            return self._label is not None

    def stuck_for(self) -> Optional[Tuple[str, float]]:
        """(label, elapsed_s), якщо поточний виклик триває довше timeout_s; інакше None."""
        with self._lock:
            if self._label is None:
                return None
            elapsed = self._clock() - self._since
            if elapsed <= self._timeout_s:
                return None
            return self._label, elapsed

    def start_thread(
        self,
        exit_fn: Callable[[int], None] = os._exit,
        poll_s: float = 1.0,
        log: logging.Logger = logging.getLogger(__name__),
    ) -> threading.Thread:
        """Запустити демон-потік нагляду. exit_fn(75) при зависанні — процес не лишається RUNNING."""

        def _watch() -> None:
            while True:
                time.sleep(poll_s)
                stuck = self.stuck_for()
                if stuck is None:
                    continue
                label, elapsed = stuck
                log.critical(
                    "LOOP_WATCHDOG_HANG label=%s stuck_s=%.0f timeout_s=%.0f -> exit %d",
                    label, elapsed, self._timeout_s, EXIT_CODE_WATCHDOG_HANG,
                )
                exit_fn(EXIT_CODE_WATCHDOG_HANG)
                return

        thread = threading.Thread(target=_watch, name="loop-watchdog", daemon=True)
        thread.start()
        return thread


def arm_exit_timer(
    grace_s: float,
    reason: str,
    exit_fn: Callable[[int], None] = os._exit,
    log: logging.Logger = logging.getLogger(__name__),
) -> threading.Timer:
    """Примусовий вихід через grace_s, якщо цикл не завершився сам (SIGTERM під нативним викликом)."""

    def _force_exit() -> None:
        log.critical("LOOP_SIGTERM_FORCED reason=%s grace_s=%.0f -> exit %d", reason, grace_s, EXIT_CODE_SIGTERM_FORCED)
        exit_fn(EXIT_CODE_SIGTERM_FORCED)

    timer = threading.Timer(grace_s, _force_exit)
    timer.daemon = True
    timer.start()
    return timer
