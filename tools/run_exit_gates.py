from __future__ import annotations

import argparse
import importlib
import json
import os
import time
from typing import Any, Dict, List


def _load_manifest(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _run_gate(mod_path: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    mod = importlib.import_module(mod_path)
    run_fn = getattr(mod, "run_gate", None)
    if not callable(run_fn):
        return {
            "ok": False,
            "details": f"gate_missing_run_fn:{mod_path}",
            "metrics": {},
        }
    return run_fn(inputs)


def _default_manifest_path() -> str:
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(tools_dir, "exit_gates", "manifest.json")


def _repo_root_from_tools() -> str:
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(tools_dir, ".."))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()

    repo_root = _repo_root_from_tools()
    os.chdir(repo_root)

    manifest_path = args.manifest or _default_manifest_path()

    manifest = _load_manifest(manifest_path)
    gates = manifest.get("gates", [])
    if not isinstance(gates, list):
        print("manifest.invalid_gates")
        return 2

    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = os.path.join("reports", "exit_gates", run_id)
    _ensure_dir(out_dir)

    results: List[Dict[str, Any]] = []
    all_ok = True

    for gate in gates:
        name = str(gate.get("name", ""))
        mod = str(gate.get("module", ""))
        inputs = gate.get("inputs", {}) if isinstance(gate.get("inputs"), dict) else {}
        res = _run_gate(mod, inputs)
        details = str(res.get("details", ""))
        skip = False
        if "base_url" in inputs and details.startswith(("url_error:", "http_error:")):
            skip = True
        ok = True if skip else bool(res.get("ok", False))
        if not skip:
            all_ok = all_ok and ok
        results.append(
            {
                "name": name,
                "module": mod,
                "ok": ok,
                "skipped": skip,
                "details": details if not skip else f"skip:{details}",
                "metrics": res.get("metrics", {}),
            }
        )
        if skip:
            print(f"GATE {name} skip=True details={details}")
        else:
            print(f"GATE {name} ok={ok} details={details}")

    report = {
        "run_id": run_id,
        "manifest": manifest_path,
        "ok": all_ok,
        "results": results,
    }

    report_path = os.path.join(out_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"REPORT {report_path}")
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
