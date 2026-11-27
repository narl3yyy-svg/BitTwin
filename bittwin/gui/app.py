"""Application entry point."""

from __future__ import annotations

import os
import sys


def main() -> int:
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication

    from .. import __version__
    from . import theme
    from .main_window import MainWindow
    from .util import app_icon

    from ..core.fs_sftp import config_dir
    from ..core.log import state_dir
    config_dir()  # ~/.config/BitTwin (settings, saved connections, known_hosts) -> 0700
    state_dir()   # ~/.local/state/BitTwin (logs, sync history) -> 0700

    app = QApplication(sys.argv)
    app.setApplicationName("BitTwin")
    app.setOrganizationName("BitTwin")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("bittwin")  # matches bittwin.desktop (Wayland taskbar icon)
    app.setWindowIcon(app_icon())
    theme.apply(app, QSettings("BitTwin", "BitTwin").value("theme", "system"))

    win = MainWindow()
    # optional: bittwin LEFT RIGHT  pre-fills the two sides
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if len(args) >= 1 and os.path.isdir(args[0]):
        win.left_panel.set_local(args[0])
    if len(args) >= 2 and os.path.isdir(args[1]):
        win.right_panel.set_local(args[1])
    win.show()
    return app.exec()
