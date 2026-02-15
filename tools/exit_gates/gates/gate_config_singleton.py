"""Exit-gate: config_singleton — перевірка відсутності дублів config path resolution.

Ціль: гарантувати що DRY порушення (локальні _pick_config_path / _resolve_config_path / _env_str)
не повертаються в кодову базу. Дозволено лише core/config_loader.py як SSOT.

Підгейти:
1. no_local_pick_config — жоден модуль(крім core/config_loader.py) не містить _pick_config_path
2. no_local_resolve_config — жоден модуль не містить def _resolve_config_path
3. no_local_env_str — жоден модуль (крім legacy redis_spec.py) не визначає def _env_str
4. no_config_local_json — config.local.json не існує на диску
5. env_no_config_path — .env не містить AI_ONE_CONFIG_PATH або AI_ONE_ENV_FILE
6. ui_has_config — ui_config.json існує в static/
7. env_no_profile_files — .env.local та .env.prod не існують
8. env_no_dispatcher_words — env_profile.py не містить dispatcher/profile
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List


# Токени що мають бути тільки у core/config_loader.py
FORBIDDEN_DEFS = {
    "def _pick_config_path": "no_local_pick_config",
    "def _resolve_config_path": "no_local_resolve_config",
}

# Файли-виключення (де ці def дозволені)
ALLOWED_FILES = {
    "core/config_loader.py",
    "tools/exit_gates/gates/gate_config_singleton.py",
}

# _env_str дозволено лише в core/config_loader.py і runtime/store/redis_spec.py
ENV_STR_ALLOWED = {
    "core/config_loader.py",
    "runtime/store/redis_spec.py",
    "tools/exit_gates/gates/gate_config_singleton.py",
}

SCAN_DIRS = ["app", "core", "runtime", "tools", "ui_chart_v3"]
SCAN_EXT = {".py"}


def _scan_forbidden(root: Path) -> Dict[str, List[str]]:
    """Шукає заборонені визначення у Python-файлах."""
    violations: Dict[str, List[str]] = {name: [] for name in FORBIDDEN_DEFS.values()}
    violations["no_local_env_str"] = []

    for scan_dir in SCAN_DIRS:
        d = root / scan_dir
        if not d.is_dir():
            continue
        for path in d.rglob("*"):
            if path.suffix not in SCAN_EXT:
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            for token, gate_name in FORBIDDEN_DEFS.items():
                if rel in ALLOWED_FILES:
                    continue
                if token in text:
                    violations[gate_name].append(rel)

            # _env_str check
            if "def _env_str" in text and rel not in ENV_STR_ALLOWED:
                violations["no_local_env_str"].append(rel)

    return violations


def run_gate(inputs: dict) -> dict:
    root = Path(str(inputs.get("root", "."))).resolve()

    results: List[dict] = []

    # Підгейти 1-3: заборонені визначення
    violations = _scan_forbidden(root)
    for gate_name, files in violations.items():
        ok = len(files) == 0
        details = "ok" if ok else f"знайдено у: {', '.join(files)}"
        results.append({"name": gate_name, "ok": ok, "details": details})

    # Підгейт 4: config.local.json не має існувати
    local_cfg = root / "config.local.json"
    no_local = not local_cfg.exists()
    results.append({
        "name": "no_config_local_json",
        "ok": no_local,
        "details": "ok" if no_local else "config.local.json існує на диску — split-brain ризик",
    })

    # Підгейт 5: .env не містить AI_ONE_CONFIG_PATH або AI_ONE_ENV_FILE (dispatcher)
    env_file = root / ".env"
    env_clean = True
    env_clean_detail = "ok"
    if env_file.exists():
        try:
            text = env_file.read_text(encoding="utf-8", errors="replace")
            bad_keys = []
            if "AI_ONE_CONFIG_PATH" in text:
                bad_keys.append("AI_ONE_CONFIG_PATH")
            if "AI_ONE_ENV_FILE" in text:
                bad_keys.append("AI_ONE_ENV_FILE")
            if bad_keys:
                env_clean = False
                env_clean_detail = ".env містить dispatcher ключі: " + ", ".join(bad_keys)
        except Exception:
            env_clean = False
            env_clean_detail = "не вдалося прочитати .env"
    results.append({
        "name": "env_no_config_path",
        "ok": env_clean,
        "details": env_clean_detail,
    })

    # Підгейт 6: ui_config.json існує
    ui_cfg = root / "ui_chart_v3" / "static" / "ui_config.json"
    ui_ok = ui_cfg.exists()
    results.append({
        "name": "ui_has_config",
        "ok": ui_ok,
        "details": "ok" if ui_ok else "ui_config.json відсутній у static/",
    })

    # Підгейт 7: .env.local / .env.prod не існують
    profile_files = [root / ".env.local", root / ".env.prod"]
    existing_profiles = [str(p.name) for p in profile_files if p.exists()]
    no_profiles = len(existing_profiles) == 0
    results.append({
        "name": "env_no_profile_files",
        "ok": no_profiles,
        "details": "ok" if no_profiles else f"знайдено profile файли: {', '.join(existing_profiles)}",
    })

    # Підгейт 8: env_profile.py не містить слів dispatcher/profile (C2)
    env_profile_py = root / "env_profile.py"
    env_no_dispatcher = True
    env_disp_detail = "ok"
    if env_profile_py.exists():
        try:
            text = env_profile_py.read_text(encoding="utf-8", errors="replace")
            found_words = []
            if "dispatcher" in text.lower():
                found_words.append("dispatcher")
            if "profile" in text.lower():
                found_words.append("profile")
            if found_words:
                env_no_dispatcher = False
                env_disp_detail = "env_profile.py містить: " + ", ".join(found_words)
        except Exception:
            env_no_dispatcher = False
            env_disp_detail = "не вдалося прочитати env_profile.py"
    results.append({
        "name": "env_no_dispatcher_words",
        "ok": env_no_dispatcher,
        "details": env_disp_detail,
    })

    all_ok = all(r["ok"] for r in results)
    summary_parts = [f"{r['name']}={'OK' if r['ok'] else 'FAIL'}" for r in results]
    return {
        "ok": all_ok,
        "details": "; ".join(summary_parts),
        "sub_gates": results,
        "metrics": {"sub_gates_total": len(results), "sub_gates_ok": sum(1 for r in results if r["ok"])},
    }


def main() -> int:
    result = run_gate({"root": "."})
    total = result["metrics"]["sub_gates_total"]
    ok_count = result["metrics"]["sub_gates_ok"]
    print(f"gate_config_singleton: {ok_count}/{total}")
    for sg in result.get("sub_gates", []):
        status = "OK" if sg["ok"] else "FAIL"
        print(f"  [{status}] {sg['name']}: {sg['details']}")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
