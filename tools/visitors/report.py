"""ADR-0105 S2: зведення відвідувачів із журналу WS-сервера — текст для власника. Доставляє ранковий звіт системного
бота (ADR-0106 §3.5): watchdog запускає цей інструмент від власника журналу і вставляє текст у звіт.

    python -m tools.visitors.report [--config config.json] [--hours 24] [--now-ms <epoch ms>] [--apply-retention]
        [--max-rows 20]

Читає всі збережені місяці `visitors.dir/sessions-YYYYMM.jsonl` («з нами з» і номер візиту — за всю історію) і
`visitors.dir/labels.json` — мітки ключів за префіксом: {"a1f3": {"name": "Юра"}, "7c20": {"name": "мій ПК", "own": true}};
own — свої пристрої, людьми не рахуються. Зіпсований рядок журналу зведення не валить, а рахується й згадується в тексті.
--apply-retention видаляє місячні файли, що цілком старші за visitors.retention_days (кожен — рядком у stderr).
--max-rows — скільки відвідувачів показати в кожній групі, решта — «…ще N» (повідомлення Telegram ≤ 4096 символів).

Коди виходу: 0 — зведення надруковано; 2 — журнал вимкнено/нема каталогу, конфіг або аргументи невалідні.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from core.config_loader import load_system_config
from runtime.visitors.journal import visitors_policy
from tools.visitors.aggregate import DAY_MS, Digest, Row, Visit, build_visitors, summarize, visitor_sessions

_SESSION_FILE_RE = re.compile(r"^sessions-(\d{4})(\d{2})\.jsonl$")
MAX_ROWS = 20  # типова межа рядків у групі (--max-rows): повідомлення Telegram ≤ 4096 символів
MAX_VISITS_PER_ROW = 3
MAX_VIEWS_PER_ROW = 8  # TF у рядку «Дивився»
MAX_OWN_SHOWN = 6  # своїх ключів у рядку «Свої»


def load_records(directory: str) -> Tuple[List[Dict[str, Any]], int]:
    """Усі сесії з місячних файлів + кількість зіпсованих рядків (невалідний JSON або нема ключа/часу)."""
    records: List[Dict[str, Any]] = []
    bad_lines = 0
    for name in sorted(os.listdir(directory)):
        if not _SESSION_FILE_RE.match(name):
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                if not _is_session_record(record):
                    bad_lines += 1
                    continue
                records.append(record)
    return records, bad_lines


def load_labels(directory: str) -> Tuple[Dict[str, Dict[str, Any]], Optional[str]]:
    """Мітки ключів; нема файлу — порожньо; невалідний — порожньо + причина для тексту зведення."""
    path = os.path.join(directory, "labels.json")
    if not os.path.exists(path):
        return {}, None
    try:
        with open(path, encoding="utf-8") as fh:
            labels = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, "labels.json не прочитано (%s)" % exc
    if not isinstance(labels, dict) or not all(isinstance(v, dict) for v in labels.values()):
        return {}, "labels.json має бути об'єктом {префікс ключа: {name, own}}"
    return labels, None


def expired_files(directory: str, now_ms: int, retention_days: int) -> List[str]:
    """Місячні файли, весь місяць яких старший за ретеншн."""
    cutoff_ms = now_ms - retention_days * DAY_MS
    expired = []
    for name in sorted(os.listdir(directory)):
        match = _SESSION_FILE_RE.match(name)
        if not match:
            continue
        year, month = int(match.group(1)), int(match.group(2))
        next_month = dt.datetime(year + month // 12, month % 12 + 1, 1, tzinfo=dt.timezone.utc)
        if next_month.timestamp() * 1000 <= cutoff_ms:
            expired.append(os.path.join(directory, name))
    return expired


def render(
    digest: Digest,
    hours: int,
    tz: ZoneInfo,
    bad_lines: int,
    labels_problem: Optional[str],
    max_rows: int = MAX_ROWS,
) -> str:
    """Простий текст для Telegram: розділи з позначками, кожен відвідувач — окремим блоком, час — у tz власника."""
    end_local = _local(digest.period_end_ms, tz)
    lines = ["👥 Відвідувачі за %s · до %s" % (_period_name(hours), end_local.strftime("%d.%m %H:%M"))]
    rows = digest.new + digest.returning
    if rows:
        visits = [v for row in rows for v in row.visits]
        lines.append(
            "Людей: %d (нових %d, повернулись %d) · візитів %d · разом %s"
            % (len(rows), len(digest.new), len(digest.returning), len(visits), _duration(sum(v.active_s for v in visits)))
        )
        for title, group in (("🆕 Нові", digest.new), ("🔁 Повернулись", digest.returning)):
            if not group:
                continue
            lines.extend(["", title])
            for number, row in enumerate(group[:max_rows], start=1):
                if number > 1:
                    lines.append("")
                lines.extend(_visitor_block(number, row, digest.period_end_ms, tz))
            if len(group) > max_rows:
                lines.append("…і ще %d" % (len(group) - max_rows))
    else:
        lines.append("Людей не було.")
    lines.append("")
    if digest.own_visits:
        devices = ", ".join("#%s %s" % own for own in digest.own[:MAX_OWN_SHOWN])
        more = " +%d" % (len(digest.own) - MAX_OWN_SHOWN) if len(digest.own) > MAX_OWN_SHOWN else ""
        lines.append("🏠 Свої: візитів %d — %s%s" % (digest.own_visits, devices, more))
    lines.append(
        "🤖 Схоже на ботів: %d (назвались ботом %d, короткі без дій %d)"
        % (digest.bot_sessions_ua + digest.bot_sessions_short, digest.bot_sessions_ua, digest.bot_sessions_short)
    )
    first = ", перша — %s" % _local(digest.first_human_ms, tz).strftime("%d.%m.%Y") if digest.first_human_ms else ""
    lines.append(
        "📈 Людей за 7 днів: %d · за 30: %d · усього: %d%s" % (digest.humans_7d, digest.humans_30d, digest.humans_all, first)
    )
    if bad_lines:
        lines.append("⚠️ Пропущено зіпсованих рядків журналу: %d" % bad_lines)
    if labels_problem:
        lines.append("⚠️ %s — мітки не застосовано" % labels_problem)
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--now-ms", type=int, default=None)
    ap.add_argument("--apply-retention", action="store_true")
    ap.add_argument("--max-rows", type=int, default=MAX_ROWS)
    args = ap.parse_args(argv)
    if args.max_rows < 1:
        ap.error("--max-rows must be >= 1")
    try:
        policy = visitors_policy(load_system_config(args.config))
    except ValueError as exc:
        print("VISITORS_REPORT_CONFIG_INVALID %s" % exc, file=sys.stderr)
        return 2
    if not policy.enabled or not os.path.isdir(policy.dir):
        print("VISITORS_REPORT_NO_JOURNAL enabled=%s dir=%s" % (policy.enabled, policy.dir), file=sys.stderr)
        return 2
    now_ms = args.now_ms if args.now_ms is not None else int(time.time() * 1000)
    if args.apply_retention:
        for path in expired_files(policy.dir, now_ms, policy.retention_days):
            os.remove(path)
            print("VISITORS_RETENTION_REMOVED %s" % path, file=sys.stderr)
    records, bad_lines = load_records(policy.dir)
    records = visitor_sessions(records)
    labels, labels_problem = load_labels(policy.dir)
    visitors = build_visitors(records, policy.human_min_session_s, policy.visit_gap_min)
    digest = summarize(visitors, records, (now_ms - args.hours * 3_600_000, now_ms), labels)
    print(render(digest, args.hours, ZoneInfo(policy.display_tz), bad_lines, labels_problem, args.max_rows))
    return 0


def _is_session_record(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and isinstance(record.get("vid"), str)
        and isinstance(record.get("start_ms"), int)
        and isinstance(record.get("end_ms"), int)
    )


def _visitor_block(number: int, row: Row, period_end_ms: int, tz: ZoneInfo) -> List[str]:
    """Відвідувач: хто й звідки, з якого дня з нами, кожен візит рядком, що дивився, з ким одночасно."""
    visitor, visits = row.visitor, row.visits
    block = [
        "%d. #%s%s · %s · %s"
        % (number, visitor.vid[:4], " " + row.name if row.name else "", _country(visitor.country), _device(visitor.device))
    ]
    if visits[0].number > 1:
        numbers = "№%d" % visits[0].number if len(visits) == 1 else "№%d–%d" % (visits[0].number, visits[-1].number)
        days = (period_end_ms - visitor.first_ms) // DAY_MS
        block.append("   з нами з %s (%d дн.), візит %s" % (_local(visitor.first_ms, tz).strftime("%d.%m"), days, numbers))
    block.extend("   • " + _visit_span(visit, tz) for visit in visits[:MAX_VISITS_PER_ROW])
    if len(visits) > MAX_VISITS_PER_ROW:
        block.append("   • …і ще %d" % (len(visits) - MAX_VISITS_PER_ROW))
    views = _views_by_symbol(visits)
    if views:
        block.append("   Дивився: " + views)
    if row.together:
        block.append("   👥 одночасно з " + ", ".join("#" + vid for vid in row.together))
    return block


def _visit_span(visit: Visit, tz: ZoneInfo) -> str:
    start, end = _local(visit.start_ms, tz), _local(visit.end_ms, tz)
    finish = end.strftime("%H:%M") if end.date() == start.date() else end.strftime("%d.%m %H:%M")
    return "%s–%s · %s" % (start.strftime("%d.%m %H:%M"), finish, _duration(visit.active_s))


def _views_by_symbol(visits: Sequence[Visit]) -> str:
    """«XAU/USD M30, M15; GER30 H1» — TF згруповано за символом у порядку перегляду, понад межу — «+N»."""
    by_symbol: Dict[str, List[str]] = {}
    shown = 0
    hidden = 0
    for visit in visits:
        for view in visit.views:
            symbol, _, tf = view.rpartition(":")
            tfs = by_symbol.setdefault(symbol or view, [])
            if tf in tfs:
                continue
            if shown >= MAX_VIEWS_PER_ROW:
                hidden += 1
                continue
            tfs.append(tf)
            shown += 1
    text = "; ".join("%s %s" % (symbol, ", ".join(tfs)) for symbol, tfs in by_symbol.items() if tfs)
    return text + (" +%d" % hidden if hidden else "")


def _country(code: Optional[str]) -> str:
    """Прапорець із коду ISO (регіональні літери Unicode) — без таблиці назв."""
    if not code or len(code) != 2 or not code.isalpha():
        return "🌐 ?"
    return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in code.upper()) + " " + code.upper()


def _device(device: Mapping[str, Any]) -> str:
    os_name, browser = device.get("os", "?"), device.get("browser", "?")
    if os_name == "?" and browser == "?":
        return "пристрій невідомий"
    return "%s, %s" % (os_name, browser)


def _period_name(hours: int) -> str:
    if hours == 24:
        return "добу"
    if hours % 24:
        return "%d год" % hours
    days = hours // 24
    if days % 10 == 1 and days % 100 != 11:
        word = "день"
    elif 2 <= days % 10 <= 4 and not 12 <= days % 100 <= 14:
        word = "дні"
    else:
        word = "днів"
    return "%d %s" % (days, word)


def _duration(seconds: float) -> str:
    minutes = int(round(seconds / 60.0))
    if minutes < 1:
        return "<1 хв"
    return "%d хв" % minutes if minutes < 60 else "%d год %d хв" % divmod(minutes, 60)


def _local(ms: int, tz: ZoneInfo) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000.0, tz=tz)


if __name__ == "__main__":
    sys.exit(main())
