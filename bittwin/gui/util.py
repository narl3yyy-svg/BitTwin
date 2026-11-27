"""Small GUI helpers."""

from __future__ import annotations

import html
import os
import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QLabel, QMessageBox

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


def esc(text) -> str:
    """Escape text (file names, server messages) before it goes into rich text."""
    return html.escape(str(text), quote=True)


def plain_tip(text) -> str:
    """Tooltip that always shows text literally, never as HTML."""
    return "<p style='white-space:pre'>" + esc(text) + "</p>"


def plain_label(text: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setTextFormat(Qt.TextFormat.PlainText)
    return lab


def message(parent, kind: str, title: str, text: str) -> None:
    """Message box whose text is shown literally (it may contain file names)."""
    icons = {"warning": QMessageBox.Icon.Warning, "critical": QMessageBox.Icon.Critical,
             "info": QMessageBox.Icon.Information}
    box = QMessageBox(icons.get(kind, QMessageBox.Icon.Information), title, str(text),
                      QMessageBox.StandardButton.Ok, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.exec()


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
