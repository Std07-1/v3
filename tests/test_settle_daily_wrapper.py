"""ADR-0103 S3d: обгортка ops/settle_daily.sh — trap стартує записувачів лише тоді, коли їх зупинив прогін.

Оркестратор, убитий timeout-ом (SIGTERM не дає Python виконати finally) чи падінням, лишає маркер у work_dir;
обгортка стартує інжест, потім читачів (порядок проду), і прибирає маркер. Без маркера — нічого не стартує: власник
міг зупинити записувачів навмисно. Лише POSIX (bash, flock, timeout) — прод і CI.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(os.name != "posix" or not shutil.which("flock"), reason="bash + flock — прод і CI")


def _executable(path, body):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


def _wrapper(tmp_path, orchestrator_leaves_marker, rc):
    code, work, bin_dir = tmp_path / "code", tmp_path / "work", tmp_path / "bin"
    for path in (code / ".venv" / "bin", work, bin_dir):
        path.mkdir(parents=True)
    (code / "config.json").write_text(json.dumps({"m1_settle": {"work_dir": str(work)}}), encoding="utf-8")
    calls = tmp_path / "calls.log"
    marker = work / "writers_stopped_by_settle"
    # «python» обгортки: -c — справжній інтерпретатор (читання work_dir), -m — оркестратор, що гине
    _executable(str(code / ".venv" / "bin" / "python"), "#!/bin/bash\n"
                'if [ "$1" = "-c" ]; then exec %s "$@"; fi\n'
                "%s\nexit %d\n" % (sys.executable, ("touch %s" % marker) if orchestrator_leaves_marker else ":", rc))
    for tool in ("supervisorctl", "logger"):
        _executable(str(bin_dir / tool), '#!/bin/bash\necho "%s $*" >> %s\n' % (tool, calls))
    env = dict(os.environ, SMC_V3_DIR=str(code), SETTLE_DAILY_LOG=str(tmp_path / "settle.log"),
               SETTLE_DAILY_LOCK=str(tmp_path / "lock"), SETTLE_TRAP_PRIME_S="0",
               PATH="%s:%s" % (bin_dir, os.environ.get("PATH", "")))
    proc = subprocess.run(["bash", os.path.join(REPO, "ops", "settle_daily.sh"), "--scheduled"], env=env,
                          capture_output=True, text=True, timeout=60)
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return proc.returncode, [ln for ln in lines if ln.startswith("supervisorctl")], lines, marker


def test_trap_restarts_writers_in_prod_order_when_the_run_died_holding_them(tmp_path):
    rc, supervisor, lines, marker = _wrapper(tmp_path, orchestrator_leaves_marker=True, rc=143)
    assert rc == 143
    assert supervisor == ["supervisorctl start smc:smc-fxcm", "supervisorctl start smc:smc-preview smc:smc-ws"]
    assert not marker.exists() and any("SETTLE_DAILY_TRAP" in ln for ln in lines)


def test_no_marker_means_no_start_even_after_a_failure(tmp_path):
    """Відмова до стопу (код 3) — записувачі живі або зупинені власником; обгортка їх не чіпає, лише syslog."""
    rc, supervisor, lines, _marker = _wrapper(tmp_path, orchestrator_leaves_marker=False, rc=3)
    assert rc == 3 and supervisor == [] and any("SETTLE_DAILY_RC=3" in ln for ln in lines)
