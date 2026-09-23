"""tools/repair/jsonl_rewrite.py — спільна частина ремонтних інструментів part-файлів JSONL.

Інструменти переписують SSOT на диску: `sort_jsonl_by_open_ms` (перестановка рядків),
`dedup_jsonl_lastwins` (прибирає переможені записи) і заміна part-файлів ADR-0095 S7
(`partfile_io`, байтовий вміст з EOL кожного рядка). Усі мусять однаково: читати рядок так, як
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


def rewrite_atomic(path: str, lines: List[str], backup_dir: Optional[str] = None) -> str:
    """Записати `lines` замість вмісту наявного `path` (кожен рядок + LF); повертає шлях бекапу з допатчевим вмістом.

    Порядок і рейки — `replace_bytes_atomic`. `backup_dir` — див. там.
    """
    backup = replace_bytes_atomic(path, "".join(line + "\n" for line in lines).encode("utf-8"), backup_dir=backup_dir)
    if backup is None:  # недосяжне: наявний файл завжди отримує бекап, а відсутній дає FileNotFoundError
        raise AssertionError("REWRITE_BACKUP_MISSING path=%s" % path)
    return backup


def replace_bytes_atomic(
    path: str, data: bytes, backup_dir: Optional[str] = None, *, like: Optional[str] = None
) -> Optional[str]:
    """Підмінити вміст `path` байтами `data` без миті, коли файла немає; повертає шлях бекапу старого вмісту.

    Порядок важливий. Наївне «спершу перейменувати оригінал у .bak, потім підставити
    .tmp» лишає вікно, у якому part-файла немає: читач у цю мить (ws_server живий і
    читає диск) отримає FileNotFoundError і МОВЧКИ пропустить файл — у графіку зʼявиться
    дірка на рівному місці. Тому: спершу пишемо .tmp у тому ж каталозі і фсинкаємо, режим і
    власник — як в оригіналу, далі бекап робимо жорстким лінком на СТАРИЙ inode (os.link не
    чіпає ім'я path), і лише потім один атомарний os.replace. Файл існує весь час; читач бачить
    або старий вміст, або новий. Після заміни — fsync каталогу (POSIX) і перечитування: вміст на
    диску мусить дорівнювати `data` байт у байт, інакше RuntimeError REWRITE_VERIFY_FAILED.

    `backup_dir` — каталог бекапу (на тій самій ФС, щоб лінк був можливий): старий inode лягає в
    `<backup_dir>/<ім'я файла>`, а наявне там ім'я — FileExistsError до будь-якого запису (бекап
    попереднього перепису не затирається). Без `backup_dir` — сусід `.bak.<unix_ts>[.<n>]`.

    Файла `path` ще немає — він створюється з режимом і власником файла `like` (сусіднього part-файла), бекапу
    немає (None); без `like` — FileNotFoundError, а не файл із правами процесу ремонту.
    """
    exists = os.path.lexists(path)
    if not exists and like is None:
        raise FileNotFoundError("REWRITE_TARGET_MISSING path=%s — для нового файла потрібен like" % path)
    tmp = "%s.tmp" % path
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        _copy_mode_and_owner(path if exists else like, tmp)
        backup: Optional[str] = None
        if exists:
            backup = _backup_old_inode(path) if backup_dir is None else _backup_into_dir(path, backup_dir)
    except BaseException:
        os.remove(tmp)
        raise
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "rb") as fh:
        on_disk = fh.read()
    if on_disk != data:
        raise RuntimeError("REWRITE_VERIFY_FAILED path=%s backup=%s — вміст на диску не той, що записано" % (path, backup))
    return backup


def _copy_mode_and_owner(path: str, tmp: str) -> None:
    """Режим доступу і власник — як у оригіналу: ремонт міняє рядки, а не права.

    На проді частина part-файлів має 666, а новий файл від smc з umask 002 отримав би 664. Власника живий писар
    потребує, щоб і далі дописувати файл: якщо змінити його не можна (не root), заміна відмовляє гучно
    (REWRITE_OWNER_NOT_PRESERVED), а не лишає файл чужим для писаря. Windows власника POSIX не має.
    """
    shutil.copymode(path, tmp)
    if not hasattr(os, "chown"):
        return
    orig, new = os.stat(path), os.stat(tmp)
    if (orig.st_uid, orig.st_gid) == (new.st_uid, new.st_gid):
        return
    try:
        os.chown(tmp, orig.st_uid, orig.st_gid)
    except OSError as exc:
        raise PermissionError(
            "REWRITE_OWNER_NOT_PRESERVED path=%s uid=%d gid=%d cause=%s" % (path, orig.st_uid, orig.st_gid, exc)
        ) from exc


def _fsync_dir(folder: str) -> None:
    """Запис імені після os.replace — на диск (POSIX). На Windows каталог не відкривається для fsync."""
    if os.name != "posix":
        return
    dir_fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _backup_into_dir(path: str, backup_dir: str) -> str:
    """Старий inode — у `<backup_dir>/<ім'я>`; наявне ім'я там — FileExistsError, а не перезапис бекапу."""
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, os.path.basename(path))
    if os.path.lexists(backup):
        raise FileExistsError("REWRITE_BACKUP_EXISTS backup=%s — бекап попереднього перепису не затирається" % backup)
    try:
        os.link(path, backup)
    except FileExistsError:  # ім'я зайняли між перевіркою і лінком — теж не затираємо
        raise
    except OSError:
        # ФС без жорстких лінків — копія (теж не чіпає ім'я path)
        shutil.copy2(path, backup)
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
