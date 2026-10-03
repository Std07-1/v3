"""ADR-0105 S2: із сесій журналу — відвідувачі, візити, людина чи бот, підсумок за період. Чиста логіка, без I/O.

Сесія = рядок журналу (одне WS-підключення). Візит = сесії одного ключа, між якими пауза не довша за `visit_gap_min`
(перепідключення, кілька вкладок). Людина — ключ, у якого є хоч одна людська сесія (ADR-0105 §3.3). Свій — ключ з міткою
own або хоч одною сесією з домашньої мережі власника (home_net): свої не рахуються ні людьми, ні ботами.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

DAY_MS = 86_400_000


@dataclass(frozen=True)
class Visit:
    number: int  # порядковий номер візиту цього відвідувача за всю історію журналу
    start_ms: int
    end_ms: int
    active_s: float  # об'єднання інтервалів сесій: паралельні вкладки час не подвоюють
    views: Tuple[str, ...]


@dataclass
class Visitor:
    vid: str
    human: bool
    home: bool  # хоч одна сесія з домашньої мережі власника
    first_ms: int
    country: Optional[str]
    device: Mapping[str, Any]
    visits: List[Visit] = field(default_factory=list)


@dataclass
class Row:
    """Відвідувач у зведенні: його візити за період, мітка, з ким дивився одночасно."""

    visitor: Visitor
    visits: List[Visit]
    name: Optional[str]
    together: List[str] = field(default_factory=list)  # короткі ключі інших людей з перетином у часі


@dataclass
class Digest:
    period_start_ms: int
    period_end_ms: int
    new: List[Row]
    returning: List[Row]
    own: List[Tuple[str, str]]  # (короткий ключ, мітка або «дім») своїх, що заходили за період
    own_visits: int
    bot_sessions_ua: int
    bot_sessions_short: int
    humans_7d: int
    humans_30d: int
    humans_all: int
    first_human_ms: Optional[int]


def visitor_sessions(records: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """Лише сесії відвідувачів: службові підключення з самого сервера повз nginx (internal) — не люди і не боти."""
    return [r for r in records if not r.get("internal")]


def is_human_session(record: Mapping[str, Any], human_min_session_s: int) -> bool:
    """UA не бот і (сесія довша за поріг або ≥2 перегляди: одне перемикання UI робить сам при відновленні пари)."""
    if record.get("bot_ua", True):
        return False
    return float(record.get("duration_s", 0)) >= human_min_session_s or len(record.get("views") or ()) >= 2


def build_visitors(
    records: Sequence[Mapping[str, Any]], human_min_session_s: int, visit_gap_min: int
) -> Dict[str, Visitor]:
    """Групує сесії за ключем у відвідувачів з пронумерованими візитами."""
    by_vid: Dict[str, List[Mapping[str, Any]]] = {}
    for record in records:
        by_vid.setdefault(record["vid"], []).append(record)
    visitors: Dict[str, Visitor] = {}
    for vid, sessions in by_vid.items():
        sessions.sort(key=lambda r: r["start_ms"])
        latest = sessions[-1]
        visitors[vid] = Visitor(
            vid=vid,
            human=any(is_human_session(r, human_min_session_s) for r in sessions),
            home=any(r.get("home_net") for r in sessions),
            first_ms=sessions[0]["start_ms"],
            country=latest.get("country"),
            device=latest.get("device") or {},
            visits=_merge_visits(sessions, visit_gap_min * 60_000),
        )
    return visitors


def summarize(
    visitors: Mapping[str, Visitor],
    records: Sequence[Mapping[str, Any]],
    period: Tuple[int, int],
    labels: Mapping[str, Mapping[str, Any]],
) -> Digest:
    """Підсумок за [start, end): люди нові/повторні, свої (виключені звідусіль), схожі на ботів, охоплення 7/30/усього."""
    start_ms, end_ms = period
    rows: List[Row] = []
    own: List[Tuple[str, str]] = []
    own_visits = 0
    for visitor in visitors.values():
        in_period = [v for v in visitor.visits if start_ms <= v.start_ms < end_ms]
        if not in_period:
            continue
        label = resolve_label(visitor.vid, labels)
        if is_own(visitor, labels):  # свої пристрої — лише в рядку «Свої», ні в людях, ні в ботах
            own_visits += len(in_period)
            own.append((visitor.vid[:4], str(label.get("name") or "дім")))
            continue
        if visitor.human:
            rows.append(Row(visitor, in_period, label.get("name")))
    _mark_together(rows)
    rows.sort(key=lambda row: row.visits[0].start_ms)
    bots = [
        r
        for r in records
        if start_ms <= r["start_ms"] < end_ms and not visitors[r["vid"]].human and not is_own(visitors[r["vid"]], labels)
    ]
    humans = [v for v in visitors.values() if v.human and not is_own(v, labels)]
    return Digest(
        period_start_ms=start_ms,
        period_end_ms=end_ms,
        new=[row for row in rows if row.visitor.first_ms >= start_ms],
        returning=[row for row in rows if row.visitor.first_ms < start_ms],
        own=own,
        own_visits=own_visits,
        bot_sessions_ua=sum(1 for r in bots if r.get("bot_ua", True)),
        bot_sessions_short=sum(1 for r in bots if not r.get("bot_ua", True)),
        humans_7d=_active_since(humans, end_ms - 7 * DAY_MS, end_ms),
        humans_30d=_active_since(humans, end_ms - 30 * DAY_MS, end_ms),
        humans_all=len(humans),
        first_human_ms=min((v.first_ms for v in humans), default=None),
    )


def is_own(visitor: Visitor, labels: Mapping[str, Mapping[str, Any]]) -> bool:
    """Свій пристрій: мітка own або сесії з домашньої мережі власника (рішення власника 03.10 — автоматично)."""
    return bool(resolve_label(visitor.vid, labels).get("own")) or visitor.home


def resolve_label(vid: str, labels: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
    """Мітка за найдовшим префіксом ключа (власник бачить у зведенні «#a1f3» і підписує саме так)."""
    matches = [key for key in labels if vid.startswith(key)]
    return labels[max(matches, key=len)] if matches else {}


def _mark_together(rows: Sequence[Row]) -> None:
    """Двоє різних людей з перетином візитів у часі — «одночасно з» (один на двох пристроях або дивились разом)."""
    for i, first in enumerate(rows):
        for second in rows[i + 1:]:
            if any(a.start_ms < b.end_ms and b.start_ms < a.end_ms for a in first.visits for b in second.visits):
                first.together.append(second.visitor.vid[:4])
                second.together.append(first.visitor.vid[:4])


def _active_since(humans: Sequence[Visitor], since_ms: int, end_ms: int) -> int:
    return sum(1 for v in humans if any(since_ms <= visit.start_ms < end_ms for visit in v.visits))


def _merge_visits(sessions: Sequence[Mapping[str, Any]], gap_ms: int) -> List[Visit]:
    clusters: List[List[Mapping[str, Any]]] = []
    cluster_end = 0
    for session in sessions:  # відсортовані за початком
        if not clusters or session["start_ms"] - cluster_end > gap_ms:
            clusters.append([])
            cluster_end = session["end_ms"]
        clusters[-1].append(session)
        cluster_end = max(cluster_end, session["end_ms"])
    return [_visit_of(number, cluster) for number, cluster in enumerate(clusters, start=1)]


def _visit_of(number: int, cluster: Sequence[Mapping[str, Any]]) -> Visit:
    active_ms, covered_to = 0, 0
    for session in cluster:
        begin = max(session["start_ms"], covered_to)
        if session["end_ms"] > begin:
            active_ms += session["end_ms"] - begin
        covered_to = max(covered_to, session["end_ms"])
    views: List[str] = []
    for session in cluster:
        views.extend(view for view in session.get("views") or () if view not in views)
    return Visit(
        number=number,
        start_ms=cluster[0]["start_ms"],
        end_ms=max(s["end_ms"] for s in cluster),
        active_s=active_ms / 1000.0,
        views=tuple(views),
    )
