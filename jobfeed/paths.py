"""Шляхи з конфіга.

Розкриваємо `%LOCALAPPDATA%`, `$HOME`, `~` — щоб конфіг лишався
host-agnostic: той самий рядок працює і на Windows, і на Linux-раннері.
Відносні шляхи рахуються від кореня проєкту, а не від cwd — планувальник
запускає скрипт з довільної теки.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def resolve(raw: str | Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(str(raw)))
    path = Path(expanded)
    return path if path.is_absolute() else (ROOT / path)
