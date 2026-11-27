"""Plain-text run logs: every copied file with its verified SHA-256."""

from __future__ import annotations

import os
import time


def private_dir(path: str) -> str:
    """Create a folder only the current user can open (logs list file names)."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def private_open(path: str, mode: str = "a"):
    """Open a text file that only the current user can read."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if mode == "a" else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    return os.fdopen(fd, mode, encoding="utf-8")


def state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return private_dir(os.path.join(base, "BitTwin"))


def logs_dir() -> str:
    return private_dir(os.path.join(state_dir(), "logs"))


class RunLog:
    def __init__(self, title: str):
        name = time.strftime("%Y-%m-%d_%H%M%S") + ".log"
        self.path = os.path.join(logs_dir(), name)
        self._f = private_open(self.path, "a")
        self.line("BitTwin", title)

    def line(self, *parts) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self._f.write(stamp + "  " + "  ".join(str(p) for p in parts) + "\n")
        self._f.flush()

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass
