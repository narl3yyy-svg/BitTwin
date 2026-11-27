"""Local filesystem backend (internal disks, USB drives, mounted shares)."""

from __future__ import annotations

import errno
import fnmatch
import hashlib
import os
import stat
from typing import BinaryIO, Callable, Optional

from .model import Cancelled, Entry, TEMP_SUFFIX, UnsafePath

CHUNK = 1024 * 1024  # 1 MiB

ProgressCB = Optional[Callable[[int], None]]
CancelCB = Optional[Callable[[], bool]]


def is_excluded(rel: str, name: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatchcase(name, pat) or fnmatch.fnmatchcase(rel, pat):
            return True
    return False


def is_safe_name(name: str) -> bool:
    """A single path component that cannot climb out of its folder."""
    return bool(name) and name not in (".", "..") and "/" not in name and "\0" not in name


def validate_rel(rel: str) -> None:
    """Refuse relative paths that are absolute or contain '.', '..' or empty parts."""
    if rel == "":
        return
    if rel.startswith("/") or not all(is_safe_name(p) for p in rel.split("/")):
        raise UnsafePath(f"Refusing unsafe path: {rel!r}")


def ancestors(rel: str):
    """'a/b/c' -> 'a', 'a/b' (the folders a write to rel passes through)."""
    parts = rel.split("/")[:-1]
    cur = ""
    for p in parts:
        cur = join_rel(cur, p)
        yield cur


def join_rel(parent: str, name: str) -> str:
    return f"{parent}/{name}" if parent else name


def temp_name_for(rel: str) -> str:
    head, _, tail = rel.rpartition("/")
    return join_rel(head, f".{tail}{TEMP_SUFFIX}")


def drop_cache(fd: int) -> None:
    """Ask the kernel to forget cached pages so the next read hits the media."""
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except (AttributeError, OSError):
        pass


class LocalFS:
    kind = "local"

    def __init__(self, root: str):
        self.root = os.path.abspath(os.path.expanduser(root))
        self._safe_dirs: set[str] = set()

    # ----------------------------------------------------------------- info
    @property
    def display(self) -> str:
        return self.root

    @property
    def uri(self) -> str:
        return "file://" + self.root

    def _p(self, rel: str) -> str:
        validate_rel(rel)
        return os.path.join(self.root, rel) if rel else self.root

    def _guard(self, rel: str) -> None:
        """Before writing to rel: no folder on the way may be a symbolic link,
        otherwise a write could land outside the chosen folder."""
        validate_rel(rel)
        for d in ancestors(rel):
            if d in self._safe_dirs:
                continue
            try:
                st = os.lstat(os.path.join(self.root, d))
            except (FileNotFoundError, NotADirectoryError):
                return  # the rest does not exist yet; mkdir creates real folders
            if stat.S_ISLNK(st.st_mode):
                raise UnsafePath(f"Refusing to write through the symbolic link '{d}'")
            if not stat.S_ISDIR(st.st_mode):
                return
            self._safe_dirs.add(d)

    def _open_nofollow(self, rel: str) -> BinaryIO:
        fd = os.open(self._p(rel), os.O_RDONLY | os.O_NOFOLLOW)
        return os.fdopen(fd, "rb", buffering=0)

    def check_root(self) -> None:
        if not os.path.isdir(self.root):
            raise FileNotFoundError(f"Folder does not exist: {self.root}")
        if not os.access(self.root, os.R_OK | os.X_OK):
            raise PermissionError(f"Folder is not readable: {self.root}")

    def free_space(self) -> Optional[int]:
        try:
            st = os.statvfs(self.root)
            return st.f_bavail * st.f_frsize
        except OSError:
            return None

    def close(self) -> None:
        pass

    # ----------------------------------------------------------------- scan
    def scan(self, excludes: list[str], cancel: CancelCB = None,
             progress: Optional[Callable[[str], None]] = None,
             start: str = "") -> dict[str, Entry]:
        """Walk the tree below `start` (default: the root). Paths are root-relative."""
        self.check_root()
        result: dict[str, Entry] = {}
        stack = [start]
        while stack:
            parent = stack.pop()
            if cancel and cancel():
                raise Cancelled()
            if progress:
                progress(parent)
            try:
                with os.scandir(self._p(parent)) as it:
                    items = list(it)
            except OSError as exc:
                raise OSError(f"Cannot read folder '{self._p(parent)}': {exc.strerror}") from exc
            for de in items:
                rel = join_rel(parent, de.name)
                if is_excluded(rel, de.name, excludes):
                    continue
                st = de.stat(follow_symlinks=False)
                if stat.S_ISLNK(st.st_mode):
                    result[rel] = Entry(rel, False, 0, st.st_mtime, 0, is_link=True)
                elif stat.S_ISDIR(st.st_mode):
                    result[rel] = Entry(rel, True, 0, st.st_mtime, stat.S_IMODE(st.st_mode))
                    stack.append(rel)
                elif stat.S_ISREG(st.st_mode):
                    result[rel] = Entry(rel, False, st.st_size, st.st_mtime,
                                        stat.S_IMODE(st.st_mode))
                # sockets, fifos and device nodes are ignored
        return result

    def stat(self, rel: str) -> Optional[Entry]:
        try:
            st = os.lstat(self._p(rel))
        except (FileNotFoundError, NotADirectoryError):
            return None
        return Entry(rel, stat.S_ISDIR(st.st_mode), st.st_size, st.st_mtime,
                     stat.S_IMODE(st.st_mode), is_link=stat.S_ISLNK(st.st_mode))

    # ------------------------------------------------------------ reading
    def open_read(self, rel: str) -> BinaryIO:
        return self._open_nofollow(rel)

    def hash_file(self, rel: str, uncached: bool = True, progress: ProgressCB = None,
                  cancel: CancelCB = None) -> tuple[str, int]:
        """SHA-256 of a file read straight from the media. Returns (hex, size)."""
        h = hashlib.sha256()
        total = 0
        with self._open_nofollow(rel) as f:
            if uncached:
                drop_cache(f.fileno())
            while True:
                if cancel and cancel():
                    raise Cancelled()
                buf = f.read(CHUNK)
                if not buf:
                    break
                h.update(buf)
                total += len(buf)
                if progress:
                    progress(len(buf))
            if uncached:
                drop_cache(f.fileno())
        return h.hexdigest(), total

    # ------------------------------------------------------------ writing
    def create_temp(self, rel: str) -> tuple[str, BinaryIO]:
        tmp = temp_name_for(rel)
        self._guard(tmp)
        path = self._p(tmp)
        try:
            os.unlink(path)  # stale temp from an interrupted run
        except FileNotFoundError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        return tmp, os.fdopen(fd, "wb", buffering=0)

    def finish_temp(self, f: BinaryIO) -> None:
        """Flush everything to the physical device before it is verified."""
        try:
            f.flush()
            os.fsync(f.fileno())
            drop_cache(f.fileno())
        finally:
            f.close()

    def set_attrs(self, rel: str, mtime: float, mode: Optional[int]) -> None:
        self._guard(rel)
        path = self._p(rel)
        if os.path.islink(path):
            raise UnsafePath(f"Refusing to change a symbolic link: {rel}")
        if mode is not None:
            try:
                os.chmod(path, mode & 0o7777)
            except OSError:
                pass  # FAT / exFAT / NTFS mounts may refuse; contents matter
        os.utime(path, (mtime, mtime))

    def replace(self, src_rel: str, dst_rel: str) -> None:
        self._guard(src_rel)
        self._guard(dst_rel)
        os.replace(self._p(src_rel), self._p(dst_rel))
        self._fsync_dir(os.path.dirname(self._p(dst_rel)))

    def move(self, src_rel: str, dst_rel: str) -> None:
        """Move within this filesystem, creating parent folders of dst."""
        self._guard(src_rel)
        self._guard(dst_rel)
        dst = self._p(dst_rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(self._p(src_rel), dst)

    def _fsync_dir(self, path: str) -> None:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def mkdir(self, rel: str) -> None:
        self._guard(rel + "/x")  # every folder up to and including rel
        os.makedirs(self._p(rel), exist_ok=True)

    def remove(self, rel: str) -> None:
        self._guard(rel)
        os.unlink(self._p(rel))

    def remove_temp(self, rel: str) -> None:
        try:
            os.unlink(self._p(rel))
        except OSError:
            pass

    def rmdir(self, rel: str) -> bool:
        """Remove an empty directory. Returns False if it is not empty."""
        self._guard(rel)
        try:
            os.rmdir(self._p(rel))
            return True
        except OSError as exc:
            if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                return False
            raise
