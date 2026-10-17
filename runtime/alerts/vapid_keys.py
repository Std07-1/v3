"""Ключ VAPID для пушу (ADR-0114 Rev 2 §2a): новий P-256 → `ALERTS_VAPID_PRIVATE_KEY` в env-файлі процесу.

Запуск на VPS: `cd /opt/smc-v3 && sudo .venv/bin/python -m runtime.alerts.vapid_keys`
- Приватний ключ не друкується й не потрапляє в argv; друкується лише публічний (його й так віддає
  `GET /api/alerts/push/key`).
- Інші рядки файлу (напр. токен Telegram) лишаються; попередній файл — поруч `*.bak.<epoch>`; запис атомарний (tmp у
  тій самій теці → 640 → root:<група> → rename), тека 750.
- Ключ уже є → відмова без `--rotate`: нова пара анулює всі підписки браузерів (кожен вмикає сповіщення знову).
"""

from __future__ import annotations

import argparse
import base64
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric import ec

from runtime.alerts.webpush import application_server_key, load_vapid

ENV_KEY = "ALERTS_VAPID_PRIVATE_KEY"
DEFAULT_ENV_FILE = "/etc/smc-v3/alerts.env"
DEFAULT_GROUP = "smc"


def generate_private_key() -> str:
    """base64url сирих 32 байт приватного скаляра P-256 — формат, який читає `webpush.load_vapid`."""
    scalar = ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value
    return base64.urlsafe_b64encode(scalar.to_bytes(32, "big")).rstrip(b"=").decode()


def upsert_env_line(text: str, key: str, value: str) -> Tuple[str, bool]:
    """Рядок `key=value` замінює наявний або додається в кінець; решта файлу — як була. → (текст, чи був ключ)."""
    lines = text.splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i], found = f"{key}={value}", True
    if not found:
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n", found


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def write_env_atomic(path: Path, text: str, group: str) -> Optional[Path]:
    """Атомарна заміна env-файлу (640, root:<група> під root). → шлях бекапу попередньої версії або None."""
    created_dir = not path.parent.exists()
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.bak.{int(time.time())}")
        shutil.copy2(path, backup)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o640)
        if _is_root():
            if created_dir:  # чужу теку (напр. /etc при кривому шляху) не перепризначаємо
                shutil.chown(path.parent, user="root", group=group)
            shutil.chown(tmp, user="root", group=group)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return backup


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m runtime.alerts.vapid_keys", description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--group", default=DEFAULT_GROUP)
    parser.add_argument("--rotate", action="store_true", help="замінити наявний ключ (усі підписки анулюються)")
    args = parser.parse_args(argv)
    path = Path(args.env_file)
    if args.env_file == DEFAULT_ENV_FILE and hasattr(os, "geteuid") and not _is_root():
        print("потрібен root: sudo .venv/bin/python -m runtime.alerts.vapid_keys", file=sys.stderr)
        return 2
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    private_key = generate_private_key()
    public_key = application_server_key(load_vapid(private_key))  # самоперевірка формату до запису
    text, existed = upsert_env_line(current, ENV_KEY, private_key)
    if existed and not args.rotate:
        print(f"{ENV_KEY} уже є в {path}; заміна анулює всі підписки браузерів — лише з --rotate", file=sys.stderr)
        return 2
    backup = write_env_atomic(path, text, args.group)
    print(f"записано {path} (640, група {args.group}){f', попередній — {backup}' if backup else ''}")
    print(f"публічний ключ: {public_key}")
    print("застосувати: sudo supervisorctl restart smc-alerts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
