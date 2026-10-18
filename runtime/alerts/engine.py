"""Двигун сповіщень (ADR-0114 §4.2–4.3): тіки й фінальні M1 → переходи стану → стан і черга на диск разом.

Без мережі: джерела (feeds.py) приносять ціни, двигун викликає чисту логіку `core.alerts.crossing`, кладе спрацювання
у вихідну чергу і комітить стан одним записом — «спрацювало» не існує на диску без запису в черзі. Викликається лише
з одного потоку (цикл asyncio процесу), тож стан без блокувань.

Деградації (I5) — прапорці з деталлю; лог лише на переході (увімкнулась / минула), не на кожен тік.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.alerts.crossing import on_m1_bar, on_tick
from core.alerts.types import AlertState, PriceAlert
from runtime.alerts.store import AlertStore

_log = logging.getLogger(__name__)

M1_MS = 60_000
DEGRADED_STATE_WRITE = "state_write_failed"

Transition = Callable[[PriceAlert], PriceAlert]


@dataclasses.dataclass(frozen=True)
class FinalBar:
    """Фінальна свічка M1 — лише те, що потрібно для перетину."""

    open_ms: int
    low: float
    high: float


def final_bars_from_lwc(rows: Iterable[Mapping[str, Any]]) -> Tuple[List[FinalBar], int]:
    """Рядки UDS (`bars_lwc`: open_time_ms, low, high, complete) → свічки за зростанням часу + кількість відкинутих.

    Не фінальна (`complete` не True) чи без чисел — відкидається: перетин рахуємо лише по фінальних (I3)."""
    bars: List[FinalBar] = []
    rejected = 0
    for row in rows:
        open_ms, low, high = row.get("open_time_ms"), row.get("low"), row.get("high")
        numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (low, high))
        if row.get("complete") is not True or not isinstance(open_ms, int) or not numbers:
            rejected += 1
            continue
        bars.append(FinalBar(open_ms, float(low), float(high)))
    bars.sort(key=lambda bar: bar.open_ms)
    return bars, rejected


class AlertEngine:
    def __init__(self, store: AlertStore, *, gap_after_ms: int, clock_ms: Callable[[], int]) -> None:
        if gap_after_ms <= 0:
            raise ValueError(f"gap_after_ms: очікується > 0, отримано {gap_after_ms!r}")
        self.store = store
        self._gap_after_ms = gap_after_ms
        self._clock_ms = clock_ms
        self._prev_tick_ms: Dict[str, int] = {}
        self._last_price: Dict[str, float] = {}
        self._degraded: Dict[str, str] = {}
        self._unsaved = False  # зміни в пам'яті, яких ще нема на диску (запис не вдався)
        # Слухачі подій (SSE сторінки): (подія, сповіщення) — "fired" після постановки в чергу, "delivered" після ok,
        # "undelivered" — доставки не буде (fail_delivery)
        self.listeners: List[Callable[[str, PriceAlert], None]] = []

    # ── сповіщення з HTTP ────────────────────────────────────────────────

    def put_alert(self, alert: PriceAlert) -> None:
        """Створити чи замінити (взведення з HTTP) — і на диск."""
        self.store.put(alert)
        self.store.drop_stale_failed()
        self._unsaved = True
        self._commit()

    def remove_alert(self, alert_id: str) -> bool:
        """Прибрати (лінію видалено). Вже спрацьоване в черзі все одно доставиться."""
        if self.store.remove(alert_id) is None:
            return False
        self.store.drop_stale_failed()
        self._unsaved = True
        self._commit()
        return True

    def last_price(self, symbol: str) -> Optional[float]:
        """Остання ціна графіка з тіків цього процесу; None — тіків символу ще не було (старт, закритий ринок)."""
        return self._last_price.get(symbol)

    # ── ціни ─────────────────────────────────────────────────────────────

    def apply_tick(self, symbol: str, price: float, tick_ts_ms: int) -> List[PriceAlert]:
        """Тік ціни графіка; повертає щойно спрацьовані."""
        prev = self._prev_tick_ms.get(symbol)
        self._prev_tick_ms[symbol] = tick_ts_ms if prev is None else max(prev, tick_ts_ms)
        self._last_price[symbol] = price

        def transition(alert: PriceAlert) -> PriceAlert:
            return on_tick(alert, price, tick_ts_ms, prev_tick_ts_ms=prev, gap_after_ms=self._gap_after_ms)

        fired, changed = self._step(symbol, transition)
        if changed:  # повтор запису, що не вдався, — не на кожен тік, а в `retry_unsaved` циклу опитування
            self._commit()
        return fired

    def apply_m1_bars(self, symbol: str, bars: Sequence[FinalBar], *, safe_until_ms: int) -> List[PriceAlert]:
        """Фінальні M1 за зростанням часу. `safe_until_ms` — до якого моменту пізніх фінальних уже не буде: позначка
        «перевірено до» стає на нього (свіжіший хвіст читається знову — переходи ідемпотентні)."""
        fired: List[PriceAlert] = []
        changed = False
        for bar in bars:
            bar_fired, bar_changed = self._step(symbol, _m1_transition(bar))
            fired += bar_fired
            changed = changed or bar_changed
        if self.store.mark_checked(symbol, safe_until_ms) or changed:
            self._commit()
        return fired

    def m1_after_ms(self, symbol: str) -> Optional[int]:
        """Виключна нижня межа читання фінальних M1 (UDS/диск читають `(after, to]`): після позначки, але не раніше
        хвилини активації найранішого взведеного; None — взведених нема, читати нічого."""
        starts = [alert.active_from_ms for alert in self._armed(symbol)]
        if not starts:
            return None
        first_minute = min(starts) // M1_MS * M1_MS
        mark = self.store.checked_until_ms.get(symbol)
        return first_minute - 1 if mark is None else max(first_minute - 1, mark)

    def ack_delivered(self, key: Tuple[str, int]) -> None:
        """Доставлено — зняти з черги й записати. Якщо запис не вдався, після рестарту буде дубль, не втрата."""
        entry = self.store.outbox.get(key)
        self.store.ack(key)
        self._unsaved = True
        self._commit()
        if entry is not None:
            self._notify("delivered", entry.alert)

    def fail_delivery(self, key: Tuple[str, int], reason: str) -> None:
        """Не доставлено й не буде: запис лишається в черзі з причиною (сторінка покаже «не доставлено»), повторів нема."""
        entry = self.store.mark_failed(key, reason)
        if entry is None:
            return
        self._unsaved = True
        self._commit()
        _log.warning("ALERTS_UNDELIVERED id=%s version=%s reason=%s", key[0], key[1], reason)
        self._notify("undelivered", entry.alert)

    def note_attempt(self, key: Tuple[str, int]) -> None:
        """Невдала спроба доставки: лічильник у записі черги (на диск — з наступним записом)."""
        entry = self.store.outbox.get(key)
        if entry is not None:
            self.store.outbox[key] = dataclasses.replace(entry, attempts=entry.attempts + 1)
            self._unsaved = True

    def retry_unsaved(self) -> None:
        """Повтор запису, що не вдався (кличе цикл опитування): без нових подій стан інакше лишився б лише в пам'яті."""
        if self._unsaved:
            self._commit()

    # ── деградації ───────────────────────────────────────────────────────

    def set_degraded(self, flag: str, detail: Optional[str]) -> None:
        """detail=None — минула. Лог лише на переході."""
        if detail is None:
            if self._degraded.pop(flag, None) is not None:
                _log.warning("ALERTS_DEGRADED_CLEARED flag=%s", flag)
            return
        if flag not in self._degraded:
            _log.error("ALERTS_DEGRADED flag=%s detail=%s", flag, detail)
        self._degraded[flag] = detail

    @property
    def degraded(self) -> Dict[str, str]:
        return dict(self._degraded)

    # ── внутрішнє ────────────────────────────────────────────────────────

    def _armed(self, symbol: str) -> List[PriceAlert]:
        return [a for a in self.store.alerts.values() if a.symbol == symbol and a.state is AlertState.ARMED]

    def _step(self, symbol: str, transition: Transition) -> Tuple[List[PriceAlert], bool]:
        """Застосувати перехід до взведених символу; зміни — у стан і чергу в пам'яті (на диск — `_commit`).
        Повертає (щойно спрацьовані, чи змінилось хоч щось — бік теж)."""
        fired: List[PriceAlert] = []
        changed = False
        for alert in self._armed(symbol):
            updated = transition(alert)
            if updated is alert:
                continue
            self.store.put(updated)
            self._unsaved = changed = True
            if updated.fired is not None:
                self.store.enqueue(updated, now_ms=self._clock_ms())
                fired.append(updated)
                self._notify("fired", updated)
                _log.info(
                    "ALERTS_FIRED id=%s sym=%s level=%s via=%s",
                    updated.alert_id,
                    symbol,
                    updated.level,
                    updated.fired.via.value,
                )
        return fired, changed

    def _notify(self, event: str, alert: PriceAlert) -> None:
        for listener in list(self.listeners):
            listener(event, alert)

    def _commit(self) -> None:
        """Стан і черга — одним записом. Збій — прапорець і повтор пізніше; пам'ять не відкочується: доставка (S4)
        бере чергу з пам'яті, тож повідомлення не загубиться через диск."""
        try:
            self.store.commit()
        except OSError as exc:
            self._unsaved = True
            self.set_degraded(DEGRADED_STATE_WRITE, f"{type(exc).__name__}: {exc}")
            return
        self._unsaved = False
        self.set_degraded(DEGRADED_STATE_WRITE, None)


def _m1_transition(bar: FinalBar) -> Transition:
    def transition(alert: PriceAlert) -> PriceAlert:
        return on_m1_bar(alert, open_ms=bar.open_ms, low=bar.low, high=bar.high)

    return transition
