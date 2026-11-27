"""SFTP backend tests against an in-process SFTP server with a
passphrase-protected Ed25519 key - the same way a real server is used."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_engine  # noqa: E402,F401  (sets XDG dirs to a temp folder)
from test_engine import readb, tree, write  # noqa: E402

import paramiko  # noqa: E402

from bittwin.core import session  # noqa: E402
from bittwin.core.fs_local import LocalFS  # noqa: E402
from bittwin.core.fs_sftp import (HostKeyChanged, SFTPConfig, SFTPFS,  # noqa: E402
                                  UnknownHostKey, known_hosts_path, load_private_key,
                                  trust_host_key)
from bittwin.core.model import Action, CompareMethod, Direction, Options, SyncMode  # noqa: E402
from sftp_server import TestSFTPServer  # noqa: E402

PASS = "correct horse battery staple"


@unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen not installed")
class SFTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bittwin-sftp-")
        cls.key = os.path.join(cls.tmp, "id_ed25519")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", PASS, "-f", cls.key],
                       check=True)
        pub = load_private_key(cls.key, PASS)
        cls.remote_root = os.path.join(cls.tmp, "server")
        os.makedirs(os.path.join(cls.remote_root, "data"))
        cls.server = TestSFTPServer(cls.remote_root, pub)
        if os.path.exists(known_hosts_path()):
            os.remove(known_hosts_path())

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.L = tempfile.mkdtemp(prefix="local-", dir=self.tmp)
        self.R = os.path.join(self.remote_root, "data")
        shutil.rmtree(self.R)
        os.makedirs(self.R)

    def cfg(self, **kw):
        base = dict(host="127.0.0.1", port=self.server.port, username="tester",
                    auth="key", key_path=self.key, passphrase=PASS, remote_path="/data")
        base.update(kw)
        return SFTPConfig(**base)

    def open_remote(self):
        try:
            return SFTPFS(self.cfg())
        except UnknownHostKey as e:
            trust_host_key(e.hostname, e.key)
            return SFTPFS(self.cfg())

    def test_0_host_key_is_confirmed_first(self):
        if os.path.exists(known_hosts_path()):
            os.remove(known_hosts_path())
        with self.assertRaises(UnknownHostKey) as cm:
            SFTPFS(self.cfg())
        self.assertTrue(cm.exception.fingerprint.startswith("SHA256:"))
        trust_host_key(cm.exception.hostname, cm.exception.key)
        SFTPFS(self.cfg()).close()

    def test_1_wrong_passphrase(self):
        with self.assertRaises(PermissionError):
            SFTPFS(self.cfg(passphrase="nope"))
        with self.assertRaises(PermissionError):
            SFTPFS(self.cfg(passphrase=""))

    def test_2_mirror_local_to_sftp_and_back(self):
        old = time.time() - 3600
        write(self.L, "doc.txt", b"hello sftp", old)
        write(self.L, "deep/a/b/c.bin", os.urandom(3 * 1024 * 1024 + 7), old)
        write(self.L, "empty", b"", old)
        write(self.L, "spaces & ünïcode/文件.txt", b"x" * 1000, old)
        write(self.R, "stale.txt", b"remove")
        rfs = self.open_remote()
        lfs = LocalFS(self.L)
        opts = Options(mode=SyncMode.MIRROR)
        items = session.compare(lfs, rfs, opts)
        s = session.synchronize(lfs, rfs, opts, items)
        self.assertEqual(s.failed, 0, s.errors)
        self.assertEqual(tree(self.L), tree(self.R))
        # whole-second SFTP timestamps still compare equal afterwards
        again = session.compare(lfs, rfs, opts)
        self.assertEqual([i.rel for i in again if i.action != Action.NONE], [])
        content = session.compare(lfs, rfs, Options(compare=CompareMethod.CONTENT))
        self.assertEqual([i.rel for i in content if i.action != Action.NONE], [])

        # pull back down into an empty local folder
        down = tempfile.mkdtemp(prefix="down-", dir=self.tmp)
        lfs2 = LocalFS(down)
        opts = Options(mode=SyncMode.MIRROR, direction=Direction.RIGHT_TO_LEFT)
        s = session.synchronize(lfs2, rfs, opts, session.compare(lfs2, rfs, opts))
        self.assertEqual(s.failed, 0, s.errors)
        self.assertEqual(tree(down), tree(self.L))
        rfs.close()

    def test_3_two_way_with_sftp(self):
        old = time.time() - 9000
        write(self.L, "left.txt", b"L", old)
        write(self.R, "right.txt", b"R", old)
        rfs, lfs = self.open_remote(), LocalFS(self.L)
        opts = Options(mode=SyncMode.TWO_WAY)
        session.synchronize(lfs, rfs, opts, session.compare(lfs, rfs, opts))
        os.remove(os.path.join(self.R, "left.txt"))
        write(self.R, "right.txt", b"R changed", old + 100)
        s = session.synchronize(lfs, rfs, opts, session.compare(lfs, rfs, opts))
        self.assertEqual(s.failed, 0, s.errors)
        self.assertFalse(os.path.exists(os.path.join(self.L, "left.txt")))
        self.assertEqual(readb(os.path.join(self.L, "right.txt")), b"R changed")
        rfs.close()

    def test_4_changed_host_key_is_refused(self):
        self.open_remote().close()
        hk = paramiko.HostKeys(known_hosts_path())
        name = f"[127.0.0.1]:{self.server.port}"
        hk.add(name, "ssh-rsa", paramiko.RSAKey.generate(2048))
        hk.save(known_hosts_path())
        try:
            with self.assertRaises(HostKeyChanged):
                SFTPFS(self.cfg())
        finally:
            os.remove(known_hosts_path())


if __name__ == "__main__":
    unittest.main()
