"""ADR-0105 S1: журнал візитів — один рядок JSONL на WS-сесію.

Ключ відвідувача — випадковий рядок у HttpOnly-cookie на шляху /ws, який видає сам сервер: без змін в UI,
у URL (а отже в логах nginx поруч з IP) ключ не потрапляє, JS сторінки його не бачить. Кожне підключення
продовжує строк cookie, тож людина, що періодично повертається, лишається тим самим ключем.

Журнал пише лише факти: ключ, час, країну (заголовок Cloudflare), клас пристрою з User-Agent, переглянуті
символ/TF. Рішення «людина чи бот», нові/повторні, ретеншн — справа зведення (S3). IP не пишеться.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import uuid
from dataclasses import MISSING, dataclass, field, fields
from typing import Any, Dict, List, Mapping, Optional, Tuple

_log = logging.getLogger(__name__)

VISITOR_COOKIE = "aione_vid"
VISITOR_COOKIE_PATH = "/ws"
# Chrome обрізає Max-Age до 400 діб; рік = горизонт ретеншну (ADR-0105 §3.2)
VISITOR_COOKIE_MAX_AGE_S = 365 * 24 * 3600
COUNTRY_HEADER = "CF-IPCountry"

_VISITOR_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_BOT_UA_RE = re.compile(
    r"bot|crawl|spider|slurp|curl|python|go-http|wget|scan|headless|preview|httpclient|okhttp|axios|"
    r"lighthouse|facebookexternalhit",
    re.I,
)
# Порядок значущий: UA iPhone містить «like Mac OS X», Android — «Linux», Chrome — «Safari», Edge/Opera — «Chrome»
_OS_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("iPhone", "iOS"),
    ("iPad", "iPadOS"),
    ("Android", "Android"),
    ("CrOS", "ChromeOS"),
    ("Windows", "Windows"),
    ("Mac OS X", "macOS"),
    ("Linux", "Linux"),
)
_BROWSER_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("Telegram", "Telegram"),
    ("Edg", "Edge"),
    ("OPR", "Opera"),
    ("FxiOS", "Firefox"),
    ("Firefox", "Firefox"),
    ("CriOS", "Chrome"),
    ("Chrome", "Chrome"),
    ("Safari", "Safari"),
)
_MOBILE_OS = frozenset({"iOS", "iPadOS", "Android"})


@dataclass(frozen=True)
class VisitorsPolicy:
    """config.json → visitors (ADR-0105). Дефолти тут = значення в config.json."""

    enabled: bool
    dir: str
    max_views_per_visit: int = 20
    retention_days: int = 365
    human_min_session_s: int = 30
    visit_gap_min: int = 30
    display_tz: str = "Europe/Prague"  # власник живе за Прагою (ADR-0106)


# Мінімуми цілих полів: відсікають безглузді налаштування (ретеншн коротший за місяць зведення не порахує)
_POLICY_INT_MINIMUMS: Dict[str, int] = {
    "max_views_per_visit": 1,
    "retention_days": 30,
    "human_min_session_s": 1,
    "visit_gap_min": 1,
}


def visitors_policy(cfg: Mapping[str, Any]) -> VisitorsPolicy:
    """Розбирає секцію `visitors`; немає секції = вимкнено. Невалідна секція → ValueError."""
    section = cfg.get("visitors")
    if section is None:
        return VisitorsPolicy(enabled=False, dir="")
    if not isinstance(section, Mapping):
        raise ValueError("CONFIG_VISITORS_INVALID: visitors must be an object")
    defaults = {f.name: f.default for f in fields(VisitorsPolicy) if f.default is not MISSING}
    enabled = section.get("enabled", False)
    directory = section.get("dir", "")
    display_tz = section.get("display_tz", defaults["display_tz"])
    if not isinstance(enabled, bool):
        raise ValueError("CONFIG_VISITORS_INVALID: enabled must be bool")
    if not isinstance(directory, str) or (enabled and not directory):
        raise ValueError("CONFIG_VISITORS_INVALID: dir must be a non-empty string")
    if not isinstance(display_tz, str) or not display_tz:
        raise ValueError("CONFIG_VISITORS_INVALID: display_tz must be a non-empty string")
    ints: Dict[str, int] = {}
    for name, minimum in _POLICY_INT_MINIMUMS.items():
        value = section.get(name, defaults[name])
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError("CONFIG_VISITORS_INVALID: %s must be int >= %d" % (name, minimum))
        ints[name] = value
    return VisitorsPolicy(enabled=enabled, dir=directory, display_tz=display_tz, **ints)


def classify_device(user_agent: str) -> Dict[str, Any]:
    """Клас пристрою з User-Agent: ОС, браузер, мобільний. Сирий UA не зберігається."""
    os_name = next((name for marker, name in _OS_MARKERS if marker in user_agent), "?")
    browser = next((name for marker, name in _BROWSER_MARKERS if marker in user_agent), "?")
    mobile = os_name in _MOBILE_OS or "Mobi" in user_agent
    return {"os": os_name, "browser": browser, "mobile": mobile}


def is_bot_user_agent(user_agent: str) -> bool:
    """UA називає себе ботом/інструментом, або порожній."""
    return not user_agent or bool(_BOT_UA_RE.search(user_agent))


def normalize_country(raw: Optional[str]) -> Optional[str]:
    """ISO-код країни від Cloudflare; «XX»/«T1» (невідомо/Tor) та сміття → None."""
    if raw is None:
        return None
    code = raw.strip().upper()
    if not _COUNTRY_RE.match(code) or code == "XX":
        return None
    return code


@dataclass
class Visit:
    """Одна WS-сесія відвідувача, поки вона відкрита."""

    vid: str
    vid_issued: bool
    country: Optional[str]
    device: Dict[str, Any]
    bot_ua: bool
    start_ms: int
    max_views: int
    messages: int = 0
    views: List[str] = field(default_factory=list)
    views_dropped: int = 0

    def note_message(self, symbol: Optional[str], tf_label: Optional[str]) -> None:
        """Після кожного повідомлення клієнта: лічильник + перегляд «символ:TF», якщо він змінився."""
        self.messages += 1
        if symbol is None or tf_label is None:
            return
        view = "%s:%s" % (symbol, tf_label)
        if self.views and self.views[-1] == view:
            return
        if len(self.views) >= self.max_views:
            self.views_dropped += 1
            return
        self.views.append(view)

    def to_record(self, client_id: str, end_ms: int, close_code: Optional[int]) -> Dict[str, Any]:
        return {
            "vid": self.vid,
            "vid_issued": self.vid_issued,
            "client_id": client_id,
            "start_ms": self.start_ms,
            "end_ms": end_ms,
            "duration_s": round(max(0, end_ms - self.start_ms) / 1000.0, 1),
            "country": self.country,
            "device": self.device,
            "bot_ua": self.bot_ua,
            "messages": self.messages,
            "views": self.views,
            "views_dropped": self.views_dropped,
            "close_code": close_code,
        }


class VisitorsJournal:
    """Пише завершені візити у `<dir>/sessions-YYYYMM.jsonl` (місяць — за початком візиту, UTC)."""

    def __init__(self, directory: str, max_views_per_visit: int) -> None:
        self._dir = directory
        self._max_views = max_views_per_visit
        self.write_failures = 0

    def start(self, cookies: Mapping[str, str], headers: Mapping[str, str], now_ms: int) -> Visit:
        """Відкриває візит за cookie й заголовками WS-запиту; немає валідного ключа → новий."""
        vid = cookies.get(VISITOR_COOKIE, "")
        issued = not _VISITOR_ID_RE.match(vid)
        if issued:
            vid = uuid.uuid4().hex
        user_agent = headers.get("User-Agent", "")
        return Visit(
            vid=vid,
            vid_issued=issued,
            country=normalize_country(headers.get(COUNTRY_HEADER)),
            device=classify_device(user_agent),
            bot_ua=is_bot_user_agent(user_agent),
            start_ms=now_ms,
            max_views=self._max_views,
        )

    def finish(self, visit: Visit, client_id: str, end_ms: int, close_code: Optional[int]) -> bool:
        """Дописує рядок візиту. Збій — гучний WARNING, WS-сервер працює далі (I5)."""
        month = dt.datetime.fromtimestamp(visit.start_ms / 1000.0, tz=dt.timezone.utc).strftime("%Y%m")
        path = os.path.join(self._dir, "sessions-%s.jsonl" % month)
        record = visit.to_record(client_id, end_ms, close_code)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            self.write_failures += 1
            _log.warning(
                "VISITORS_JOURNAL_WRITE_FAIL path=%s err=%s failures=%d", path, exc, self.write_failures
            )
            return False
        return True


def visitor_cookie_attrs(origin: str) -> Dict[str, Any]:
    """Атрибути cookie ключа; Secure — коли сторінка відкрита по https (заголовок Origin WS-запиту)."""
    return {
        "path": VISITOR_COOKIE_PATH,
        "max_age": VISITOR_COOKIE_MAX_AGE_S,
        "httponly": True,
        "samesite": "Strict",
        "secure": origin.startswith("https://"),
    }


def open_journal(cfg: Mapping[str, Any]) -> Optional[VisitorsJournal]:
    """Журнал для WS-сервера або None з одним рядком у лог чому (каталог створює ops, не сервер)."""
    try:
        policy = visitors_policy(cfg)
    except ValueError as exc:
        _log.warning("VISITORS_JOURNAL_OFF reason=config_invalid err=%s", exc)
        return None
    if not policy.enabled:
        _log.info("VISITORS_JOURNAL_OFF reason=disabled")
        return None
    if not os.path.isdir(policy.dir):
        _log.warning("VISITORS_JOURNAL_OFF reason=dir_missing dir=%s", policy.dir)
        return None
    if not os.access(policy.dir, os.W_OK):
        _log.warning("VISITORS_JOURNAL_OFF reason=dir_not_writable dir=%s", policy.dir)
        return None
    _log.info("VISITORS_JOURNAL_ON dir=%s", policy.dir)
    return VisitorsJournal(policy.dir, policy.max_views_per_visit)
