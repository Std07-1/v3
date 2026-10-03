"""ADR-0106: watchdog VPS — свої SSH-входи мовчки, ресурси як стан, падіння з причиною і простоєм, ранковий звіт,
обрізання довгих повідомлень. Лише POSIX (bash + coreutils) — прод і CI; системні команди — стаби в PATH, відправник
пише текст у файл замість Telegram.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import stat
import subprocess
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="bash watchdog — прод і CI")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCHDOG = os.path.join(REPO, "ops", "vps", "aione-watchdog.sh")
ALERT_SENDER = os.path.join(REPO, "ops", "vps", "aione-alert.sh")
SEPARATOR = "\n=====\n"
OWN_KEY = "SHA256:OWNKEY"

STUBS = {
    # відправник: текст у файл; код виходу — з alert.rc (імітація збою Telegram)
    "alert": 'printf "%s' + SEPARATOR.replace("\n", "\\n") + '" "$1" >> "$BOX/sent.txt"; exit "$(cat "$BOX/alert.rc" 2>/dev/null || echo 0)"\n',
    "systemctl": 'echo active\n',
    "supervisorctl": 'case "$1" in status) cat "$BOX/supervisor_status";; tail) cat "$BOX/stderr_tail" 2>/dev/null;; esac\n',
    "journalctl": (
        'cursor=""; for a in "$@"; do case "$a" in --cursor-file=*) cursor="${a#--cursor-file=}";; esac; done\n'
        'case " $* " in *" -n1 "*) [ -n "$cursor" ] && touch "$cursor"; exit 0;; esac\n'
        'case " $* " in *" ssh.service "*) cat "$BOX/ssh_lines" 2>/dev/null; : > "$BOX/ssh_lines";; esac\n'
    ),
    "ps": (
        'case " $* " in\n'
        '  *" --no-headers "*) seq "$(cat "$BOX/procs_count")";;\n'
        '  *"pcpu="*) echo "95.0 python3"; echo "3.0 bash";;\n'
        '  *"rss="*) echo "900000 python3"; echo "20000 bash";;\n'
        '  *) cat "$BOX/ps_names";;\n'
        'esac\n'
    ),
    "df": 'echo "Filesystem 1024-blocks Used Available Capacity Mounted"; echo "/dev/sda1 100 38 62 38% /"\n',
    "uptime": 'echo "up 3 weeks"\n',
    "runuser": 'echo "$*" >> "$BOX/runuser_args"; echo "Відвідувачі за добу: тест"\n',
    # команди власника: на засів — NEXT 5, далі — вміст commands_out; аргумент виклику — у commands_args
    "commands": (
        'echo "$1" >> "$BOX/commands_args"\n'
        'if [ "$1" = seed ]; then echo "NEXT 5"; else cat "$BOX/commands_out" 2>/dev/null || echo "NEXT $1"; fi\n'
    ),
}


class Box:
    """Пісочниця одного прогону watchdog-а: стаби, стан, файли-джерела."""

    def __init__(self, root):
        self.root = root
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name, body in STUBS.items():
            path = bin_dir / name
            path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        (root / "supervisor_status").write_text("smc:smc-ws RUNNING pid 1, uptime 1:00:00\n", encoding="utf-8")
        (root / "procs_count").write_text("100", encoding="utf-8")
        (root / "ps_names").write_text("python3\npython3\npython3\nbash\n", encoding="utf-8")
        (root / "loadavg").write_text("0.10 0.20 0.30 1/200 123\n", encoding="utf-8")
        (root / "meminfo").write_text(
            "MemTotal: 8000000 kB\nMemAvailable: 5000000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n", encoding="utf-8"
        )
        (root / "known_ips").write_text("203.0.113. дім\n", encoding="utf-8")
        (root / "known_keys").write_text(OWN_KEY + " мій ключ\n", encoding="utf-8")
        (root / "backups").mkdir()
        (root / "backups" / "trader-v3-x.tar.gz").write_text("", encoding="utf-8")
        (root / "fail2ban.log").write_text("", encoding="utf-8")
        (root / "settle.json").write_text(
            json.dumps({"finished_at": "2026-09-28T21:10", "exit_code": 0, "problems": []}), encoding="utf-8"
        )
        self.env = dict(
            os.environ,
            BOX=str(root),
            PATH="%s:%s" % (bin_dir, os.environ.get("PATH", "")),
            STATE_DIR=str(root / "state"),
            LOG=str(root / "watchdog.log"),
            ALERT=str(bin_dir / "alert"),
            COMMANDS=str(bin_dir / "commands"),
            KNOWN_IPS_FILE=str(root / "known_ips"),
            KNOWN_KEYS_FILE=str(root / "known_keys"),
            BACKUP_DIR=str(root / "backups"),
            PROC_LOADAVG=str(root / "loadavg"),
            PROC_MEMINFO=str(root / "meminfo"),
            FAIL2BAN_LOG=str(root / "fail2ban.log"),
            SETTLE_STATUS=str(root / "settle.json"),
            PLATFORM_DIR=str(root),
            WATCH_SERVICES="nginx",
            AUTOHEAL="0",
            REPORT_HOUR="0",
            REPORT_TZ="Europe/Prague",
        )

    def write(self, name, text):
        (self.root / name).write_text(text, encoding="utf-8")

    def state(self, name):
        path = self.root / "state" / name
        return path.read_text(encoding="utf-8").strip() if path.exists() else None

    def tick(self):
        subprocess.run(["bash", WATCHDOG], env=self.env, check=True, timeout=60)

    def sent(self):
        path = self.root / "sent.txt"
        if not path.exists():
            return []
        return [m for m in path.read_text(encoding="utf-8").split(SEPARATOR) if m]


@pytest.fixture
def box(tmp_path):
    sandbox = Box(tmp_path)
    sandbox.tick()  # перший тік лише засіває стан
    assert sandbox.sent() == []
    return sandbox


def test_own_ssh_login_is_silent_new_network_and_unknown_key_alert(box):
    box.write("ssh_lines", "\n".join([
        "Accepted publickey for ubuntu from 203.0.113.7 port 5000 ssh2: ED25519 " + OWN_KEY,
        "Accepted publickey for ubuntu from 198.51.100.9 port 5001 ssh2: ED25519 " + OWN_KEY,
        "Accepted publickey for root from 203.0.113.8 port 5002 ssh2: RSA SHA256:STRANGER",
    ]) + "\n")
    box.tick()
    sent = box.sent()
    assert len(sent) == 2
    assert "твій ключ, але нова мережа" in sent[0] and "198.51.100.9" in sent[0]
    assert "НЕВІДОМИМ ключем" in sent[1] and "(дім)" in sent[1] and "SHA256:STRANGER" in sent[1]
    assert (box.state("ssh_own.tally"), box.state("ssh_other.tally")) == ("1", "2")


def test_resource_problem_alerts_once_on_start_and_once_on_end(box):
    box.write("procs_count", "500")
    box.tick()
    assert box.sent() == []  # один поганий тік — ще не проблема
    box.tick()
    box.tick()
    started = box.sent()
    assert len(started) == 1
    assert "сплеск процесів: 500 (поріг 400)" in started[0] and "python3×3" in started[0]
    box.write("procs_count", "100")
    for _ in range(4):
        box.tick()
    assert len(box.sent()) == 1
    box.tick()
    ended = box.sent()[1]
    assert ended.startswith("✅ минуло: процесів знову норма (100) — тривало")


def test_short_resource_blip_never_alerts(box):
    for count in ("500", "100", "500", "100"):
        box.write("procs_count", count)
        box.tick()
    assert box.sent() == []


def test_fatal_program_reports_masked_error_and_downtime(box):
    box.write("supervisor_status", "smc:smc-fxcm RUNNING pid 1, uptime 1:00:00\n")
    box.tick()  # нова програма спершу засівається
    box.write("supervisor_status", "smc:smc-fxcm FATAL Exited too quickly\n")
    box.write("stderr_tail", "start\n\nlogin failed password=hunter2\nERROR boom token: abc123\n")
    box.tick()
    fatal = box.sent()[0]
    assert "smc-fxcm впав: FATAL" in fatal and "ціни всіх символів" in fatal
    assert "password=***" in fatal and "token: ***" in fatal and "ERROR boom" in fatal
    assert "hunter2" not in fatal and "abc123" not in fatal
    box.write("supervisor_status", "smc:smc-fxcm RUNNING pid 2, uptime 0:00:05\n")
    box.tick()
    recovered = box.sent()[1]
    assert "smc-fxcm знову RUNNING" in recovered and "простій <1 хв" in recovered


def test_morning_report_has_sections_and_resets_daily_counters(box):
    state = box.root / "state"
    (state / "report.date").write_text("2000-01-01\n", encoding="utf-8")
    (state / "events.day").write_text("%d\t🔴 smc-fxcm впав: FATAL\n" % int(time.time()), encoding="utf-8")
    (state / "ssh_own.tally").write_text("3\n", encoding="utf-8")
    (state / "ban.tally").write_text("7\n", encoding="utf-8")
    box.tick()
    (report,) = box.sent()
    assert report.startswith("☀️ Ранковий звіт — ")
    for fragment in (
        "Сервер: 3 weeks; load 0.10, 0.20, 0.30; памʼять вільно 4.8 з 7.6 ГБ; диск 38%",
        "Працюють: smc-ws",
        "Нічний settle: 28.09 23:10 — ок",
        "Арчі: бекап 0 год тому",
        "SSH за добу: 3 — усі твої",
        "Банів fail2ban: 7",
        "Події за добу: 1",
        "🔴 smc-fxcm впав: FATAL",
        "Відвідувачі за добу: тест",
    ):
        assert fragment in report, fragment
    today = dt.datetime.now(tz=__import__("zoneinfo").ZoneInfo("Europe/Prague")).strftime("%Y-%m-%d")
    assert box.state("report.date") == today
    assert (box.state("events.day"), box.state("ssh_own.tally"), box.state("ban.tally")) == ("", "0", "0")
    box.tick()
    assert len(box.sent()) == 1  # один звіт на добу


def test_morning_report_retried_until_delivered(box):
    state = box.root / "state"
    (state / "report.date").write_text("2000-01-01\n", encoding="utf-8")
    (state / "ssh_own.tally").write_text("3\n", encoding="utf-8")
    box.write("alert.rc", "3")
    box.tick()
    assert box.state("report.date") == "2000-01-01" and box.state("ssh_own.tally") == "3"
    box.write("alert.rc", "0")
    box.tick()
    assert box.state("report.date") != "2000-01-01" and box.state("ssh_own.tally") == "0"


def test_owner_commands_answered_and_offset_advanced_only_after_replies(box):
    assert box.state("tg.offset") == "5"  # перший тік лише засіяв offset
    box.write("commands_out", "NEXT 9\nCMD /visitors 30\nCMD /help\n")
    box.tick()
    visitors_reply, help_reply = box.sent()
    assert visitors_reply == "Відвідувачі за добу: тест" and "--hours 720" in (box.root / "runuser_args").read_text()
    assert help_reply.startswith("Команди:") and "/status" in help_reply
    assert box.state("tg.offset") == "9"
    assert (box.root / "commands_args").read_text().split() == ["seed", "5"]


def test_owner_command_retried_when_reply_fails(box):
    box.write("commands_out", "NEXT 9\nCMD /status\n")
    box.write("alert.rc", "3")
    box.tick()
    assert box.state("tg.offset") == "5"  # відповідь не доставлено — команду буде виконано знову
    box.write("alert.rc", "0")
    box.tick()
    assert box.state("tg.offset") == "9"
    assert box.sent()[-1].startswith("📟 Стан зараз — ") and "Працюють: smc-ws" in box.sent()[-1]


def _sender_env(tmp_path, curl_body):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text("#!/bin/bash\n" + curl_body, encoding="utf-8")
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    env_file = tmp_path / "env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=1:x\nALERT_CHAT_ID=42\nHOSTNAME_LABEL=vps\n", encoding="utf-8")
    return dict(os.environ, BOX=str(tmp_path), PATH="%s:%s" % (bin_dir, os.environ.get("PATH", "")),
                ENV_FILE=str(env_file), LOG=str(tmp_path / "alert.log"))


COMMANDS_SCRIPT = os.path.join(REPO, "ops", "vps", "aione-commands.sh")
UPDATES = {"ok": True, "result": [
    {"update_id": 10, "message": {"chat": {"id": 42}, "text": "/visitors 30 please"}},
    {"update_id": 11, "message": {"chat": {"id": 99}, "text": "/visitors"}},
    {"update_id": 12, "message": {"chat": {"id": 42}, "text": "/Status@monitor_smsbot"}},
    {"update_id": 13, "message": {"chat": {"id": 42}, "text": "просто текст"}},
]}
CURL_UPDATES = (  # як справжній curl: код відповіді в stdout лише з -w
    'out=""; prev=""; code=0\n'
    'for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; [ "$a" = "-w" ] && code=1; prev="$a"; echo "$a" >> "$BOX/curl_args"; done\n'
    '[ -n "$out" ] && cat "$BOX/updates.json" > "$out"; [ "$code" = 1 ] && printf 200; exit 0\n'
)


def test_commands_script_returns_owner_commands_only(tmp_path):
    env = _sender_env(tmp_path, CURL_UPDATES)
    (tmp_path / "updates.json").write_text(json.dumps(UPDATES), encoding="utf-8")
    out = subprocess.run(["bash", COMMANDS_SCRIPT, "10"], env=env, check=True, timeout=30,
                         capture_output=True, text=True).stdout
    assert out.splitlines() == ["NEXT 14", "CMD /visitors 30", "CMD /status"]


def test_commands_script_seed_skips_backlog_and_registers_menu(tmp_path):
    env = _sender_env(tmp_path, CURL_UPDATES)
    (tmp_path / "updates.json").write_text(json.dumps(UPDATES), encoding="utf-8")
    out = subprocess.run(["bash", COMMANDS_SCRIPT, "seed"], env=env, check=True, timeout=30,
                         capture_output=True, text=True).stdout
    assert out.splitlines() == ["NEXT 14"]  # старі команди не виконуються
    curl_args = (tmp_path / "curl_args").read_text(encoding="utf-8")
    assert "offset=-1" in curl_args and "/setMyCommands" in curl_args and "1:x" not in out


def test_alert_sender_truncates_long_text_by_characters(tmp_path):
    env = _sender_env(tmp_path, (
        'out=""; prev=""\n'
        'for a in "$@"; do\n'
        '  [ "$prev" = "-o" ] && out="$a"\n'
        '  case "$a" in text=*) printf "%s" "${a#text=}" > "$BOX/text.txt";; esac\n'
        '  prev="$a"\n'
        "done\n"
        'echo \'{"ok":true}\' > "$out"; printf 200\n'
    ))
    subprocess.run(["bash", ALERT_SENDER, "ї" * 5000], env=env, check=True, timeout=30)
    text = (tmp_path / "text.txt").read_text(encoding="utf-8")
    assert len(text) == 4000 and text.startswith("[vps] ї") and text.endswith("…")
