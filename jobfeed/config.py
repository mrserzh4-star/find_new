"""Конфіг — TOML, читається stdlib `tomllib` (Python 3.11+). Зайвих залежностей нема.

Правити фільтр можна не торкаючись коду: `config/filter.toml`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

DEFAULT_PATH = Path("config/filter.toml")


def load(path: str | Path = DEFAULT_PATH) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"конфіг не знайдено: {path}")
    with path.open("rb") as fh:
        return tomllib.load(fh)
