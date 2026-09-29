"""ADR-0105 S3: доставка зведення в Telegram — окремий бот сповіщень платформи (не бот Арчі: одна особистість на бота).

Токен і чат — лише з оточення (або `.env` проду): VISITORS_TG_BOT_TOKEN, VISITORS_TG_CHAT_ID. Значення не друкуються
ніде, зокрема в текстах помилок (URL із токеном у повідомлення не потрапляє).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Tuple

from core.config_loader import env_str

TOKEN_ENV = "VISITORS_TG_BOT_TOKEN"
CHAT_ENV = "VISITORS_TG_CHAT_ID"
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_TEXT_LIMIT = 4096  # ліміт sendMessage
SEND_TIMEOUT_S = 20.0


class NotifyError(RuntimeError):
    """Зведення не доставлено; текст — без секретів."""


def telegram_credentials() -> Tuple[str, str]:
    token, chat_id = env_str(TOKEN_ENV), env_str(CHAT_ENV)
    missing = [name for name, value in ((TOKEN_ENV, token), (CHAT_ENV, chat_id)) if not value]
    if missing:
        raise NotifyError("VISITORS_TG_NOT_CONFIGURED missing=%s" % ",".join(missing))
    return str(token), str(chat_id)


def send_telegram(
    text: str,
    token: str,
    chat_id: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout_s: float = SEND_TIMEOUT_S,
) -> None:
    """Одне повідомлення; довший за ліміт текст обрізається з «…». Будь-який збій → NotifyError."""
    if len(text) > TELEGRAM_TEXT_LIMIT:
        text = text[: TELEGRAM_TEXT_LIMIT - 1] + "…"
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"})
    request = urllib.request.Request(
        "%s/bot%s/sendMessage" % (TELEGRAM_API, token), data=body.encode("utf-8"), method="POST"
    )
    try:
        # адреса стала (https Telegram API), схему не бере з даних — тому B310 тут хибний
        with opener(request, timeout=timeout_s) as response:  # nosec B310
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        description = _telegram_description(exc)
        raise NotifyError("VISITORS_TG_SEND_FAIL status=%d desc=%s" % (exc.code, description)) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise NotifyError("VISITORS_TG_SEND_FAIL err=%s" % type(exc).__name__) from None
    if not isinstance(payload, dict) or not payload.get("ok"):
        description = payload.get("description") if isinstance(payload, dict) else payload
        raise NotifyError("VISITORS_TG_SEND_FAIL desc=%s" % str(description)[:200])


def _telegram_description(exc: urllib.error.HTTPError) -> str:
    """Поле description з тіла помилки Telegram (напр. «chat not found»); тіло без нього — лише його довжина."""
    try:
        raw = exc.read().decode("utf-8", "replace")
    except OSError:
        return "?"
    try:
        description = json.loads(raw).get("description")
    except (ValueError, AttributeError):
        return "body_len=%d" % len(raw)
    return str(description)[:200] if description else "body_len=%d" % len(raw)
