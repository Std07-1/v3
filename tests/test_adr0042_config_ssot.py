"""
tests/test_adr0042_config_ssot.py — ADR-0042 P3: fvg_grace_bars config SSOT.

Перевіряє що fvg_grace_bars є в config.json і в SmcConfig.

pytest tests/test_adr0042_config_ssot.py -v
"""

from __future__ import annotations

import json
import pathlib

import pytest


def test_fvg_grace_bars_in_config_json():
    """K5: fvg_grace_bars present in config.json smc section."""
    cfg = json.loads(pathlib.Path("config.json").read_text(encoding="utf-8"))
    smc = cfg.get("smc", {})
    assert "fvg_grace_bars" in smc, "config.json smc must have fvg_grace_bars (ADR-0042)"
    assert isinstance(smc["fvg_grace_bars"], int), "fvg_grace_bars must be int"
    assert smc["fvg_grace_bars"] >= 1, "fvg_grace_bars must be >= 1"


def test_fvg_grace_bars_in_smc_config_class():
    """S5: SmcConfig.fvg_grace_bars parsed from config.json."""
    from core.smc.config import SmcConfig

    cfg = json.loads(pathlib.Path("config.json").read_text(encoding="utf-8"))
    smc_cfg = SmcConfig.from_dict(cfg.get("smc", {}))
    assert smc_cfg.fvg_grace_bars == cfg["smc"]["fvg_grace_bars"]


def test_fvg_grace_bars_default():
    """SmcConfig default fvg_grace_bars = 3."""
    from core.smc.config import SmcConfig

    cfg = SmcConfig()
    assert cfg.fvg_grace_bars == 3
