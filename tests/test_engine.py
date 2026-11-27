"""End-to-end tests for the BitTwin engine on real local folders.

Run with:  python3 -m unittest discover -s tests -v
"""

import hashlib
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_STATE = tempfile.mkdtemp(prefix="bittwin-state-")
os.environ["XDG_STATE_HOME"] = _STATE
os.environ["XDG_CONFIG_HOME"] = _STATE

from bittwin.core import session  # noqa: E402
from bittwin.core.fs_local import LocalFS  # noqa: E402
from bittwin.core.model import (ARCHIVE_DIR, TEMP_SUFFIX, Action, CompareMethod,  # noqa: E402
                                Direction, Options, Status, SyncMode)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def readb(path):
    with open(path, "rb") as f:
        return f.read()


def tree(root, with_hash=True):
    """{rel: sha256 or 'DIR'} for every item, ignoring BitTwin's own files."""
    out = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d != ARCHIVE_DIR]
        for d in dns:
            out[os.path.relpath(os.path.join(dp, d), root)] = "DIR"
        for fn in fns:
            if fn.endswith(TEMP_SUFFIX):
                continue
            p = os.path.join(dp, fn)
            out[os.path.relpath(p, root)] = sha(p) if with_hash else None
    return out


def write(root, rel, data, mtime=None):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bittwin-test-")
        self.L = os.path.join(self.tmp, "left")
        self.R = os.path.join(self.tmp, "right")
        os.makedirs(self.L)
        os.makedirs(self.R)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_sync(self, **kw):
        opts = Options(**kw)
        lfs, rfs = LocalFS(self.L), LocalFS(self.R)
        items = session.compare(lfs, rfs, opts)
        summary = session.synchronize(lfs, rfs, opts, items)
        return items, summary

    def plan(self, **kw):
        opts = Options(**kw)
        return session.compare(LocalFS(self.L), LocalFS(self.R), opts)

    def pending(self, **kw):
        return [i for i in self.plan(**kw) if i.action not in (Action.NONE, Action.SKIP)]

    def populate(self):
        old = time.time() - 3600
        write(self.L, "a.txt", b"hello world\n", old)
        write(self.L, "empty.bin", b"", old)
        write(self.L, "dir one/nested/ünïcödé 文件.dat", os.urandom(300_000), old)
        write(self.L, "dir one/big.bin", os.urandom(5 * 1024 * 1024 + 123), old)
        os.makedirs(os.path.join(self.L, "empty dir/deeper"))


class MirrorTests(Base):
    def test_mirror_bit_for_bit(self):
        self.populate()
        write(self.R, "stale.txt", b"remove me")
        write(self.R, "old dir/x.txt", b"remove me too")
        items, s = self.run_sync(mode=SyncMode.MIRROR)
        self.assertEqual(s.failed, 0, s.errors)
        self.assertEqual(tree(self.L), tree(self.R))
        self.assertEqual(s.copied, s.verified)
        # modification times are preserved
        for rel in ("a.txt", "dir one/big.bin"):
            self.assertAlmostEqual(os.stat(os.path.join(self.L, rel)).st_mtime,
                                   os.stat(os.path.join(self.R, rel)).st_mtime, places=3)
        # removed files went to the archive, not into the void
        arch = os.path.join(self.R, ARCHIVE_DIR)
        found = [f for _, _, fs in os.walk(arch) for f in fs]
        self.assertIn("stale.txt", found)
        self.assertIn("x.txt", found)
        # nothing left to do afterwards, in either compare mode
        self.assertEqual(self.pending(mode=SyncMode.MIRROR), [])
        self.assertEqual(self.pending(mode=SyncMode.MIRROR, compare=CompareMethod.CONTENT), [])

    def test_mirror_no_archive_really_deletes(self):
        write(self.L, "keep.txt", b"1")
        write(self.R, "gone/deep/file.txt", b"2")
        _, s = self.run_sync(mode=SyncMode.MIRROR, archive=False)
        self.assertEqual(s.failed, 0, s.errors)
        self.assertEqual(sorted(os.listdir(self.R)), ["keep.txt"])

    def test_mirror_overwrite_archives_old_version(self):
        write(self.L, "doc.txt", b"new version", time.time())
        write(self.R, "doc.txt", b"old version", time.time() - 500)
        _, s = self.run_sync(mode=SyncMode.MIRROR)
        self.assertEqual(readb(os.path.join(self.R, "doc.txt")), b"new version")
        arch = [os.path.join(dp, f) for dp, _, fs in os.walk(os.path.join(self.R, ARCHIVE_DIR))
                for f in fs]
        self.assertEqual(len(arch), 1)
        self.assertEqual(readb(arch[0]), b"old version")

    def test_mirror_right_to_left(self):
        self.populate()
        shutil.rmtree(self.R)
        shutil.copytree(self.L, self.R)
        shutil.rmtree(self.L)
        os.makedirs(self.L)
        _, s = self.run_sync(mode=SyncMode.MIRROR, direction=Direction.RIGHT_TO_LEFT)
        self.assertEqual(s.failed, 0, s.errors)
        self.assertEqual(tree(self.L), tree(self.R))

    def test_content_compare_finds_silent_corruption(self):
        t = time.time() - 100
        data = bytearray(os.urandom(100_000))
        write(self.L, "photo.raw", bytes(data), t)
        data[54_321] ^= 0x01  # flip a single bit, keep size and time identical
        write(self.R, "photo.raw", bytes(data), t)
        self.assertEqual(self.pending(mode=SyncMode.MIRROR), [])  # fast mode can't see it
        items = self.pending(mode=SyncMode.MIRROR, compare=CompareMethod.CONTENT)
        self.assertEqual(len(items), 1)
        self.run_sync(mode=SyncMode.MIRROR, compare=CompareMethod.CONTENT)
        self.assertEqual(sha(os.path.join(self.L, "photo.raw")),
                         sha(os.path.join(self.R, "photo.raw")))

    def test_type_mismatch(self):
        write(self.L, "thing", b"i am a file")
        write(self.R, "thing/inner.txt", b"i am in a folder")
        write(self.L, "other/inside.txt", b"folder on left")
        write(self.R, "other", b"file on right")
        _, s = self.run_sync(mode=SyncMode.MIRROR)
        self.assertEqual(s.failed, 0, s.errors)
        self.assertEqual(tree(self.L), tree(self.R))

    def test_symlinks_are_skipped(self):
        write(self.L, "real.txt", b"x")
        os.symlink("real.txt", os.path.join(self.L, "link.txt"))
        items, s = self.run_sync(mode=SyncMode.MIRROR)
        link = [i for i in items if i.rel == "link.txt"][0]
        self.assertEqual(link.status, Status.SYMLINK)
        self.assertFalse(os.path.lexists(os.path.join(self.R, "link.txt")))

    def test_excludes(self):
        write(self.L, "keep.txt", b"1")
        write(self.L, "cache/junk.tmp", b"2")
        write(self.L, "node.log", b"3")
        self.run_sync(mode=SyncMode.MIRROR, excludes=["cache", "*.log"])
        self.assertEqual(sorted(os.listdir(self.R)), ["keep.txt"])


class UpdateTests(Base):
    def test_update_never_deletes_or_downgrades(self):
        now = time.time()
        write(self.L, "new.txt", b"new")
        write(self.L, "changed.txt", b"left newer", now)
        write(self.R, "changed.txt", b"right older", now - 1000)
        write(self.L, "keep.txt", b"left older", now - 1000)
        write(self.R, "keep.txt", b"right newer", now)
        write(self.R, "only-right.txt", b"stays")
        items, s = self.run_sync(mode=SyncMode.UPDATE)
        rd = lambda r: readb(os.path.join(self.R, r))
        self.assertEqual(rd("new.txt"), b"new")
        self.assertEqual(rd("changed.txt"), b"left newer")
        self.assertEqual(rd("keep.txt"), b"right newer")
        self.assertEqual(rd("only-right.txt"), b"stays")
        self.assertEqual(s.deleted, 0)


class TwoWayTests(Base):
    def test_two_way_lifecycle(self):
        old = time.time() - 5000
        write(self.L, "shared.txt", b"v1", old)
        write(self.L, "left-new.txt", b"L", old)
        write(self.R, "right-new.txt", b"R", old)
        _, s = self.run_sync(mode=SyncMode.TWO_WAY)
        self.assertEqual(s.failed, 0, s.errors)
        self.assertEqual(tree(self.L), tree(self.R))
        self.assertEqual(self.pending(mode=SyncMode.TWO_WAY), [])

        # change on left, delete on right, new folder on right
        write(self.L, "shared.txt", b"v2 from left", old + 100)
        os.remove(os.path.join(self.R, "left-new.txt"))
        write(self.R, "photos/p1.jpg", os.urandom(20_000), old)
        items, s = self.run_sync(mode=SyncMode.TWO_WAY)
        self.assertEqual(s.failed, 0, s.errors)
        self.assertEqual(tree(self.L), tree(self.R))
        self.assertEqual(readb(os.path.join(self.R, "shared.txt")), b"v2 from left")
        self.assertFalse(os.path.exists(os.path.join(self.L, "left-new.txt")))

        # conflict: both sides edited - newer wins, older is archived
        write(self.L, "shared.txt", b"left edit", old + 200)
        write(self.R, "shared.txt", b"right edit (newer)", old + 300)
        items = self.plan(mode=SyncMode.TWO_WAY)
        c = [i for i in items if i.rel == "shared.txt"][0]
        self.assertEqual(c.status, Status.CONFLICT)
        self.assertEqual(c.action, Action.COPY_RTL)
        self.run_sync(mode=SyncMode.TWO_WAY)
        self.assertEqual(readb(os.path.join(self.L, "shared.txt")),
                         b"right edit (newer)")

        # folder deleted on one side propagates
        shutil.rmtree(os.path.join(self.R, "photos"))
        self.run_sync(mode=SyncMode.TWO_WAY)
        self.assertFalse(os.path.exists(os.path.join(self.L, "photos")))
        self.assertEqual(tree(self.L), tree(self.R))

    def test_two_way_without_history_never_deletes(self):
        write(self.L, "a.txt", b"a")
        write(self.R, "b.txt", b"b")
        _, s = self.run_sync(mode=SyncMode.TWO_WAY)
        self.assertEqual(s.deleted, 0)
        self.assertEqual(sorted(os.listdir(self.L)), ["a.txt", "b.txt"])


class SafetyTests(Base):
    def test_verification_failure_retries_then_succeeds(self):
        write(self.L, "f.bin", os.urandom(50_000))
        calls = {"n": 0}
        orig = LocalFS.hash_file

        def flaky(self_, rel, **kw):
            d, n = orig(self_, rel, **kw)
            if rel.endswith(TEMP_SUFFIX):
                calls["n"] += 1
                if calls["n"] == 1:
                    return "0" * 64, n  # pretend the first copy came back corrupt
            return d, n

        LocalFS.hash_file = flaky
        try:
            _, s = self.run_sync(mode=SyncMode.MIRROR)
        finally:
            LocalFS.hash_file = orig
        self.assertEqual(calls["n"], 2)
        self.assertEqual(s.failed, 0)
        self.assertEqual(sha(os.path.join(self.L, "f.bin")), sha(os.path.join(self.R, "f.bin")))

    def test_persistent_corruption_never_replaces_good_file(self):
        write(self.L, "f.bin", b"new data", time.time())
        write(self.R, "f.bin", b"precious old data", time.time() - 999)
        orig = LocalFS.hash_file

        def broken(self_, rel, **kw):
            d, n = orig(self_, rel, **kw)
            return ("f" * 64, n) if rel.endswith(TEMP_SUFFIX) else (d, n)

        LocalFS.hash_file = broken
        try:
            _, s = self.run_sync(mode=SyncMode.MIRROR)
        finally:
            LocalFS.hash_file = orig
        self.assertEqual(s.failed, 1)
        self.assertEqual(readb(os.path.join(self.R, "f.bin")), b"precious old data")
        self.assertEqual(os.listdir(self.R), ["f.bin"])  # temp file cleaned up

    def test_source_changed_after_compare(self):
        write(self.L, "f.txt", b"one")
        opts = Options()
        lfs, rfs = LocalFS(self.L), LocalFS(self.R)
        items = session.compare(lfs, rfs, opts)
        write(self.L, "f.txt", b"one two three")
        s = session.synchronize(lfs, rfs, opts, items)
        self.assertEqual(s.failed, 1)
        self.assertFalse(os.path.exists(os.path.join(self.R, "f.txt")))

    def test_cancel(self):
        for i in range(20):
            write(self.L, f"f{i}.bin", os.urandom(10_000))
        opts = Options()
        lfs, rfs = LocalFS(self.L), LocalFS(self.R)
        items = session.compare(lfs, rfs, opts)
        n = {"c": 0}

        def cancel():
            n["c"] += 1
            return n["c"] > 15

        s = session.synchronize(lfs, rfs, opts, items, cancel=cancel)
        self.assertTrue(s.cancelled)
        self.assertFalse([f for f in os.listdir(self.R) if f.endswith(TEMP_SUFFIX)])
        for f in os.listdir(self.R):  # whatever made it across is complete
            self.assertEqual(sha(os.path.join(self.L, f)), sha(os.path.join(self.R, f)))

    def test_log_records_hashes(self):
        write(self.L, "f.txt", b"abc")
        _, s = self.run_sync()
        text = open(s.log_path, encoding="utf-8").read()
        self.assertIn(hashlib.sha256(b"abc").hexdigest(), text)
        self.assertIn("VERIFIED", text)


if __name__ == "__main__":
    unittest.main()
