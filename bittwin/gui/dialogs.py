"""Dialogs: SFTP connection, remote folder picker, settings, confirm, results."""

from __future__ import annotations

import os
import posixpath
import stat as stmod

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog,
                             QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                             QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
                             QRadioButton, QSpinBox, QVBoxLayout, QWidget)

from ..core.fs_sftp import HostKeyChanged, SFTPConfig, UnknownHostKey, trust_host_key
from ..core.model import DEFAULT_EXCLUDES, Action, CompareMethod, Options, PlanItem
from . import theme
from .util import human_size, icon
from .workers import ConnectWorker


def ask_trust_host(parent, exc: UnknownHostKey) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Confirm server identity")
    box.setText(f"<b>First connection to {exc.hostname}</b>")
    box.setInformativeText(
        "BitTwin has not seen this server before. Check that the fingerprint below matches "
        "the server's key (on the server run <code>ssh-keygen -lf /etc/ssh/ssh_host_*_key.pub</code>)."
        f"<br><br>Key type: <b>{exc.key_type}</b><br>Fingerprint:<br><code>{exc.fingerprint}</code>"
        "<br><br>Trust this server and remember it?")
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.button(QMessageBox.StandardButton.Yes).setText("Trust and connect")
    box.button(QMessageBox.StandardButton.No).setText("Cancel")
    box.setDefaultButton(QMessageBox.StandardButton.No)
    if box.exec() == QMessageBox.StandardButton.Yes:
        trust_host_key(exc.hostname, exc.key)
        return True
    return False


def _hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"color: {theme.colors()['border']};")
    return f


# ======================================================================= SFTP
class SFTPDialog(QDialog):
    def __init__(self, parent=None, saved: list[dict] | None = None,
                 current: SFTPConfig | None = None):
        super().__init__(parent)
        self.setWindowTitle("Connect to SFTP server")
        self.setMinimumWidth(560)
        self.saved = saved or []
        self.result_cfg: SFTPConfig | None = None
        self._fs = None
        self._worker = None
        self._then = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        title = QLabel("SFTP connection")
        title.setObjectName("SectionTitle")
        sub = QLabel("Sync with a remote server or cloud drive over SSH. "
                     "Your passphrase is kept in memory only and never saved.")
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(sub)

        self.saved_box = QComboBox()
        self.saved_box.addItem("New connection…")
        for s in self.saved:
            port = "" if int(s.get("port", 22)) == 22 else f":{s['port']}"
            self.saved_box.addItem(f"{s.get('username')}@{s.get('host')}{port}  {s.get('remote_path', '')}")
        self.saved_box.currentIndexChanged.connect(self._load_saved)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if self.saved:
            form.addRow("Saved", self.saved_box)

        self.host = QLineEdit()
        self.host.setPlaceholderText("server.example.com or 192.168.1.20")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(22)
        self.port.setFixedWidth(100)
        hp = QHBoxLayout()
        hp.addWidget(self.host, 1)
        hp.addWidget(QLabel("Port"))
        hp.addWidget(self.port)
        form.addRow("Host", hp)

        self.user = QLineEdit()
        self.user.setPlaceholderText("username")
        form.addRow("Username", self.user)

        self.rb_key = QRadioButton("Private key file")
        self.rb_pw = QRadioButton("Password")
        self.rb_key.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.rb_key)
        grp.addButton(self.rb_pw)
        ab = QHBoxLayout()
        ab.setSpacing(22)
        ab.addWidget(self.rb_key)
        ab.addWidget(self.rb_pw)
        ab.addStretch()
        form.addRow("Sign in with", ab)

        self.key = QLineEdit()
        self.key.setPlaceholderText("~/.ssh/id_ed25519")
        kb = QPushButton("Browse…")
        kb.clicked.connect(self._pick_key)
        kl = QHBoxLayout()
        kl.addWidget(self.key, 1)
        kl.addWidget(kb)
        self.key_row = QWidget()
        self.key_row.setLayout(kl)
        kl.setContentsMargins(0, 0, 0, 0)
        form.addRow("Key file", self.key_row)

        self.passphrase = QLineEdit()
        self.passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        self.passphrase.setPlaceholderText("Leave empty if the key has no passphrase")
        form.addRow("Key passphrase", self.passphrase)

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Password", self.password)

        self.remote = QLineEdit("/")
        self.remote.setPlaceholderText("/home/user/backup")
        rb = QPushButton("Browse…")
        rb.clicked.connect(self._browse_remote)
        rl = QHBoxLayout()
        rl.addWidget(self.remote, 1)
        rl.addWidget(rb)
        form.addRow("Remote folder", rl)
        lay.addLayout(form)

        self.remember = QCheckBox("Remember this connection (host, user, key path - never the passphrase)")
        self.remember.setChecked(True)
        lay.addWidget(self.remember)

        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.msg)

        btns = QHBoxLayout()
        self.test_btn = QPushButton("Test connection")
        self.test_btn.clicked.connect(lambda: self._connect(None))
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton("Use this server")
        self.ok_btn.setObjectName("Primary")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(lambda: self._connect(self._accept_ok))
        btns.addWidget(self.test_btn)
        btns.addStretch()
        btns.addWidget(self.cancel_btn)
        btns.addWidget(self.ok_btn)
        lay.addLayout(btns)

        self.rb_key.toggled.connect(self._auth_changed)
        self._auth_changed()
        if current is not None:
            self._fill(current.public_dict())
            self.passphrase.setText(current.passphrase)
            self.password.setText(current.password)
        elif self.saved:
            self.saved_box.setCurrentIndex(1)

    # ---------------------------------------------------------------- form
    def _auth_changed(self):
        key = self.rb_key.isChecked()
        self.key_row.setEnabled(key)
        self.passphrase.setEnabled(key)
        self.password.setEnabled(not key)

    def _fill(self, d: dict):
        self.host.setText(d.get("host", ""))
        self.port.setValue(int(d.get("port", 22)))
        self.user.setText(d.get("username", ""))
        (self.rb_pw if d.get("auth") == "password" else self.rb_key).setChecked(True)
        self.key.setText(d.get("key_path", ""))
        self.remote.setText(d.get("remote_path", "/") or "/")

    def _load_saved(self, idx: int):
        if idx >= 1:
            self._fill(self.saved[idx - 1])
            self.passphrase.clear()
            self.password.clear()
            self._close_fs()

    def _pick_key(self):
        start = os.path.expanduser("~/.ssh")
        path, _ = QFileDialog.getOpenFileName(self, "Choose SSH private key",
                                              start if os.path.isdir(start) else os.path.expanduser("~"),
                                              "All files (*)")
        if path:
            self.key.setText(path)

    def config(self) -> SFTPConfig:
        return SFTPConfig(host=self.host.text().strip(), port=self.port.value(),
                          username=self.user.text().strip(),
                          auth="key" if self.rb_key.isChecked() else "password",
                          key_path=self.key.text().strip(),
                          passphrase=self.passphrase.text(), password=self.password.text(),
                          remote_path=self.remote.text().strip() or "/")

    def _validate(self) -> str:
        c = self.config()
        if not c.host:
            return "Enter the server address."
        if not c.username:
            return "Enter your username."
        if c.auth == "key":
            if not c.key_path:
                return "Choose your SSH private key file."
            if not os.path.isfile(os.path.expanduser(c.key_path)):
                return "That key file does not exist."
            if c.key_path.endswith(".pub"):
                return "That is the public key (.pub). Choose the private key file instead."
        return ""

    def _set_msg(self, text: str, kind: str = "muted"):
        color = theme.colors().get(kind, theme.colors()["muted"])
        self.msg.setStyleSheet(f"color: {color};")
        self.msg.setText(text)

    # ----------------------------------------------------------- connect
    def _busy(self, busy: bool):
        for w in (self.test_btn, self.ok_btn):
            w.setEnabled(not busy)

    def _connect(self, then):
        err = self._validate()
        if err:
            self._set_msg(err, "danger")
            return
        self._close_fs()
        self._then = then
        self._busy(True)
        self._set_msg("Connecting…")
        self._worker = ConnectWorker(self.config(), self)
        self._worker.done.connect(self._connected)
        self._worker.failed.connect(self._connect_failed)
        self._worker.start()

    def _connected(self, fs):
        self._busy(False)
        self._fs = fs
        self.remote.setText(fs.root)
        free = fs.free_space()
        extra = f"  ·  {human_size(free)} free" if free is not None else ""
        self._set_msg(f"✓  Connected to {fs.cfg.host} - folder {fs.root}{extra}", "success")
        if self._then:
            self._then()

    def _connect_failed(self, exc):
        self._busy(False)
        if isinstance(exc, UnknownHostKey):
            if ask_trust_host(self, exc):
                self._connect(self._then)
            else:
                self._set_msg("Connection cancelled - server not trusted.", "warn")
            return
        if isinstance(exc, HostKeyChanged):
            QMessageBox.critical(self, "Server identity changed", str(exc))
        self._set_msg(f"✗  {exc}", "danger")

    def _accept_ok(self):
        self.result_cfg = self.config()
        self.result_cfg.remote_path = self._fs.root if self._fs else self.result_cfg.remote_path
        self._close_fs()
        self.accept()

    def _browse_remote(self):
        if self._fs is None:
            self._connect(self._browse_remote)
            return
        dlg = RemoteFolderDialog(self._fs, self.remote.text() or self._fs.root, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.remote.setText(dlg.path)

    def _close_fs(self):
        if self._fs is not None:
            self._fs.close()
            self._fs = None

    def done(self, r):
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(16000)
        self._close_fs()
        super().done(r)


class RemoteFolderDialog(QDialog):
    def __init__(self, fs, start: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose remote folder")
        self.resize(520, 480)
        self.fs = fs
        self.path = start
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        top = QHBoxLayout()
        up = QPushButton(icon("go-up"), "Up")
        up.clicked.connect(self._up)
        self.where = QLineEdit()
        self.where.returnPressed.connect(lambda: self._go(self.where.text()))
        top.addWidget(up)
        top.addWidget(self.where, 1)
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda it: self._go(it.data(Qt.ItemDataRole.UserRole)))
        lay.addWidget(self.list, 1)
        new = QPushButton(icon("folder-new"), "New folder…")
        new.clicked.connect(self._mkdir)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Select this folder")
        bb.button(QDialogButtonBox.StandardButton.Ok).setObjectName("Primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(new)
        bottom.addStretch()
        bottom.addWidget(bb)
        lay.addLayout(bottom)
        self._go(start)

    def _go(self, path: str):
        try:
            path = self.fs.sftp.normalize(path)
            entries = self.fs.sftp.listdir_attr(path)
        except IOError as exc:
            QMessageBox.warning(self, "Cannot open folder", f"{path}\n\n{exc}")
            return
        self.path = path
        self.where.setText(path)
        self.list.clear()
        folder = icon("folder")
        for a in sorted(entries, key=lambda a: a.filename.lower()):
            if stmod.S_ISDIR(a.st_mode or 0) and not a.filename.startswith("."):
                it = QListWidgetItem(folder, a.filename)
                it.setData(Qt.ItemDataRole.UserRole, posixpath.join(path, a.filename))
                self.list.addItem(it)

    def _up(self):
        self._go(posixpath.dirname(self.path.rstrip("/")) or "/")

    def _mkdir(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if ok and name.strip():
            try:
                self.fs.sftp.mkdir(posixpath.join(self.path, name.strip()))
            except IOError as exc:
                QMessageBox.warning(self, "Could not create folder", str(exc))
            self._go(self.path)


# =================================================================== settings
class SettingsDialog(QDialog):
    def __init__(self, opts: Options, theme_choice: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("BitTwin settings")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(14)

        from PyQt6.QtWidgets import QGroupBox
        g1 = QGroupBox("Comparison")
        f1 = QFormLayout(g1)
        self.compare = QComboBox()
        self.compare.addItem("Fast - file size and modification time", CompareMethod.TIME_SIZE.value)
        self.compare.addItem("Bit for bit - read and hash every file (slow, exact)",
                             CompareMethod.CONTENT.value)
        self.compare.setCurrentIndex(0 if opts.compare == CompareMethod.TIME_SIZE else 1)
        f1.addRow("Compare by", self.compare)
        self.tol = QDoubleSpinBox()
        self.tol.setRange(0, 3600)
        self.tol.setDecimals(1)
        self.tol.setSuffix(" s")
        self.tol.setValue(opts.time_tolerance)
        self.tol.setToolTip("FAT/exFAT drives and SFTP store coarse timestamps; 2 s is recommended.")
        f1.addRow("Time tolerance", self.tol)
        lay.addWidget(g1)

        g2 = QGroupBox("Safety")
        v2 = QVBoxLayout(g2)
        self.archive = QCheckBox("Keep replaced and removed files in a .bittwin-archive folder")
        self.archive.setChecked(opts.archive)
        self.reverify = QCheckBox("Re-read the source after every copy (detects failing drives / cables)")
        self.reverify.setChecked(opts.reverify_source)
        self.newer = QCheckBox("Two-way: resolve conflicts in favour of the newer file")
        self.newer.setChecked(opts.conflict_newer_wins)
        v2.addWidget(self.archive)
        v2.addWidget(self.reverify)
        v2.addWidget(self.newer)
        rr = QHBoxLayout()
        rr.addWidget(QLabel("Retries after a failed verification"))
        self.retries = QSpinBox()
        self.retries.setRange(0, 10)
        self.retries.setValue(opts.retries)
        rr.addWidget(self.retries)
        rr.addStretch()
        v2.addLayout(rr)
        note = QLabel("Every copy is always written to a temporary file, flushed to disk, read back "
                      "and checked with SHA-256 before it replaces anything.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        v2.addWidget(note)
        lay.addWidget(g2)

        g3 = QGroupBox("Exclude (one pattern per line, e.g.  *.tmp   node_modules   cache/*)")
        v3 = QVBoxLayout(g3)
        self.excludes = QPlainTextEdit("\n".join(opts.excludes))
        self.excludes.setFixedHeight(110)
        reset = QPushButton("Restore defaults")
        reset.clicked.connect(lambda: self.excludes.setPlainText("\n".join(DEFAULT_EXCLUDES)))
        v3.addWidget(self.excludes)
        v3.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(g3)

        g4 = QGroupBox("Appearance")
        f4 = QFormLayout(g4)
        self.theme = QComboBox()
        for label, val in (("Follow system", "system"), ("Dark", "dark"), ("Light", "light")):
            self.theme.addItem(label, val)
        self.theme.setCurrentIndex(max(0, self.theme.findData(theme_choice)))
        f4.addRow("Theme", self.theme)
        lay.addWidget(g4)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Save).setObjectName("Primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def apply_to(self, opts: Options) -> str:
        opts.compare = CompareMethod(self.compare.currentData())
        opts.time_tolerance = self.tol.value()
        opts.archive = self.archive.isChecked()
        opts.reverify_source = self.reverify.isChecked()
        opts.conflict_newer_wins = self.newer.isChecked()
        opts.retries = self.retries.value()
        pats = [p.strip() for p in self.excludes.toPlainText().splitlines() if p.strip()]
        for must in (".bittwin-archive", "*.bittwin-tmp"):
            if must not in pats:
                pats.insert(0, must)
        opts.excludes = pats
        return self.theme.currentData()


# ==================================================================== confirm
def plan_stats(items: list[PlanItem]) -> dict:
    s = {"ltr": 0, "ltr_bytes": 0, "rtl": 0, "rtl_bytes": 0, "mk_l": 0, "mk_r": 0,
         "del_l": 0, "del_r": 0, "skip": 0, "equal": 0}
    for it in items:
        a = it.action
        if a == Action.COPY_LTR:
            s["ltr"] += 1
            s["ltr_bytes"] += it.copy_bytes
        elif a == Action.COPY_RTL:
            s["rtl"] += 1
            s["rtl_bytes"] += it.copy_bytes
        elif a == Action.MKDIR_RIGHT:
            s["mk_r"] += 1
        elif a == Action.MKDIR_LEFT:
            s["mk_l"] += 1
        elif a == Action.DELETE_LEFT:
            s["del_l"] += 1
        elif a == Action.DELETE_RIGHT:
            s["del_r"] += 1
        elif a == Action.SKIP:
            s["skip"] += 1
        else:
            s["equal"] += 1
    return s


class ConfirmDialog(QDialog):
    def __init__(self, items, opts: Options, info: dict, left_label: str, right_label: str,
                 mode_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start synchronization?")
        self.setMinimumWidth(560)
        c = theme.colors()
        st = plan_stats(items)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(12)
        t = QLabel(f"Ready to sync  ·  {mode_name}")
        t.setObjectName("SectionTitle")
        lay.addWidget(t)
        paths = QLabel(f"<span style='color:{c['muted']}'>Left</span>&nbsp;&nbsp;{left_label}<br>"
                       f"<span style='color:{c['muted']}'>Right</span>&nbsp;{right_label}")
        paths.setWordWrap(True)
        lay.addWidget(paths)
        lay.addWidget(_hline())

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        verb = "Archive" if opts.archive else "Permanently delete"
        rows = [
            ("Copy to right  →", f"{st['ltr']:,} files", human_size(st['ltr_bytes']), st["ltr"], "accent"),
            ("←  Copy to left", f"{st['rtl']:,} files", human_size(st['rtl_bytes']), st["rtl"], "accent"),
            ("New folders", f"{st['mk_l'] + st['mk_r']:,}", "", st["mk_l"] + st["mk_r"], "accent"),
            (f"{verb} on right", f"{st['del_r']:,} items", "", st["del_r"], "danger"),
            (f"{verb} on left", f"{st['del_l']:,} items", "", st["del_l"], "danger"),
            ("Skipped", f"{st['skip']:,}", "", st["skip"], "muted"),
        ]
        r = 0
        for label, count, size, n, color in rows:
            if not n:
                continue
            a = QLabel(label)
            b = QLabel(f"<b style='color:{c[color]}'>{count}</b>")
            s = QLabel(size)
            s.setObjectName("Muted")
            grid.addWidget(a, r, 0)
            grid.addWidget(b, r, 1)
            grid.addWidget(s, r, 2)
            r += 1
        lay.addLayout(grid)

        warnings = []
        if not opts.archive and (st["del_l"] or st["del_r"]):
            warnings.append(f"<b style='color:{c['danger']}'>Archive is turned off - removed files "
                            "will be permanently deleted.</b>")
        if info.get("free_right") is not None and st["ltr_bytes"] > info["free_right"]:
            warnings.append(f"<b style='color:{c['danger']}'>Not enough free space on the right "
                            f"({human_size(info['free_right'])} free).</b>")
        if info.get("free_left") is not None and st["rtl_bytes"] > info["free_left"]:
            warnings.append(f"<b style='color:{c['danger']}'>Not enough free space on the left "
                            f"({human_size(info['free_left'])} free).</b>")
        lay.addWidget(_hline())
        verify = QLabel("✓  Every file is verified with SHA-256 after copying"
                        + (" and the source is re-read." if opts.reverify_source else "."))
        verify.setStyleSheet(f"color: {c['success']};")
        lay.addWidget(verify)
        for w in warnings:
            lab = QLabel(w)
            lab.setWordWrap(True)
            lay.addWidget(lab)

        bb = QHBoxLayout()
        bb.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        go = QPushButton("Start sync")
        go.setObjectName("Primary")
        go.setDefault(True)
        go.clicked.connect(self.accept)
        bb.addWidget(cancel)
        bb.addWidget(go)
        lay.addLayout(bb)


class ResultDialog(QDialog):
    def __init__(self, summary, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sync finished")
        self.setMinimumWidth(540)
        c = theme.colors()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(12)
        if summary.cancelled:
            head, color = "Sync cancelled", c["warn"]
            sub = "Files that finished copying are complete and verified. Nothing is half-written."
        elif summary.failed:
            head, color = f"Finished with {summary.failed} problem(s)", c["danger"]
            sub = "Files that failed were left untouched at the destination."
        else:
            head, color = "Everything is in sync", c["success"]
            sub = (f"All {summary.verified:,} copied files were verified bit for bit (SHA-256)."
                   if summary.verified else "No files needed copying.")
        t = QLabel(head)
        t.setStyleSheet(f"font-size: 15pt; font-weight: 700; color: {color};")
        lay.addWidget(t)
        s = QLabel(sub)
        s.setWordWrap(True)
        s.setObjectName("Muted")
        lay.addWidget(s)

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        stats = [("Copied", f"{summary.copied:,}"), ("Data", human_size(summary.copied_bytes)),
                 ("Verified", f"{summary.verified:,}"), ("Removed", f"{summary.deleted:,}"),
                 ("Folders", f"{summary.dirs_created:,}"), ("Failed", f"{summary.failed:,}")]
        for i, (k, v) in enumerate(stats):
            box = QVBoxLayout()
            val = QLabel(v)
            val.setObjectName("StatValue")
            lab = QLabel(k)
            lab.setObjectName("StatLabel")
            box.addWidget(val)
            box.addWidget(lab)
            grid.addLayout(box, 0, i)
        lay.addLayout(grid)

        if summary.errors:
            lst = QPlainTextEdit("\n".join(f"{p}: {e}" for p, e in summary.errors))
            lst.setReadOnly(True)
            lst.setFixedHeight(140)
            lay.addWidget(lst)

        bb = QHBoxLayout()
        if summary.log_path:
            log = QPushButton(icon("text-x-log", "document-open"), "Open log")
            log.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(summary.log_path)))
            bb.addWidget(log)
        bb.addStretch()
        ok = QPushButton("Done")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        bb.addWidget(ok)
        lay.addLayout(bb)
