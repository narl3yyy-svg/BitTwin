"""BitTwin main window."""

from __future__ import annotations

import json
import os

from PyQt6.QtCore import QSettings, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QFrame, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                             QProgressBar, QPushButton, QSizePolicy, QStackedWidget, QTableView,
                             QVBoxLayout, QWidget)

from .. import __version__
from ..core.fs_sftp import HostKeyChanged, SFTPConfig, UnknownHostKey
from ..core.location import Location
from ..core.log import logs_dir
from ..core.model import (Action, Cancelled, CompareMethod, Direction, Options, PlanItem, Status,
                          SyncMode)
from . import theme
from .dialogs import (ConfirmDialog, ResultDialog, SettingsDialog, SFTPDialog, ask_trust_host,
                      plan_stats)
from .location_panel import LocationPanel
from .plan_model import (C_ACTION, C_LNAME, C_LSIZE, C_LTIME, C_RNAME, C_RSIZE, C_RTIME,
                         PlanFilter, PlanModel)
from .util import app_icon, human_duration, human_size, icon, message, plain_label
from .workers import CompareWorker, SyncWorker

MODE_NAMES = {SyncMode.MIRROR: "Mirror", SyncMode.UPDATE: "Update", SyncMode.TWO_WAY: "Two-way"}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BitTwin")
        self.setWindowIcon(app_icon())
        self.resize(1280, 820)
        self.setMinimumSize(940, 620)
        self.settings = QSettings("BitTwin", "BitTwin")
        self.opts = self._load_options()
        self.theme_choice = self.settings.value("theme", "system")
        self.items: list[PlanItem] = []
        self.info: dict = {}
        self.worker = None
        self._recheck_after_sync = False

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(20, 18, 20, 12)
        bl.setSpacing(14)
        bl.addLayout(self._build_locations())
        bl.addLayout(self._build_actions())
        bl.addLayout(self._build_filters())
        bl.addWidget(self._build_results(), 1)
        root.addWidget(body, 1)
        root.addWidget(self._build_footer())

        QShortcut(QKeySequence("F5"), self, activated=self.compare)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.start_sync)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.search.setFocus())
        QShortcut(QKeySequence("Escape"), self, activated=self.cancel_work)

        self._restore_locations()
        self._sync_mode_buttons()
        self._update_state()

    # ================================================================ build
    def _build_header(self) -> QWidget:
        w = QFrame()
        w.setObjectName("Header")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(20, 12, 16, 12)
        lay.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(36, 36))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        t = QLabel("BitTwin")
        t.setObjectName("AppTitle")
        s = QLabel("Verified, bit-for-bit folder sync")
        s.setObjectName("AppSubtitle")
        titles.addWidget(t)
        titles.addWidget(s)
        lay.addWidget(logo)
        lay.addLayout(titles)
        lay.addStretch()

        lay.addWidget(self._muted("Mode"))
        self.mode_group = QButtonGroup(self)
        self.mode_btns = {}
        seg = QHBoxLayout()
        seg.setSpacing(0)
        modes = [(SyncMode.MIRROR, "Mirror", "SegFirst",
                  "Make the target an exact copy of the source"),
                 (SyncMode.UPDATE, "Update", "Seg",
                  "Copy new and newer files to the target; never delete anything"),
                 (SyncMode.TWO_WAY, "Two-way", "SegLast",
                  "Keep both sides identical: changes and deletions flow both ways")]
        for mode, label, obj, tip in modes:
            b = QPushButton(label)
            b.setObjectName(obj)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, m=mode: self.set_mode(m))
            self.mode_group.addButton(b)
            self.mode_btns[mode] = b
            seg.addWidget(b)
        lay.addLayout(seg)

        lay.addSpacing(10)
        self.dir_label = self._muted("Direction")
        lay.addWidget(self.dir_label)
        dseg = QHBoxLayout()
        dseg.setSpacing(0)
        self.dir_group = QButtonGroup(self)
        self.ltr_btn = QPushButton("Left  →  Right")
        self.ltr_btn.setObjectName("SegFirst")
        self.rtl_btn = QPushButton("Left  ←  Right")
        self.rtl_btn.setObjectName("SegLast")
        for b, d in ((self.ltr_btn, Direction.LEFT_TO_RIGHT), (self.rtl_btn, Direction.RIGHT_TO_LEFT)):
            b.setCheckable(True)
            b.setMinimumWidth(b.sizeHint().width() + 24)
            b.clicked.connect(lambda _=False, d=d: self.set_direction(d))
            self.dir_group.addButton(b)
            dseg.addWidget(b)
        lay.addLayout(dseg)

        lay.addSpacing(10)
        menu_btn = QPushButton(icon("application-menu", "open-menu", "configure"), "")
        menu_btn.setObjectName("Ghost")
        menu_btn.setToolTip("Settings and more")
        menu = QMenu(self)
        menu.addAction(icon("configure"), "Settings…", self.open_settings)
        menu.addAction(icon("document-open-folder"), "Open logs folder",
                       lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(logs_dir())))
        menu.addSeparator()
        menu.addAction(icon("help-about"), "About BitTwin", self.about)
        menu_btn.setMenu(menu)
        menu_btn.setStyleSheet("QPushButton::menu-indicator { image: none; width: 0; }")
        lay.addWidget(menu_btn)
        return w

    def _muted(self, text: str) -> QLabel:
        l = QLabel(text)
        l.setObjectName("Muted")
        return l

    def _build_locations(self):
        lay = QHBoxLayout()
        lay.setSpacing(12)
        self.left_panel = LocationPanel("Left")
        self.right_panel = LocationPanel("Right")
        for p in (self.left_panel, self.right_panel):
            p.changed.connect(self._locations_changed)
        self.left_panel.sftp_requested.connect(lambda: self.open_sftp(self.left_panel))
        self.right_panel.sftp_requested.connect(lambda: self.open_sftp(self.right_panel))

        mid = QVBoxLayout()
        mid.addStretch()
        self.flow_label = QLabel("→")
        self.flow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.flow_label.setStyleSheet(f"font-size: 22pt; font-weight: 700; color: {theme.colors()['accent']};")
        self.flow_label.setFixedWidth(64)
        swap = QPushButton(icon("exchange-positions", "view-refresh"), "")
        swap.setObjectName("Ghost")
        swap.setToolTip("Swap left and right")
        swap.clicked.connect(self.swap_sides)
        self.swap_btn = swap
        mid.addWidget(self.flow_label)
        mid.addWidget(swap, 0, Qt.AlignmentFlag.AlignHCenter)
        mid.addStretch()

        lay.addWidget(self.left_panel, 1)
        lay.addLayout(mid)
        lay.addWidget(self.right_panel, 1)
        return lay

    def _build_actions(self):
        lay = QHBoxLayout()
        lay.setSpacing(10)
        self.compare_btn = QPushButton(icon("view-refresh"), "Compare")
        self.compare_btn.setToolTip("Scan both sides and show what will change (F5)")
        self.compare_btn.clicked.connect(self.compare)
        self.verify_btn = QPushButton(icon("security-high", "checkmark", "dialog-ok"), "Verify bit for bit")
        self.verify_btn.setToolTip("Read every file on both sides and compare SHA-256 hashes.\n"
                                   "Slow, but proves the two sides are identical.")
        self.verify_btn.clicked.connect(lambda: self.compare(content=True))
        self.mode_hint = QLabel()
        self.mode_hint.setObjectName("ModeHint")
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.sync_btn = QPushButton(icon("media-playback-start"), "Synchronize")
        self.sync_btn.setObjectName("Primary")
        self.sync_btn.setToolTip("Apply the changes shown below (Ctrl+Enter)")
        self.sync_btn.clicked.connect(self.start_sync)
        lay.addWidget(self.compare_btn)
        lay.addWidget(self.verify_btn)
        lay.addSpacing(6)
        lay.addWidget(self.mode_hint, 1)
        lay.addWidget(self.sync_btn)
        return lay

    def _build_filters(self):
        lay = QHBoxLayout()
        lay.setSpacing(4)
        self.filter_group = QButtonGroup(self)
        self.filter_btns = {}
        for mode, label in ((PlanFilter.CHANGES, "Changes"), (PlanFilter.ATTENTION, "Needs attention"),
                            (PlanFilter.ALL, "All"), (PlanFilter.EQUAL, "Identical"),
                            (PlanFilter.ERRORS, "Errors")):
            b = QPushButton(label)
            b.setObjectName("Filter")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, m=mode: self.proxy.set_mode(m))
            self.filter_group.addButton(b)
            self.filter_btns[mode] = (b, label)
            lay.addWidget(b)
        self.filter_btns[PlanFilter.CHANGES][0].setChecked(True)
        lay.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by name…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(260)
        self.search.textChanged.connect(lambda t: self.proxy.set_text(t))
        lay.addWidget(self.search)
        return lay

    def _build_results(self) -> QWidget:
        self.stack = QStackedWidget()
        empty = QFrame()
        empty.setObjectName("Card")
        el = QVBoxLayout(empty)
        el.addStretch()
        ic = QLabel()
        ic.setPixmap(app_icon().pixmap(72, 72))
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title = QLabel("Choose two folders to get started")
        self.empty_title.setObjectName("EmptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text = QLabel("Pick a folder, drive or SFTP server for each side, choose a mode, "
                                 "then press <b>Compare</b>.<br>Nothing is changed until you press "
                                 "<b>Synchronize</b>.")
        self.empty_text.setObjectName("Empty")
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text.setWordWrap(True)
        el.addWidget(ic)
        el.addSpacing(8)
        el.addWidget(self.empty_title)
        el.addWidget(self.empty_text)
        el.addStretch()
        self.stack.addWidget(empty)

        self.model = PlanModel(self)
        self.proxy = PlanFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(C_LNAME, Qt.SortOrder.AscendingOrder)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        h = self.table.horizontalHeader()
        h.setHighlightSections(False)
        for col in (C_LNAME, C_RNAME):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        for col, w in ((C_LSIZE, 90), (C_RSIZE, 90), (C_LTIME, 130), (C_RTIME, 130), (C_ACTION, 130)):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, w)
        self.stack.addWidget(self.table)
        return self.stack

    def _build_footer(self) -> QWidget:
        w = QFrame()
        w.setObjectName("Footer")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(20, 10, 20, 10)
        lay.setSpacing(14)
        self.status = plain_label("Ready")
        self.status.setObjectName("Muted")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(260)
        self.progress.setFixedHeight(10)
        self.progress.hide()
        self.speed = QLabel("")
        self.speed.setObjectName("Muted")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.clicked.connect(self.cancel_work)
        self.cancel_btn.hide()
        lay.addWidget(self.status, 1)
        lay.addWidget(self.speed)
        lay.addWidget(self.progress)
        lay.addWidget(self.cancel_btn)
        return w

    # ============================================================ settings
    def _load_options(self) -> Options:
        o = Options()
        s = self.settings
        try:
            o.mode = SyncMode(s.value("mode", o.mode.value))
            o.direction = Direction(s.value("direction", o.direction.value))
            o.compare = CompareMethod(s.value("compare", o.compare.value))
        except ValueError:
            pass
        o.time_tolerance = float(s.value("time_tolerance", o.time_tolerance))
        o.archive = s.value("archive", o.archive, type=bool)
        o.reverify_source = s.value("reverify_source", o.reverify_source, type=bool)
        o.conflict_newer_wins = s.value("conflict_newer_wins", o.conflict_newer_wins, type=bool)
        o.retries = int(s.value("retries", o.retries))
        ex = s.value("excludes", None)
        if ex:
            try:
                o.excludes = json.loads(ex)
            except ValueError:
                pass
        return o

    def _save_options(self) -> None:
        s, o = self.settings, self.opts
        s.setValue("mode", o.mode.value)
        s.setValue("direction", o.direction.value)
        s.setValue("compare", o.compare.value)
        s.setValue("time_tolerance", o.time_tolerance)
        s.setValue("archive", o.archive)
        s.setValue("reverify_source", o.reverify_source)
        s.setValue("conflict_newer_wins", o.conflict_newer_wins)
        s.setValue("retries", o.retries)
        s.setValue("excludes", json.dumps(o.excludes))
        s.setValue("theme", self.theme_choice)

    def _loc_to_json(self, loc: Location) -> str:
        if not loc.is_set:
            return ""
        if loc.kind == "sftp":
            return json.dumps({"kind": "sftp", "sftp": loc.sftp.public_dict()})
        return json.dumps({"kind": "local", "path": loc.path})

    def _loc_from_json(self, text: str) -> Location:
        try:
            d = json.loads(text) if text else {}
        except ValueError:
            return Location()
        if d.get("kind") == "sftp":
            return Location("sftp", sftp=SFTPConfig(**d["sftp"]))
        if d.get("kind") == "local" and d.get("path"):
            return Location("local", d["path"])
        return Location()

    def _restore_locations(self) -> None:
        for panel, key in ((self.left_panel, "left"), (self.right_panel, "right")):
            loc = self._loc_from_json(self.settings.value(key, ""))
            panel.blockSignals(True)
            panel.set_location(loc)
            panel.blockSignals(False)

    def _saved_sftp(self) -> list[dict]:
        try:
            return json.loads(self.settings.value("sftp_saved", "[]"))
        except ValueError:
            return []

    def _remember_sftp(self, cfg: SFTPConfig) -> None:
        saved = [s for s in self._saved_sftp()
                 if not (s.get("host") == cfg.host and s.get("username") == cfg.username
                         and int(s.get("port", 22)) == cfg.port
                         and s.get("remote_path") == cfg.remote_path)]
        saved.insert(0, cfg.public_dict())
        self.settings.setValue("sftp_saved", json.dumps(saved[:12]))

    # ============================================================== state
    def set_mode(self, mode: SyncMode) -> None:
        self.opts.mode = mode
        self._sync_mode_buttons()
        self._invalidate()

    def set_direction(self, d: Direction) -> None:
        self.opts.direction = d
        self._sync_mode_buttons()
        self._invalidate()

    def _sync_mode_buttons(self) -> None:
        self.mode_btns[self.opts.mode].setChecked(True)
        two_way = self.opts.mode == SyncMode.TWO_WAY
        ltr = self.opts.direction == Direction.LEFT_TO_RIGHT
        (self.ltr_btn if ltr else self.rtl_btn).setChecked(True)
        for b in (self.ltr_btn, self.rtl_btn, self.dir_label):
            b.setEnabled(not two_way)
        self.flow_label.setText("⇄" if two_way else ("→" if ltr else "←"))
        src, dst = ("LEFT", "RIGHT") if ltr else ("RIGHT", "LEFT")
        keep = " (kept in .bittwin-archive)" if self.opts.archive else " (permanently deleted)"
        hints = {
            SyncMode.MIRROR: f"<b>Mirror:</b> makes {dst} an exact copy of {src}. "
                             f"Files that exist only on {dst} are removed{keep}.",
            SyncMode.UPDATE: f"<b>Update:</b> copies new and newer files from {src} to {dst}. "
                             "Nothing is ever deleted.",
            SyncMode.TWO_WAY: "<b>Two-way:</b> changes, additions and deletions on either side are "
                              "applied to the other. Conflicts are highlighted for review.",
        }
        self.mode_hint.setText(hints[self.opts.mode])
        self._save_options()

    def _locations_changed(self) -> None:
        self.settings.setValue("left", self._loc_to_json(self.left_panel.location))
        self.settings.setValue("right", self._loc_to_json(self.right_panel.location))
        self._invalidate()

    def _invalidate(self) -> None:
        if self.worker is not None:
            return
        self.items = []
        self.model.set_items([])
        self.stack.setCurrentIndex(0)
        both = self.left_panel.location.is_set and self.right_panel.location.is_set
        if both:
            self.empty_title.setText("Ready to compare")
            self.empty_text.setText("Press <b>Compare</b> (F5) to see exactly what will change. "
                                    "Nothing is modified until you press <b>Synchronize</b>.")
        else:
            self.empty_title.setText("Choose two folders to get started")
            self.empty_text.setText("Pick a folder, drive or SFTP server for each side - or drag a "
                                    "folder onto a card.<br>Then choose a mode and press "
                                    "<b>Compare</b>.")
        self._set_status("Ready")
        self._update_state()

    def _update_state(self) -> None:
        busy = self.worker is not None
        both = self.left_panel.location.is_set and self.right_panel.location.is_set
        work = any(it.action not in (Action.NONE, Action.SKIP) for it in self.items)
        self.compare_btn.setEnabled(both and not busy)
        self.verify_btn.setEnabled(both and not busy)
        self.sync_btn.setEnabled(work and not busy)
        self.swap_btn.setEnabled(not busy)
        for b in list(self.mode_btns.values()):
            b.setEnabled(not busy)
        two_way = self.opts.mode == SyncMode.TWO_WAY
        self.ltr_btn.setEnabled(not busy and not two_way)
        self.rtl_btn.setEnabled(not busy and not two_way)
        self.left_panel.set_busy(busy)
        self.right_panel.set_busy(busy)
        self.cancel_btn.setVisible(busy)
        self._update_filter_counts()

    def _update_filter_counts(self) -> None:
        from .plan_model import needs_attention
        counts = {PlanFilter.ALL: len(self.items),
                  PlanFilter.CHANGES: sum(1 for i in self.items if i.action != Action.NONE or i.error),
                  PlanFilter.ATTENTION: sum(1 for i in self.items if needs_attention(i) or i.error),
                  PlanFilter.EQUAL: sum(1 for i in self.items if i.action == Action.NONE),
                  PlanFilter.ERRORS: sum(1 for i in self.items if i.error)}
        for mode, (b, label) in self.filter_btns.items():
            n = counts.get(mode, 0)
            b.setText(f"{label}  {n:,}" if self.items else label)
            if mode == PlanFilter.ERRORS:
                b.setVisible(n > 0)

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    def swap_sides(self) -> None:
        l, r = self.left_panel.location, self.right_panel.location
        self.left_panel.blockSignals(True)
        self.right_panel.blockSignals(True)
        self.left_panel.set_location(r)
        self.right_panel.set_location(l)
        self.left_panel.blockSignals(False)
        self.right_panel.blockSignals(False)
        self._locations_changed()

    # ================================================================ SFTP
    def open_sftp(self, panel: LocationPanel, message: str = "") -> bool:
        cur = panel.location.sftp if panel.location.kind == "sftp" else None
        dlg = SFTPDialog(self, self._saved_sftp(), cur)
        if message:
            dlg._set_msg(message, "warn")
        if dlg.exec() and dlg.result_cfg is not None:
            if dlg.remember.isChecked():
                self._remember_sftp(dlg.result_cfg)
            panel.set_location(Location("sftp", sftp=dlg.result_cfg))
            return True
        return False

    # ============================================================= compare
    def _check_pair(self) -> str:
        l, r = self.left_panel.location, self.right_panel.location
        if not (l.is_set and r.is_set):
            return "Choose a location for both sides first."
        if l.kind == "local" and r.kind == "local":
            a = os.path.realpath(l.path)
            b = os.path.realpath(r.path)
            for p, side in ((a, "Left"), (b, "Right")):
                if not os.path.isdir(p):
                    return f"{side} folder does not exist:\n{p}\n\nIs the drive connected?"
            if a == b:
                return "Left and right are the same folder."
            if a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/"):
                return "One folder is inside the other. Choose two separate folders."
        if l.kind == "sftp" and r.kind == "sftp" and l.sftp.label == r.sftp.label:
            return "Left and right are the same remote folder."
        return ""

    def compare(self, content: bool = False) -> None:
        if self.worker is not None:
            return
        err = self._check_pair()
        if err:
            message(self, "warning", "Cannot compare", err)
            return
        opts = Options(**{**self.opts.__dict__, "excludes": list(self.opts.excludes)})
        if content:
            opts.compare = CompareMethod.CONTENT
        self._content_run = content or opts.compare == CompareMethod.CONTENT
        w = CompareWorker(self.left_panel.location, self.right_panel.location, opts, self)
        w.status.connect(self._set_status)
        w.done.connect(self._compare_done)
        w.failed.connect(self._worker_failed)
        w.finished.connect(self._worker_finished)
        self.worker = w
        self.progress.setRange(0, 0)
        self.progress.show()
        self.speed.setText("")
        self._update_state()
        w.start()

    def _compare_done(self, items: list[PlanItem], info: dict) -> None:
        self.items = items
        self.info = info
        self.model.set_items(items)
        self.stack.setCurrentIndex(1)
        st = plan_stats(items)
        work = st["ltr"] + st["rtl"] + st["mk_l"] + st["mk_r"] + st["del_l"] + st["del_r"]
        attention = sum(1 for i in items if i.status in (Status.CONFLICT, Status.DIFFERENT,
                                                         Status.TYPE_MISMATCH))
        bits = []
        if st["ltr"] or st["mk_r"]:
            bits.append(f"→ {st['ltr']:,} files ({human_size(st['ltr_bytes'])})")
        if st["rtl"] or st["mk_l"]:
            bits.append(f"← {st['rtl']:,} files ({human_size(st['rtl_bytes'])})")
        if st["del_l"] or st["del_r"]:
            bits.append(f"{st['del_l'] + st['del_r']:,} to remove")
        if attention:
            bits.append(f"⚠ {attention:,} need attention")
        how = "bit for bit (SHA-256)" if self._content_run else "by size and date"
        if self._recheck_after_sync:
            self._recheck_after_sync = False
            if work == 0:
                self._set_status(f"✓  Re-checked: both sides are in sync  ·  {len(items):,} items")
            else:
                self._set_status(f"Re-checked: {work:,} items still differ - see the list")
        elif work == 0:
            if self._content_run:
                self._set_status(f"✓  Verified bit for bit: all {st['equal']:,} items are identical"
                                 + (f", {st['skip']:,} skipped" if st["skip"] else ""))
            else:
                self._set_status(f"✓  Already in sync  ·  {len(items):,} items compared {how}")
        else:
            self._set_status(f"Compared {len(items):,} items {how}:  " + "   ·   ".join(bits))
        if info.get("rejected"):
            shown = "\n".join(info["rejected"][:15])
            more = len(info["rejected"]) - 15
            message(self, "warning", "Unsafe names ignored",
                    "The server sent file names that could point outside the chosen folder. "
                    "They were ignored for your safety:\n\n" + shown
                    + (f"\n… and {more} more" if more > 0 else ""))
        if work == 0 and attention == 0:
            self.filter_btns[PlanFilter.ALL][0].click()
        elif attention and self.opts.mode == SyncMode.TWO_WAY:
            self.filter_btns[PlanFilter.CHANGES][0].click()
        else:
            self.filter_btns[PlanFilter.CHANGES][0].click()

    def _worker_failed(self, exc) -> None:
        self._recheck_after_sync = False
        if isinstance(exc, Cancelled):
            self._set_status("Cancelled")
            return
        if isinstance(exc, UnknownHostKey):
            if ask_trust_host(self, exc):
                QTimer.singleShot(0, lambda: self.compare(self._content_run))
            else:
                self._set_status("Cancelled - server not trusted")
            return
        if isinstance(exc, HostKeyChanged):
            message(self, "critical", "Server identity changed", str(exc))
            self._set_status("Refused: server identity changed")
            return
        # an encrypted key with no passphrase in memory (e.g. after a restart)
        if isinstance(exc, PermissionError):
            for panel in (self.left_panel, self.right_panel):
                if panel.location.kind == "sftp":
                    self._set_status(str(exc))
                    QTimer.singleShot(0, lambda p=panel, m=str(exc): self._reauth(p, m))
                    return
        self._set_status(f"Error: {exc}")
        message(self, "critical", "Something went wrong", str(exc))

    def _reauth(self, panel, message):
        if self.open_sftp(panel, message):
            self.compare(self._content_run)

    def _worker_finished(self) -> None:
        self.worker = None
        self.progress.hide()
        self.progress.setRange(0, 1000)
        self.speed.setText("")
        self._update_state()

    def cancel_work(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self._set_status("Cancelling - finishing the current file safely…")

    # ================================================================ sync
    def start_sync(self) -> None:
        if self.worker is not None or not self.sync_btn.isEnabled():
            return
        dlg = ConfirmDialog(self.items, self.opts, self.info, self.left_panel.location.label,
                            self.right_panel.location.label, MODE_NAMES[self.opts.mode], self)
        if not dlg.exec():
            return
        opts = Options(**{**self.opts.__dict__, "excludes": list(self.opts.excludes)})
        w = SyncWorker(self.left_panel.location, self.right_panel.location, opts, self.items, self)
        w.status.connect(self._set_status)
        w.progress.connect(self._sync_progress)
        w.done.connect(self._sync_done)
        w.failed.connect(self._worker_failed)
        w.finished.connect(self._worker_finished)
        self.worker = w
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.show()
        self._update_state()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.model.refresh)
        self._refresh_timer.start(500)
        w.start()

    def _sync_progress(self, p: dict) -> None:
        total = max(p["total"], 1)
        self.progress.setValue(int(1000 * p["done"] / total))
        self._set_status(f"{p['phase']}  ·  {p['file']}" if p.get("file") else p.get("phase", ""))
        rate = p.get("rate", 0)
        remaining = (total - p["done"]) / rate if rate > 0 else 0
        self.speed.setText(f"{int(100 * p['done'] / total)}%   ·   {human_size(rate)}/s   ·   "
                           f"{human_duration(remaining)} left")

    def _sync_done(self, summary) -> None:
        if hasattr(self, "_refresh_timer"):
            self._refresh_timer.stop()
        self.model.refresh()
        self._update_filter_counts()
        ResultDialog(summary, self).exec()
        # Re-compare so the list reflects reality and proves the result.
        self._recheck_after_sync = True
        QTimer.singleShot(0, lambda: self.compare(False))

    # ======================================================== context menu
    def _selected_items(self) -> list[PlanItem]:
        rows = {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}
        return [self.model.item(r) for r in sorted(rows)]

    def _context_menu(self, pos) -> None:
        if self.worker is not None:
            return
        sel = self._selected_items()
        if not sel:
            return
        menu = QMenu(self)
        n = len(sel)
        label = f"{n} items" if n > 1 else os.path.basename(sel[0].rel)
        head = menu.addAction(label)
        head.setEnabled(False)
        menu.addSeparator()

        def can(it: PlanItem, a: Action) -> bool:
            if a in (Action.COPY_LTR, Action.MKDIR_RIGHT):
                return it.left is not None and not it.left.is_link
            if a in (Action.COPY_RTL, Action.MKDIR_LEFT):
                return it.right is not None and not it.right.is_link
            if a == Action.DELETE_LEFT:
                return it.left is not None and not it.left.is_link
            if a == Action.DELETE_RIGHT:
                return it.right is not None and not it.right.is_link
            return True

        def resolve(it: PlanItem, a: Action) -> Action:
            if a == Action.COPY_LTR and it.left and it.left.is_dir:
                return Action.MKDIR_RIGHT
            if a == Action.COPY_RTL and it.right and it.right.is_dir:
                return Action.MKDIR_LEFT
            return a

        choices = [(icon("go-next"), "Copy left  →  right", Action.COPY_LTR),
                   (icon("go-previous"), "Copy left  ←  right", Action.COPY_RTL),
                   None,
                   (icon("edit-delete"), "Remove from left", Action.DELETE_LEFT),
                   (icon("edit-delete"), "Remove from right", Action.DELETE_RIGHT),
                   None,
                   (icon("dialog-cancel"), "Skip (do nothing)", Action.SKIP)]
        for ch in choices:
            if ch is None:
                menu.addSeparator()
                continue
            ic, text, a = ch
            act = menu.addAction(ic, text)
            ok = [it for it in sel if can(it, a)]
            act.setEnabled(bool(ok))
            act.triggered.connect(lambda _=False, a=a, ok=ok: self._override(ok, a, resolve))
        menu.addSeparator()
        menu.addAction(icon("edit-undo"), "Reset to suggestion",
                       lambda: self._override(sel, None, resolve))
        local = [(self.left_panel.location, "left"), (self.right_panel.location, "right")]
        for loc, side in local:
            if loc.kind == "local" and n == 1:
                path = os.path.join(loc.path, sel[0].rel)
                folder = path if os.path.isdir(path) else os.path.dirname(path)
                if os.path.isdir(folder):
                    menu.addAction(icon("document-open-folder"), f"Open folder on {side}",
                                   lambda f=folder: QDesktopServices.openUrl(QUrl.fromLocalFile(f)))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _override(self, items: list[PlanItem], action, resolve) -> None:
        for it in items:
            if it.done:
                continue
            it.action = it.suggested if action is None else resolve(it, action)
            it.error = ""
        self.model.refresh()
        self.proxy.invalidate()
        self._update_state()

    # ============================================================== misc
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.opts, self.theme_choice, self)
        if dlg.exec():
            self.theme_choice = dlg.apply_to(self.opts)
            theme.apply(QApplication.instance(), self.theme_choice)
            self.flow_label.setStyleSheet(
                f"font-size: 22pt; font-weight: 700; color: {theme.colors()['accent']};")
            self._sync_mode_buttons()
            self._save_options()
            self._invalidate()

    def about(self) -> None:
        QMessageBox.about(
            self, "About BitTwin",
            f"<h3>BitTwin {__version__}</h3>"
            "<p>Verified, bit-for-bit folder synchronization for Linux.</p>"
            "<p>Every copy is written to a temporary file, flushed to disk, read back and "
            "checked with SHA-256 before it replaces anything.</p>"
            "<p>No accounts, no telemetry, no background services - BitTwin only runs when you "
            "press a button.</p>"
            "<p>© narl3yyy-svg · GNU GPL v3.0</p>")

    def closeEvent(self, e):
        if self.worker is not None:
            r = QMessageBox.question(self, "Stop and quit?",
                                     "BitTwin is still working. Stop safely after the current file "
                                     "and quit?")
            if r != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self.worker.cancel()
            self.worker.wait(60000)
        self._save_options()
        super().closeEvent(e)
