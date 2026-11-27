"""Plain-text run logs: every copied file with its verified SHA-256."""

from __future__ import annotations

import os
import time


def state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    path = os.path.join(base, "BitTwin")
    os.makedirs(path, exist_ok=True)
    return path


def logs_dir() -> str:
    path = os.path.join(state_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


class RunLog:
    def __init__(self, title: str):
        name = time.strftime("%Y-%m-%d_%H%M%S") + ".log"
        self.path = os.path.join(logs_dir(), name)
        self._f = open(self.path, "a", encoding="utf-8")
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
