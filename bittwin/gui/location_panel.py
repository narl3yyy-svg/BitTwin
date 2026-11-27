"""One side of the sync: shows the selected folder, drive or SFTP server."""

from __future__ import annotations

import os

from PyQt6.QtCore import QStorageInfo, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
                             QSizePolicy, QVBoxLayout)

from ..core.location import Location
from .util import human_size, icon, plain_label, plain_tip

HIDDEN_MOUNTS = ("/boot", "/snap", "/var/snap", "/run/user", "/run/credentials", "/efi")


def list_drives() -> list[tuple[str, str, int, int]]:
    """(label, path, free, total) for user-relevant mounted volumes."""
    out = []
    seen = set()
    home = os.path.expanduser("~")
    out.append(("Home folder", home, *(_space(home))))
    seen.add(home)
    for v in QStorageInfo.mountedVolumes():
        if not (v.isValid() and v.isReady()):
            continue
        root = v.rootPath()
        if root in seen or any(root == m or root.startswith(m + "/") for m in HIDDEN_MOUNTS):
            continue
        fstype = bytes(v.fileSystemType()).decode(errors="ignore")
        if fstype in ("tmpfs", "squashfs", "overlay", "devtmpfs", "efivarfs", "ramfs"):
            continue
        if root != "/" and not (root.startswith("/media") or root.startswith("/run/media")
                                or root.startswith("/mnt") or root.startswith("/home")
                                or root.startswith("/srv") or root.startswith("/data")):
            continue
        seen.add(root)
        name = v.displayName() if root != "/" else "System (/)"
        if not name or name == root:
            name = os.path.basename(root.rstrip("/")) or root
        out.append((name, root, v.bytesAvailable(), v.bytesTotal()))
    return out


def _space(path: str) -> tuple[int, int]:
    s = QStorageInfo(path)
    return (s.bytesAvailable(), s.bytesTotal()) if s.isValid() else (0, 0)


class LocationPanel(QFrame):
    changed = pyqtSignal()
    sftp_requested = pyqtSignal()

    def __init__(self, side: str, parent=None):
        super().__init__(parent)
        self.side = side
        self.location = Location()
        self.setObjectName("Card")
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(6)

        top = QHBoxLayout()
        self.side_label = QLabel(side.upper())
        self.side_label.setObjectName("CardSide")
        self.kind_badge = QLabel("")
        self.kind_badge.setObjectName("Badge")
        self.kind_badge.hide()
        top.addWidget(self.side_label)
        top.addWidget(self.kind_badge)
        top.addStretch()
        lay.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(34, 34)
        self.path_label = plain_label()
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_label.setMinimumWidth(80)
        self.path_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.icon_label)
        row.addWidget(self.path_label, 1)
        lay.addLayout(row)

        self.meta = plain_label()
        self.meta.setObjectName("CardMeta")
        lay.addWidget(self.meta)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.folder_btn = QPushButton(icon("folder-open", "document-open-folder"), "Folder…")
        self.folder_btn.setToolTip("Choose a local folder")
        self.folder_btn.clicked.connect(self.pick_folder)
        self.drive_btn = QPushButton(icon("drive-harddisk", "drive-removable-media"), "Drives")
        self.drive_btn.setToolTip("Pick a mounted drive")
        self.drive_menu = QMenu(self)
        self.drive_menu.aboutToShow.connect(self._fill_drives)
        self.drive_btn.setMenu(self.drive_menu)
        self.sftp_btn = QPushButton(icon("network-server", "folder-remote"), "SFTP…")
        self.sftp_btn.setToolTip("Connect to a remote server / cloud drive over SFTP")
        self.sftp_btn.clicked.connect(self.sftp_requested.emit)
        for b in (self.folder_btn, self.drive_btn, self.sftp_btn):
            btns.addWidget(b)
        btns.addStretch()
        lay.addLayout(btns)
        self._render()

    # --------------------------------------------------------------- state
    def set_location(self, loc: Location) -> None:
        self.location = loc
        self._render()
        self.changed.emit()

    def set_local(self, path: str) -> None:
        self.set_location(Location("local", os.path.abspath(path)))

    def _render(self) -> None:
        loc = self.location
        if not loc.is_set:
            self.path_label.setObjectName("CardPathEmpty")
            self._full = ""
            self.path_label.setText("No folder selected")
            self.icon_label.setPixmap(icon("folder").pixmap(34, 34))
            self.meta.setText("Choose a folder, a drive, or an SFTP server")
            self.kind_badge.hide()
        elif loc.kind == "sftp":
            cfg = loc.sftp
            self.path_label.setObjectName("CardPath")
            self._full = cfg.label
            self.icon_label.setPixmap(icon("network-server", "folder-remote").pixmap(34, 34))
            how = "key " + os.path.basename(cfg.key_path) if cfg.auth == "key" else "password"
            self.meta.setText(f"{cfg.username}@{cfg.host}:{cfg.port}  ·  signed in with {how}")
            self.kind_badge.setText("SFTP")
            self.kind_badge.show()
        else:
            self.path_label.setObjectName("CardPath")
            self._full = loc.path
            ok = os.path.isdir(loc.path)
            is_mount = os.path.ismount(loc.path)
            self.icon_label.setPixmap(
                icon("drive-removable-media" if loc.path.startswith(("/media", "/run/media"))
                     else ("drive-harddisk" if is_mount else "folder")).pixmap(34, 34))
            if ok:
                free, total = _space(loc.path)
                self.meta.setText(f"{human_size(free)} free of {human_size(total)}" if total else "")
            else:
                self.meta.setText("⚠  Folder not found - is the drive connected?")
            self.kind_badge.setText("DRIVE" if loc.path.startswith(("/media", "/run/media", "/mnt"))
                                    else "LOCAL")
            self.kind_badge.show()
        pm = self.icon_label.pixmap()
        self.icon_label.setVisible(pm is not None and not pm.isNull())
        self.path_label.setToolTip(plain_tip(self._full) if self._full else "")
        self.path_label.style().unpolish(self.path_label)
        self.path_label.style().polish(self.path_label)
        self._elide()

    def _elide(self) -> None:
        if not self._full:
            return
        fm = QFontMetrics(self.path_label.font())
        w = max(self.path_label.width(), 120)
        self.path_label.setText(fm.elidedText(self._full, Qt.TextElideMode.ElideMiddle, w))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def set_busy(self, busy: bool) -> None:
        for b in (self.folder_btn, self.drive_btn, self.sftp_btn):
            b.setEnabled(not busy)
        self.setAcceptDrops(not busy)

    # ------------------------------------------------------------- pickers
    def pick_folder(self) -> None:
        start = self.location.path if self.location.kind == "local" and self.location.path \
            else os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, f"Choose {self.side} folder", start)
        if path:
            self.set_local(path)

    def _fill_drives(self) -> None:
        self.drive_menu.clear()
        for name, path, free, total in list_drives():
            ic = icon("user-home") if path == os.path.expanduser("~") else (
                icon("drive-removable-media") if path.startswith(("/media", "/run/media"))
                else icon("drive-harddisk"))
            label = f"{name}    {human_size(free)} free" if total else name
            act = self.drive_menu.addAction(ic, label)
            act.setToolTip(plain_tip(path))
            act.triggered.connect(lambda _=False, p=path: self.set_local(p))
        if not self.drive_menu.actions():
            self.drive_menu.addAction("No drives found").setEnabled(False)

    # --------------------------------------------------------- drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            urls = [u for u in e.mimeData().urls() if u.isLocalFile() and os.path.isdir(u.toLocalFile())]
            if urls:
                e.acceptProposedAction()
                self.setProperty("active", True)
                self.style().unpolish(self)
                self.style().polish(self)

    def dragLeaveEvent(self, e):
        self.setProperty("active", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, e):
        self.dragLeaveEvent(e)
        for u in e.mimeData().urls():
            if u.isLocalFile() and os.path.isdir(u.toLocalFile()):
                self.set_local(u.toLocalFile())
                break
