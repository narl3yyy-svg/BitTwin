"""Saving SFTP passphrases: only ever in a secure desktop password store
(KDE Wallet / GNOME Keyring), never in BitTwin's own files."""

import json
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_engine  # noqa: E402,F401  (temp XDG folders)

try:
    import keyring
    from keyring.backend import KeyringBackend
    HAVE_KEYRING = True
except ImportError:  # pragma: no cover
    HAVE_KEYRING = False
    KeyringBackend = object

from bittwin.core import secrets  # noqa: E402
from bittwin.core.fs_sftp import SFTPConfig  # noqa: E402


class MemoryVault(KeyringBackend):
    """Stand-in for KDE Wallet during tests."""
    priority = 10

    def __init__(self):
        super().__init__()
        self.store = {}

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


class PlaintextKeyring(MemoryVault):
    """Name matches an insecure file-based backend - must be refused."""


def demo_cfg(**kw):
    base = dict(host="sftp.example.com", port=22, username="demo", auth="key",
                key_path="~/.ssh/id_demo", passphrase="", remote_path="/backup")
    base.update(kw)
    return SFTPConfig(**base)


@unittest.skipUnless(HAVE_KEYRING, "python3-keyring not installed")
class SecretStoreTests(unittest.TestCase):
    def setUp(self):
        self._old = keyring.get_keyring()
        self.vault = MemoryVault()
        keyring.set_keyring(self.vault)

    def tearDown(self):
        keyring.set_keyring(self._old)

    def test_save_load_forget(self):
        cfg = demo_cfg()
        self.assertTrue(secrets.available())
        self.assertTrue(secrets.save(cfg, "s3cret phrase"))
        self.assertEqual(secrets.load(cfg), "s3cret phrase")
        # a different account or key file is a different secret
        self.assertIsNone(secrets.load(demo_cfg(username="other")))
        self.assertIsNone(secrets.load(demo_cfg(key_path="~/.ssh/other")))
        secrets.forget(cfg)
        self.assertIsNone(secrets.load(cfg))

    def test_fill_in_only_when_chosen(self):
        cfg = demo_cfg()
        secrets.save(cfg, "pw1")
        secrets.fill_in(cfg)
        self.assertEqual(cfg.passphrase, "")          # not opted in
        cfg.remember_secret = True
        secrets.fill_in(cfg)
        self.assertEqual(cfg.passphrase, "pw1")

    def test_secret_never_in_saved_settings(self):
        cfg = demo_cfg(passphrase="do-not-store", password="nor-this", remember_secret=True)
        text = json.dumps(cfg.public_dict())
        self.assertNotIn("do-not-store", text)
        self.assertNotIn("nor-this", text)
        self.assertIn('"remember_secret": true', text)

    def test_insecure_store_is_refused(self):
        keyring.set_keyring(PlaintextKeyring())
        self.assertFalse(secrets.available())
        self.assertFalse(secrets.save(demo_cfg(), "x"))
        from keyring.backends import fail
        keyring.set_keyring(fail.Keyring())
        self.assertFalse(secrets.available())

    def test_dialog_prefills_and_forgets(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
        from bittwin.gui.dialogs import SFTPDialog
        cfg = demo_cfg(remember_secret=True)
        secrets.save(cfg, "from-wallet")
        saved = [cfg.public_dict()]
        dlg = SFTPDialog(None, saved)
        self.assertEqual(dlg.passphrase.text(), "from-wallet")
        self.assertTrue(dlg.save_secret.isChecked())
        self.assertIn("encrypted", dlg.save_secret.text())
        # untick and accept -> removed from the wallet
        dlg.save_secret.setChecked(False)
        dlg._accept_ok()
        self.assertIsNone(secrets.load(cfg))
        self.assertFalse(dlg.result_cfg.remember_secret)

    def test_dialog_saves_on_accept(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
        from bittwin.gui.dialogs import SFTPDialog
        dlg = SFTPDialog(None, [])
        dlg.host.setText("sftp.example.com")
        dlg.user.setText("demo")
        dlg.key.setText("~/.ssh/id_demo")
        dlg.passphrase.setText("typed-pass")
        dlg.save_secret.setChecked(True)
        dlg._accept_ok()
        self.assertEqual(secrets.load(dlg.result_cfg), "typed-pass")


if __name__ == "__main__":
    unittest.main()
