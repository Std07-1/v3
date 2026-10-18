"""ADR-0114 / ADR-0091 P2: Redis-користувач `smc_alerts` для процесу smc-alerts — лише читання і підписка.

Запуск на VPS (під root, з кореня репо):
    sudo .venv/bin/python -B ops/vps/alerts-redis-acl.py create   # користувач + креденшели в supervisor-програмі
    sudo supervisorctl reread && sudo supervisorctl update smc-alerts
    sudo .venv/bin/python -B ops/vps/alerts-redis-acl.py check    # ACL LOG: відмови smc_alerts

Redis проду з 06.09.2026 під ACL (`default off`), сервіси беруть креденшели з `environment` своєї supervisor-програми
(`AI_ONE_REDIS_USERNAME`/`AI_ONE_REDIS_PASSWORD`). smc-alerts відкритий в інтернет (POST /api/alerts/*), тому не
отримує `smc_platform`: лише GET/TTL/LRANGE ключів `v3_local:*` (tick:last, UDS-читач M1) і SUBSCRIBE на канал тіків.
Без PUBLISH (підробка тіків для графіка), без запису, без KEYS/SCAN, без адмін-команд. Redis 6.0: `&<channel>` не
підтримується — канали ACL не звузити (ADR-0091 P2).

Пароль генерується тут, не йде в argv і не друкується: `/root/redis-acl-alerts-<ts>.txt` (600) і `environment` у
`/etc/supervisor/conf.d/alerts.conf` (600 root, як інші конфіги з паролями). Перед змінами — бекапи `redis.conf` і
`alerts.conf`; персистенція — `CONFIG REWRITE` (як P2). Будь-яке відхилення — зупинка з причиною, без часткового
запису в supervisor. Відкат: `ACL DELUSER smc_alerts` + `CONFIG REWRITE`, `alerts.conf` з бекапу, `update smc-alerts`.
"""

from __future__ import annotations

import glob
import os
import re
import secrets
import shutil
import sys
import time
from typing import Callable, List

import redis

USER = "smc_alerts"
SUPERVISOR_CONF = "/etc/supervisor/conf.d/alerts.conf"
REDIS_CONF = "/etc/redis/redis.conf"
ADMIN_SECRETS_GLOB = "/root/redis-acl-*.txt"
# Новий користувач стартує без прав — правила лише додають дозволене.
ACL_RULES: List[str] = [
    "on", "~v3_local:*", "+@read", "-@dangerous", "-keys", "-scan", "+select", "+ping",
    "+subscribe", "+unsubscribe", "+psubscribe", "+punsubscribe",
]
# redis-py на кожному з'єднанні шле CLIENT SETINFO і сам ігнорує відмову; CLIENT (@admin) не даємо нікому з сервісів.
EXPECTED_DENIED = {"client"}


class StopError(Exception):
    """Умова не виконана — нічого далі не робимо."""


def _admin_client() -> redis.Redis:
    for path in sorted(glob.glob(ADMIN_SECRETS_GLOB)):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("smc_admin="):
                    return redis.Redis(host="127.0.0.1", port=6379, username="smc_admin",
                                       password=line.split("=", 1)[1].strip())
    raise StopError(f"нема smc_admin у {ADMIN_SECRETS_GLOB}")


def _expect_denied(label: str, call: Callable[[], object]) -> None:
    try:
        call()
    except redis.exceptions.NoPermissionError:
        print(f"  {label}: NOPERM — як задумано")
        return
    raise StopError(f"{label} дозволено — правило ширше, ніж задумано")


def _probe_rules(password: str) -> None:
    probe = redis.Redis(host="127.0.0.1", port=6379, db=1, username=USER, password=password)
    try:
        tick = probe.get("v3_local:tick:last:XAU_USD")
        print(f"  PING={probe.ping()}, GET tick:last — {'є' if tick else 'нема ключа'}")
        _expect_denied("SET v3_local:*", lambda: probe.set("v3_local:acl_probe_smc_alerts", "1"))
        _expect_denied("PUBLISH", lambda: probe.publish("acl_probe_channel", "x"))
        _expect_denied("KEYS", lambda: probe.keys("v3_local:*"))
        _expect_denied("FLUSHDB", lambda: probe.flushdb())
    finally:
        probe.close()


def _with_redis_env(conf_text: str, password: str) -> str:
    env_lines = re.findall(r"^environment=.*$", conf_text, flags=re.M)
    if "[program:smc-alerts]" not in conf_text or len(env_lines) != 1:
        raise StopError(f"{SUPERVISOR_CONF}: очікував програму smc-alerts з одним environment, маю {len(env_lines)}")
    addition = f',AI_ONE_REDIS_USERNAME="{USER}",AI_ONE_REDIS_PASSWORD="{password}"'
    return re.sub(r"^(environment=.*)$", lambda m: m.group(1) + addition, conf_text, count=1, flags=re.M)


def _write_root_only(path: str, text: str) -> None:
    """Атомарно, 600 root: тимчасовий файл поруч створюється вже з правами, потім rename."""
    tmp = f"{path}.tmp.{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def create() -> None:
    admin = _admin_client()
    if admin.acl_getuser(USER) is not None:
        raise StopError(f"користувач {USER} уже є")
    with open(SUPERVISOR_CONF, encoding="utf-8") as f:
        conf_text = f.read()
    if "AI_ONE_REDIS_" in conf_text:
        raise StopError(f"{SUPERVISOR_CONF} уже має креденшели Redis")
    password = secrets.token_hex(32)
    new_conf = _with_redis_env(conf_text, password)  # перевірка форми — до будь-яких змін

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    redis_backup = f"{REDIS_CONF}.bak.pre-{USER}.{stamp}"
    conf_backup = f"/root/alerts.conf.bak.pre-redis-acl.{stamp}"
    shutil.copy2(REDIS_CONF, redis_backup)
    shutil.copy2(SUPERVISOR_CONF, conf_backup)
    print(f"бекапи: {redis_backup}, {conf_backup}")

    admin.execute_command("ACL", "SETUSER", USER, ACL_RULES[0], f">{password}", *ACL_RULES[1:])
    info = admin.acl_getuser(USER)
    print(f"ACL {USER}: flags={info['flags']} keys={info['keys']} commands={info['commands']}")
    _probe_rules(password)

    admin.config_rewrite()
    with open(REDIS_CONF, encoding="utf-8") as f:
        persisted = [line for line in f if line.startswith(f"user {USER} ")]
    if len(persisted) != 1:
        raise StopError(f"CONFIG REWRITE: рядків «user {USER}» у {REDIS_CONF} — {len(persisted)}")
    print("redis.conf:", re.sub(r"#[0-9a-f]{64}", "#***", persisted[0].strip()))

    secret_path = f"/root/redis-acl-alerts-{stamp}.txt"
    _write_root_only(secret_path, f"{USER}={password}\n")
    _write_root_only(SUPERVISOR_CONF, new_conf)
    print(f"пароль: {secret_path} (600); {SUPERVISOR_CONF}: 600 root, environment +AI_ONE_REDIS_USERNAME/PASSWORD")
    print("далі: sudo supervisorctl reread && sudo supervisorctl update smc-alerts; потім — check")


def check() -> None:
    admin = _admin_client()
    info = admin.acl_getuser(USER)
    if info is None:
        raise StopError(f"користувача {USER} нема")
    print(f"ACL {USER}: flags={info['flags']} keys={info['keys']} commands={info['commands']}")
    denied = [entry for entry in admin.acl_log(count=128) if entry.get("username") == USER]
    unexpected = [entry for entry in denied if str(entry.get("object")) not in EXPECTED_DENIED]
    for entry in denied:
        print(f"  відмова: {entry.get('reason')} {entry.get('object')} ×{entry.get('count')} "
              f"(вік {entry.get('age-seconds')} с)")
    print(f"відмов {USER}: {len(denied)}, неочікуваних: {len(unexpected)}")
    if unexpected:
        raise StopError("є відмови поза очікуваними — процесу бракує прав (або щось пробує писати)")


def main(argv: List[str]) -> int:
    action = argv[1] if len(argv) > 1 else ""
    if action not in ("create", "check"):
        print(__doc__)
        return 2
    if not hasattr(os, "geteuid") or os.geteuid() != 0:  # лише VPS (Linux, root)
        print("потрібен root: sudo .venv/bin/python -B ops/vps/alerts-redis-acl.py " + action, file=sys.stderr)
        return 2
    try:
        if action == "create":
            create()
        else:
            check()
    except StopError as exc:
        print(f"STOP: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
