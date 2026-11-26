"""Apertura desde el sistema e instancia única, con catálogo temporal."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication
from desktop_runtime import DesktopInstance, desktop_data_dir


class DesktopRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_frozen_catalog_uses_user_data_and_source_catalog_stays_local(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/arandu-xdg-data"}, clear=True):
            self.assertEqual(desktop_data_dir("/tmp/source", True), Path("/tmp/arandu-xdg-data/arandu"))
            self.assertEqual(desktop_data_dir("/tmp/source", False), Path("/tmp/source/.arandu"))
        with patch.dict(os.environ, {"ARANDU_DATA_DIR": "/tmp/arandu-specific"}):
            self.assertEqual(desktop_data_dir("/tmp/source", True), Path("/tmp/arandu-specific"))

    def test_secondary_launch_forwards_multiple_paths_to_same_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            primary = DesktopInstance(folder, self.app)
            secondary = DesktopInstance(folder, self.app)
            received = []
            primary.requested.connect(received.append)
            primary.listen()
            self.assertTrue(secondary.forward(["/tmp/Un libro.pdf", "/tmp/Otra lectura.epub"]))
            for _ in range(4):
                self.app.processEvents()
            self.assertEqual(received, [["/tmp/Un libro.pdf", "/tmp/Otra lectura.epub"]])
            primary.server.close()
            primary.deleteLater()
            secondary.deleteLater()
