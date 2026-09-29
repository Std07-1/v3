"""ADR-0105 S3: доставка зведення в Telegram — успіх, помилки без секретів, конфігурація з оточення, CLI."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

import pytest

from tools.visitors import report
from tools.visitors.notify import TELEGRAM_TEXT_LIMIT, NotifyError, send_telegram, telegram_credentials

TOKEN = "123456:SECRET-token-value"


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _opener_returning(payload, sent):
    def opener(request, timeout):
        sent.append((request, timeout))
        return _Response(json.dumps(payload).encode("utf-8"))

    return opener


def test_send_telegram_posts_text_to_chat_and_truncates_long_text():
    sent = []
    send_telegram("x" * (TELEGRAM_TEXT_LIMIT + 10), TOKEN, "42", opener=_opener_returning({"ok": True}, sent))
    (request, timeout), = sent
    body = urllib.parse.parse_qs(request.data.decode("utf-8"))
    assert request.get_method() == "POST" and request.full_url.endswith("/sendMessage")
    assert body["chat_id"] == ["42"]
    assert len(body["text"][0]) == TELEGRAM_TEXT_LIMIT and body["text"][0].endswith("…")
    assert timeout > 0


@pytest.mark.parametrize(
    "failure, expected",
    [
        (urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(b'{"ok":false,"description":"chat not found"}')),
         "status=400 desc=chat not found"),
        (urllib.error.URLError("dns"), "err=URLError"),
        (TimeoutError(), "err=TimeoutError"),
    ],
)
def test_send_telegram_failures_are_loud_without_token(failure, expected):
    def opener(request, timeout):
        raise failure

    with pytest.raises(NotifyError) as err:
        send_telegram("hi", TOKEN, "42", opener=opener)
    assert expected in str(err.value) and "SECRET" not in str(err.value)


def test_send_telegram_rejects_not_ok_payload():
    with pytest.raises(NotifyError, match="Forbidden"):
        send_telegram("hi", TOKEN, "42", opener=_opener_returning({"ok": False, "description": "Forbidden"}, []))


def test_telegram_credentials_name_missing_variables_only(monkeypatch):
    monkeypatch.delenv("VISITORS_TG_BOT_TOKEN", raising=False)
    monkeypatch.setenv("VISITORS_TG_CHAT_ID", "42")
    with pytest.raises(NotifyError, match="missing=VISITORS_TG_BOT_TOKEN$"):
        telegram_credentials()
    monkeypatch.setenv("VISITORS_TG_BOT_TOKEN", TOKEN)
    assert telegram_credentials() == (TOKEN, "42")


def test_report_main_exit_3_when_telegram_not_configured(tmp_path, monkeypatch, capsys):
    journal = tmp_path / "visitors"
    journal.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"visitors": {"enabled": True, "dir": str(journal)}}), encoding="utf-8")
    monkeypatch.delenv("VISITORS_TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("VISITORS_TG_CHAT_ID", raising=False)
    code = report.main(["--config", str(config), "--telegram", "--env-file", str(tmp_path / "missing.env")])
    out, err = capsys.readouterr()
    assert code == 3 and "Людей за період не було." in out and "VISITORS_TG_NOT_CONFIGURED" in err


def test_report_main_sends_digest(tmp_path, monkeypatch, capsys):
    journal = tmp_path / "visitors"
    journal.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"visitors": {"enabled": True, "dir": str(journal)}}), encoding="utf-8")
    monkeypatch.setenv("VISITORS_TG_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("VISITORS_TG_CHAT_ID", "42")
    delivered = []
    monkeypatch.setattr(report, "send_telegram", lambda text, token, chat_id: delivered.append((text, chat_id)))
    assert report.main(["--config", str(config), "--telegram", "--env-file", str(tmp_path / "missing.env")]) == 0
    assert delivered and delivered[0][1] == "42" and delivered[0][0].startswith("Відвідувачі за добу")
    assert "VISITORS_TG_SENT" in capsys.readouterr().err
