"""Carry out a plan with verified, crash-safe copies.

Every file copy follows the same procedure:

1. Stream the source into a hidden temp file next to the destination while
   computing its SHA-256.
2. Flush and fsync the temp file so the data is physically on the device,
   then drop it from the page cache.
3. Read the temp file back from the device and hash it again. The two hashes
   must match bit for bit.
4. Optionally re-read the source (also bypassing the cache) to make sure the
   source itself read back consistently.
5. Only then is the old destination archived (or replaced) and the temp file
   atomically renamed into place. A half-written file never replaces a good one.

If any check fails the temp file is discarded and the copy is retried.
"""

from __future__ import annotations

import hashlib
import time
from typing import Callable, Optional

from .fs_local import CHUNK
from .log import RunLog
from .model import (ARCHIVE_DIR, Action, Cancelled, Entry, Options, PlanItem,
                    Summary, SyncError, UnsafePath, VerificationError)


def _write_all(f, buf) -> None:
    view = memoryview(buf)
    while view:
        n = f.write(view)
        if n is None:          # paramiko returns None after writing everything
            return
        view = view[n:]


def _parent(rel: str) -> str:
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


class Executor:
    def __init__(self, left_fs, right_fs, opts: Options, log: Optional[RunLog] = None,
                 cancel: Optional[Callable[[], bool]] = None,
                 progress: Optional[Callable[[dict], None]] = None):
        self.fs = {"L": left_fs, "R": right_fs}
        self.opts = opts
        self.log = log
        self.cancel = cancel or (lambda: False)
        self.progress = progress
        self.stamp = time.strftime("%Y-%m-%d_%H%M%S")
        self._dirs_ok: dict[str, set] = {"L": set(), "R": set()}
        self._work_done = 0
        self._work_total = 0
        self._started = 0.0
        self._last_emit = 0.0
        self._current = ""
        self._phase = ""

    # ------------------------------------------------------------ progress
    def _emit(self, force: bool = False, **extra) -> None:
        if not self.progress:
            return
        now = time.monotonic()
        if not force and now - self._last_emit < 0.1:
            return
        self._last_emit = now
        elapsed = max(now - self._started, 1e-6)
        info = {"done": self._work_done, "total": self._work_total, "file": self._current,
                "phase": self._phase, "elapsed": elapsed,
                "rate": self._work_done / elapsed}
        info.update(extra)
        self.progress(info)

    def _tick(self, n: int) -> None:
        self._work_done += n
        self._emit()

    def _check_cancel(self) -> None:
        if self.cancel():
            raise Cancelled()

    def _log(self, *parts) -> None:
        if self.log:
            self.log.line(*parts)

    # --------------------------------------------------------------- main
    def run(self, items: list[PlanItem]) -> Summary:
        s = Summary()
        passes = 3 if self.opts.reverify_source else 2
        work = [it for it in items if it.action not in (Action.NONE, Action.SKIP)]
        self._work_total = sum(it.copy_bytes for it in work) * passes + len(work)
        self._started = time.monotonic()

        mkdirs = sorted((it for it in work if it.action in (Action.MKDIR_LEFT, Action.MKDIR_RIGHT)),
                        key=lambda it: it.rel.count("/"))
        copies = [it for it in work if it.is_copy]
        file_deletes = [it for it in work if it.is_delete and not it.is_dir]
        dir_deletes = sorted((it for it in work if it.is_delete and it.is_dir),
                             key=lambda it: it.rel.count("/"), reverse=True)
        s.skipped = sum(1 for it in items if it.action == Action.SKIP)

        try:
            for group, fn in ((mkdirs, self._do_mkdir), (copies, self._do_copy),
                              (file_deletes, self._do_delete), (dir_deletes, self._do_rmdir)):
                for it in group:
                    self._check_cancel()
                    self._current = it.rel
                    try:
                        fn(it, s)
                        it.done = True
                    except Cancelled:
                        raise
                    except Exception as exc:  # record and continue with the next file
                        it.error = str(exc) or exc.__class__.__name__
                        s.failed += 1
                        s.errors.append((it.rel, it.error))
                        self._log("FAILED", it.action.value, it.rel, it.error)
                    self._tick(1)
        except Cancelled:
            s.cancelled = True
            self._log("CANCELLED", "by user")
        self._emit(force=True)
        return s

    # ------------------------------------------------------------- helpers
    def _sides(self, action: Action) -> tuple[str, str]:
        if action in (Action.COPY_LTR, Action.MKDIR_RIGHT, Action.DELETE_RIGHT):
            return "L", "R"
        return "R", "L"

    def _ensure_parent(self, side: str, rel: str) -> None:
        parent = _parent(rel)
        if not parent or parent in self._dirs_ok[side]:
            return
        fs = self.fs[side]
        st = fs.stat(parent)
        if st is not None and st.is_link:
            raise UnsafePath(f"Destination folder '{parent}' is a symbolic link - skipped for safety")
        if st is not None and not st.is_dir:
            self._discard(side, parent)
            st = None
        if st is None:
            fs.mkdir(parent)
        self._dirs_ok[side].add(parent)

    def _archive_rel(self, rel: str) -> str:
        return f"{ARCHIVE_DIR}/{self.stamp}/{rel}"

    def _discard(self, side: str, rel: str) -> None:
        """Remove whatever is at rel (file or folder), archiving if enabled."""
        fs = self.fs[side]
        st = fs.stat(rel)
        if st is None:
            return
        if self.opts.archive:
            fs.move(rel, self._archive_rel(rel))
            self._log("ARCHIVED", side, rel, "->", self._archive_rel(rel))
        elif st.is_dir:
            self._remove_tree(fs, rel)
        else:
            fs.remove(rel)

    def _remove_tree(self, fs, rel: str) -> None:
        entries = fs.scan([], start=rel)
        for e in sorted(entries.values(), key=lambda e: e.rel.count("/"), reverse=True):
            if e.is_dir:
                fs.rmdir(e.rel)
            else:
                fs.remove(e.rel)
        fs.rmdir(rel)

    # ---------------------------------------------------------- operations
    def _do_mkdir(self, it: PlanItem, s: Summary) -> None:
        _, dst = self._sides(it.action)
        fs = self.fs[dst]
        self._phase = "Creating folder"
        st = fs.stat(it.rel)
        if st is not None and not st.is_dir:
            self._discard(dst, it.rel)
            st = None
        if st is None:
            self._ensure_parent(dst, it.rel)
            fs.mkdir(it.rel)
            s.dirs_created += 1
            self._log("MKDIR", dst, it.rel)
        self._dirs_ok[dst].add(it.rel)
        after = fs.stat(it.rel)
        if dst == "R":
            it.right_after, it.left_after = after, it.left
        else:
            it.left_after, it.right_after = after, it.right

    def _do_delete(self, it: PlanItem, s: Summary) -> None:
        _, dst = self._sides(it.action)
        self._phase = "Archiving" if self.opts.archive else "Deleting"
        st = self.fs[dst].stat(it.rel)
        if st is None:
            return  # already gone (e.g. removed together with its folder)
        self._discard(dst, it.rel)
        s.deleted += 1
        self._log("DELETED" if not self.opts.archive else "REMOVED", dst, it.rel)

    def _do_rmdir(self, it: PlanItem, s: Summary) -> None:
        _, dst = self._sides(it.action)
        fs = self.fs[dst]
        st = fs.stat(it.rel)
        if st is None:
            return
        if not st.is_dir:
            self._discard(dst, it.rel)
            s.deleted += 1
            return
        if fs.rmdir(it.rel):
            s.deleted += 1
            self._log("RMDIR", dst, it.rel)
        else:
            it.note = "Folder kept - it still contains excluded or skipped items"
            self._log("KEPT", dst, it.rel, "(not empty)")

    def _do_copy(self, it: PlanItem, s: Summary) -> None:
        src, dst = self._sides(it.action)
        entry: Entry = it.left if src == "L" else it.right
        last_error: Optional[Exception] = None
        for attempt in range(self.opts.retries + 1):
            self._check_cancel()
            try:
                digest, final = self._copy_once(src, dst, entry)
                it.digest = digest
                if dst == "R":
                    it.left_after, it.right_after = entry, final
                else:
                    it.left_after, it.right_after = final, entry
                s.copied += 1
                s.verified += 1
                s.copied_bytes += entry.size
                self._log("COPIED", f"{src}->{dst}", it.rel, f"{entry.size} bytes",
                          f"sha256={digest}", "VERIFIED")
                return
            except VerificationError as exc:
                last_error = exc
                self._log("RETRY", it.rel, f"attempt {attempt + 1}:", str(exc))
        raise last_error or SyncError("copy failed")

    def _copy_once(self, src: str, dst: str, entry: Entry) -> tuple[str, Entry]:
        sfs, dfs = self.fs[src], self.fs[dst]
        rel = entry.rel
        before = sfs.stat(rel)
        if before is None:
            raise SyncError("Source file no longer exists - compare again")
        if before.is_link:
            raise UnsafePath("Source is now a symbolic link - skipped for safety")
        if before.is_dir or before.size != entry.size or abs(before.mtime - entry.mtime) > 1e-3:
            raise SyncError("Source file changed since the comparison - compare again")

        self._ensure_parent(dst, rel)
        tmp, out = dfs.create_temp(rel)
        ok = False
        try:
            # 1. copy + hash
            self._phase = "Copying"
            h = hashlib.sha256()
            written = 0
            try:
                with sfs.open_read(rel) as inp:
                    while True:
                        self._check_cancel()
                        buf = inp.read(CHUNK)
                        if not buf:
                            break
                        h.update(buf)
                        _write_all(out, buf)
                        written += len(buf)
                        self._tick(len(buf))
            finally:
                # 2. flush + fsync to the device
                dfs.finish_temp(out)
            src_digest = h.hexdigest()

            after = sfs.stat(rel)
            if after is None or after.size != before.size or after.mtime != before.mtime \
                    or written != before.size:
                raise VerificationError("Source changed while it was being copied")

            # 3. read the copy back from the device
            self._phase = "Verifying copy"
            dst_digest, dst_size = dfs.hash_file(tmp, uncached=True, progress=self._tick,
                                                 cancel=self.cancel)
            if dst_size != written or dst_digest != src_digest:
                raise VerificationError(
                    f"Verification failed: copy hashes {dst_digest[:16]}..., "
                    f"source {src_digest[:16]}...")

            # 4. re-read the source
            if self.opts.reverify_source:
                self._phase = "Re-checking source"
                again, _ = sfs.hash_file(rel, uncached=True, progress=self._tick,
                                         cancel=self.cancel)
                if again != src_digest:
                    raise VerificationError("Source returned different data on a second read "
                                            "(failing drive or cable?)")

            # 5. attributes, archive old version, atomic rename
            dfs.set_attrs(tmp, entry.mtime, entry.mode)
            existing = dfs.stat(rel)
            if existing is not None and (existing.is_dir or self.opts.archive):
                self._discard(dst, rel)
            dfs.replace(tmp, rel)
            ok = True
        finally:
            if not ok:
                dfs.remove_temp(tmp)

        final = dfs.stat(rel)
        if final is None or final.size != entry.size:
            raise SyncError("File missing or wrong size after rename")
        return src_digest, final
