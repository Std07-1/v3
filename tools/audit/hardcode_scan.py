#!/usr/bin/env python3
"""hardcode_scan — пошук захардкоджених констант/патернів у репозиторії.

MODE=PATCH  Slice-4
Ціль: виявити підозрілі hardcode-и (TF-списки, magic numbers, timezone,
      пряме читання data_v3, дублювання bucket_start_ms тощо).

Чистий Python, без ripgrep або інших зовнішніх залежностей.
Виходи:
  reports/mpv_proof/hardcode_hits.md
  reports/mpv_proof/hardcode_hits.json

Запуск:
  python -m tools.audit.hardcode_scan          # з кореня репо
  python tools/audit/hardcode_scan.py          # напряму
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ─── Кореневий каталог репозиторію ────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]

# ─── Які файли скануємо ──────────────────────────────────────────────
_SCAN_EXTENSIONS = {".py", ".js", ".html", ".json", ".md"}
_SKIP_DIRS = {
    "__pycache__", ".git", "node_modules", ".venv", "venv",
    "History", "logs", "reports", "data_v3", "dump.rdb",
}
_SKIP_FILES = {"changelog.jsonl", "dump.rdb", "requirements.txt", "pyproject.toml"}

# ─── Патерни ──────────────────────────────────────────────────────────
# Кожен патерн: (id, compiled_regex, опис, severity)
# severity: "warn" — потенційна проблема, "info" — інформаційний хіт
_PATTERNS: list[tuple[str, re.Pattern[str], str, str]] = [
    (
        "HARDCODED_TF_LIST",
        re.compile(
            r"""(?:TF_ALLOWLIST|DEFAULT_TF|ALLOWED_TF|tf_allowlist|tf_list)\s*[=:]\s*[\[\{(]""",
            re.IGNORECASE,
        ),
        "Захардкоджений список TF (повинен бути у config.json SSOT)",
        "warn",
    ),
    (
        "HARDCODED_TF_MAGIC",
        re.compile(
            r"""\b(?:14400|86400|3600|1800|900|300|180)\b""",
        ),
        "Magic TF число без іменованої константи (потенційний hardcode)",
        "info",
    ),
    (
        "BASE_TF_HARDCODE",
        re.compile(
            r"""base_tf_s\s*=\s*\d+""",
        ),
        "Пряме присвоєння base_tf_s числом (повинно бути з config)",
        "warn",
    ),
    (
        "COLD_START_HARDCODE",
        re.compile(
            r"""cold_start_bars\s*=\s*\d+""",
        ),
        "Захардкоджене cold_start_bars",
        "warn",
    ),
    (
        "WARMUP_BARS_HARDCODE",
        re.compile(
            r"""warmup_bars\s*=\s*\d+""",
        ),
        "Захардкоджене warmup_bars",
        "warn",
    ),
    (
        "REDIS_TAIL_HARDCODE",
        re.compile(
            r"""redis_tail_by_tf|REDIS_TAIL_BY_TF""",
        ),
        "Хардкоджена redis_tail_by_tf карта (повинна бути у config)",
        "warn",
    ),
    (
        "DIRECT_DATA_DIR_ACCESS",
        re.compile(
            r"""os\.listdir\s*\(\s*(?:data_root|data_dir|DATA_ROOT|DATA_DIR)""",
        ),
        "Пряме os.listdir(data_root) — має йти через абстракцію",
        "info",
    ),
    (
        "BUCKET_START_DUP",
        re.compile(
            r"""def\s+bucket_start_ms\b""",
        ),
        "Дублювання bucket_start_ms (SSOT = core/buckets.py)",
        "warn",
    ),
    (
        "TIMEZONE_HARDCODE",
        re.compile(
            r"""(?:Europe/Prague|Europe/Berlin|CET\b|CEST\b|Prague|прага|пражськ)""",
            re.IGNORECASE,
        ),
        "Захардкоджена timezone (Prague/CET) — повинно бути UTC",
        "warn",
    ),
    (
        "TIMEZONE_LOCAL",
        re.compile(
            r"""(?:\.astimezone\s*\(\s*\)|localtime|tzlocal|local_tz)""",
            re.IGNORECASE,
        ),
        "Використання local timezone (має бути UTC)",
        "warn",
    ),
    (
        "SILENT_EXCEPT",
        re.compile(
            r"""except\s*(?:Exception)?\s*:\s*(?:pass|\.\.\.)\s*$""",
        ),
        "Silent except (порушує Правило №9 — degraded-but-loud)",
        "warn",
    ),
]


def _should_skip(path: Path) -> bool:
    """Перевіряємо, чи файл/каталог треба пропустити."""
    parts = path.relative_to(_REPO_ROOT).parts
    for skip in _SKIP_DIRS:
        if skip in parts:
            return True
    if path.name in _SKIP_FILES:
        return True
    if path.suffix not in _SCAN_EXTENSIONS:
        return True
    return False


def _is_self(path: Path) -> bool:
    """Пропускаємо цей самий файл, щоб не знаходити власні патерни."""
    try:
        # Порівнюємо по абсолютному шляху (os.path.normcase для Windows)
        self_path = os.path.normcase(os.path.abspath(__file__))
        check_path = os.path.normcase(os.path.abspath(str(path)))
        return self_path == check_path
    except Exception:
        return False


def scan_repo() -> list[dict[str, Any]]:
    """Сканує файли репозиторію і повертає список хітів."""
    hits: list[dict[str, Any]] = []

    for dirpath, dirnames, filenames in os.walk(_REPO_ROOT):
        # Фільтруємо каталоги in-place щоб os.walk не заходив
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        dp = Path(dirpath)
        for fname in filenames:
            fpath = dp / fname
            if _should_skip(fpath) or _is_self(fpath):
                continue
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            lines = text.splitlines()
            rel = str(fpath.relative_to(_REPO_ROOT)).replace("\\", "/")
            for line_no, line in enumerate(lines, start=1):
                for pat_id, pat_re, description, severity in _PATTERNS:
                    if pat_re.search(line):
                        # Для info-патернів з magic numbers — пропускаємо коментарі/документацію
                        if pat_id == "HARDCODED_TF_MAGIC":
                            stripped = line.strip()
                            if stripped.startswith("#") or stripped.startswith("//"):
                                continue
                            if stripped.startswith("*") or stripped.startswith("- "):
                                continue
                            # Пропускаємо, якщо це не Python/JS-код
                            if fpath.suffix in {".md", ".json"}:
                                continue
                        hits.append({
                            "pattern_id": pat_id,
                            "severity": severity,
                            "file": rel,
                            "line": line_no,
                            "text": line.strip()[:200],
                            "description": description,
                        })
    return hits


def _write_md(hits: list[dict[str, Any]], out_path: Path) -> None:
    """Записує знайдені хіти у markdown-звіт."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    warn_hits = [h for h in hits if h["severity"] == "warn"]
    info_hits = [h for h in hits if h["severity"] == "info"]

    lines = [
        "# Hardcode Scan Report",
        f"Згенеровано: {ts}",
        "",
        f"**Всього хітів**: {len(hits)} (warn: {len(warn_hits)}, info: {len(info_hits)})",
        "",
    ]

    # Групуємо по pattern_id
    by_pattern: dict[str, list[dict[str, Any]]] = {}
    for h in hits:
        by_pattern.setdefault(h["pattern_id"], []).append(h)

    for pat_id in sorted(by_pattern.keys()):
        pat_hits = by_pattern[pat_id]
        sev = pat_hits[0]["severity"]
        desc = pat_hits[0]["description"]
        icon = "⚠️" if sev == "warn" else "ℹ️"
        lines.append(f"## {icon} {pat_id} ({len(pat_hits)} хітів)")
        lines.append(f"> {desc}")
        lines.append("")
        lines.append("| Файл | Рядок | Фрагмент |")
        lines.append("|------|-------|----------|")
        for h in pat_hits[:50]:  # Обмежуємо 50 на патерн
            text_esc = h["text"].replace("|", "\\|")
            lines.append(f"| `{h['file']}` | {h['line']} | `{text_esc}` |")
        if len(pat_hits) > 50:
            lines.append(f"| ... | ... | ще {len(pat_hits) - 50} хітів |")
        lines.append("")

    lines.append("---")
    lines.append(f"Скан завершено. Перевірте warn-хіти на відповідність Правилам №3, №4, №6, №9.")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def _write_json(hits: list[dict[str, Any]], out_path: Path) -> None:
    """Записує хіти у JSON."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "generated_utc": ts,
        "total": len(hits),
        "warn_count": sum(1 for h in hits if h["severity"] == "warn"),
        "info_count": sum(1 for h in hits if h["severity"] == "info"),
        "hits": hits,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    print(f"[hardcode_scan] Сканую {_REPO_ROOT} ...")
    hits = scan_repo()
    warn_count = sum(1 for h in hits if h["severity"] == "warn")
    info_count = sum(1 for h in hits if h["severity"] == "info")
    print(f"[hardcode_scan] Знайдено {len(hits)} хітів (warn={warn_count}, info={info_count})")

    out_dir = _REPO_ROOT / "reports" / "mpv_proof"
    md_path = out_dir / "hardcode_hits.md"
    json_path = out_dir / "hardcode_hits.json"

    _write_md(hits, md_path)
    _write_json(hits, json_path)

    print(f"[hardcode_scan] Звіт: {md_path}")
    print(f"[hardcode_scan] JSON:  {json_path}")

    if warn_count > 0:
        print(f"[hardcode_scan] ⚠️ {warn_count} warn-хітів потребують уваги.")
    else:
        print("[hardcode_scan] ✅ Жодних warn-хітів.")


if __name__ == "__main__":
    main()
