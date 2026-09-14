"""Сигнали зупинки first_tick_m1: перший перериває роботу винятком, наступні не обривають фіналізацію (ADR-0096 §3.3 B).

Навіщо. apply/rollback/fetch пишуть стан (маніфест, staging) і тримають дитину з сесією FXCM. SIGTERM від supervisor
чи SIGHUP закритої SSH-сесії без обробника вбивав процес посеред запису; другий Ctrl+C під час фіналізації обірвав би
саму фіналізацію. Обробник викликається напряму — `os.kill(getpid, SIGTERM)` на Windows = TerminateProcess. Python 3.7.
"""
from __future__ import annotations

import signal

import pytest

from tools.repair.first_tick_m1.common import StopSignal, StopSignals


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


def test_stop_signal_is_not_swallowed_by_except_exception():
    """Гілки `except Exception` посеред роботи не мають права проковтнути зупинку."""
    with pytest.raises(StopSignal):
        try:
            raise StopSignal(signal.SIGTERM)
        except Exception:  # noqa: BLE001 — саме це і перевіряється
            pytest.fail("StopSignal проковтнуто except Exception")
