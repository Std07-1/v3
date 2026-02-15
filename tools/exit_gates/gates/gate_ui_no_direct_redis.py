from __future__ import annotations

from pathlib import Path


FORBIDDEN = [
    "import redis",
    "redis_lib",
    "_redis_client_from_cfg",
    "FXCM_REDIS_",
]

ALLOW_FILES = {
    # UI не має містити прямий Redis-код.
}


def run_gate(inputs: dict) -> dict:
    root = Path(str(inputs.get("root_dir", "ui_chart_v3")))
    bad: list[str] = []
    for path in root.rglob("*.py"):
        rel = str(path).replace("\\", "/")
        if rel in ALLOW_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            bad.append(f"{rel}: не вдалося прочитати файл: {exc}")
            continue
        for token in FORBIDDEN:
            if token in text:
                bad.append(f"{rel}: знайдено заборонений токен: {token}")
    ok = not bad
    details = "ok" if ok else "found=" + "; ".join(bad)
    return {"ok": ok, "details": details, "metrics": {"files": len(list(root.rglob('*.py')))}}


def main() -> int:
    result = run_gate({})
    if not result.get("ok"):
        print("EXIT_GATE_FAIL: UI не має містити прямий Redis-код. Знайдено:")
        details = str(result.get("details", ""))
        if details.startswith("found="):
            details = details[len("found=") :]
        for line in details.split("; "):
            if line:
                print(" - " + line)
        return 2
    print("EXIT_GATE_OK: UI не містить прямого Redis-коду")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
