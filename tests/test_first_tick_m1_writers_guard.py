"""Перевірка записувачів SSOT через /proc — доказ зупинки, а не прапорець (ADR-0096 §3.3 B, B6).

Навіщо. `os.replace` відчіпляє відкритий FD живого записувача, і його дописи тихо зникають у бекапі. Прецедент:
`supervisorctl stop smc-fxcm` без префікса `smc:` мовчки нічого не зупиняє. Скан мусить бачити записувача за
argv і за FD на запис, не плутати з ним grep/tail та власний процес apply з предками, і відмовляти, коли /proc
не дає довести зупинку. Дерево /proc — фейкове в tmp_path.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools.repair.first_tick_m1 import writers_guard as guard

REPO = Path(__file__).resolve().parents[1]
SELF_PID = 5000


def _proc(root: Path, pid: int, argv, ppid=1, fds=None, flags=None):
    directory = root / str(pid)
    directory.mkdir(parents=True)
    (directory / "cmdline").write_bytes(b"".join(arg.encode("utf-8") + b"\0" for arg in argv))
    (directory / "status").write_text("Name:\tx\nPPid:\t%d\n" % ppid, encoding="utf-8")
    (directory / "fd").mkdir()
    (directory / "fdinfo").mkdir()
    for fd, target in (fds or {}).items():
        (directory / "fd" / fd).write_text(target, encoding="utf-8")  # фейковий readlink читає ціль звідси
        (directory / "fdinfo" / fd).write_text("pos:\t0\nflags:\t%s\nmnt_id:\t1\n" % (flags or {}).get(fd, "0100000"),
                                               encoding="utf-8")
    return directory


def _readlink(path):
    return Path(path).read_text(encoding="utf-8")


@pytest.fixture()
def proc(tmp_path):
    root = tmp_path / "proc"
    _proc(root, 1, ["/sbin/init"], ppid=0)
    _proc(root, 900, ["-bash"], ppid=1)
    _proc(root, SELF_PID, ["python", "-m", "tools.repair.first_tick_m1", "apply"], ppid=900)
    return root


def _scan(root, **kw):
    return guard.scan_writers(str(root), readlink=_readlink, self_pid=SELF_PID, **kw)


@pytest.mark.parametrize("argv, match", [
    (["/opt/smc-v3/.venv/bin/python", "-u", "-m", "runtime.ingest.m1_ingestion_worker"],
     "runtime.ingest.m1_ingestion_worker"),
    (["/opt/smc-v3/.venv37/bin/python", "-u", "-m", "runtime.ingest.broker_sidecar"], "runtime.ingest.broker_sidecar"),
    (["python", "-m", "runtime.ingest.tick_preview_worker"], "runtime.ingest.tick_preview_worker"),
    (["python", "-u", "-m", "app.main", "--mode", "m1_poller"], "app.main:m1_poller"),
    (["python", "-m", "app.main"], "app.main:all"),
    (["python", "-m", "app.main", "--mode=replay"], "app.main:replay"),
])
def test_dash_m_writer_detected(proc, argv, match):
    _proc(proc, 1234, argv)
    scan = _scan(proc)
    assert scan.available and not scan.clear
    assert [(w.pid, w.match) for w in scan.writers] == [(1234, match)]


@pytest.mark.parametrize("argv", [
    ["grep", "-rn", "runtime.ingest.broker_sidecar", "/opt/smc-v3/logs"],
    ["tail", "-n", "50", "/opt/smc-v3/logs/runtime.ingest.m1_ingestion_worker.log"],
    ["python", "-u", "-m", "app.main", "--mode", "ws_server"],
    ["grep", "-m", "1", "runtime.ingest.m1_ingestion_worker"],
])
def test_mentions_are_not_writers(proc, argv):
    _proc(proc, 1234, argv)
    assert _scan(proc).clear


def test_script_path_invocation_detected(proc):
    _proc(proc, 1234, ["python3", "tools/rebuild_from_m1.py", "--symbols", "XAU/USD"])
    _proc(proc, 1235, ["/opt/smc-v3/.venv/bin/python", "/opt/smc-v3/tools/repair/repair_m1_gaps.py"])
    assert [w.match for w in _scan(proc).writers] == ["tools.rebuild_from_m1", "tools.repair.repair_m1_gaps"]


def test_own_process_and_ancestors_excluded(tmp_path):
    root = tmp_path / "proc"
    _proc(root, 1, ["/sbin/init"], ppid=0)
    wrapper = ["timeout", "600", "python", "-m", "tools.repair.first_tick_m1", "apply", "--plan-dir", "/p"]
    _proc(root, 4000, wrapper, ppid=1)
    _proc(root, SELF_PID, ["python", "-m", "tools.repair.first_tick_m1", "apply", "--plan-dir", "/p"], ppid=4000)
    assert _scan(root).clear
    _proc(root, 4100, wrapper, ppid=1)  # контроль: такий самий процес, але не предок — інший apply
    assert [(w.pid, w.match) for w in _scan(root).writers] == [(4100, "tools.repair.first_tick_m1")]


@pytest.mark.parametrize("layout, reason", [("no_proc", "no_proc"), ("hidepid", "proc_hidepid"),
                                            ("cmdline_denied", "cmdline_unreadable")])
def test_check_unavailable(tmp_path, monkeypatch, layout, reason):
    root = tmp_path / "proc"
    if layout != "no_proc":
        root.mkdir()
        _proc(root, 777, ["python", "-m", "runtime.ingest.m1_ingestion_worker"])
    if layout == "cmdline_denied":
        _proc(root, 1, ["/sbin/init"], ppid=0)
        real_open = open

        def deny(path, *args, **kwargs):
            if str(path).replace("\\", "/").endswith("/777/cmdline"):
                raise PermissionError(path)
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", deny)
    scan = _scan(root)
    assert not scan.available and scan.reason.startswith(reason) and not scan.clear


def test_write_fd_on_target_dir_is_writer_read_fd_is_not(proc, tmp_path):
    target = tmp_path / "data" / "XAU_USD" / "tf_60"
    target.mkdir(parents=True)
    part = str(target / "part-20260727.jsonl")
    _proc(proc, 2000, ["python", "-m", "some.reader"], fds={"3": part}, flags={"3": "0100000"})
    _proc(proc, 2001, ["python", "-m", "some.other"], fds={"4": "socket:[123]", "5": str(tmp_path / "x.log")},
          flags={"5": "0102001"})
    assert _scan(proc, target_dirs=[str(target)]).clear
    _proc(proc, 2002, ["python", "-m", "mystery.writer"], fds={"7": part}, flags={"7": "0100001"})
    scan = _scan(proc, target_dirs=[str(target)])
    assert scan.writers == () and [(w.pid, w.match) for w in scan.fd_writers] == [(2002, "fd:%s" % part)]


def _module_name(path: Path) -> str:
    rel = path.relative_to(REPO).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _writes_ssot(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name in ("JsonlAppender", "rewrite_atomic") for a in node.names):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "build_uds_from_config":
                role = next((k.value for k in node.keywords if k.arg == "role"), None)
                if not (isinstance(role, ast.Constant) and role.value == "reader"):
                    return True
    return False


def test_every_ssot_writer_module_in_repo_is_listed():
    """Гейт: новий записувач SSOT без місця в переліку — apply на проді його не побачив би."""
    listed = set(guard.WRITER_MODULES) | set(guard.NON_TARGET_WRITERS)
    found, unlisted = [], []
    for top in ("runtime", "tools", "app"):
        for path in (REPO / top).rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if "/_archive" in rel:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            if not _writes_ssot(tree):
                continue
            module = _module_name(path)
            found.append(module)
            in_package = any(module == p or module.startswith(p + ".") for p in guard.WRITER_PACKAGES)
            if module not in listed and not in_package:
                unlisted.append(module)
    assert "tools.repair.dedup_jsonl_lastwins" in found and "runtime.ingest.m1_ingestion_worker" in found
    assert unlisted == [], unlisted
    missing = [m for m in sorted(listed) if not (REPO / (m.replace(".", "/") + ".py")).exists()]
    assert missing == [], "у переліку модулі, яких немає в репо: %s" % missing
