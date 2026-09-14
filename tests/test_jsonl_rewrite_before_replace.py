"""`rewrite_atomic(before_replace=...)`: хук бачить готовий бекап і ще старий файл (ADR-0096 §3.3 B).

Навіщо. Інструмент, що веде маніфест (first_tick_m1 apply/rollback), мусить записати шлях бекапу ДО os.replace —
інакше процес, убитий одразу після підміни, лишає переписаний part-файл без відомого бекапу.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.repair.jsonl_rewrite import rewrite_atomic


def test_hook_runs_with_backup_ready_and_path_still_old(tmp_path):
    path = tmp_path / "part-20260727.jsonl"
    path.write_bytes(b'{"a":1}\n')
    seen = []

    def hook(backup):
        seen.append((Path(backup).read_bytes(), path.read_bytes()))

    backup = rewrite_atomic(str(path), ['{"a":2}'], before_replace=hook)
    assert seen == [(b'{"a":1}\n', b'{"a":1}\n')]
    assert path.read_bytes() == b'{"a":2}\n' and Path(backup).read_bytes() == b'{"a":1}\n'


def test_hook_failure_cancels_replace(tmp_path):
    path = tmp_path / "part-20260727.jsonl"
    path.write_bytes(b'{"a":1}\n')

    def hook(backup):
        raise OSError("No space left on device")

    with pytest.raises(OSError):
        rewrite_atomic(str(path), ['{"a":2}'], before_replace=hook)
    assert path.read_bytes() == b'{"a":1}\n'
