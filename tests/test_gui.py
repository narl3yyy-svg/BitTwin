"""Smoke test of the real window (headless) plus checks that file names are
always shown as plain text, never interpreted as HTML."""

import os
import shutil
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_engine  # noqa: E402,F401  (temp XDG folders, so real settings are untouched)
from test_engine import write  # noqa: E402

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PyQt6 not installed")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)
        from bittwin.gui import theme
        theme.apply(cls.app, "dark")

    def test_file_names_are_escaped(self):
        from bittwin.core.model import Action, Entry, PlanItem, Status
        from bittwin.gui.plan_model import PlanModel
        evil = "<img src='x'><b>bold</b>.txt"
        m = PlanModel()
        m.set_items([PlanItem(evil, Entry(evil, False, 1, 0), None, Status.LEFT_ONLY,
                              Action.COPY_LTR, note="<i>n</i>", error="<u>e</u>")])
        tip = m.data(m.index(0, 0), Qt.ItemDataRole.ToolTipRole)
        self.assertNotIn("<img", tip)
        self.assertIn("&lt;img", tip)
        self.assertNotIn("<u>", tip)

    def test_window_compares_and_syncs(self):
        import bittwin.gui.main_window as mw
        tmp = tempfile.mkdtemp()
        try:
            L, R = os.path.join(tmp, "L"), os.path.join(tmp, "R")
            os.makedirs(R)
            write(L, "a/b.txt", b"hello")
            write(L, "<b>tricky<i>.txt", b"x")
            win = mw.MainWindow()
            win.left_panel.set_local(L)
            win.right_panel.set_local(R)
            self.assertEqual(win.left_panel.path_label.textFormat(), Qt.TextFormat.PlainText)
            win.compare()
            deadline = time.time() + 20
            while win.worker is not None and time.time() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertIsNone(win.worker)
            self.assertTrue(win.sync_btn.isEnabled())
            self.assertEqual(sorted(i.rel for i in win.items), ["<b>tricky<i>.txt", "a", "a/b.txt"])
            win.close()
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
