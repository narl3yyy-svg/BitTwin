"""Security tests: BitTwin must never read or write outside the chosen folders,
even when a server or a folder tries to trick it."""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_engine  # noqa: E402,F401  (temp XDG folders)
from test_engine import readb, write  # noqa: E402

import paramiko  # noqa: E402
from paramiko import SFTPAttributes  # noqa: E402

from bittwin.core import session  # noqa: E402
from bittwin.core.fs_local import LocalFS, validate_rel  # noqa: E402
from bittwin.core.fs_sftp import (SFTPConfig, SFTPFS, UnknownHostKey,  # noqa: E402
                                  load_private_key, trust_host_key)
from bittwin.core.log import logs_dir, state_dir  # noqa: E402
from bittwin.core.model import Direction, Options, SyncMode, UnsafePath  # noqa: E402
from sftp_server import TestSFTPServer, _SFTP  # noqa: E402


class PathValidationTests(unittest.TestCase):
    def test_rejects_escaping_paths(self):
        for bad in ("..", "../x", "a/../../b", "/etc/passwd", "a//b", "a/./b", "a\0b", "./a"):
            with self.assertRaises(UnsafePath, msg=bad):
                validate_rel(bad)
        for good in ("a", "a/b c/d.txt", "..hidden", "dots...", "ünïcode/文件"):
            validate_rel(good)

    def test_backend_refuses_escaping_paths(self):
        tmp = tempfile.mkdtemp()
        try:
            fs = LocalFS(os.path.join(tmp, "root"))
            os.makedirs(fs.root)
            for op in (lambda: fs.create_temp("../evil"), lambda: fs.remove("../evil"),
                       lambda: fs.mkdir("a/../../evil"), lambda: fs.open_read("../evil"),
                       lambda: fs.move("x", "../evil")):
                with self.assertRaises(UnsafePath):
                    op()
            self.assertEqual(os.listdir(tmp), ["root"])
        finally:
            shutil.rmtree(tmp)


class SymlinkEscapeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bittwin-sec-")
        self.L = os.path.join(self.tmp, "left")
        self.R = os.path.join(self.tmp, "right")
        self.OUT = os.path.join(self.tmp, "outside")  # stands in for e.g. ~/.ssh
        for d in (self.L, self.R, self.OUT):
            os.makedirs(d)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sync(self, **kw):
        opts = Options(**kw)
        lfs, rfs = LocalFS(self.L), LocalFS(self.R)
        return session.synchronize(lfs, rfs, opts, session.compare(lfs, rfs, opts))

    def test_symlinked_folder_in_destination(self):
        os.symlink(self.OUT, os.path.join(self.R, "docs"))
        write(self.L, "docs/secret.txt", b"must not leave the sync folder")
        write(self.L, "ok.txt", b"fine")
        s = self.sync(mode=SyncMode.MIRROR)
        self.assertEqual(os.listdir(self.OUT), [])            # nothing written outside
        self.assertTrue(os.path.islink(os.path.join(self.R, "docs")))  # link untouched
        self.assertEqual(readb(os.path.join(self.R, "ok.txt")), b"fine")
        self.assertEqual(s.failed, 1)
        self.assertIn("symbolic link", s.errors[0][1])

    def test_symlinked_folder_deeper_in_destination(self):
        os.makedirs(os.path.join(self.R, "a"))
        os.symlink(self.OUT, os.path.join(self.R, "a", "b"))
        write(self.L, "a/b/c/d/file.txt", b"x")
        self.sync(mode=SyncMode.MIRROR)
        self.assertEqual(os.listdir(self.OUT), [])

    def test_symlink_in_source_is_not_followed(self):
        write(self.OUT, "private.key", b"secret")
        os.symlink(self.OUT, os.path.join(self.L, "shortcut"))
        os.symlink(os.path.join(self.OUT, "private.key"), os.path.join(self.L, "key.txt"))
        self.sync(mode=SyncMode.MIRROR)
        self.assertEqual(os.listdir(self.R), [])


class PermissionTests(unittest.TestCase):
    def test_logs_and_history_are_private(self):
        tmp = tempfile.mkdtemp()
        try:
            L, R = os.path.join(tmp, "l"), os.path.join(tmp, "r")
            os.makedirs(R)
            write(L, "f.txt", b"x")
            opts = Options()
            lfs, rfs = LocalFS(L), LocalFS(R)
            s = session.synchronize(lfs, rfs, opts, session.compare(lfs, rfs, opts))
            mode = lambda p: stat.S_IMODE(os.stat(p).st_mode)  # noqa: E731
            self.assertEqual(mode(s.log_path), 0o600)
            self.assertEqual(mode(logs_dir()), 0o700)
            self.assertEqual(mode(state_dir()), 0o700)
            pairs = os.path.join(state_dir(), "pairs")
            for f in os.listdir(pairs):
                self.assertEqual(mode(os.path.join(pairs, f)), 0o600)
        finally:
            shutil.rmtree(tmp)


class _EvilSFTP(_SFTP):
    """A malicious server that lists names designed to escape the folder."""

    def list_folder(self, path):
        out = super().list_folder(path)
        if isinstance(out, list):
            for evil in ("../escaped.txt", "sub/../../escaped2.txt", "..", "."):
                a = SFTPAttributes()
                a.filename = evil
                a.st_mode = stat.S_IFREG | 0o644
                a.st_size = 5
                a.st_mtime = int(time.time())
                out.append(a)
        return out


@unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen not installed")
class MaliciousServerTests(unittest.TestCase):
    def test_server_cannot_escape_local_folder(self):
        tmp = tempfile.mkdtemp(prefix="bittwin-evil-")
        try:
            key = os.path.join(tmp, "k")
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "pw", "-f", key], check=True)
            os.makedirs(os.path.join(tmp, "srv", "data"))
            write(os.path.join(tmp, "srv", "data"), "real.txt", b"real")
            srv = TestSFTPServer(os.path.join(tmp, "srv"), load_private_key(key, "pw"),
                                 sftp_cls=_EvilSFTP)
            cfg = SFTPConfig("127.0.0.1", srv.port, "tester", "key", key, "pw", "", "/data")
            try:
                rfs = SFTPFS(cfg)
            except UnknownHostKey as e:
                trust_host_key(e.hostname, e.key)
                rfs = SFTPFS(cfg)
            local = os.path.join(tmp, "local", "inbox")
            os.makedirs(local)
            lfs = LocalFS(local)
            opts = Options(mode=SyncMode.MIRROR, direction=Direction.RIGHT_TO_LEFT)
            s = session.synchronize(lfs, rfs, opts, session.compare(lfs, rfs, opts))
            rfs.close()
            srv.stop()
            self.assertEqual(s.failed, 0, s.errors)
            self.assertEqual(os.listdir(local), ["real.txt"])
            self.assertEqual(sorted(os.listdir(os.path.join(tmp, "local"))), ["inbox"])
            self.assertEqual(len(rfs.rejected), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen not installed")
class KeyLoadingTests(unittest.TestCase):
    """The fallback used with older Paramiko (e.g. Ubuntu 24.04)."""

    def test_fallback_loader(self):
        tmp = tempfile.mkdtemp()
        try:
            key = os.path.join(tmp, "k")
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "pw", "-f", key], check=True)
            with mock.patch.object(paramiko.PKey, "from_path", None, create=True):
                self.assertIsInstance(load_private_key(key, "pw"), paramiko.PKey)
                with self.assertRaises(PermissionError):
                    load_private_key(key, "wrong")
                with self.assertRaises(PermissionError):
                    load_private_key(key, "")
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
