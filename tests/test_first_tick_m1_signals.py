"""Сигнали зупинки first_tick_m1: перший перериває роботу винятком, наступні не обривають фіналізацію (ADR-0096 §3.3 B).

Навіщо. apply/rollback/fetch пишуть стан (маніфест, staging) і тримають дитину з сесією FXCM. SIGTERM від supervisor
чи SIGHUP закритої SSH-сесії без обробника вбивав процес посеред запису; другий Ctrl+C під час фіналізації обірвав би
саму фіналізацію. Обробник викликається напряму — `os.kill(getpid, SIGTERM)` на Windows = TerminateProcess. Python 3.7.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys

import pytest

from tools.repair.first_tick_m1.common import REPO_ROOT, StopSignal, StopSignals


def test_first_signal_raises_stop_signal_with_shell_exit_code():
    before = signal.getsignal(signal.SIGTERM)
    with StopSignals("TEST") as signals:
        handler = signal.getsignal(signal.SIGTERM)
        with pytest.raises(StopSignal) as caught:
            handler(signal.SIGTERM, None)
        assert caught.value.exit_code == 128 + signal.SIGTERM and signals.received == signal.SIGTERM
        handler(signal.SIGTERM, None)  # другий сигнал — лише фіксується: фіналізація не обривається
    assert signal.getsignal(signal.SIGTERM) == before


def test_disarmed_gate_defers_signal_without_raising(caplog):
    with StopSignals("TEST") as signals:
        signals.disarm()
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        assert signals.received == signal.SIGINT
    assert "TEST_SIGNAL_DEFERRED" in caplog.text


def test_inherited_sig_ign_is_kept_and_logged(caplog):
    """Ловить затирання успадкованого SIG_IGN: `nohup` ігнорує SIGHUP, щоб довгий fetch пережив закриту SSH-сесію, а
    безумовний обробник перетворював SIGHUP назад на зупинку. Інші сигнали обробник отримують як і раніше."""
    ignored = signal.SIGTERM  # є і на Windows; SIGHUP — лише POSIX
    before = signal.signal(ignored, signal.SIG_IGN)
    try:
        with StopSignals("TEST") as signals:
            assert signal.getsignal(ignored) == signal.SIG_IGN
            assert getattr(signal.getsignal(signal.SIGINT), "__self__", None) is signals
        assert signal.getsignal(ignored) == signal.SIG_IGN
        assert "TEST_SIGNAL_IGNORED_INHERITED signal=SIGTERM" in caplog.text
    finally:
        signal.signal(ignored, before)


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="SIGHUP — лише POSIX")
def test_nohup_process_survives_real_sighup_under_stop_signals(tmp_path):
    code = ("import os, signal, sys; from tools.repair.first_tick_m1.common import StopSignals\n"
            "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
            "with StopSignals('TEST'):\n"
            "    os.kill(os.getpid(), signal.SIGHUP)\n"
            "sys.exit(7)\n")
    proc = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)), timeout=30)
    assert proc.returncode == 7


def test_stop_signal_is_not_swallowed_by_except_exception():
    """Гілки `except Exception` посеред роботи не мають права проковтнути зупинку."""
    with pytest.raises(StopSignal):
        try:
            raise StopSignal(signal.SIGTERM)
        except Exception:  # noqa: BLE001 — саме це і перевіряється
            pytest.fail("StopSignal проковтнуто except Exception")
