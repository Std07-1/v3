"""tools/repair/jsonl_rewrite.py — спільна частина ремонтних інструментів part-файлів JSONL.

Два інструменти переписують SSOT на диску: `sort_jsonl_by_open_ms` (перестановка рядків) і
`dedup_jsonl_lastwins` (прибирає переможені записи). Обидва мусять однаково: читати рядок так, як
його бачать читачі, розпізнавати ключ так, як його розпізнають читачі (`isinstance(open_ms, int)`,
`runtime/store/layers/disk_layer.py`), і підміняти файл без миті, коли його немає. Друга копія
цієї процедури в іншому інструменті рано чи пізно розійшлась би з першою — саме так сталося з
вибирачем бару (ADR-0094).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from typing import List, Optional


def read_lines(path: str) -> List[str]:
    """Непорожні рядки файла без завершального переводу рядка, текст — як на диску."""
    with open(path, encoding="utf-8") as fh:
        return [ln.rstrip("\n").rstrip("\r") for ln in fh if ln.strip()]


def open_ms_of(line: str) -> Optional[int]:
    """Цілий `open_time_ms` рядка або None, якщо читач цей рядок пропустив би."""
    try:
        value = json.loads(line)["open_time_ms"]
    except Exception:
        return None
    return value if isinstance(value, int) else None


def rewrite_atomic(path: str, lines: List[str]) -> str:
    """Записати `lines` замість вмісту `path`; повертає шлях бекапу з допатчевим вмістом.

    Порядок важливий. Наївне «спершу перейменувати оригінал у .bak, потім підставити
    .tmp» лишає вікно, у якому part-файла немає: читач у цю мить (ws_server живий і
    читає диск) отримає FileNotFoundError і МОВЧКИ пропустить файл — у графіку зʼявиться
    дірка на рівному місці. Тому: спершу пишемо .tmp і фсинкаємо, далі бекап робимо
    жорстким лінком на СТАРИЙ inode (os.link не чіпає ім'я path), і лише потім один
    атомарний os.replace. Файл існує весь час; читач бачить або старий вміст, або новий.
    """
    tmp = "%s.tmp" % path
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    # Режим доступу — як у оригіналу: ремонт міняє рядки, а не права (на проді частина
    # part-файлів має 666, а новий файл від smc з umask 002 отримав би 664).
    shutil.copymode(path, tmp)
    backup = _backup_old_inode(path)
    os.replace(tmp, path)
    return backup


def _backup_old_inode(path: str) -> str:
    """Бекап допатчевого вмісту під ВІЛЬНИМ іменем `.bak.<unix_ts>[.<n>]`.

    Бекап — єдина копія рядків, які ремонт прибрав. Другий перепис того самого файла в ту саму
    секунду (сорт, одразу за ним дедуп) не має права його затерти: колись тут `copy2` перезаписував
    попередній `.bak.<ts>`, щойно `os.link` падав з FileExistsError.
    """
    base = "%s.bak.%d" % (path, int(time.time()))
    attempt = 0
    while True:
        backup = base if attempt == 0 else "%s.%d" % (base, attempt)
        attempt += 1
        try:
            os.link(path, backup)
            return backup
        except FileExistsError:
            continue
        except OSError:
            # ФС без жорстких лінків — копія (теж не чіпає ім'я path), лише у вільне ім'я.
            if os.path.lexists(backup):
                continue
            shutil.copy2(path, backup)
            return backup
