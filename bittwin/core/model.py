"""Shared data types for the BitTwin engine."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

# Names BitTwin itself creates inside a sync root. They are never synced.
ARCHIVE_DIR = ".bittwin-archive"
TEMP_SUFFIX = ".bittwin-tmp"

DEFAULT_EXCLUDES = [
    ARCHIVE_DIR,
    "*" + TEMP_SUFFIX,
    ".Trash-*",
    "lost+found",
    "System Volume Information",
    "$RECYCLE.BIN",
]


class SyncMode(str, enum.Enum):
    MIRROR = "mirror"      # make target an exact copy of source (deletes extras)
    UPDATE = "update"      # copy new / newer files to target, never delete
    TWO_WAY = "two_way"    # propagate changes and deletions in both directions


class Direction(str, enum.Enum):
    LEFT_TO_RIGHT = "ltr"
    RIGHT_TO_LEFT = "rtl"


class CompareMethod(str, enum.Enum):
    TIME_SIZE = "time_size"  # fast: size + modification time
    CONTENT = "content"      # thorough: SHA-256 of both sides


class Action(str, enum.Enum):
    NONE = "none"                  # identical, nothing to do
    COPY_LTR = "copy_ltr"          # copy left -> right (create or overwrite)
    COPY_RTL = "copy_rtl"          # copy right -> left
    DELETE_LEFT = "delete_left"
    DELETE_RIGHT = "delete_right"
    MKDIR_LEFT = "mkdir_left"
    MKDIR_RIGHT = "mkdir_right"
    SKIP = "skip"                  # user / planner decided not to touch it


class Status(str, enum.Enum):
    EQUAL = "equal"
    LEFT_ONLY = "left_only"
    RIGHT_ONLY = "right_only"
    LEFT_NEWER = "left_newer"
    RIGHT_NEWER = "right_newer"
    DIFFERENT = "different"        # same time, different size / content
    CONFLICT = "conflict"          # changed on both sides (two-way)
    TYPE_MISMATCH = "type_mismatch"
    DELETED_LEFT = "deleted_left"  # two-way: removed on left since last sync
    DELETED_RIGHT = "deleted_right"
    SYMLINK = "symlink"            # symlinks are never followed or copied


@dataclass
class Entry:
    """One file or directory found while scanning a side."""

    rel: str                 # POSIX relative path, no leading slash
    is_dir: bool
    size: int = 0
    mtime: float = 0.0
    mode: int = 0o644
    is_link: bool = False
    digest: Optional[str] = None  # filled in by content comparison


@dataclass
class PlanItem:
    rel: str
    left: Optional[Entry]
    right: Optional[Entry]
    status: Status
    action: Action
    suggested: Action = Action.NONE
    note: str = ""
    # filled in by the executor
    done: bool = False
    error: str = ""
    digest: str = ""                       # verified SHA-256 of the copied file
    left_after: Optional[Entry] = None     # state after the operation
    right_after: Optional[Entry] = None

    def __post_init__(self) -> None:
        if self.suggested == Action.NONE:
            self.suggested = self.action

    @property
    def is_dir(self) -> bool:
        e = self.left or self.right
        return bool(e and e.is_dir)

    @property
    def is_copy(self) -> bool:
        return self.action in (Action.COPY_LTR, Action.COPY_RTL)

    @property
    def is_delete(self) -> bool:
        return self.action in (Action.DELETE_LEFT, Action.DELETE_RIGHT)

    @property
    def copy_bytes(self) -> int:
        if self.action == Action.COPY_LTR and self.left and not self.left.is_dir:
            return self.left.size
        if self.action == Action.COPY_RTL and self.right and not self.right.is_dir:
            return self.right.size
        return 0


@dataclass
class Options:
    mode: SyncMode = SyncMode.MIRROR
    direction: Direction = Direction.LEFT_TO_RIGHT
    compare: CompareMethod = CompareMethod.TIME_SIZE
    # Seconds two mtimes may differ and still count as equal. FAT drives store
    # 2 s resolution and SFTP v3 only whole seconds, so 2 is a safe default.
    time_tolerance: float = 2.0
    # Move overwritten / deleted files to .bittwin-archive instead of erasing.
    archive: bool = True
    # Re-read the source (bypassing the page cache) after each copy and check
    # it still hashes the same - catches flaky cables / failing source media.
    reverify_source: bool = True
    # How many times a file is re-copied after a verification failure.
    retries: int = 2
    excludes: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    # Two-way conflicts are pre-resolved in favour of the newer file.
    conflict_newer_wins: bool = True


@dataclass
class Summary:
    copied: int = 0
    copied_bytes: int = 0
    verified: int = 0
    deleted: int = 0
    dirs_created: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False
    log_path: str = ""


class SyncError(Exception):
    pass


class VerificationError(SyncError):
    pass


class UnsafePath(SyncError):
    """A path that could escape the chosen folder (bad name or symlinked folder)."""


class Cancelled(Exception):
    pass
