"""
Scoop — Configuration loader

Reads config.yaml once at import time and exposes it as `cfg`.

Usage:
    from config import cfg
    print(cfg["digest"]["signals_per_email"])  # 10
"""

from __future__ import annotations

from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).parent / "config.yaml"

with open(_CONFIG_PATH) as f:
    cfg: dict = yaml.safe_load(f)
