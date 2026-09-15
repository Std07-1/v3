"""tools/repair/jsonl_rewrite.py — спільна частина ремонтних інструментів part-файлів JSONL.

Два інструменти переписують SSOT на диску: `sort_jsonl_by_open_ms` (перестановка рядків) і
`dedup_jsonl_lastwins` (прибирає переможені записи). Обидва мусять однаково: читати рядок так, як
його бачать читачі, розпізнавати ключ так, як його розпізнають читачі (`isinstance(open_ms, int)`,
`runtime/store/layers/disk_layer.py`), і підміняти файл без миті, коли його немає. Друга копія
цієї процедури в іншому інструменті рано чи пізно розійшлась би з першою — саме так сталося з
вибирачем бару (ADR-0094).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
import time
from typing import Callable, Dict, List, Optional, Tuple

from core.model.bar_choice import choose_better_bar
from tools.repair.durable_fs import fsync_parent_dir


@dataclasses.dataclass(frozen=True)
class KeyGroup:
    """Рядки одного `open_time_ms` у файлі: індекси в порядку файла і той, що бачать читачі."""

    winner: int
    members: Tuple[int, ...]


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


def key_groups(lines: List[str]) -> Dict[int, KeyGroup]:
    """Групи рядків за ключем і переможець кожної — тим самим вибирачем і в тому самому порядку, що читачі.

    Рядки обходяться в порядку файла: крок нічиєї `choose_better_bar` віддає перемогу пізнішому запису.
    Рядки без цілого `open_time_ms` читачі пропускають — у групи вони не входять. Спільне для дедупу
    (прибирає переможених) і ремонту значень (патчить саме переможця, ADR-0096 §3.3 B): друга копія
    фолда розійшлась би з першою так само, як колись розійшлися вибирачі (ADR-0094).
    """
    winners: Dict[int, Tuple[int, dict]] = {}
    members: Dict[int, List[int]] = {}
    for index, line in enumerate(lines):
        key = open_ms_of(line)
        if key is None:
            continue
        bar = json.loads(line)
        members.setdefault(key, []).append(index)
        current = winners.get(key)
        if current is None or choose_better_bar(current[1], bar) is bar:
            winners[key] = (index, bar)
    return {key: KeyGroup(winner=winners[key][0], members=tuple(members[key])) for key in sorted(winners)}


def rewrite_tmp_path(path: str) -> str:
    """Тимчасовий файл `rewrite_atomic` для `path` — теж шлях запису (рейки цілі перевіряють і його)."""
    return "%s.tmp" % path


def rewrite_atomic(path: str, lines: List[str], before_replace: Optional[Callable[[str], None]] = None) -> str:
    """Записати `lines` замість вмісту `path`; повертає шлях бекапу з допатчевим вмістом.

    Порядок важливий. Наївне «спершу перейменувати оригінал у .bak, потім підставити
    .tmp» лишає вікно, у якому part-файла немає: читач у цю мить (ws_server живий і
    читає диск) отримає FileNotFoundError і МОВЧКИ пропустить файл — у графіку зʼявиться
    дірка на рівному місці. Тому: спершу пишемо .tmp і фсинкаємо, далі бекап робимо
    жорстким лінком на СТАРИЙ inode (os.link не чіпає ім'я path), і лише потім один
    атомарний os.replace і fsync каталогу (після збою живлення ім'я не повертається до допатчевого inode). Файл
    існує весь час; читач бачить або старий вміст, або новий.

    `before_replace(backup)` викликається, коли бекап уже є, а `path` ще старий: інструмент, що веде
    маніфест, записує шлях бекапу ДО підміни — процес, убитий одразу після os.replace, не лишить
    переписаний файл без відомого бекапу. Виняток із хука скасовує підміну: `path` не змінено, `.tmp` прибрано
    (бекап лишається — він уже названий у маніфесті).
    """
    tmp = rewrite_tmp_path(path)
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    # Режим доступу — як у оригіналу: ремонт міняє рядки, а не права (на проді частина
    # part-файлів має 666, а новий файл від smc з umask 002 отримав би 664).
    shutil.copymode(path, tmp)
    backup = _backup_old_inode(path)
    if before_replace is not None:
        try:
            before_replace(backup)
        except BaseException:
            _discard_tmp(tmp)
            raise
    os.replace(tmp, path)
    fsync_parent_dir(path)
    return backup


def _discard_tmp(tmp: str) -> None:
    """Підміну скасовано: тимчасовий файл не лишається поруч із part-файлом; невдача прибирання — гучна."""
    try:
        os.remove(tmp)
    except OSError as exc:
        logging.getLogger("tools.repair.jsonl_rewrite").error("JSONL_REWRITE_TMP_CLEANUP_FAILED path=%s err=%s", tmp, exc)


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
