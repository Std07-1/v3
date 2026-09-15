"""Довговічність імені файла після os.replace / O_EXCL-створення: fsync каталогу (ADR-0096 §3.3 B).

fsync файла зберігає його байти, але не запис у каталозі: після os.replace і збою живлення на POSIX-ФС ім'я може
вказувати на стару версію (part-файл до ремонту при маніфесті `rewritten`, маніфест без останнього стану), а щойно
створений файл — зникнути. Закріплює ім'я лише fsync каталогу. На Windows каталог не відкривається через os.open —
fsync каталогу там немає (NTFS журналює метадані сама), це не деградація. Спільне для `jsonl_rewrite` і
`first_tick_m1` (fetch-сторона — Python 3.7, без платформних залежностей).
"""

from __future__ import annotations

import errno
import logging
import os
from typing import Callable, Optional

logger = logging.getLogger("tools.repair.durable_fs")

# ФС без fsync каталогу (EINVAL на частині FUSE/мережевих ФС): ім'я не закріплено — видно в лозі, а підміна, яка вже
# відбулась, не перетворюється на відмову.
_UNSUPPORTED_ERRNOS = frozenset(
    code for code in (errno.EINVAL, getattr(errno, "ENOTSUP", None), getattr(errno, "EOPNOTSUPP", None)) if code
)


def _posix_dir_fsync(directory: str) -> None:
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# fsync каталогу цієї ОС; None — ОС без нього (Windows). Тести підміняють, щоб бачити виклик на будь-якій ОС.
dir_fsync: Optional[Callable[[str], None]] = _posix_dir_fsync if os.name == "posix" else None


def fsync_parent_dir(path: str) -> bool:
    """fsync каталогу, що містить `path`, — після os.replace чи створення файла; False — ОС чи ФС не підтримує."""
    if dir_fsync is None:
        return False
    directory = os.path.dirname(os.path.abspath(str(path)))
    try:
        dir_fsync(directory)
    except OSError as exc:
        if exc.errno not in _UNSUPPORTED_ERRNOS:
            raise
        logger.warning("FS_DIR_FSYNC_UNSUPPORTED dir=%s err=%s", directory, exc)
        return False
    return True
