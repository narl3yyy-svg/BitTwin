"""Background threads so the window never freezes."""

from __future__ import annotations

import os
import threading
import traceback

from PyQt6.QtCore import QThread, pyqtSignal

from ..core import session
from ..core.model import Cancelled, Options


class _Base(QThread):
    failed = pyqtSignal(object)   # the exception
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def cancelled(self) -> bool:
        return self._cancel.is_set()


class CompareWorker(_Base):
    done = pyqtSignal(object, object)  # items, info dict

    def __init__(self, left, right, opts: Options, parent=None):
        super().__init__(parent)
        self.left, self.right, self.opts = left, right, opts
        self._hashed = 0

    def _hash_progress(self, rel: str, n: int) -> None:
        self._hashed += n
        if self._hashed % (16 << 20) < n:
            from .util import human_size
            self.status.emit(f"Reading contents bit for bit… {human_size(self._hashed)}  ·  {rel}")

    def run(self):
        lfs = rfs = None
        try:
            self.status.emit("Connecting to left side…")
            lfs = self.left.open()
            self.status.emit("Connecting to right side…")
            rfs = self.right.open()
            items = session.compare(lfs, rfs, self.opts, self.cancelled, self.status.emit,
                                    self._hash_progress)
            info = {"free_left": lfs.free_space(), "free_right": rfs.free_space(),
                    "left": lfs.display, "right": rfs.display,
                    "rejected": getattr(lfs, "rejected", []) + getattr(rfs, "rejected", [])}
            self.done.emit(items, info)
        except Cancelled:
            self.failed.emit(Cancelled())
        except Exception as exc:  # noqa: BLE001 - shown to the user
            if os.environ.get("BITTWIN_DEBUG"):
                traceback.print_exc()
            self.failed.emit(exc)
        finally:
            for fs in (lfs, rfs):
                if fs is not None:
                    fs.close()


class SyncWorker(_Base):
    progress = pyqtSignal(object)  # dict from Executor
    done = pyqtSignal(object)      # Summary

    def __init__(self, left, right, opts: Options, items, parent=None):
        super().__init__(parent)
        self.left, self.right, self.opts, self.items = left, right, opts, items

    def run(self):
        lfs = rfs = None
        try:
            self.status.emit("Connecting…")
            lfs = self.left.open()
            rfs = self.right.open()
            summary = session.synchronize(lfs, rfs, self.opts, self.items, self.cancelled,
                                          self.progress.emit)
            self.done.emit(summary)
        except Exception as exc:  # noqa: BLE001
            if os.environ.get("BITTWIN_DEBUG"):
                traceback.print_exc()
            self.failed.emit(exc)
        finally:
            for fs in (lfs, rfs):
                if fs is not None:
                    fs.close()


class ConnectWorker(_Base):
    """Opens an SFTP connection (used by the connection dialog)."""
    done = pyqtSignal(object)  # SFTPFS

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def run(self):
        try:
            from ..core.fs_sftp import SFTPFS
            fs = SFTPFS(self.cfg)
            fs.check_root()
            self.done.emit(fs)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(exc)
