# tools/stress_jsonl_parallel.py
import argparse
import json
import os
import threading
import multiprocessing as mp
from pathlib import Path

# важливо: запуск з кореня репо, щоб імпорти працювали
from runtime.store.ssot_jsonl import JsonlAppender
from core.model.bars import CandleBar


def _mk_bar(i: int, pad_size: int, tag: str) -> CandleBar:
    tf_s = 60
    tf_ms = tf_s * 1000
    open_ms = (
        i % 1000
    ) * tf_ms  # тримаємо в межах однієї доби => один part-YYYYMMDD.jsonl
    close_ms = open_ms + tf_ms
    ext = {"tag": tag}
    if pad_size > 0:
        ext["pad"] = "x" * pad_size
    return CandleBar(
        symbol="XAU/USD",
        tf_s=tf_s,
        open_time_ms=open_ms,
        close_time_ms=close_ms,
        o=1.0,
        h=2.0,
        low=0.5,
        c=1.5,
        v=123.0,
        complete=True,
        src="history",
        extensions=ext,
    )


def _thread_worker(app: JsonlAppender, writes: int, pad: int, tag: str) -> None:
    for i in range(writes):
        app.append(_mk_bar(i, pad, tag))


def _proc_worker(root: str, threads: int, writes: int, pad: int, proc_id: int) -> None:
    app = JsonlAppender(root)
    ts = []
    for t in range(threads):
        tag = f"p{proc_id}-t{t}"
        th = threading.Thread(target=_thread_worker, args=(app, writes, pad, tag))
        th.start()
        ts.append(th)
    for th in ts:
        th.join()
    app.close()


def _find_part_file(root: str) -> str:
    p = Path(root) / "XAU_USD" / "tf_60"
    parts = sorted(p.glob("part-*.jsonl"))
    if not parts:
        raise SystemExit(f"part file not found in {p}")
    return str(parts[0])


def _verify_jsonl(path: str) -> dict:
    total = 0
    bad = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for idx, line in enumerate(f, start=1):
            total += 1
            try:
                json.loads(line)
            except Exception as e:
                bad.append((idx, str(e), line[:200], line[-200:]))
                if len(bad) >= 5:
                    break
    return {"total_lines": total, "bad_samples": bad}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="_tmp_jsonl_stress", help="output dir")
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--writes", type=int, default=50000)
    ap.add_argument("--pad", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(args.root, exist_ok=True)

    ctx = mp.get_context("spawn")  # важливо для Windows
    ps = []
    for p in range(args.procs):
        pr = ctx.Process(
            target=_proc_worker,
            args=(args.root, args.threads, args.writes, args.pad, p),
        )
        pr.start()
        ps.append(pr)
    for pr in ps:
        pr.join()

    part_path = _find_part_file(args.root)
    rep = _verify_jsonl(part_path)
    expected = args.procs * args.threads * args.writes

    print(f"PART={part_path}")
    print(f"EXPECTED_LINES={expected} ACTUAL_LINES={rep['total_lines']}")
    print(f"JSON_PARSE_FAILURES={len(rep['bad_samples'])}")
    if rep["bad_samples"]:
        for idx, err, head, tail in rep["bad_samples"]:
            print(f"BAD_LINE idx={idx} err={err}\nHEAD={head}\nTAIL={tail}\n")


if __name__ == "__main__":
    main()
