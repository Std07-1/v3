"""Cowork event-trigger watcher (slice cowork.004).

Long-running daemon that addresses gap **G2** from
`cowork_prompt_template_v3.md` STEP 0a: between scheduled cadence slots
(S1-S4) the cowork bot accepts an `event_flag.json` written by an external
watcher. Without that watcher the secondary trigger is always `absent` and
ad-hoc events (new TDA signal, bias flip) do not wake the bot.

Polling loop:
    1. Read latest TDA signal journal entries → fire `tda_signal` when a
       new wall_ms appears for the watched symbol.
    2. GET `/api/v3/bias/latest?symbol=...` → hash multi-TF bias map, fire
       `bias_flip` when the hash changes.
    3. Persist last-seen state in `<triggers_dir>/.watcher_state.json`
       (atomic write) so a restart does not refire stale events.
    4. Write `event_flag.json` atomically (`.tmp` + `os.replace`) so
       `cowork.runner` never reads a half-written file (CR2).

Cold start policy: first poll seeds the state without firing — only
*subsequent* changes are treated as triggers (otherwise every restart
would emit a spurious wake-up).

Environment configuration (all optional except token):
    COWORK_TRIGGERS_DIR             default `/opt/smc-cowork/triggers`
    COWORK_SIGNALS_DIR              default `data_v3/_signals`
    COWORK_EVENT_WATCHER_API_BASE   default `http://127.0.0.1:8000`
    COWORK_EVENT_WATCHER_TOKEN      required for bias polling
    COWORK_EVENT_WATCHER_INTERVAL_S default `30`
    COWORK_EVENT_WATCHER_SYMBOL     default `XAU/USD`
    COWORK_EVENT_WATCHER_TRIGGER_EVENTS
                                   csv of journal events that count as a
                                   tda_signal. Default
                                   `signal_emitted,trade_entered,
                                    trade_exited,scenario_invalidated`

Exit codes:
    0 — clean shutdown via SIGTERM / SIGINT
    1 — fatal misconfiguration
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger("cowork.event_watcher")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_TRIGGERS_DIR_LINUX = Path("/opt/smc-cowork/triggers")
DEFAULT_TRIGGERS_DIR_FALLBACK = Path("triggers")
DEFAULT_SIGNALS_DIR = Path("data_v3/_signals")
DEFAULT_API_BASE = "http://127.0.0.1:8000"
DEFAULT_INTERVAL_S = 30
DEFAULT_SYMBOL = "XAU/USD"
DEFAULT_TRIGGER_EVENTS = (
    "signal_emitted",
    "trade_entered",
    "trade_exited",
    "scenario_invalidated",
)

EVENT_FLAG_FILENAME = "event_flag.json"
STATE_FILENAME = ".watcher_state.json"


# ---------------------------------------------------------------------------
# Config + state dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WatcherConfig:
    triggers_dir: Path
    signals_dir: Path
    api_base: str
    api_token: Optional[str]
    interval_s: int
    symbol: str
    trigger_events: frozenset[str]


@dataclass
class WatcherState:
    last_signal_wall_ms: int = 0
    last_bias_hash: str = ""
    seeded: bool = False
    symbol: str = ""

    def to_dict(self) -> dict:
        return {
            "last_signal_wall_ms": self.last_signal_wall_ms,
            "last_bias_hash": self.last_bias_hash,
            "seeded": self.seeded,
            "symbol": self.symbol,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "WatcherState":
        return cls(
            last_signal_wall_ms=int(payload.get("last_signal_wall_ms", 0)),
            last_bias_hash=str(payload.get("last_bias_hash", "")),
            seeded=bool(payload.get("seeded", False)),
            symbol=str(payload.get("symbol", "")),
        )


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _resolve_default_triggers_dir() -> Path:
    if DEFAULT_TRIGGERS_DIR_LINUX.parent.exists():
        return DEFAULT_TRIGGERS_DIR_LINUX
    return DEFAULT_TRIGGERS_DIR_FALLBACK


def load_config_from_env(env: Optional[dict[str, str]] = None) -> WatcherConfig:
    e = env if env is not None else os.environ
    raw_events = e.get("COWORK_EVENT_WATCHER_TRIGGER_EVENTS")
    if raw_events:
        events = frozenset(s.strip() for s in raw_events.split(",") if s.strip())
    else:
        events = frozenset(DEFAULT_TRIGGER_EVENTS)
    return WatcherConfig(
        triggers_dir=Path(
            e.get("COWORK_TRIGGERS_DIR", str(_resolve_default_triggers_dir()))
        ),
        signals_dir=Path(e.get("COWORK_SIGNALS_DIR", str(DEFAULT_SIGNALS_DIR))),
        api_base=e.get("COWORK_EVENT_WATCHER_API_BASE", DEFAULT_API_BASE).rstrip("/"),
        api_token=e.get("COWORK_EVENT_WATCHER_TOKEN"),
        interval_s=int(e.get("COWORK_EVENT_WATCHER_INTERVAL_S", DEFAULT_INTERVAL_S)),
        symbol=e.get("COWORK_EVENT_WATCHER_SYMBOL", DEFAULT_SYMBOL),
        trigger_events=events,
    )


# ---------------------------------------------------------------------------
# Atomic file IO
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_state(state_path: Path, symbol: str) -> WatcherState:
    if not state_path.exists():
        return WatcherState(symbol=symbol)
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "event_watcher: state read failed path=%s err=%s — restarting cold",
            state_path,
            exc,
        )
        return WatcherState(symbol=symbol)
    state = WatcherState.from_dict(payload)
    if state.symbol != symbol:
        log.info(
            "event_watcher: symbol changed (%s -> %s) — reseeding state",
            state.symbol,
            symbol,
        )
        return WatcherState(symbol=symbol)
    return state


def save_state(state_path: Path, state: WatcherState) -> None:
    _atomic_write_json(state_path, state.to_dict())


# ---------------------------------------------------------------------------
# Phase 1 — TDA signal poll
# ---------------------------------------------------------------------------


def _today_journal_paths(signals_dir: Path, now_utc: datetime) -> list[Path]:
    """Return today's + yesterday's journals (cover UTC midnight rollover)."""
    today = now_utc.strftime("%Y-%m-%d")
    yday = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
    paths: list[Path] = []
    for d in (yday, today):
        p = signals_dir / f"journal-{d}.jsonl"
        if p.exists():
            paths.append(p)
    return paths


def scan_latest_signal(
    signals_dir: Path,
    symbol: str,
    trigger_events: frozenset[str],
    now_utc: datetime,
) -> Optional[int]:
    """Return the largest `wall_ms` of a matching trigger entry, or None."""
    latest_ms: Optional[int] = None
    for path in _today_journal_paths(signals_dir, now_utc):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("symbol") != symbol:
                        continue
                    if entry.get("event") not in trigger_events:
                        continue
                    wm = entry.get("wall_ms")
                    if isinstance(wm, (int, float)):
                        wm_int = int(wm)
                        if latest_ms is None or wm_int > latest_ms:
                            latest_ms = wm_int
        except OSError as exc:
            log.warning("event_watcher: journal read failed path=%s err=%s", path, exc)
    return latest_ms


# ---------------------------------------------------------------------------
# Phase 2 — Bias poll
# ---------------------------------------------------------------------------


def fetch_bias_map(config: WatcherConfig) -> Optional[dict]:
    """GET `/api/v3/bias/latest`. Returns parsed `bias` dict or None on error."""
    if not config.api_token:
        log.error(
            "event_watcher: COWORK_EVENT_WATCHER_TOKEN missing — bias poll disabled"
        )
        return None
    url = (
        f"{config.api_base}/api/v3/bias/latest"
        f"?symbol={urllib.parse.quote(config.symbol, safe='')}"
    )
    req = urllib.request.Request(url, headers={"X-API-Key": config.api_token})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        log.warning("event_watcher: bias fetch failed url=%s err=%s", url, exc)
        return None
    data = payload.get("data") or {}
    bias = data.get("bias")
    if not isinstance(bias, dict):
        return None
    return bias


def hash_bias_map(bias: dict) -> str:
    canonical = json.dumps(bias, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Phase 3 — Trigger emission
# ---------------------------------------------------------------------------


def write_event_flag(triggers_dir: Path, trigger: str, now_utc: datetime) -> Path:
    flag_path = triggers_dir / EVENT_FLAG_FILENAME
    payload = {
        "trigger": trigger,
        "ts": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _atomic_write_json(flag_path, payload)
    return flag_path


def evaluate_tick(
    config: WatcherConfig,
    state: WatcherState,
    now_utc: datetime,
    bias_map: Optional[dict],
    latest_signal_ms: Optional[int],
) -> Optional[str]:
    """Pure decision: given current observations + prior state, return the
    trigger name to emit (or None). Mutates `state` to record observations.

    Cold-start (`state.seeded == False`) records observations without firing.
    """
    fired: Optional[str] = None

    if not state.seeded:
        if latest_signal_ms is not None:
            state.last_signal_wall_ms = latest_signal_ms
        if bias_map is not None:
            state.last_bias_hash = hash_bias_map(bias_map)
        state.seeded = True
        return None

    if latest_signal_ms is not None and latest_signal_ms > state.last_signal_wall_ms:
        state.last_signal_wall_ms = latest_signal_ms
        fired = "tda_signal"

    if bias_map is not None:
        bias_hash = hash_bias_map(bias_map)
        if bias_hash != state.last_bias_hash:
            state.last_bias_hash = bias_hash
            # tda_signal wins if both fire in the same tick (more actionable).
            if fired is None:
                fired = "bias_flip"

    return fired


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


class _ShutdownFlag:
    def __init__(self) -> None:
        self.stop = False


def _install_signal_handlers(flag: _ShutdownFlag) -> None:
    def handler(signum, frame) -> None:  # noqa: ANN001 — stdlib signature
        log.info("event_watcher: received signal=%d — shutting down", signum)
        flag.stop = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            # SIGTERM may not be available on all platforms / threads.
            pass


def run_loop(
    config: WatcherConfig,
    *,
    shutdown: Optional[_ShutdownFlag] = None,
    max_ticks: Optional[int] = None,
    sleep_fn=time.sleep,
    now_fn=lambda: datetime.now(timezone.utc),
) -> None:
    """Polling loop. `max_ticks` is for tests; production = unbounded."""
    flag = shutdown if shutdown is not None else _ShutdownFlag()
    state_path = config.triggers_dir / STATE_FILENAME
    state = load_state(state_path, config.symbol)
    log.info(
        "event_watcher: starting symbol=%s interval_s=%d triggers_dir=%s seeded=%s",
        config.symbol,
        config.interval_s,
        config.triggers_dir,
        state.seeded,
    )

    tick = 0
    while not flag.stop:
        tick += 1
        now_utc = now_fn()
        latest_ms = scan_latest_signal(
            config.signals_dir,
            config.symbol,
            config.trigger_events,
            now_utc,
        )
        bias_map = fetch_bias_map(config)
        trigger = evaluate_tick(config, state, now_utc, bias_map, latest_ms)
        save_state(state_path, state)
        if trigger is not None:
            flag_path = write_event_flag(config.triggers_dir, trigger, now_utc)
            log.info(
                "event_watcher: trigger_fired trigger=%s path=%s ts=%s",
                trigger,
                flag_path,
                now_utc.isoformat(),
            )
        else:
            log.debug(
                "event_watcher: no_change tick=%d last_signal_ms=%d bias_hash=%s",
                tick,
                state.last_signal_wall_ms,
                state.last_bias_hash,
            )
        if max_ticks is not None and tick >= max_ticks:
            break
        # Sleep responsive to shutdown.
        for _ in range(config.interval_s):
            if flag.stop:
                break
            sleep_fn(1)


def main(argv: Optional[Iterable[str]] = None) -> int:
    logging.basicConfig(
        level=os.environ.get("COWORK_EVENT_WATCHER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        config = load_config_from_env()
    except (ValueError, KeyError) as exc:
        log.error("event_watcher: bad config err=%s", exc)
        return 1
    flag = _ShutdownFlag()
    _install_signal_handlers(flag)
    run_loop(config, shutdown=flag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
