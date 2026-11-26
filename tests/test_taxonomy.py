"""Clasificación persistente y flujos nativos de etiquetas y categorías."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox

from qt_helpers import drain_qt_events

from desktop import DetailDialog, MainWindow
from library import Library, filter_books
from taxonomy import TaxonomyFilterDialog, TaxonomyManagerDialog
from test_arandu import make_epub
from test_desktop import make_pdf


class TaxonomyStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data")
        self.epub = self.root / "Novela.epub"
        self.pdf = self.root / "Ensayo.pdf"
        make_epub(self.epub)
        make_pdf(self.pdf)
        self.library.add(self.epub)
        self.library.add(self.pdf)
        books = self.library.listing()["books"]
        self.novel = next(book["id"] for book in books if book["format"] == "EPUB")
        self.essay = next(book["id"] for book in books if book["format"] == "PDF")

    def tearDown(self):
        self.temp.cleanup()

    def test_classifications_persist_normalize_and_combine_with_search(self):
        self.library.update(self.novel, {
            "categories": [" Ficción ", "ficcio\u0301n", "Clásicos"],
            "tags": ["Paraguay", "paraguay", "Leer  pronto"],
            "favorite": True,
        })
        self.library.update(self.essay, {"categories": ["Historia"], "tags": ["PARAGUAY"]})
        reopened = Library(self.library.data_dir)
        self.assertEqual(reopened.get(self.novel)["categories"], ["Ficción", "Clásicos"])
        self.assertEqual(reopened.get(self.novel)["tags"], ["Paraguay", "Leer pronto"])
        self.assertEqual(reopened.get(self.essay)["tags"], ["Paraguay"])
        self.assertEqual(reopened.taxonomy_counts("tags"), {"Leer pronto": 1, "Paraguay": 2})
        books = reopened.listing()["books"]
        self.assertEqual([book["id"] for book in filter_books(books, "favorites", "clasicos", "EPUB", category="ficción", tag="paraguay")], [self.novel])
        self.assertEqual(filter_books(books, category="Historia", tag="Leer pronto"), [])
        self.assertEqual(filter_books(books, tag="Para"), [])
        reopened.ingest(self.epub, force=True)
        self.assertEqual(reopened.get(self.novel)["categories"], ["Ficción", "Clásicos"])

    def test_rename_and_remove_update_all_books_and_keep_other_data(self):
        for book_id in (self.novel, self.essay):
            self.library.update(book_id, {"categories": ["Historia", "Ensayo"], "tags": ["Investigar"], "notes": "Mi nota", "progress": 40})
        self.library.save_taxonomy("categories", "Historia local", "historia")
        self.library.save_taxonomy("tags", "Revisar", "Investigar")
        with self.assertRaises(ValueError):
            self.library.save_taxonomy("categories", "Ensayo", "Historia local")
        self.library.remove_taxonomy("categories", "ENSAYO")
        self.library.remove_taxonomy("tags", "Revisar")
        reopened = Library(self.library.data_dir)
        for book_id in (self.novel, self.essay):
            book = reopened.get(book_id)
            self.assertEqual((book["categories"], book["tags"], book["notes"], book["progress"]), (["Historia local"], [], "Mi nota", 40))
            self.assertTrue(book["available"])
        self.assertEqual(reopened.taxonomy_counts("categories"), {"Historia local": 2})
        self.assertEqual(reopened.taxonomy_names("tags"), [])

    def test_invalid_changes_do_not_leave_partial_names(self):
        for changes in (
            {"categories": "Novela"}, {"categories": [1]},
            {"categories": ["Nombre, con coma"]},
            {"categories": ["Sin guardar"], "tags": ["Nueva"], "series_number": -1},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.library.update(self.novel, changes)
        self.assertEqual(self.library.taxonomy_names("categories"), [])
        self.assertEqual(self.library.taxonomy_names("tags"), [])
        with self.assertRaises(ValueError):
            self.library.taxonomy_names("notes")
        self.library.save_taxonomy("categories", "Poesía")
        self.assertEqual(Library(self.library.data_dir).taxonomy_counts("categories"), {"Poesía": 0})


class TaxonomyWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data")
        epub, pdf = self.root / "Novela.epub", self.root / "Historia.pdf"
        make_epub(epub)
        make_pdf(pdf)
        self.library.add(epub)
        self.library.add(pdf)
        books = self.library.listing()["books"]
        self.novel = next(book["id"] for book in books if book["format"] == "EPUB")
        self.history = next(book["id"] for book in books if book["format"] == "PDF")
        self.library.update(self.novel, {"categories": ["Novela"], "tags": ["Paraguay", "Recomendado"], "collections": ["Lecturas"]})
        self.library.update(self.history, {"categories": ["Historia"], "tags": ["Paraguay"]})
        self.window = MainWindow(self.library)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        drain_qt_events(self.app)
        self.temp.cleanup()

    def screenshot(self, widget, name):
        folder = os.environ.get("ARANDU_TEST_ARTIFACTS")
        if folder:
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            self.app.processEvents()
            self.assertTrue(widget.grab().save(str(target / name)))

    def test_native_filter_applies_both_classifications_and_cancel_preserves_them(self):
        def choose(dialog):
            dialog.show()
            dialog.category_combo.setCurrentIndex(dialog.category_combo.findData("Novela"))
            dialog.tag_combo.setCurrentIndex(dialog.tag_combo.findData("Paraguay"))
            self.screenshot(dialog, "classification-filters.png")
            self.assertLess(dialog.category_combo.geometry().bottom(), dialog.tag_combo.geometry().top())
            apply = next(button for button in dialog.findChildren(QDialogButtonBox)[0].buttons() if button.text() == "Aplicar")
            QTest.mouseClick(apply, Qt.LeftButton)
            return dialog.result()
        with patch.object(TaxonomyFilterDialog, "exec_", choose):
            QTest.mouseClick(self.window.taxonomy_filter_button, Qt.LeftButton)
        self.assertEqual(self.window.book_list.count(), 1)
        self.assertEqual(self.window.book_list.item(0).data(Qt.UserRole)["id"], self.novel)
        self.assertEqual(self.window.taxonomy_filter_button.text(), "Filtros (2)")
        self.window.book_list.setCurrentRow(0)
        self.screenshot(self.window, "classification-catalog.png")
        def cancel(dialog):
            dialog.clear()
            return QDialog.Rejected
        with patch.object(TaxonomyFilterDialog, "exec_", cancel):
            self.window.filter_classification()
        self.assertEqual((self.window.category_filter, self.window.tag_filter), ("Novela", "Paraguay"))

    def test_show_all_clears_classifications_collection_search_format_and_view(self):
        self.window.category_filter, self.window.tag_filter = "Historia", "Recomendado"
        self.window.collection_combo.setCurrentIndex(self.window.collection_combo.findData("Lecturas"))
        self.window.search.setText("sin coincidencias")
        self.window.format_combo.setCurrentIndex(1)
        self.window.set_view("favorites")
        self.assertEqual(self.window.stack.currentWidget(), self.window.empty_widget)
        QTest.mouseClick(self.window.empty_button, Qt.LeftButton)
        self.assertEqual(self.window.book_list.count(), 2)
        self.assertEqual((self.window.category_filter, self.window.tag_filter, self.window.collection_combo.currentData(), self.window.search.text(), self.window.view), ("", "", "", "", "all"))

    def test_manager_renames_active_filter_and_removes_membership(self):
        self.window.category_filter = "Novela"
        self.window.render()
        def manage(dialog):
            dialog.show()
            dialog.tabs.setCurrentIndex(1)
            with patch("taxonomy.QInputDialog.getText", return_value=("Poesía", True)):
                QTest.mouseClick(dialog.new_button, Qt.LeftButton)
            self.assertEqual(self.library.taxonomy_counts("categories")["Poesía"], 0)
            items = dialog.lists["categories"]
            items.setCurrentItem(next(items.item(index) for index in range(items.count()) if items.item(index).data(Qt.UserRole) == "Novela"))
            with patch("taxonomy.QInputDialog.getText", return_value=("Literatura", True)):
                QTest.mouseClick(dialog.rename_button, Qt.LeftButton)
            self.assertEqual(self.window.category_filter, "Literatura")
            self.assertEqual(self.window.book_list.count(), 1)
            self.screenshot(dialog, "classification-manager.png")
            with patch("taxonomy.QMessageBox.question", return_value=QMessageBox.Yes):
                QTest.mouseClick(dialog.remove_button, Qt.LeftButton)
            self.assertEqual(self.window.category_filter, "")
            self.assertEqual(self.window.book_list.count(), 2)
            return QDialog.Accepted
        with patch.object(TaxonomyManagerDialog, "exec_", manage):
            self.window.manage_classification()
        self.assertEqual(self.library.get(self.novel)["categories"], [])
        self.assertEqual(self.library.get(self.novel)["tags"], ["Paraguay", "Recomendado"])

    def test_fiche_completion_preserves_previous_tag_and_cancel_discards_edits(self):
        self.window.set_theme("nogal")
        original = self.library.get(self.novel)
        dialog = DetailDialog(self.library, original, self.window)
        dialog.show()
        self.app.processEvents()
        dialog.tags_edit.setText("Paraguay, ")
        dialog.tags_edit.setFocus()
        QTest.keyClicks(dialog.tags_edit, "Reco")
        self.app.processEvents()
        popup = dialog.tags_edit.completer().popup()
        self.assertTrue(popup.isVisible())
        popup.setCurrentIndex(popup.model().index(0, 0))
        QTest.keyClick(popup, Qt.Key_Return)
        self.assertEqual(dialog.tags_edit.text(), "Paraguay, Recomendado")
        dialog.focus_classification()
        self.assertTrue(dialog.categories_edit.hasFocus())
        self.screenshot(dialog, "classification-fiche-nogal.png")
        dialog.categories_edit.setText("Sin guardar")
        dialog.reject()
        self.assertEqual(self.library.get(self.novel)["categories"], original["categories"])
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
