"""Fuentes parciales, exclusiones sin recorrido y recuperación de fichas ocultas."""

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog

from backups import create_backup, restore_backup
from qt_helpers import drain_qt_events

from desktop import DetailDialog, MainWindow
from library import Library, filter_books
from source_dialogs import HiddenBooksDialog, SourcesDialog
from sources import canonical_path
from test_desktop import make_pdf


def source_fixture(root):
    folder = root / "Libros"
    folder.mkdir()
    for name in ("Novela", "Historia", "Ensayo", "Descartes", "Material ajeno"):
        child = folder / name
        child.mkdir()
        make_pdf(child / (name + ".pdf"))
    keep, drop = folder / "Quiero leer.pdf", folder / "Random.pdf"
    make_pdf(keep)
    make_pdf(drop)
    return folder, keep, drop


def exclusions_for(folder, drop):
    return [{"path": str(folder / "Descartes"), "kind": "folder"}, {"path": str(folder / "Material ajeno"), "kind": "folder"}, {"path": str(drop), "kind": "file"}]


class SourcesStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data")
        self.folder, self.keep, self.drop = source_fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_five_subfolders_and_two_pdfs_only_import_selected_three_and_one(self):
        self.library.configure_sources([str(self.folder)], exclusions_for(self.folder, self.drop))
        visited = []
        original_scandir = os.scandir
        def record(path):
            visited.append(str(path))
            return original_scandir(path)
        with patch("sources.os.scandir", side_effect=record):
            result = self.library.scan([self.folder])
        self.assertEqual((result["added"], result["ignored"], result["errors"]), (4, 3, []))
        self.assertNotIn(str(self.folder / "Descartes"), visited)
        self.assertNotIn(str(self.folder / "Material ajeno"), visited)
        paths = {book["path"] for book in self.library.listing()["books"]}
        self.assertIn(str(self.keep), paths)
        self.assertNotIn(str(self.drop), paths)
        self.assertEqual(len(paths), 4)
        reopened = Library(self.library.data_dir)
        again = reopened.scan([self.folder])
        self.assertEqual((again["added"], again["skipped"], again["ignored"]), (0, 4, 3))
        with patch.object(reopened, "digest", side_effect=AssertionError("No debe leer archivos ignorados")):
            self.assertEqual(reopened.ingest(self.drop), "ignored")
        self.assertTrue(self.drop.is_file())

    def test_directory_exclusions_are_recursive_and_do_not_match_similar_prefixes(self):
        nested = self.folder / "Descartes" / "Mucho más abajo"
        nested.mkdir()
        (nested / "dañado.pdf").write_text("No es un PDF")
        similar = self.folder / "Descartes válidos"
        similar.mkdir()
        make_pdf(similar / "Incluido.pdf")
        self.library.ignore_paths([{"path": str(self.folder / "Descartes"), "kind": "folder"}])
        result = self.library.scan([self.folder, nested])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["added"], 7)
        self.assertFalse(self.library.is_ignored(similar / "Incluido.pdf"))
        self.assertTrue(self.library.is_ignored(nested / "nuevo.pdf"))

    def test_ignored_existing_books_keep_notes_and_return_when_reincluded(self):
        self.library.scan([self.folder])
        book = next(book for book in self.library.listing()["books"] if book["path"] == str(self.drop))
        self.library.update(book["id"], {"notes": "Mi nota", "progress": 43, "tags": ["Única"]})
        self.library.add_annotation(book["id"], 0, "note", note="Anotación")
        self.library.ignore_paths([{"path": str(self.drop), "kind": "file"}])
        self.assertEqual(len(self.library.listing()["books"]), 6)
        self.assertEqual(len(self.library.listing(include_ignored=True)["books"]), 7)
        self.assertEqual(self.library.taxonomy_counts("tags")["Única"], 0)
        self.assertNotIn(book["id"], [hit["book_id"] for hit in self.library.search_text("Primera")])
        self.assertNotIn(book["id"], [entry["id"] for entry in filter_books(self.library.listing(include_ignored=True)["books"])])
        self.library.include_paths([self.drop])
        restored = self.library.get(book["id"])
        self.assertEqual((restored["notes"], restored["progress"]), ("Mi nota", 43))
        self.assertEqual(self.library.annotations(book["id"])[0]["note"], "Anotación")
        self.assertEqual(self.library.taxonomy_counts("tags")["Única"], 1)
        self.assertEqual(len(self.library.listing()["books"]), 7)

    def test_hidden_is_independent_of_ignoring_and_survives_updates(self):
        self.library.scan([self.folder])
        book = next(book for book in self.library.listing()["books"] if book["path"] == str(self.keep))
        self.library.set_hidden([book["id"]])
        self.library.scan([self.folder])
        self.assertEqual(len(self.library.hidden_books()), 1)
        self.assertEqual(len(self.library.listing()["books"]), 6)
        self.assertFalse(self.library.get(book["id"])["ignored"])
        self.assertNotIn(book["id"], [hit["book_id"] for hit in self.library.search_text("Primera")])
        self.assertEqual(len(self.library.search_text("Primera", book["id"])), 1)
        self.library.ignore_paths([{"path": str(self.keep), "kind": "file"}])
        self.library.set_hidden([book["id"]], False)
        self.assertEqual(len(self.library.listing()["books"]), 6)
        self.library.include_paths([self.keep])
        self.assertEqual(len(self.library.listing()["books"]), 7)
        with self.assertRaises(ValueError):
            self.library.set_hidden([book["id"]], "sí")

    def test_reincluded_subfolder_imports_new_files_on_next_scan(self):
        self.library.configure_sources([self.folder], exclusions_for(self.folder, self.drop))
        self.library.scan([self.folder])
        make_pdf(self.folder / "Descartes" / "Ahora sí.pdf")
        self.assertEqual(self.library.scan([self.folder])["added"], 0)
        self.library.include_paths([self.folder / "Descartes"])
        self.assertEqual(self.library.scan([self.folder])["added"], 2)
        self.assertEqual(len(self.library.listing()["books"]), 6)

    def test_configuration_failure_is_atomic_and_missing_ignored_source_is_skipped(self):
        self.library.configure_sources([self.folder], exclusions_for(self.folder, self.drop))
        with self.assertRaises(ValueError):
            self.library.configure_sources([], [{"path": str(self.keep), "kind": "invalid"}])
        self.assertEqual(self.library.listing()["folders"], [str(self.folder)])
        self.assertEqual(len(self.library.exclusions()), 3)
        missing = self.root / "Disco desconectado"
        self.library.ignore_paths([{"path": str(missing), "kind": "folder"}])
        result = self.library.scan([missing])
        self.assertEqual((result["ignored"], result["errors"]), (1, []))

    def test_portable_backup_keeps_hidden_books_and_exclusions(self):
        self.library.scan([self.folder])
        book = next(book for book in self.library.listing()["books"] if book["path"] == str(self.keep))
        self.library.update(book["id"], {"notes": "Conservar", "hidden": True})
        self.library.configure_sources([self.folder], exclusions_for(self.folder, self.drop))
        archive = self.root / "portable.zip"
        create_backup(self.library, archive, True)
        ignored_id = next(book["id"] for book in self.library.listing(include_hidden=True, include_ignored=True)["books"] if book["path"] == str(self.drop))
        self.keep.unlink()
        self.drop.unlink()
        recovered = Library(self.root / "recovered")
        restore_backup(recovered, archive)
        self.assertEqual(len(recovered.exclusions()), 4)
        self.assertTrue(recovered.get(ignored_id)["ignored"])
        self.assertNotIn(ignored_id, [entry["id"] for entry in recovered.listing()["books"]])
        restored = recovered.get(book["id"])
        self.assertTrue(restored["hidden"])
        self.assertEqual(restored["notes"], "Conservar")
        self.assertTrue(restored["available"])
        self.assertNotIn(book["id"], [entry["id"] for entry in recovered.listing()["books"]])
        recovered.set_hidden([book["id"]], False)
        self.assertIn(book["id"], [entry["id"] for entry in recovered.listing()["books"]])


class SourcesWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data")
        self.folder, self.keep, self.drop = source_fixture(self.root)
        self.window = MainWindow(self.library)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.wait(lambda: not self.window.busy)
        self.window.close()
        self.window.deleteLater()
        drain_qt_events(self.app)
        self.temp.cleanup()

    def wait(self, condition, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(20)
        self.fail("La tarea no terminó a tiempo.")

    def screenshot(self, widget, name):
        folder = os.environ.get("ARANDU_TEST_ARTIFACTS")
        if folder:
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            self.app.processEvents()
            self.assertTrue(widget.grab().save(str(target / name)))

    def test_connect_preview_excludes_two_folders_and_one_pdf_before_import(self):
        def configure(dialog):
            dialog.show()
            root_item = dialog.tree.topLevelItem(0)
            self.assertEqual(root_item.childCount(), 7)
            ignored_names = {"Descartes", "Material ajeno", "Random.pdf"}
            for index in range(root_item.childCount()):
                child = root_item.child(index)
                if child.text(0) in ignored_names:
                    child.setSelected(True)
            QTest.mouseClick(dialog.ignore_button, Qt.LeftButton)
            self.assertEqual(len(dialog.exclusions), 3)
            self.assertEqual(self.library.listing()["folders"], [])
            self.screenshot(dialog, "sources-selection.png")
            dialog.save()
            return dialog.result()
        with patch("desktop.QFileDialog.getExistingDirectory", return_value=str(self.folder)), patch.object(SourcesDialog, "exec_", configure):
            QTest.mouseClick(self.window.folder_button, Qt.LeftButton)
        self.wait(lambda: not self.window.busy)
        self.assertEqual(self.window.book_list.count(), 4)
        watched = set(self.window.watcher.directories() + self.window.watcher.files())
        self.assertNotIn(str(self.drop), watched)
        self.assertNotIn(str(self.folder / "Descartes"), watched)
        self.assertNotIn(str(self.folder / "Material ajeno"), watched)
        self.assertIn(str(self.keep), watched)
        self.screenshot(self.window, "sources-filtered-catalog.png")

    def test_cancel_does_not_change_sources_or_exclusions(self):
        dialog = SourcesDialog(self.library, self.window, self.folder)
        dialog.stage_ignored(exclusions_for(self.folder, self.drop))
        dialog.reject()
        self.assertEqual(self.library.exclusions(), [])
        self.assertEqual(self.library.listing()["folders"], [])
        dialog.deleteLater()

    def test_hidden_selection_returns_from_empty_catalog_without_losing_details(self):
        self.library.add(self.keep)
        book_id = self.library.listing()["books"][0]["id"]
        self.library.update(book_id, {"notes": "Para después", "progress": 32})
        self.window.reload()
        self.window.book_list.setCurrentRow(0)
        self.window.hide_selection()
        self.assertEqual(self.window.book_list.count(), 0)
        self.assertEqual(self.window.empty_button.text(), "Revisar libros ocultos")
        def restore(dialog):
            dialog.show()
            dialog.items.selectAll()
            self.screenshot(dialog, "hidden-books.png")
            QTest.mouseClick(dialog.restore_button, Qt.LeftButton)
            return QDialog.Accepted
        with patch.object(HiddenBooksDialog, "exec_", restore):
            QTest.mouseClick(self.window.empty_button, Qt.LeftButton)
        self.assertEqual(self.window.book_list.count(), 1)
        self.assertEqual(self.library.get(book_id)["notes"], "Para después")
        self.assertEqual(self.library.get(book_id)["progress"], 32)

    def test_ignore_selected_then_reinclude_and_synchronize(self):
        self.library.scan([self.folder])
        self.window.reload()
        item = next(self.window.book_list.item(index) for index in range(self.window.book_list.count()) if self.window.book_list.item(index).data(Qt.UserRole)["path"] == str(self.drop))
        self.window.book_list.setCurrentItem(item)
        self.window.ignore_selection()
        self.assertEqual(self.window.book_list.count(), 6)
        self.assertNotIn(str(self.drop), self.window.watcher.files())
        self.window.sync_now()
        self.wait(lambda: not self.window.busy)
        self.assertEqual(self.window.book_list.count(), 6)
        def include(dialog):
            self.window.sync_now()
            self.assertFalse(self.window.busy)
            dialog.tabs.setCurrentIndex(1)
            dialog.ignored.topLevelItem(0).setSelected(True)
            dialog.include_selection()
            dialog.save()
            return dialog.result()
        with patch.object(SourcesDialog, "exec_", include):
            self.window.manage_sources()
        self.wait(lambda: not self.window.busy)
        self.assertEqual(self.window.book_list.count(), 7)
        self.assertIn(str(self.drop), self.window.watcher.files())

    def test_fiche_hidden_checkbox_and_inherited_exclusion_state(self):
        self.library.add(self.keep)
        book = self.library.listing()["books"][0]
        details = DetailDialog(self.library, book, self.window)
        details.hidden_check.setChecked(True)
        details.save()
        self.assertTrue(self.library.get(book["id"])["hidden"])
        self.library.configure_sources([self.folder], [{"path": str(self.folder / "Descartes"), "kind": "folder"}])
        self.window.set_theme("nogal")
        dialog = SourcesDialog(self.library, self.window)
        dialog.show()
        root = dialog.tree.topLevelItem(0)
        excluded = next(root.child(index) for index in range(root.childCount()) if root.child(index).text(0) == "Descartes")
        excluded.setExpanded(True)
        child = excluded.child(0)
        child.setSelected(True)
        self.assertEqual(child.text(1), "Ignorado por carpeta")
        self.assertFalse(dialog.include_button.isEnabled())
        self.assertFalse(dialog.ignore_button.isEnabled())
        self.assertIn("quitá primero", dialog.selection_hint.text())
        dialog.resize(680, 560)
        self.app.processEvents()
        for button in (dialog.ignore_button, dialog.include_button, dialog.connect_button):
            point = button.mapTo(dialog, QPoint(0, 0))
            self.assertLessEqual(point.x() + button.width(), dialog.width())
        self.screenshot(dialog, "sources-inherited-nogal.png")
        dialog.tabs.setCurrentIndex(1)
        self.screenshot(dialog, "ignored-routes-nogal.png")
        dialog.close()
        dialog.deleteLater()
        details.deleteLater()


if __name__ == "__main__":
    unittest.main()
