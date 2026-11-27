"""Small GUI helpers."""

from __future__ import annotations

import os
import time

from PyQt6.QtGui import QIcon

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      "assets")


def app_icon() -> QIcon:
    path = os.path.join(ASSETS, "bittwin.svg")
    if os.path.exists(path):
        return QIcon(path)
    return QIcon.fromTheme("bittwin")


def icon(*names: str) -> QIcon:
    for n in names:
        ic = QIcon.fromTheme(n)
        if not ic.isNull():
            return ic
    return QIcon()


def human_size(n: float) -> str:
    if n is None:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def human_time(ts: float) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def human_duration(sec: float) -> str:
    sec = int(max(sec, 0))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m {sec % 60:02d}s"
    return f"{sec // 3600}h {(sec % 3600) // 60:02d}m"
