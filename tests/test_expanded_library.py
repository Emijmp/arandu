"""Pruebas funcionales de índice, archivos movidos, lectura y recuperación."""

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, Qt, QTimer
from PyQt5.QtGui import QTextCursor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox

from backups import create_backup, restore_backup
from qt_helpers import drain_qt_events

from desktop import DetailDialog, MainWindow
from extraction import pdf_words
from library import Library, filter_books
from library_dialogs import ContentSearchDialog
from reading_tools import ReadingSettingsDialog, reading_settings
from test_arandu import make_epub
from test_desktop import make_pdf
from theme_manager import ThemeManagerDialog, export_theme, import_themes
from themes import THEMES


class ExpandedStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data")
        self.pdf = self.root / "Documento.pdf"
        self.epub = self.root / "Novela.epub"
        make_pdf(self.pdf, title="Mi documento", author="Autora de prueba")
        make_epub(self.epub)

    def tearDown(self):
        self.temp.cleanup()

    def test_pdf_metadata_cover_page_index_and_word_coordinates(self):
        self.library.add(self.pdf)
        book = self.library.listing()["books"][0]
        self.assertEqual((book["title"], book["author"]), ("Mi documento", "Autora de prueba"))
        self.assertTrue(Path(book["cover_path"]).read_bytes().startswith(b"\x89PNG"))
        matches = self.library.search_text("Segunda")
        self.assertEqual([int(item["position"]) for item in matches], [1])
        self.assertEqual(len(self.library.search_text('"Primera pagina"')), 1)
        words = pdf_words(self.pdf, 0)
        self.assertEqual(" ".join(word["text"] for word in words), "Primera pagina")
        self.assertTrue(all(0 <= value <= 1 for word in words for value in word["rect"]))
        self.library.search_text('"; DROP TABLE books; --')
        self.assertEqual(len(self.library.listing()["books"]), 1)

    def test_epub_index_is_accent_insensitive_and_excludes_scripts(self):
        self.library.add(self.epub)
        self.assertEqual(int(self.library.search_text("continuara")[0]["position"]), 1)
        self.assertEqual(self.library.search_text("window"), [])

    def test_duplicates_and_moved_files_preserve_notes_and_annotations(self):
        self.library.add(self.pdf)
        book = self.library.listing()["books"][0]
        self.library.update(book["id"], {"notes": "Conservar", "progress": 63, "status": "reading"})
        self.library.toggle_bookmark(book["id"], 1)
        copy = self.root / "Mismo contenido con otro nombre.pdf"
        shutil.copyfile(self.pdf, copy)
        self.library.add(copy)
        self.assertEqual(len(self.library.duplicates()), 1)
        moved = self.root / "Movido.pdf"
        self.pdf.rename(moved)
        self.assertEqual(self.library.ingest(moved), "relinked")
        restored = self.library.get(book["id"])
        self.assertEqual((restored["path"], restored["notes"], restored["progress"]), (str(moved), "Conservar", 63))
        self.assertEqual(self.library.annotations(book["id"])[0]["position"], 1)
        self.assertEqual(len(self.library.listing()["books"]), 2)

    def test_collections_and_series_changes_keep_books(self):
        self.library.add(self.pdf)
        self.library.add(self.epub)
        books = self.library.listing()["books"]
        self.library.update(books[0]["id"], {"collections": ["Lecturas"], "series": "Saga", "series_number": 2})
        self.library.update(books[1]["id"], {"collections": ["Lecturas"], "series": "Saga", "series_number": 1})
        self.library.save_collection("Favoritas", "Lecturas")
        self.assertTrue(all(book["collections"] == ["Favoritas"] for book in self.library.listing()["books"]))
        ordered = filter_books(self.library.listing()["books"], order="series")
        self.assertEqual([book["series_number"] for book in ordered], [1, 2])
        self.library.remove_collection("Favoritas")
        self.assertEqual(len(self.library.listing()["books"]), 2)
        self.assertTrue(self.pdf.exists())

    def test_portable_backup_restores_books_notes_annotations_and_settings(self):
        self.library.add(self.pdf)
        self.library.add(self.epub)
        book = next(book for book in self.library.listing()["books"] if book["format"] == "PDF")
        self.library.update(book["id"], {"notes": "Mis notas", "collections": ["Trabajo"], "progress": 50, "tags": ["Investigar"], "categories": ["Ensayo"]})
        self.library.save_taxonomy("categories", "Poesía")
        self.library.add_annotation(book["id"], 1, "note", quote="Segunda pagina", note="Recordar esto")
        (self.library.data_dir / "reader.json").write_text(json.dumps({"font_size": 24, "margin": 60, "line_spacing": 170}))
        backup = self.root / "portable.zip"
        create_backup(self.library, backup, include_books=True)
        self.pdf.unlink()
        self.epub.unlink()
        recovered = Library(self.root / "recovered")
        safety = restore_backup(recovered, backup)
        self.assertTrue(Path(safety).is_file())
        self.assertTrue(all(book["available"] for book in recovered.listing()["books"]))
        self.assertEqual(recovered.get(book["id"])["notes"], "Mis notas")
        self.assertEqual(recovered.get(book["id"])["collections"], ["Trabajo"])
        self.assertEqual(recovered.get(book["id"])["tags"], ["Investigar"])
        self.assertEqual(recovered.get(book["id"])["categories"], ["Ensayo"])
        self.assertEqual(recovered.taxonomy_counts("categories"), {"Ensayo": 1, "Poesía": 0})
        self.assertEqual(recovered.annotations(book["id"])[0]["note"], "Recordar esto")
        self.assertEqual(reading_settings(recovered.data_dir)["font_size"], 24)
        self.assertEqual(len(recovered.search_text("Segunda")), 1)

    def test_restore_rejects_traversal_without_touching_current_catalog(self):
        self.library.add(self.pdf)
        backup = self.root / "malicioso.zip"
        with zipfile.ZipFile(backup, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"format": "arandu-backup", "version": 1, "files": {"1": "../escaped"}}))
            archive.writestr("../escaped", "bad")
        with self.assertRaises(ValueError):
            restore_backup(self.library, backup)
        self.assertFalse((self.library.data_dir / "escaped").exists())
        self.assertEqual(len(self.library.listing()["books"]), 1)

    def test_portable_restore_preserves_duplicate_records_and_existing_recovered_files(self):
        self.library.add(self.pdf)
        copy = self.root / "Copia.pdf"
        shutil.copyfile(self.pdf, copy)
        self.library.add(copy)
        ids = [book["id"] for book in self.library.listing()["books"]]
        self.library.add_annotation(ids[0], 0, "note", note="Nota de una copia")
        self.library.add_annotation(ids[1], 1, "note", note="Nota de la otra")
        backup = self.root / "duplicados.zip"
        create_backup(self.library, backup, True)
        digest = self.library.digest(self.pdf)
        self.pdf.unlink()
        copy.unlink()
        recovered = Library(self.root / "new")
        old = recovered.uploads / "recovered" / f"{ids[0]}-{digest}.pdf"
        old.parent.mkdir()
        old.write_bytes(b"Contenido local que se debe conservar")
        restore_backup(recovered, backup)
        self.assertEqual(old.read_bytes(), b"Contenido local que se debe conservar")
        books = recovered.listing()["books"]
        self.assertEqual(len({book["path"] for book in books}), 2)
        self.assertTrue(all(book["available"] for book in books))
        self.assertEqual(len(recovered.duplicates()), 1)
        self.assertEqual(len(recovered.annotations(ids[0])), 1)
        self.assertEqual(len(recovered.annotations(ids[1])), 1)

    def test_old_catalog_migration_preserves_data_and_can_be_indexed(self):
        folder = self.root / "legacy"
        folder.mkdir()
        with closing(sqlite3.connect(folder / "library.sqlite3")) as db, db:
            db.execute("CREATE TABLE books(id INTEGER PRIMARY KEY,path TEXT UNIQUE NOT NULL,title TEXT NOT NULL,author TEXT NOT NULL,format TEXT NOT NULL,size INTEGER NOT NULL,cover TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'pending',favorite INTEGER NOT NULL DEFAULT 0,progress INTEGER NOT NULL DEFAULT 0,chapter INTEGER NOT NULL DEFAULT 0,tags TEXT NOT NULL DEFAULT '[]',notes TEXT NOT NULL DEFAULT '',added_at REAL NOT NULL,updated_at REAL NOT NULL)")
            db.execute("INSERT INTO books(path,title,author,format,size,notes,progress,added_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (str(self.pdf), "Título mío", "Autor desconocido", "PDF", self.pdf.stat().st_size, "Nota anterior", 42, 1, 1))
            db.execute("UPDATE books SET tags=?", (json.dumps(["Anterior", "Ficción"]),))
        migrated = Library(folder)
        self.assertTrue(migrated.needs_maintenance())
        migrated.refresh_all()
        self.assertEqual((migrated.get(1)["title"], migrated.get(1)["notes"], migrated.get(1)["progress"]), ("Título mío", "Nota anterior", 42))
        self.assertEqual(len(migrated.search_text("Primera")), 1)
        self.assertEqual(migrated.get(1)["categories"], [])
        self.assertEqual(migrated.get(1)["tags"], ["Anterior", "Ficción"])
        self.assertEqual(migrated.taxonomy_counts("tags"), {"Anterior": 1, "Ficción": 1})

    def test_theme_export_import_keeps_presets_and_renames_collisions(self):
        target = self.root / "tema.json"
        export_theme(THEMES["tinta"], target)
        imported = import_themes(target, THEMES)
        self.assertEqual(len(imported), 1)
        self.assertTrue(imported[0].id.startswith("custom-"))
        self.assertNotEqual(imported[0].name, "Tinta")
        self.assertEqual(imported[0].accent, THEMES["tinta"].accent)


class ExpandedWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data")
        self.pdf = self.root / "Documento.pdf"
        self.epub = self.root / "Novela.epub"
        make_pdf(self.pdf)
        make_epub(self.epub)
        self.library.add(self.pdf)
        self.library.add(self.epub)
        self.window = MainWindow(self.library)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.wait(lambda: not self.window.busy)
        self.window.close()
        self.window.deleteLater()
        drain_qt_events(self.app)
        self.temp.cleanup()

    def wait(self, condition, seconds=9):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(20)
        self.fail("La operación no terminó a tiempo.")

    def book(self, format):
        return next(book for book in self.library.listing()["books"] if book["format"] == format)

    def screenshot(self, name, widget):
        directory = os.environ.get("ARANDU_TEST_ARTIFACTS")
        if directory:
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            self.app.processEvents()
            self.assertTrue(widget.grab().save(str(path / name)))

    def test_pdf_selection_copy_highlights_bookmarks_and_page_notes(self):
        book = self.book("PDF")
        original = self.library.digest(self.pdf)
        self.window.open_book(book["id"])
        reader = next(iter(self.window.readers))
        self.wait(lambda: bool(reader.viewer.page.words))
        page = reader.viewer.page
        page.setFocus()
        QTest.keyClick(page, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(page, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), "Primera pagina")
        self.assertTrue(reader.highlight_button.isEnabled())
        reader.add_highlight()
        reader.toggle_bookmark()
        with patch("readers.QInputDialog.getMultiLineText", return_value=("Nota de la primera página", True)):
            reader.add_note()
        entries = self.library.annotations(book["id"])
        self.assertEqual({entry["kind"] for entry in entries}, {"highlight", "bookmark", "note"})
        self.assertEqual(self.library.digest(self.pdf), original)
        self.screenshot("reader-pdf-annotations.png", reader)
        reader.close()
        self.app.processEvents()
        self.window.open_book(book["id"])
        reader = next(iter(self.window.readers))
        self.wait(lambda: bool(reader.viewer.page.words))
        self.assertTrue(reader.bookmark_button.isChecked())
        self.assertEqual(len(reader.viewer.page.annotations), 3)

    def test_search_result_opens_the_correct_pdf_page(self):
        dialog = ContentSearchDialog(self.library, parent=self.window)
        dialog.resultSelected.connect(lambda book_id, position, query: self.window.open_book(book_id, position, query))
        dialog.query.setText("Segunda")
        dialog.search()
        self.assertEqual(dialog.results.topLevelItemCount(), 1)
        self.screenshot("content-search.png", dialog)
        dialog.open_result(dialog.results.topLevelItem(0))
        reader = next(iter(self.window.readers))
        self.wait(lambda: bool(reader.viewer.page.words))
        self.assertEqual(reader.viewer.index, 1)
        self.assertTrue(reader.viewer.page.matches)
        dialog.deleteLater()

    def test_epub_highlights_reading_controls_and_fullscreen(self):
        book = self.book("EPUB")
        self.window.open_book(book["id"])
        reader = next(iter(self.window.readers))
        self.wait(lambda: "historia" in reader.viewer.browser.toPlainText())
        cursor = reader.viewer.browser.document().find("historia")
        reader.viewer.browser.setTextCursor(cursor)
        reader.add_highlight()
        self.assertEqual(len(reader.viewer.browser.extraSelections()), 1)
        captured = []
        def adjust():
            dialog = self.app.activeModalWidget()
            captured.append(isinstance(dialog, ReadingSettingsDialog))
            if isinstance(dialog, ReadingSettingsDialog):
                dialog.fields["font_size"].setValue(24)
                dialog.fields["margin"].setValue(70)
                dialog.fields["line_spacing"].setValue(180)
                dialog.save()
            elif dialog:
                dialog.reject()
        QTimer.singleShot(0, adjust)
        reader.open_reading_settings()
        self.assertEqual(captured, [True])
        self.assertEqual(reader.viewer.browser.document().documentMargin(), 70)
        self.assertEqual(reading_settings(self.library.data_dir)["font_size"], 24)
        self.assertEqual(len(reader.viewer.browser.extraSelections()), 1)
        reader.toggle_fullscreen()
        self.assertTrue(reader.isFullScreen())
        self.assertFalse(reader.tools.isVisible())
        QTest.keyClick(reader, Qt.Key_Escape)
        self.assertFalse(reader.isFullScreen())
        self.assertTrue(reader.isVisible())
        self.assertTrue(reader.viewer.previous.isVisible())
        self.assertTrue(reader.viewer.next.isVisible())
        QTest.mouseClick(reader.viewer.next, Qt.LeftButton)
        self.assertEqual(reader.viewer.index, 1)
        self.screenshot("reader-epub-settings.png", reader)

    def test_watcher_detects_new_and_moved_books_without_losing_progress(self):
        folder = self.root / "Libros"
        folder.mkdir()
        first = folder / "Uno.pdf"
        make_pdf(first)
        self.library.scan([str(folder)])
        self.window.reload()
        second = folder / "Dos.pdf"
        shutil.copyfile(first, second)
        self.wait(lambda: len(self.library.listing()["books"]) == 4)
        self.wait(lambda: not self.window.busy)
        book = next(book for book in self.library.listing()["books"] if book["path"] == str(second))
        self.library.update(book["id"], {"progress": 77, "notes": "No perder"})
        renamed = folder / "Renombrado.pdf"
        second.rename(renamed)
        self.wait(lambda: self.library.get(book["id"])["path"] == str(renamed))
        self.assertEqual(self.library.get(book["id"])["progress"], 77)
        self.assertEqual(self.library.get(book["id"])["notes"], "No perder")

    def test_collection_and_series_fields_filter_the_library(self):
        book = self.book("PDF")
        dialog = DetailDialog(self.library, book, self.window)
        dialog.collections_edit.setText("Trabajo, Pendientes")
        dialog.series_edit.setText("Cuadernos")
        dialog.series_number.setValue(2)
        dialog.save()
        self.window.reload()
        self.window.collection_combo.setCurrentIndex(self.window.collection_combo.findData("Trabajo"))
        self.assertEqual(self.window.book_list.count(), 1)
        self.assertEqual(self.window.book_list.item(0).data(Qt.UserRole)["series_number"], 2)
        dialog.deleteLater()

    def test_theme_management_duplicates_exports_removes_and_scales_text(self):
        dialog = ThemeManagerDialog(self.window)
        with patch("theme_manager.QInputDialog.getText", return_value=("Mi copia", True)):
            dialog.duplicate()
        self.assertEqual(len(self.window.custom_themes), 1)
        self.assertEqual(self.window.theme.name, "Mi copia")
        with patch("theme_manager.QMessageBox.question", return_value=QMessageBox.Yes):
            dialog.remove()
        self.assertEqual(len(self.window.custom_themes), 0)
        self.assertEqual(len(THEMES), 6)
        self.window.set_text_scale(125)
        self.window.resize(680, 560)
        self.app.processEvents()
        for widget in (self.window.add_button, self.window.search, self.window.collection_combo, self.window.content_button):
            right = widget.mapTo(self.window, QPoint(widget.width(), widget.height()))
            self.assertLessEqual(right.x(), self.window.width())
            self.assertLessEqual(right.y(), self.window.height())
        self.screenshot("library-scaled.png", self.window)
        dialog.deleteLater()

    def test_restore_action_recovers_previous_catalog_and_keeps_a_safety_copy(self):
        book = self.book("PDF")
        self.library.update(book["id"], {"notes": "Antes"})
        backup = self.root / "catalogo.zip"
        create_backup(self.library, backup)
        self.library.update(book["id"], {"notes": "Después"})
        with patch("desktop.QFileDialog.getOpenFileName", return_value=(str(backup), "")), patch("desktop.QMessageBox.question", return_value=QMessageBox.Yes):
            self.window.restore_library()
        self.wait(lambda: not self.window.busy)
        self.assertEqual(self.library.get(book["id"])["notes"], "Antes")
        self.assertTrue(list((self.library.data_dir / "backups").glob("antes-de-restaurar-*.zip")))


if __name__ == "__main__":
    unittest.main()
