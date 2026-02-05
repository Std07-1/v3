from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional, Tuple

from core.buckets import (
    bucket_start_ms,
    resolve_anchor_offset_ms,
    tf_to_ms,
)
from core.time_geom import bar_close_excl
from v3_polling_b import (
    CandleBar,
    JsonlAppender,
    load_config,
    ms_to_utc_dt,
    setup_logging,
    utc_now_ms,
)

try:
    from forexconnect import ForexConnect, fxcorepy  # type: ignore
except Exception:  # noqa: BLE001
    ForexConnect = None  # type: ignore
    fxcorepy = None  # type: ignore


def _get_attr(row: Any, *names: str) -> Optional[Any]:
    for name in names:
        if hasattr(row, name):
            return getattr(row, name)
        if isinstance(row, dict) and name in row:
            return row[name]
    return None


def _extract_bid_ask(row: Any) -> Optional[Tuple[float, float]]:
    bid = _get_attr(row, "bid", "Bid")
    ask = _get_attr(row, "ask", "Ask")
    if bid is None or ask is None:
        return None
    return float(bid), float(ask)


def _extract_instrument(row: Any) -> Optional[str]:
    inst = _get_attr(row, "instrument", "Instrument")
    return str(inst) if inst is not None else None


class LiveCandleBuilder:
    def __init__(
        self,
        symbol: str,
        tf_s: int = 60,
        store_enabled: bool = False,
        store_root: Optional[str] = None,
        use_mid_price: bool = True,
        anchor_offset_ms: int = 0,
    ) -> None:
        self._symbol = symbol
        self._tf_s = tf_s
        self._store_enabled = bool(store_enabled)
        self._store_root = store_root
        self._use_mid_price = bool(use_mid_price)
        self._anchor_offset_ms = int(anchor_offset_ms)
        self._current: Optional[CandleBar] = None
        self._last_tick_ms: Optional[int] = None
        self._last_price: Optional[float] = None

        self._writer: Optional[JsonlAppender] = None
        if self._store_enabled and self._store_root:
            self._writer = JsonlAppender(self._store_root)

    def _price_from_tick(self, bid: float, ask: float) -> float:
        if self._use_mid_price:
            return (bid + ask) / 2.0
        return bid

    def _publish_closed(self, bar: CandleBar) -> None:
        logging.info(
            "LIVE_BAR_CLOSE: %s o=%.5f h=%.5f l=%.5f c=%.5f v=%.0f",
            ms_to_utc_dt(bar.open_time_ms).isoformat(),
            bar.o,
            bar.h,
            bar.low,
            bar.c,
            bar.v,
        )
        if self._writer:
            self._writer.append(bar)

    def _publish_live(self, bar: CandleBar) -> None:
        logging.debug(
            "LIVE_BAR: %s o=%.5f h=%.5f l=%.5f c=%.5f v=%.0f",
            ms_to_utc_dt(bar.open_time_ms).isoformat(),
            bar.o,
            bar.h,
            bar.low,
            bar.c,
            bar.v,
        )

    def current_bar(self) -> Optional[CandleBar]:
        return self._current

    def close(self) -> None:
        if self._writer:
            self._writer.close()

    def last_price(self) -> Optional[float]:
        return self._last_price

    def last_tick_ms(self) -> Optional[int]:
        return self._last_tick_ms

    def on_tick(self, bid: float, ask: float, ts_ms: int) -> None:
        price = self._price_from_tick(bid, ask)
        self._last_price = price
        tf_ms = tf_to_ms(self._tf_s)
        bucket_open_ms = bucket_start_ms(ts_ms, tf_ms, self._anchor_offset_ms)
        if self._current is None or bucket_open_ms != self._current.open_time_ms:
            if self._current is not None:
                closed = CandleBar(
                    symbol=self._current.symbol,
                    tf_s=self._current.tf_s,
                    open_time_ms=self._current.open_time_ms,
                    close_time_ms=self._current.close_time_ms,
                    o=self._current.o,
                    h=self._current.h,
                    low=self._current.low,
                    c=self._current.c,
                    v=self._current.v,
                    complete=True,
                    src="live_tick",
                )
                self._publish_closed(closed)

            self._current = CandleBar(
                symbol=self._symbol,
                tf_s=self._tf_s,
                open_time_ms=bucket_open_ms,
                close_time_ms=bar_close_excl(bucket_open_ms, tf_ms),
                o=price,
                h=price,
                low=price,
                c=price,
                v=1.0,
                complete=False,
                src="live_tick",
            )
            self._publish_live(self._current)
        else:
            b = self._current
            b = CandleBar(
                symbol=b.symbol,
                tf_s=b.tf_s,
                open_time_ms=b.open_time_ms,
                close_time_ms=b.close_time_ms,
                o=b.o,
                h=max(b.h, price),
                low=min(b.low, price),
                c=price,
                v=b.v + 1.0,
                complete=False,
                src=b.src,
            )
            self._current = b
            self._publish_live(b)

        self._last_tick_ms = ts_ms


def _find_offer_row(offers: Any, symbol: str) -> Optional[Any]:
    for row in offers:
        inst = _extract_instrument(row)
        if inst == symbol:
            return row
    return None


def _wait_for_tables(tm: Any, timeout_s: float = 10.0) -> bool:
    if hasattr(tm, "wait_for_tables"):
        tm.wait_for_tables()
        return True
    start = time.time()
    while True:
        try:
            offers = tm.get_table(fxcorepy.O2GTableType.OFFERS)
            if offers is not None:
                return True
        except Exception:
            pass
        if time.time() - start >= timeout_s:
            return False
        time.sleep(0.2)


def _resolve_cfg_path(path: str | None, config_path: str) -> Optional[str]:
    if not path or not isinstance(path, str):
        return None
    if os.path.isabs(path):
        return path
    base = os.path.dirname(os.path.abspath(config_path))
    return os.path.join(base, path)


def _write_state(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    for attempt in range(3):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt < 2:
                time.sleep(0.05)
                continue
            logging.warning("LIVE_BAR: live_state зайнятий, пропускаю запис")
            try:
                os.remove(tmp)
            except Exception:
                pass
            return


def run() -> None:
    setup_logging(verbose=False)
    config_path = "config.json"
    cfg = load_config(config_path)
    if not bool(cfg.get("live_candle_enabled", False)):
        logging.info("LIVE_BAR: вимкнено (live_candle_enabled=false)")
        return
    symbols_raw = cfg.get("symbols")
    if isinstance(symbols_raw, list) and symbols_raw:
        symbols = [str(s).strip() for s in symbols_raw if str(s).strip()]
    else:
        symbols = []
    primary_symbol = str(cfg.get("symbol", "")).strip()
    if primary_symbol and primary_symbol not in symbols:
        symbols.append(primary_symbol)
    if not symbols:
        raise ValueError("config.symbols або config.symbol не задано")

    store_enabled = bool(cfg.get("live_candle_store_enabled", False))
    store_root = _resolve_cfg_path(cfg.get("live_candle_store_root"), config_path)
    state_path = _resolve_cfg_path(cfg.get("live_candle_state_path"), config_path)
    poll_s = float(cfg.get("live_candle_poll_s", 0.2))
    use_mid_price = bool(cfg.get("live_candle_use_mid_price", True))
    tfs_raw = cfg.get("live_candle_tfs_s")
    if not isinstance(tfs_raw, list) or not tfs_raw:
        tfs_s = [60]
    else:
        tfs_s = [int(x) for x in tfs_raw if isinstance(x, (int, float, str))]
        tfs_s = [x for x in tfs_s if x > 0]
        if not tfs_s:
            tfs_s = [60]

    if ForexConnect is None:
        raise RuntimeError("ForexConnect не доступний. Перевірте встановлення SDK/обгортки.")

    fx = ForexConnect()
    fx.login(
        cfg.get("user_id"),
        cfg.get("password"),
        cfg.get("url"),
        cfg.get("connection"),
    )

    builders: dict[int, LiveCandleBuilder] = {}
    try:
        tm = fx.table_manager
        if not _wait_for_tables(tm):
            raise RuntimeError("Не вдалось дочекатися таблиць (OFFERS)")
        offers = tm.get_table(fxcorepy.O2GTableType.OFFERS)
        missing = [s for s in symbols if _find_offer_row(offers, s) is None]
        if missing:
            logging.warning("LIVE_BAR: інструменти не знайдені в OFFERS: %s", missing)

        builders = {
            sym: {
                tf_s: LiveCandleBuilder(
                    symbol=sym,
                    tf_s=tf_s,
                    store_enabled=store_enabled,
                    store_root=store_root,
                    use_mid_price=use_mid_price,
                    anchor_offset_ms=resolve_anchor_offset_ms(tf_s, cfg),
                )
                for tf_s in tfs_s
            }
            for sym in symbols
        }

        last_bid_ask: dict[str, tuple[float, float]] = {}
        state_symbols: dict[str, dict[str, Any]] = {}
        logging.info("LIVE_BAR: старт, symbols=%s poll_s=%.2f tfs=%s", symbols, poll_s, tfs_s)
        while True:
            changed_any = False
            last_tick_ts = None
            for sym in symbols:
                offer = _find_offer_row(offers, sym)
                if offer is None:
                    continue
                bid_ask = _extract_bid_ask(offer)
                if bid_ask is None:
                    continue
                bid, ask = bid_ask
                prev = last_bid_ask.get(sym)
                if prev is not None and prev[0] == bid and prev[1] == ask:
                    continue
                ts_ms = utc_now_ms()
                last_tick_ts = ts_ms
                last_bid_ask[sym] = (bid, ask)
                for b in builders.get(sym, {}).values():
                    b.on_tick(bid, ask, ts_ms)
                if isinstance(state_path, str) and state_path:
                    payload_symbol: dict[str, Any] = {
                        "last_tick_ts": ts_ms,
                        "bars": {},
                    }
                    for tf_s, b in builders.get(sym, {}).items():
                        cur = b.current_bar()
                        if cur is not None:
                            raw = cur.to_dict()
                            raw["last_price"] = b.last_price()
                            raw["last_tick_ts"] = b.last_tick_ms()
                            payload_symbol["bars"][str(tf_s)] = raw
                    state_symbols[sym] = payload_symbol
                    changed_any = True

            if changed_any and isinstance(state_path, str) and state_path:
                payload: dict[str, Any] = {
                    "symbols": state_symbols,
                }
                if len(symbols) == 1 and symbols[0] in state_symbols:
                    payload["symbol"] = symbols[0]
                    payload["bars"] = state_symbols[symbols[0]].get("bars", {})
                    payload["last_tick_ts"] = state_symbols[symbols[0]].get("last_tick_ts")
                elif last_tick_ts is not None:
                    payload["last_tick_ts"] = last_tick_ts
                _write_state(state_path, payload)

            time.sleep(poll_s)
    except KeyboardInterrupt:
        logging.info("LIVE_BAR: зупинено користувачем (KeyboardInterrupt).")
    finally:
        for per_symbol in builders.values():
            for b in per_symbol.values():
                b.close()
        fx.logout()


if __name__ == "__main__":
    run()