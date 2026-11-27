"""Table model showing the comparison result side by side."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtGui import QColor, QFont

from ..core.model import Action, PlanItem, Status
from . import theme
from .util import esc, human_size, human_time, icon

COLS = ["Left", "Size", "Modified", "Action", "Right", "Size", "Modified"]
C_LNAME, C_LSIZE, C_LTIME, C_ACTION, C_RNAME, C_RSIZE, C_RTIME = range(7)

ACTION_TEXT = {
    Action.COPY_LTR: "Copy  →",
    Action.COPY_RTL: "←  Copy",
    Action.DELETE_LEFT: "✕  Remove",
    Action.DELETE_RIGHT: "Remove  ✕",
    Action.MKDIR_LEFT: "←  Create",
    Action.MKDIR_RIGHT: "Create  →",
    Action.SKIP: "Skip",
}

STATUS_TEXT = {
    Status.EQUAL: "Identical",
    Status.LEFT_ONLY: "Only on left",
    Status.RIGHT_ONLY: "Only on right",
    Status.LEFT_NEWER: "Left is newer",
    Status.RIGHT_NEWER: "Right is newer",
    Status.DIFFERENT: "Same date, different content",
    Status.CONFLICT: "Conflict",
    Status.TYPE_MISMATCH: "File vs. folder",
    Status.DELETED_LEFT: "Deleted on left",
    Status.DELETED_RIGHT: "Deleted on right",
    Status.SYMLINK: "Symbolic link",
}

ATTENTION = {Status.CONFLICT, Status.DIFFERENT, Status.TYPE_MISMATCH}


def needs_attention(it: PlanItem) -> bool:
    return it.status in ATTENTION or (it.action == Action.SKIP and it.status != Status.SYMLINK)


class PlanModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[PlanItem] = []
        self._folder = icon("folder")
        self._file = icon("text-x-generic", "unknown")
        self._link = icon("emblem-symbolic-link", "inode-symlink")
        self._bold = QFont()
        self._bold.setWeight(QFont.Weight.DemiBold)

    def set_items(self, items: list[PlanItem]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()

    def refresh(self) -> None:
        if self.items:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.items) - 1, len(COLS) - 1))

    def item(self, row: int) -> PlanItem:
        return self.items[row]

    # Qt API --------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return COLS[section]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                if section in (C_LSIZE, C_RSIZE):
                    return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if section == C_ACTION:
                    return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        it = self.items[index.row()]
        col = index.column()
        left_side = col < C_ACTION
        e = it.left if left_side else it.right
        c = theme.colors()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == C_ACTION:
                return self._action_text(it)
            if e is None:
                return ""
            if col in (C_LNAME, C_RNAME):
                return e.rel
            if col in (C_LSIZE, C_RSIZE):
                return "" if e.is_dir or e.is_link else human_size(e.size)
            if col in (C_LTIME, C_RTIME):
                return human_time(e.mtime)
        elif role == Qt.ItemDataRole.DecorationRole:
            if col in (C_LNAME, C_RNAME) and e is not None:
                return self._link if e.is_link else (self._folder if e.is_dir else self._file)
        elif role == Qt.ItemDataRole.ForegroundRole:
            if col == C_ACTION:
                return QColor(self._action_color(it, c))
            if e is None:
                return None
            if it.action == Action.NONE and it.status == Status.EQUAL:
                return QColor(c["muted"])
            target_left = it.action in (Action.COPY_RTL, Action.MKDIR_LEFT, Action.DELETE_LEFT)
            target_right = it.action in (Action.COPY_LTR, Action.MKDIR_RIGHT, Action.DELETE_RIGHT)
            if (left_side and target_left) or (not left_side and target_right):
                if it.is_delete:
                    return QColor(c["danger"])
            return None
        elif role == Qt.ItemDataRole.FontRole:
            if col == C_ACTION:
                return self._bold
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (C_LSIZE, C_RSIZE):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if col == C_ACTION:
                return int(Qt.AlignmentFlag.AlignCenter)
        elif role == Qt.ItemDataRole.ToolTipRole:
            lines = [f"<b>{esc(it.rel)}</b>", STATUS_TEXT.get(it.status, "")]
            if it.note:
                lines.append(esc(it.note))
            if it.error:
                lines.append(f"<span style='color:{c['danger']}'>Error: {esc(it.error)}</span>")
            if it.digest:
                lines.append(f"Verified SHA-256: <code>{it.digest}</code>")
            return "<br>".join(x for x in lines if x)
        elif role == Qt.ItemDataRole.UserRole:  # sort key
            if col in (C_LSIZE, C_RSIZE):
                return -1 if e is None else e.size
            if col in (C_LTIME, C_RTIME):
                return 0 if e is None else e.mtime
            if col == C_ACTION:
                return self._action_text(it)
            return it.rel.lower()
        return None

    def _action_text(self, it: PlanItem) -> str:
        if it.error:
            return "✗  Failed"
        if it.done:
            return "✓  Done"
        if it.action == Action.NONE:
            return "=" if it.status == Status.EQUAL else "Keep"
        text = ACTION_TEXT.get(it.action, "")
        if it.status == Status.CONFLICT:
            text = "⚠ " + text
        return text

    def _action_color(self, it: PlanItem, c: dict) -> str:
        if it.error:
            return c["danger"]
        if it.done:
            return c["success"]
        if it.status in ATTENTION:
            return c["warn"]
        if it.is_delete:
            return c["danger"]
        if it.action in (Action.NONE, Action.SKIP):
            return c["faint"]
        return c["accent"]


class PlanFilter(QSortFilterProxyModel):
    ALL, CHANGES, ATTENTION, ERRORS, EQUAL = range(5)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = self.CHANGES
        self.text = ""
        self.setSortRole(Qt.ItemDataRole.UserRole)

    def set_mode(self, mode: int) -> None:
        self.mode = mode
        self.invalidateFilter()

    def set_text(self, text: str) -> None:
        self.text = text.lower().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        it: PlanItem = self.sourceModel().items[row]
        if self.text and self.text not in it.rel.lower():
            return False
        if self.mode == self.CHANGES:
            return it.action not in (Action.NONE,) or bool(it.error)
        if self.mode == self.ATTENTION:
            return needs_attention(it) or bool(it.error)
        if self.mode == self.ERRORS:
            return bool(it.error)
        if self.mode == self.EQUAL:
            return it.action == Action.NONE
        return True
