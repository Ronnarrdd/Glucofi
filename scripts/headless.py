"""Relance un module Python dans scripts/headless.sh : compositeur Wayland sans écran, rendu stable."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
HEADLESS_SH = ROOT / "scripts" / "headless.sh"
ENV = "GLUCOFI_HEADLESS_SIZE"


def in_headless() -> bool:
    return ENV in os.environ


def reexec(module: str, argv: list[str], size: str = "1280x1200") -> NoReturn:
    """Remplace le processus courant par `python3 -m module argv` sous mutter --headless."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT), env.get("PYTHONPATH"))))
    os.execve(str(HEADLESS_SH), [str(HEADLESS_SH), "--size", size, sys.executable, "-m", module, *argv], env)
