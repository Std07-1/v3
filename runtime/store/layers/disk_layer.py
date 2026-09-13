from __future__ import annotations

import json
import logging
import os
from collections.abc import Set as AbstractSet
from typing import Any, Optional

from core.model.bar_choice import choose_better_bar, is_complete, is_final_source

logger = logging.getLogger("disk_layer")


def _select_newest_keys(
    paths: list[str],
    since_open_ms: Optional[int],
    to_open_ms: Optional[int],
    limit: int,
    *,
    final_only: bool,
    skip_preview: bool,
    final_sources: Optional[AbstractSet[str]],
) -> list[dict[str, Any]]:
    """Вікно читання: `limit` найновіших РІЗНИХ open_time_ms у (since, to] — спільне для TAIL і RANGE.

    До ADR-0094 P2 обидва читачі брали останні N РЯДКІВ (TAIL — з кінця файлів, RANGE — через
    deque(maxlen)), а part-файл не зобовʼязаний бути відсортованим: `tools/fetch_tf_backfill` лишає шов
    на кожному кроці ланцюжка сіяння. На такому файлі вікно мало дірку — заміряно 775 M1-барів
    відставання — і сортування прочитаного її не лікувало: те, чого не прочитали, не відновити.

    Тепер ключі відбираються за значенням. Part-файли обходяться від найновішого, кожен читається
    ЦІЛКОМ, і обхід зупиняється, щойно назбирано `limit` ключів або зачеплено межу `since`: імʼя
    `part-YYYYMMDD` — календарна доба open_time_ms без якоря (`ssot_jsonl`), тож кожен старіший файл
    містить лише менші ключі (скан 1 440 469 барів: 0 перетинів сусідніх файлів). Зайвим читається
    не більше одного файла.

    Повертаються ВСІ записи обраних ключів — за зростанням ключа і в порядку файла всередині ключа,
    щоб вибирач дублікатів (`core.model.bar_choice`) бачив цілу групу, а нічия дісталась пізнішому запису.
    """
    if limit <= 0:
        return []
    by_key: dict[int, list[dict[str, Any]]] = {}
    for path in reversed(paths):
        reached_since = False
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        logger.debug("DISK_JSON_DECODE_FAIL path=%s", path)
                        continue
                    open_ms = obj.get("open_time_ms")
                    if not isinstance(open_ms, int):
                        continue
                    if since_open_ms is not None and open_ms <= since_open_ms:
                        reached_since = True
                        continue
                    if to_open_ms is not None and open_ms > to_open_ms:
                        continue
                    if not _bar_passes_filters(
                        obj,
                        final_only=final_only,
                        skip_preview=skip_preview,
                        final_sources=final_sources,
                    ):
                        continue
                    by_key.setdefault(open_ms, []).append(obj)
        except FileNotFoundError:
            logger.debug("DISK_FILE_GONE path=%s", path)
            continue
        except OSError:
            # Нечитабельний SSOT-файл — не тиха дірка у вікні, а гучний сигнал (I5).
            logger.warning("DISK_PART_READ_FAILED path=%s", path, exc_info=True)
            continue
        if len(by_key) >= limit or reached_since:
            break
    keys = sorted(by_key)[-limit:]
    return [bar for key in keys for bar in by_key[key]]


def _needs_sort_by_open_ms(bars: list[dict[str, Any]]) -> bool:
    prev_open: Optional[int] = None
    for bar in bars:
        open_ms = bar.get("open_time_ms")
        if not isinstance(open_ms, int):
            continue
        if prev_open is not None and open_ms < prev_open:
            return True
        prev_open = open_ms
    return False


def _needs_dedup_by_open_ms(bars: list[dict[str, Any]]) -> bool:
    seen: set[int] = set()
    for bar in bars:
        open_ms = bar.get("open_time_ms")
        if not isinstance(open_ms, int):
            continue
        if open_ms in seen:
            return True
        seen.add(open_ms)
    return False


def _bar_has_canonical_ohlc(bar: dict[str, Any]) -> bool:
    o = bar.get("o", bar.get("open"))
    h = bar.get("h", bar.get("high"))
    l = bar.get("low", bar.get("l"))
    c = bar.get("c", bar.get("close"))
    if o is None or h is None or l is None or c is None:
        return False
    try:
        float(o)
        float(h)
        float(l)
        float(c)
    except Exception:
        logging.debug("DISK_LAYER_NON_CANONICAL_OHLC bar=%r", bar, exc_info=True)
        return False
    return True


def _bar_passes_filters(
    bar: dict[str, Any],
    *,
    final_only: bool,
    skip_preview: bool,
    final_sources: Optional[AbstractSet[str]],
) -> bool:
    complete = is_complete(bar)
    if skip_preview and not complete:
        return False
    if final_only:
        if complete:
            if not is_final_source(bar, final_sources):
                return False
        else:
            if not is_final_source(bar, final_sources):
                return False
            if not _bar_has_canonical_ohlc(bar):
                return False
    return True


def _dedup_open_ms(bars: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Згортає дублікати open_time_ms єдиним вибирачем (ADR-0094).

    `bars` мусять іти в порядку файла: крок нічиєї вибирача віддає перемогу пізнішому запису.
    `_finalize_tail_with_geom` сортує стабільно, тож взаємний порядок дублікатів зберігається.
    """
    deduped: dict[int, dict[str, Any]] = {}
    dropped = 0
    for bar in bars:
        open_ms = bar.get("open_time_ms")
        if not isinstance(open_ms, int):
            continue
        existing = deduped.get(open_ms)
        if existing is None:
            deduped[open_ms] = bar
            continue
        deduped[open_ms] = choose_better_bar(existing, bar)
        dropped += 1
    result = [deduped[k] for k in sorted(deduped.keys())]
    return result, dropped


def _finalize_tail_with_geom(
    out: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
    needs_sort = _needs_sort_by_open_ms(out)
    needs_dedup = _needs_dedup_by_open_ms(out)
    if not needs_sort and not needs_dedup:
        return out, None
    out.sort(key=lambda x: x.get("open_time_ms", 0))
    deduped, dropped = _dedup_open_ms(out)
    geom = {"sorted": True, "dedup_dropped": dropped}
    return deduped, geom


def _scan_open_ms(path: str) -> Optional[tuple[int, int]]:
    """(максимальний, останній-у-файлі) open_time_ms; None якщо валідних барів немає.

    Повертає обидва значення, бо саме їх розбіжність і є сигналом, що part-файл не
    відсортований за часом (див. `DiskLayer.last_open_ms`).
    """
    max_open_ms: Optional[int] = None
    last_line_open_ms: Optional[int] = None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    logging.debug(
                        "DISK_LAYER_LAST_JSON_DECODE_FAILED path=%s line=%r",
                        path,
                        line,
                        exc_info=True,
                    )
                    continue
                open_ms = obj.get("open_time_ms")
                if not isinstance(open_ms, int):
                    continue
                last_line_open_ms = open_ms
                if max_open_ms is None or open_ms > max_open_ms:
                    max_open_ms = open_ms
    except FileNotFoundError:
        logging.debug("DISK_LAYER_LAST_JSON_FILE_MISSING path=%s", path, exc_info=True)
        return None
    if max_open_ms is None or last_line_open_ms is None:
        return None
    return max_open_ms, last_line_open_ms


class DiskLayer:
    """Дисковий шар: читання JSONL SSOT."""

    def __init__(self, data_root: str) -> None:
        self._data_root = data_root

    def list_parts(self, symbol: str, tf_s: int) -> list[str]:
        d = os.path.join(self._data_root, symbol.replace("/", "_"), f"tf_{tf_s}")
        if not os.path.isdir(d):
            return []
        parts = [
            os.path.join(d, x)
            for x in os.listdir(d)
            if x.startswith("part-") and x.endswith(".jsonl")
        ]
        parts.sort()
        return parts

    def read_window_with_geom(
        self,
        symbol: str,
        tf_s: int,
        limit: int,
        *,
        since_open_ms: Optional[int] = None,
        to_open_ms: Optional[int] = None,
        use_tail: bool = False,
        final_only: bool = False,
        skip_preview: bool = False,
        final_sources: Optional[AbstractSet[str]] = None,
    ) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
        parts = self.list_parts(symbol, tf_s)
        if not parts:
            return [], None
        window = _select_newest_keys(
            parts,
            since_open_ms,
            to_open_ms,
            limit,
            final_only=final_only,
            skip_preview=skip_preview,
            final_sources=final_sources,
        )
        if use_tail:
            return _finalize_tail_with_geom(window)
        return window, None

    def last_open_ms(self, symbol: str, tf_s: int) -> Optional[int]:
        """Найбільший open_time_ms на диску — джерело watermark UDS.

        МАКСИМУМ, а не останній рядок файла: part-файл не зобов'язаний бути
        відсортованим за часом. `tools/fetch_tf_backfill` дописує сторінки історії у
        зворотному порядку і лишає шов на кожному кроці ланцюжка (health-check бачить
        це як `unsorted=15..17` на кожному засіяному символі). Останній рядок такого
        файла старіший за максимум, а занижений watermark нічого не блокує — навпаки,
        пускає назад бари, які на диску вже є (`uds._watermark_drop_reason`:
        `open_ms > wm` → приймається), і вони дописуються вдруге.

        Ім'я `part-YYYYMMDD` — це календарна доба `open_time_ms` без жодного якоря
        (`ssot_jsonl`), тому імена строго монотонні за часом і максимум завжди лежить
        у найновішому файлі. Старіші читаємо лише тоді, коли найновіший не дав жодного
        валідного бару: інакше порожній або битий файл дав би `watermark=None`, тобто
        прийняв би назад усю історію.
        """
        for path in reversed(self.list_parts(symbol, tf_s)):
            scanned = _scan_open_ms(path)
            if scanned is None:
                continue
            max_open_ms, last_line_open_ms = scanned
            if max_open_ms != last_line_open_ms:
                logger.warning(
                    "DISK_PART_UNSORTED path=%s max_open_ms=%d last_line_open_ms=%d "
                    "behind_ms=%d — watermark узято з максимуму",
                    path,
                    max_open_ms,
                    last_line_open_ms,
                    max_open_ms - last_line_open_ms,
                )
            return max_open_ms
        return None
