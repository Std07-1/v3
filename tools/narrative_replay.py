"""tools/narrative_replay.py — прогін системного наративу по історії (ADR-0113 Ф1).

Читає SSOT-JSONL з диску (лише читання) і відтворює наратив так, як його бачив би глядач у кожен момент закриття бару:

- прогрів — живий `SmcRunner.warmup()` через читача історії, обрізаного моментом старту (той самий lookback, ті самі
  ~48 год M1 для сесійних H/L);
- далі закриті бари в часовому порядку — тими самими викликами рушія, що й `SmcRunner.on_bar_dict` (M1 — у стрічку
  сесій, compute TF — `on_bar`), але без relay, журналу сигналів і TDA: прогін нічого не шле й не пише, крім `--out`;
- на кожному закритті бару кроку (`--step`, типово M5) — розбір для кожного TF глядача:
  `get_narrative(..., now_ms=час закриття)`; календар, сесія й рівні беруть цей момент, а не годинник (R10). Той самий
  вхід → той самий вихід.

Крок, а не закриття бару глядача: глядач відкриває H4 чи D1 будь-коли, а закриття D1 (21:00 UTC влітку) — пауза ринку,
тож розбір «на закритті D1» завжди був би «Ринок закрито». Рівномірний крок дає те, що бачив би відвідувач у випадковий
момент, і метрики зважені за часом. Ціна розбору — close найменшого TF, що закрився в цей момент (M1); ATR у рядку —
виміряний ATR базового TF зон глядача.

Приклади:

    python -m tools.narrative_replay --symbol XAU/USD --from 2026-09-01 --to 2026-09-08 --out replay.jsonl
    python -m tools.narrative_replay --symbol XAG/USD --from 2026-07-01 --to 2026-10-01 --step M15 --out xag.jsonl
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import logging
import os
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Sequence

from core.config_loader import load_system_config, resolve_config_path
from core.smc.config import SmcConfig
from core.smc.engine import _SESSION_M1_CAPACITY, SmcEngine
from runtime.smc.smc_runner import SmcRunner, _bar_dict_to_candle_bar
from runtime.store.layers.disk_layer import DiskLayer

TF_BY_NAME = {"M1": 60, "M3": 180, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
M1_S = 60

Row = Dict[str, Any]


class HistoryReader:
    """Читач для `SmcRunner.warmup`: найновіші `limit` барів TF, що закрились не пізніше моменту старту."""

    def __init__(self, rows_by_tf: Dict[int, List[Row]], cutoff_ms: int) -> None:
        self._rows = {
            tf_s: [r for r in rows if r["open_time_ms"] + tf_s * 1000 <= cutoff_ms] for tf_s, rows in rows_by_tf.items()
        }

    def read_window(self, spec: Any, policy: Any) -> SimpleNamespace:
        rows = self._rows.get(spec.tf_s, [])
        return SimpleNamespace(bars_lwc=rows[-spec.limit:] if spec.limit > 0 else [])


def replay_config(cfg: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    """Конфіг прогону: той самий SSOT, лише без побічних ефектів — журнал сигналів і TDA вимкнені, один символ."""
    replay_cfg = copy.deepcopy(cfg)
    replay_cfg["symbols"] = [symbol]
    smc_cfg = replay_cfg.setdefault("smc", {})
    smc_cfg["signal_journal"] = {"enabled": False, "path": os.path.join(os.devnull, "narrative_replay")}
    smc_cfg["tda_cascade"] = dict(smc_cfg.get("tda_cascade", {}), enabled=False)
    return replay_cfg


def replay_narrative(cfg: Dict[str, Any], rows_by_tf: Dict[int, List[Row]], symbol: str, viewer_tfs: Sequence[int],
                     step_tf_s: int, start_ms: int, end_ms: int, with_inputs: bool = False) -> Iterable[Row]:
    """Рядки розбору для кожного TF глядача на кожному закритті бару кроку в (start_ms, end_ms].

    rows_by_tf — відсортовані рядки диска без дублів (load_rows); рядки кроку мають бути серед них.
    with_inputs — додати в рядок повний вхід розбору (NarrativeInputs.to_dict) для golden-випадків; на закритому
    ринку входу немає (None).
    """
    replay_cfg = replay_config(cfg, symbol)
    engine = SmcEngine(SmcConfig.from_dict(replay_cfg.get("smc", {})))
    runner = SmcRunner(replay_cfg, engine)
    runner.warmup(HistoryReader(rows_by_tf, start_ms))
    compute_tfs = set(int(tf) for tf in replay_cfg["smc"]["compute_tfs"])  # без власного default: SSOT — config
    closes: Dict[int, List[tuple]] = {}
    for tf_s, rows in rows_by_tf.items():
        for row in rows:
            close_ms = row["open_time_ms"] + tf_s * 1000
            if start_ms < close_ms <= end_ms and row.get("complete", True):
                closes.setdefault(close_ms, []).append((tf_s, row))
    for close_ms in sorted(closes):
        group = sorted(closes[close_ms], key=lambda item: item[0])
        for tf_s, row in group:
            # Як живий шлях: M1 — feed_m1_bar_dict (стрічка сесій + остання ціна для range_exhaustion), compute TF —
            # on_bar рушія з тим самим конвертером, що в on_bar_dict (без relay/журналу/TDA)
            if tf_s == M1_S:
                runner.feed_m1_bar_dict(symbol, row)
            if tf_s in compute_tfs:
                bar = _bar_dict_to_candle_bar(row, symbol, tf_s)
                if bar is not None:
                    engine.on_bar(bar)
        if step_tf_s not in {tf_s for tf_s, _ in group}:
            continue
        price = float(group[0][1]["c"])
        for viewer_tf_s in viewer_tfs:
            block = runner.get_narrative(symbol, viewer_tf_s, price, 0.0, now_ms=close_ms)
            if block is None:
                continue
            line = {"t": close_ms, "symbol": symbol, "tf_s": viewer_tf_s, "price": price,
                    "atr": runner.get_measured_atr(symbol, engine.display_base_tf(viewer_tf_s)),
                    "narrative": block.to_wire()}
            if with_inputs:
                inputs = runner.get_narrative_inputs(symbol, viewer_tf_s, price, 0.0, now_ms=close_ms)
                line["inputs"] = inputs.to_dict() if inputs is not None else None
            yield line


def load_rows(disk: DiskLayer, cfg: Dict[str, Any], symbol: str, step_tf_s: int, start_ms: int,
              end_ms: int) -> Dict[int, List[Row]]:
    """Рядки диска: прогрів (lookback compute TF, 48 год M1) + вікно прогону, без preview, відсортовані й без дублів."""
    smc_cfg = cfg["smc"]
    lookback = int(smc_cfg["lookback_bars"])  # без власних default: SSOT — config, як у SmcRunner
    compute_tfs = [int(tf) for tf in smc_cfg["compute_tfs"]]
    warmup_bars = {tf_s: lookback for tf_s in compute_tfs}
    warmup_bars[M1_S] = max(lookback, _SESSION_M1_CAPACITY)  # стрічка сесій рушія більше не вміщує
    rows_by_tf: Dict[int, List[Row]] = {}
    for tf_s in sorted(set(warmup_bars) | {step_tf_s}):
        span = (end_ms - start_ms) // (tf_s * 1000) + 1
        rows, _geom = disk.read_window_with_geom(symbol, tf_s, warmup_bars.get(tf_s, 0) + span,
                                                 to_open_ms=end_ms - tf_s * 1000, use_tail=True, skip_preview=True)
        rows_by_tf[tf_s] = rows
    return rows_by_tf


def configure_replay_logging() -> None:
    """WARNING і вище; SMC_SLOW — бюджет живого бару (10 мс), не деградація прогону, а помилки рушія (ERROR) видно."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("core.smc.engine").setLevel(logging.ERROR)


def _day_ms(day: str) -> int:
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Прогін системного наративу по історії (ADR-0113 Ф1)")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD UTC, включно")
    parser.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD UTC, не включно")
    parser.add_argument("--tfs", default="M5,M15,H1,H4", help="TF глядача через кому")
    parser.add_argument("--step", default="M5", help="крок розбору — TF, на закритті бару якого розбираємо")
    parser.add_argument("--out", required=True, help="JSONL з рядками розбору")
    parser.add_argument("--config", default=None)
    parser.add_argument("--data-root", default=None, help="за замовчуванням — data_root з config")
    args = parser.parse_args(argv)
    configure_replay_logging()
    cfg = load_system_config(resolve_config_path(args.config))
    viewer_tfs = [TF_BY_NAME[name.strip().upper()] for name in args.tfs.split(",") if name.strip()]
    step_tf_s = TF_BY_NAME[args.step.strip().upper()]
    start_ms, end_ms = _day_ms(args.date_from), _day_ms(args.date_to)
    disk = DiskLayer(args.data_root or cfg.get("data_root", "./data_v3"))
    rows_by_tf = load_rows(disk, cfg, args.symbol, step_tf_s, start_ms, end_ms)
    count = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for line in replay_narrative(cfg, rows_by_tf, args.symbol, viewer_tfs, step_tf_s, start_ms, end_ms):
            out.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    print("NARRATIVE_REPLAY symbol=%s from=%s to=%s tfs=%s step=%s rows=%d out=%s"
          % (args.symbol, args.date_from, args.date_to, args.tfs, args.step, count, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
