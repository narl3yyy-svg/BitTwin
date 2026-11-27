"""Compare two scanned trees and decide what to do with every path."""

from __future__ import annotations

from typing import Callable, Optional

from .model import (Action, Cancelled, CompareMethod, Direction, Entry, Options,
                    PlanItem, Status, SyncMode)

Baseline = dict[str, dict]


def _parent_dirs(rel: str):
    while "/" in rel:
        rel = rel.rsplit("/", 1)[0]
        yield rel


class Planner:
    def __init__(self, opts: Options, left_fs=None, right_fs=None,
                 cancel: Optional[Callable[[], bool]] = None,
                 hash_progress: Optional[Callable[[str, int], None]] = None):
        self.opts = opts
        self.left_fs = left_fs
        self.right_fs = right_fs
        self.cancel = cancel
        self.hash_progress = hash_progress

    # ------------------------------------------------------------ helpers
    def _time_eq(self, a: float, b: float) -> bool:
        return abs(a - b) <= self.opts.time_tolerance

    def _digest(self, fs, e: Entry) -> str:
        if e.digest is None:
            cb = (lambda n: self.hash_progress(e.rel, n)) if self.hash_progress else None
            e.digest, _ = fs.hash_file(e.rel, uncached=True, progress=cb, cancel=self.cancel)
        return e.digest

    def files_equal(self, l: Entry, r: Entry) -> bool:
        if l.size != r.size:
            return False
        if self.opts.compare == CompareMethod.CONTENT:
            return self._digest(self.left_fs, l) == self._digest(self.right_fs, r)
        return self._time_eq(l.mtime, r.mtime)

    def _diff_status(self, l: Entry, r: Entry) -> Status:
        if self._time_eq(l.mtime, r.mtime):
            return Status.DIFFERENT
        return Status.LEFT_NEWER if l.mtime > r.mtime else Status.RIGHT_NEWER

    def _changed(self, e: Entry, base: dict, side: str) -> bool:
        if base.get("is_dir") != e.is_dir:
            return True
        if e.is_dir:
            return False
        return e.size != base.get("size") or not self._time_eq(e.mtime, base.get(side, 0.0))

    # ----------------------------------------------------------------- main
    def build(self, left: dict[str, Entry], right: dict[str, Entry],
              baseline: Optional[Baseline] = None) -> list[PlanItem]:
        baseline = baseline or {}
        items: list[PlanItem] = []
        for rel in sorted(set(left) | set(right)):
            if self.cancel and self.cancel():
                raise Cancelled()
            l, r = left.get(rel), right.get(rel)
            if (l and l.is_link) or (r and r.is_link):
                items.append(PlanItem(rel, l, r, Status.SYMLINK, Action.SKIP,
                                      note="Symbolic links are not synced"))
                continue
            if self.opts.mode == SyncMode.TWO_WAY:
                items.append(self._two_way(rel, l, r, baseline.get(rel)))
            else:
                items.append(self._one_way(rel, l, r))
        if self.opts.mode == SyncMode.TWO_WAY:
            self._fix_two_way_dirs(items)
        return items

    # --------------------------------------------------- mirror / update
    def _one_way(self, rel, l, r) -> PlanItem:
        ltr = self.opts.direction == Direction.LEFT_TO_RIGHT
        src, dst = (l, r) if ltr else (r, l)
        copy = Action.COPY_LTR if ltr else Action.COPY_RTL
        mkdir = Action.MKDIR_RIGHT if ltr else Action.MKDIR_LEFT
        delete = Action.DELETE_RIGHT if ltr else Action.DELETE_LEFT
        src_only = Status.LEFT_ONLY if ltr else Status.RIGHT_ONLY
        dst_only = Status.RIGHT_ONLY if ltr else Status.LEFT_ONLY
        mirror = self.opts.mode == SyncMode.MIRROR

        if src and not dst:
            return PlanItem(rel, l, r, src_only, mkdir if src.is_dir else copy)
        if dst and not src:
            if mirror:
                return PlanItem(rel, l, r, dst_only, delete,
                                note="Not in source - removed by mirror")
            return PlanItem(rel, l, r, dst_only, Action.NONE, note="Only in target - kept")
        # both exist
        if src.is_dir and dst.is_dir:
            return PlanItem(rel, l, r, Status.EQUAL, Action.NONE)
        if src.is_dir != dst.is_dir:
            if mirror:
                return PlanItem(rel, l, r, Status.TYPE_MISMATCH,
                                mkdir if src.is_dir else copy,
                                note="File/folder type differs - target replaced")
            return PlanItem(rel, l, r, Status.TYPE_MISMATCH, Action.SKIP,
                            note="File/folder type differs")
        if self.files_equal(l, r):
            return PlanItem(rel, l, r, Status.EQUAL, Action.NONE)
        status = self._diff_status(l, r)
        if mirror:
            return PlanItem(rel, l, r, status, copy)
        src_newer = Status.LEFT_NEWER if ltr else Status.RIGHT_NEWER
        if status == src_newer:
            return PlanItem(rel, l, r, status, copy)
        if status == Status.DIFFERENT:
            return PlanItem(rel, l, r, status, Action.SKIP,
                            note="Same time but different content - review")
        return PlanItem(rel, l, r, status, Action.SKIP, note="Target is newer - kept")

    # ------------------------------------------------------------ two-way
    def _two_way(self, rel, l, r, base) -> PlanItem:
        if l and r:
            if l.is_dir and r.is_dir:
                return PlanItem(rel, l, r, Status.EQUAL, Action.NONE)
            if l.is_dir != r.is_dir:
                return PlanItem(rel, l, r, Status.TYPE_MISMATCH, Action.SKIP,
                                note="File on one side, folder on the other - resolve manually")
            if self.files_equal(l, r):
                return PlanItem(rel, l, r, Status.EQUAL, Action.NONE)
            status = self._diff_status(l, r)
            if base and not base.get("is_dir"):
                cl, cr = self._changed(l, base, "lm"), self._changed(r, base, "rm")
                if cl and not cr:
                    return PlanItem(rel, l, r, status, Action.COPY_LTR, note="Changed on left")
                if cr and not cl:
                    return PlanItem(rel, l, r, status, Action.COPY_RTL, note="Changed on right")
            return self._conflict(rel, l, r, status,
                                  "Changed on both sides" if base else "Differs - no sync history yet")
        e = l or r
        on_left = l is not None
        if e.is_dir:
            # resolved later in _fix_two_way_dirs, once children are known
            if base and base.get("is_dir"):
                return PlanItem(rel, l, r, Status.DELETED_RIGHT if on_left else Status.DELETED_LEFT,
                                Action.DELETE_LEFT if on_left else Action.DELETE_RIGHT,
                                note="Folder deleted on the other side")
            return PlanItem(rel, l, r, Status.LEFT_ONLY if on_left else Status.RIGHT_ONLY,
                            Action.MKDIR_RIGHT if on_left else Action.MKDIR_LEFT)
        copy = Action.COPY_LTR if on_left else Action.COPY_RTL
        if base and not base.get("is_dir"):
            side = "lm" if on_left else "rm"
            if not self._changed(e, base, side):
                return PlanItem(rel, l, r,
                                Status.DELETED_RIGHT if on_left else Status.DELETED_LEFT,
                                Action.DELETE_LEFT if on_left else Action.DELETE_RIGHT,
                                note="Deleted on the other side")
            return PlanItem(rel, l, r, Status.CONFLICT, copy, suggested=copy,
                            note="Deleted on one side but modified on the other - restoring")
        return PlanItem(rel, l, r, Status.LEFT_ONLY if on_left else Status.RIGHT_ONLY, copy)

    def _conflict(self, rel, l, r, status, note) -> PlanItem:
        action = Action.SKIP
        if self.opts.conflict_newer_wins:
            if status == Status.LEFT_NEWER:
                action = Action.COPY_LTR
            elif status == Status.RIGHT_NEWER:
                action = Action.COPY_RTL
        if action == Action.SKIP:
            note += " - choose which side to keep"
        else:
            note += " - newer file wins"
        return PlanItem(rel, l, r, Status.CONFLICT, action, note=note)

    def _fix_two_way_dirs(self, items: list[PlanItem]) -> None:
        """A folder may only be deleted if nothing inside it is being kept."""
        keep: set[str] = set()
        for it in items:
            if it.is_dir:
                continue
            if it.action not in (Action.DELETE_LEFT, Action.DELETE_RIGHT):
                for p in _parent_dirs(it.rel):
                    keep.add(p)
        for it in items:
            if it.is_dir and it.is_delete and it.rel in keep:
                on_left = it.left is not None
                it.action = it.suggested = Action.MKDIR_RIGHT if on_left else Action.MKDIR_LEFT
                it.status = Status.LEFT_ONLY if on_left else Status.RIGHT_ONLY
                it.note = "Folder deleted on the other side but has new content - recreating"


# ------------------------------------------------------------------ baseline
def baseline_from_plan(items: list[PlanItem], old: Baseline) -> Baseline:
    """Snapshot of paths that are in sync after execution (used by two-way).

    Anything that was not completed (skipped, failed, cancelled) keeps its
    previous record so nothing is mistaken for a deletion next time."""
    new: Baseline = {}
    for it in items:
        l, r = it.left, it.right
        if it.action == Action.NONE:
            if l and r and it.status == Status.EQUAL:
                new[it.rel] = _rec(l, r)
        elif it.done and not it.error:
            if it.action in (Action.COPY_LTR, Action.COPY_RTL,
                             Action.MKDIR_LEFT, Action.MKDIR_RIGHT):
                nl, nr = it.left_after or l, it.right_after or r
                if nl and nr:
                    new[it.rel] = _rec(nl, nr)
            # completed deletions simply drop out of the baseline
        elif it.rel in old:
            new[it.rel] = old[it.rel]
    return new


def _rec(l: Entry, r: Entry) -> dict:
    if l.is_dir:
        return {"is_dir": True}
    return {"is_dir": False, "size": l.size, "lm": l.mtime, "rm": r.mtime}
