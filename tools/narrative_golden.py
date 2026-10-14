"""tools/narrative_golden.py — golden-випадок системного наративу з реальних даних (ADR-0113 Ф1).

Знімає на момент закриття бару кроку повний вхід розбору (`NarrativeInputs`) з прогону історії і `NarrativeBlock`,
який з нього вийшов. `tests/test_narrative_golden.py` відтворює з входу той самий блок, тож зміна методики, що міняє
наратив, який читають UI й Арчі, стає видимою, а не тихою. Оновлювати golden — лише свідомо, з причиною в коміті.

Момент `--at` — закриття бару кроку (UTC, напр. 09:15 для M15); прогрів — барами, що закрились до попереднього кроку,
далі один крок прогону (`tools.narrative_replay`).

Приклад:

    python -m tools.narrative_golden --symbol XAU/USD --at 2026-06-10T09:15 --tf M15 --name xau_m15_short \\
        --note "trade: short від OB H1, ціль PDL"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from typing import Any, Dict, Optional, Sequence

from core.config_loader import load_system_config, resolve_config_path
from runtime.store.layers.disk_layer import DiskLayer
from tools.narrative_replay import TF_BY_NAME, configure_replay_logging, load_rows, replay_narrative

GOLDEN_DIR = os.path.join("tests", "golden", "narrative")
# Ім'я файла — лише латиниця, цифри, «_.-»: двокрапка на Windows створює потік NTFS замість файла
CASE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def capture_case(cfg: Dict[str, Any], disk: DiskLayer, symbol: str, at_ms: int, tf_s: int, step_tf_s: int,
                 note: str) -> Dict[str, Any]:
    """Golden-випадок на закриття бару кроку at_ms; на закритому ринку входу розбору немає — відмова."""
    step_ms = step_tf_s * 1000
    if at_ms % step_ms:
        raise ValueError("--at має бути закриттям бару кроку (кратним %d с)" % step_tf_s)
    start_ms = at_ms - step_ms
    rows_by_tf = load_rows(disk, cfg, symbol, step_tf_s, start_ms, at_ms)
    lines = list(replay_narrative(cfg, rows_by_tf, symbol, [tf_s], step_tf_s, start_ms, at_ms, with_inputs=True))
    if not lines or lines[0]["inputs"] is None:
        raise ValueError("на %s немає входу розбору (ринок закрито чи бару кроку немає на диску)" % at_ms)
    line = lines[0]
    return {"symbol": symbol, "tf_s": tf_s, "t": at_ms, "price": line["price"], "note": note,
            "inputs": line["inputs"], "expected": line["narrative"]}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Golden-випадок системного наративу з реальних даних (ADR-0113 Ф1)")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--at", required=True, help="YYYY-MM-DDTHH:MM UTC — закриття бару кроку")
    parser.add_argument("--tf", required=True, help="TF глядача (M5, M15, H1, H4, ...)")
    parser.add_argument("--step", default="M15", help="крок прогону — TF, на закритті якого знімаємо")
    parser.add_argument("--name", required=True, help="ім'я файла випадку без .json")
    parser.add_argument("--note", required=True, help="що саме цей випадок фіксує")
    parser.add_argument("--config", default=None)
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--out-dir", default=GOLDEN_DIR)
    args = parser.parse_args(argv)
    if not CASE_NAME_RE.match(args.name):
        parser.error("--name: лише латиниця, цифри та _.- (отримано %r)" % args.name)
    configure_replay_logging()
    cfg = load_system_config(resolve_config_path(args.config))
    at_ms = int(dt.datetime.strptime(args.at, "%Y-%m-%dT%H:%M").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    disk = DiskLayer(args.data_root or cfg.get("data_root", "./data_v3"))
    case = capture_case(cfg, disk, args.symbol, at_ms, TF_BY_NAME[args.tf.upper()], TF_BY_NAME[args.step.upper()],
                        args.note)
    os.makedirs(args.out_dir, exist_ok=True)
    path = os.path.join(args.out_dir, args.name + ".json")
    with open(path, "w", encoding="utf-8", newline="\n") as out:
        json.dump(case, out, ensure_ascii=False, indent=1, sort_keys=True)
        out.write("\n")
    sys.stdout.reconfigure(encoding="utf-8")  # заголовок з емодзі; консоль Windows — cp1251
    print("NARRATIVE_GOLDEN %s headline=%r bytes=%d" % (path, case["expected"]["headline"], os.path.getsize(path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
