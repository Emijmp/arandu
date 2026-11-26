"""Flujos reales de organización, lectura, OCR y recuperación de la biblioteca."""

from datetime import datetime
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QBuffer, QIODevice, QPoint, QRect, Qt
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPdfWriter
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox

from backups import create_backup, restore_backup
from cover_tools import cover_image
from qt_helpers import drain_qt_events

from desktop import DetailDialog, MainWindow
from extraction import pdf_pages
from library import Library, filter_books
from ocr import OCRDialog, recognize_pdf
from organization_dialogs import BatchEditDialog, EditionsDialog, RuleEditorDialog, RulesDialog, ShelfEditorDialog, SmartShelvesDialog, StatisticsDialog, TrashDialog
from organizer import match_shelf
from test_arandu import make_epub
from test_desktop import make_pdf
from themes import load_fonts


def png_bytes(color="#C8B29A"):
    image = QImage(140, 200, QImage.Format_RGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def scanned_pdf(path):
    image = QImage(1200, 1700, QImage.Format_RGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    painter.setPen(Qt.black)
    painter.setFont(QFont("Karla", 44))
    painter.drawText(100, 300, "Biblioteca local")
    painter.drawText(100, 420, "Paraguay")
    painter.drawText(100, 540, "Lecturas para recordar")
    painter.end()
    writer = QPdfWriter(str(path))
    writer.setResolution(120)
    painter = QPainter(writer)
    painter.drawImage(QRect(0, 0, painter.viewport().width(), painter.viewport().height()), image)
    painter.end()


class OrganizationStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data")
        self.epub, self.pdf = self.root / "Novela.epub", self.root / "Historia.pdf"
        make_epub(self.epub)
        make_pdf(self.pdf)
        self.library.add(self.epub)
        self.library.add(self.pdf)
        self.ids = [book["id"] for book in self.library.listing()["books"]]

    def tearDown(self):
        self.temp.cleanup()

    def test_batch_add_remove_replace_and_atomic_rollback(self):
        self.library.update(self.ids[0], {"tags": ["Anterior"]})
        self.assertEqual(self.library.batch_update(self.ids, {"tags": ["Nueva"], "categories": ["Ensayo"], "status": "reading"}), 2)
        self.assertEqual(self.library.get(self.ids[0])["tags"], ["Anterior", "Nueva"])
        self.library.batch_update(self.ids, {"tags": ["nueva"]}, "remove")
        self.assertEqual(self.library.get(self.ids[0])["tags"], ["Anterior"])
        self.library.batch_update(self.ids, {"categories": []}, "replace")
        self.assertTrue(all(not book["categories"] for book in self.library.listing()["books"]))
        original_update = self.library.update
        def failure(book_id, changes, _db=None):
            if book_id == self.ids[1]:
                raise sqlite3.IntegrityError("Fallo simulado en el segundo libro")
            return original_update(book_id, changes, _db)
        with patch.object(self.library, "update", failure), self.assertRaises(sqlite3.IntegrityError):
            self.library.batch_update(self.ids, {"author": "Sin guardar", "tags": ["Temporal"]})
        self.assertNotEqual(self.library.get(self.ids[0])["author"], "Sin guardar")
        self.assertNotIn("Temporal", self.library.taxonomy_names("tags"))

    def test_smart_shelf_updates_and_taxonomy_rename_preserves_its_filters(self):
        shelf_id = self.library.save_shelf("Novelas pendientes", {"category": "Novela", "view": "pending"})
        self.assertEqual(match_shelf(self.library.listing()["books"], self.library.smart_shelves()[0]["filters"]), [])
        self.library.update(self.ids[0], {"categories": ["Novela"]})
        self.library.save_taxonomy("categories", "Literatura", "Novela")
        reopened = Library(self.library.data_dir)
        shelf = reopened.smart_shelves()[0]
        self.assertEqual((shelf["id"], shelf["filters"]["category"]), (shelf_id, "Literatura"))
        self.assertEqual(len(match_shelf(reopened.listing()["books"], shelf["filters"])), 1)
        reopened.update(self.ids[0], {"status": "finished"})
        self.assertEqual(match_shelf(reopened.listing()["books"], shelf["filters"]), [])
        with self.assertRaises(ValueError):
            reopened.save_shelf("NOVELAS PENDIENTES", {})

    def test_rules_apply_on_import_and_to_existing_books_without_replacing_names(self):
        rule_id = self.library.save_rule("Novelas locales", {"folder": str(self.root), "author": "autora", "filename": "novela", "format": "EPUB"}, {"tags": ["Investigar"], "categories": ["Novela"], "collections": ["Locales"]})
        self.library.save_rule("Pausada", {"format": "PDF"}, {"tags": ["No aplicar"]}, enabled=False)
        self.library.update(self.ids[0], {"tags": ["Anterior"]})
        self.assertEqual(self.library.apply_all_rules(), 1)
        book = next(book for book in self.library.listing()["books"] if book["format"] == "EPUB")
        self.assertEqual((book["categories"], book["collections"], book["tags"]), (["Novela"], ["Locales"], ["Investigar"]))
        self.assertNotIn("No aplicar", self.library.taxonomy_names("tags"))
        self.library.save_taxonomy("tags", "Consultar", "Investigar")
        self.assertEqual(self.library.rules()[0]["actions"]["tags"], ["Consultar"])
        additional = self.root / "Novela nueva.epub"
        shutil.copyfile(self.epub, additional)
        self.library.add(additional)
        imported = next(book for book in self.library.listing()["books"] if book["path"] == str(additional))
        self.assertEqual(imported["tags"], ["Consultar"])
        self.library.delete_rule(rule_id)
        self.assertEqual(self.library.get(imported["id"])["categories"], ["Novela"])
        with self.assertRaises(ValueError):
            self.library.save_rule("Sin condiciones", {}, {"tags": ["X"]})

    def test_trash_keeps_notes_annotations_search_and_progress_until_restored(self):
        book_id = next(book["id"] for book in self.library.listing()["books"] if book["format"] == "PDF")
        self.library.update(book_id, {"notes": "Mi idea", "progress": 35, "tags": ["Revisar"]})
        self.library.add_annotation(book_id, 1, "note", note="Nota de página")
        self.library.delete(book_id)
        self.assertEqual(len(self.library.trash()), 1)
        self.assertEqual(self.library.search_text("Primera"), [])
        self.library.scan([self.root])
        self.assertEqual(len(self.library.listing()["books"]), 1)
        self.library.restore_book(book_id)
        self.assertEqual(self.library.get(book_id)["notes"], "Mi idea")
        self.assertEqual(self.library.get(book_id)["progress"], 35)
        self.assertEqual(self.library.annotations(book_id)[0]["note"], "Nota de página")
        self.assertEqual(len(self.library.search_text("Primera")), 1)
        with self.assertRaises(ValueError):
            self.library.purge_book(book_id)
        self.library.delete(book_id)
        self.library.purge_book(book_id)
        self.assertTrue(self.pdf.exists())
        self.assertEqual(self.library.annotations(book_id), [])
        self.assertEqual(self.library.trash(), [])

    def test_editions_collapse_choose_preferred_and_keep_independent_progress(self):
        self.library.update(self.ids[0], {"progress": 30})
        self.library.update(self.ids[1], {"progress": 65})
        work_id = self.library.group_editions(self.ids)
        self.library.prefer_edition(work_id, self.ids[1])
        entries = self.library.catalog_entries(self.library.listing()["books"])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], self.ids[1])
        self.assertEqual(set(entries[0]["edition_ids"]), set(self.ids))
        self.assertEqual(self.library.get(self.ids[0])["progress"], 30)
        filtered = filter_books(self.library.listing()["books"], file_format="PDF")
        self.assertEqual(self.library.catalog_entries(filtered)[0]["format"], "PDF")
        self.library.ungroup_edition(self.ids[0])
        self.assertEqual(len(self.library.catalog_entries(self.library.listing()["books"])), 2)

    def test_statistics_goals_completions_and_months_persist(self):
        stamp = datetime(2026, 11, 25, 10).timestamp()
        self.library.record_reading(self.ids[0], 90, "session-one", stamp)
        self.library.record_reading(self.ids[0], 30, "session-one", stamp + 30)
        self.library.record_reading(self.ids[1], 300, "session-two", datetime(2026, 10, 5, 12).timestamp())
        with patch("library.time.time", return_value=stamp):
            self.library.batch_update(self.ids, {"status": "finished"})
            self.library.update(self.ids[0], {"status": "finished"})
        self.library.save_goals(2026, 24, 15)
        reopened = Library(self.library.data_dir)
        stats = reopened.reading_stats(2026, datetime(2026, 11, 25))
        self.assertEqual((stats["seconds"], len(stats["sessions"]), stats["books"], stats["today_seconds"]), (420, 2, 2, 120))
        self.assertEqual(stats["months"][10]["books"], 2)
        self.assertEqual(stats["months"][9]["seconds"], 300)
        self.assertEqual((stats["goals"]["books"], stats["goals"]["daily_minutes"]), (24, 15))
        reopened.group_editions(self.ids)
        self.assertEqual(reopened.reading_stats(2026)["books"], 1)
        with self.assertRaises(ValueError):
            reopened.record_reading(self.ids[0], float("nan"))

    def test_exports_contain_book_location_full_quotes_and_notes(self):
        book_id = self.ids[0]
        self.library.update(book_id, {"notes": "Idea general"})
        self.library.add_annotation(book_id, 3, "highlight", quote="Una cita\ncon otra línea", note="Mi comentario")
        target = self.root / "notas.md"
        self.library.export_annotations(target, [book_id])
        text = target.read_text()
        self.assertIn("Página 4", text)
        self.assertIn("> Una cita\n> con otra línea", text)
        self.assertIn("Idea general", text)
        self.assertIn("Mi comentario", text)
        self.assertNotIn("Un libro de prueba", text)
        txt = self.root / "notas.txt"
        self.library.export_annotations(txt, [book_id])
        self.assertNotIn("##", txt.read_text())
        with self.assertRaises(ValueError):
            self.library.export_annotations(self.pdf)


class OrganizationWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")
        load_fonts()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data")
        self.pdf, self.epub = self.root / "Historia.pdf", self.root / "Novela.epub"
        make_pdf(self.pdf)
        make_epub(self.epub)
        self.library.add(self.pdf)
        self.library.add(self.epub)
        self.ids = [book["id"] for book in self.library.listing()["books"]]
        self.window = MainWindow(self.library)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.wait(lambda: not self.window.busy)
        self.window.close()
        self.window.deleteLater()
        drain_qt_events(self.app)
        self.temp.cleanup()

    def wait(self, condition, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(20)
        self.fail("La operación no terminó a tiempo.")

    def screenshot(self, widget, name):
        folder = os.environ.get("ARANDU_TEST_ARTIFACTS")
        if folder:
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            self.app.processEvents()
            self.assertTrue(widget.grab().save(str(target / name)))

    def test_multiple_selection_edits_all_and_keeps_selection_after_reload(self):
        self.window.book_list.setCurrentRow(0)
        self.window.book_list.item(1).setSelected(True)
        self.app.processEvents()
        self.assertEqual(set(self.window.selected_ids()), set(self.ids))
        self.assertFalse(self.window.read_button.isEnabled())
        def edit(dialog):
            dialog.show()
            dialog.checks["tags"].setChecked(True)
            dialog.fields["tags"].setText("Paraguay, Recomendado")
            dialog.checks["categories"].setChecked(True)
            dialog.fields["categories"].setText("Literatura")
            self.screenshot(dialog, "batch-edit.png")
            dialog.save()
            return dialog.result()
        with patch.object(BatchEditDialog, "exec_", edit):
            QTest.mouseClick(self.window.detail_button, Qt.LeftButton)
        self.assertTrue(all(book["tags"] == ["Paraguay", "Recomendado"] for book in self.library.listing()["books"]))
        self.assertEqual(set(self.window.selected_ids()), set(self.ids))
        self.screenshot(self.window, "multiple-selection.png")

    def test_shelf_and_rule_editors_save_apply_and_filter(self):
        shelf = ShelfEditorDialog(self.library, parent=self.window)
        shelf.show()
        shelf.name.setText("Pendientes PDF")
        shelf.view.setCurrentIndex(shelf.view.findData("pending"))
        shelf.format.setCurrentIndex(shelf.format.findData("PDF"))
        self.screenshot(shelf, "smart-shelf-editor.png")
        shelf.save()
        self.assertEqual(shelf.result(), QDialog.Accepted)
        self.window.reload()
        self.window.collection_combo.setCurrentIndex(self.window.collection_combo.findData("@smart:1"))
        self.assertEqual(self.window.book_list.count(), 1)
        self.assertEqual(self.window.book_list.item(0).data(Qt.UserRole)["format"], "PDF")
        rule = RuleEditorDialog(self.library, parent=self.window)
        rule.show()
        rule.name.setText("Documentos")
        rule.format.setCurrentIndex(rule.format.findData("PDF"))
        rule.actions["categories"].setText("Historia")
        self.screenshot(rule, "rule-editor.png")
        rule.save()
        self.assertEqual(rule.result(), QDialog.Accepted)
        def apply(dialog):
            dialog.apply()
            return dialog.result()
        with patch.object(RulesDialog, "exec_", apply):
            self.window.manage_rules()
        self.wait(lambda: not self.window.busy)
        pdf_book = next(book for book in self.library.listing()["books"] if book["format"] == "PDF")
        self.assertEqual(pdf_book["categories"], ["Historia"])
        shelf.deleteLater()
        rule.deleteLater()

    def test_custom_cover_preview_cancel_restore_and_portable_backup(self):
        image = self.root / "portada.png"
        image.write_bytes(png_bytes())
        book_id = self.ids[0]
        dialog = DetailDialog(self.library, self.library.get(book_id), self.window)
        with patch("desktop.QFileDialog.getOpenFileName", return_value=(str(image), "")):
            dialog.choose_cover()
        dialog.reject()
        self.assertEqual(self.library.get(book_id)["custom_cover"], "")
        dialog = DetailDialog(self.library, self.library.get(book_id), self.window)
        dialog.show()
        with patch("desktop.QFileDialog.getOpenFileName", return_value=(str(image), "")):
            dialog.choose_cover()
        self.screenshot(dialog, "custom-cover-preview.png")
        dialog.save()
        custom = Path(self.library.get(book_id)["custom_cover"])
        self.assertTrue(custom.is_file())
        self.library.ingest(self.library.get(book_id)["path"], force=True)
        self.assertEqual(self.library.get(book_id)["custom_cover"], str(custom))
        self.library.save_shelf("Todo", {})
        self.library.save_rule("PDF", {"format": "PDF"}, {"tags": ["Documento"]})
        self.library.save_goals(2026, 30, 25)
        self.library.group_editions(self.ids)
        self.library.record_reading(book_id, 45)
        self.library.delete(book_id)
        backup = self.root / "portable.zip"
        create_backup(self.library, backup, True)
        custom.unlink()
        self.pdf.unlink()
        self.epub.unlink()
        recovered = Library(self.root / "recovered")
        restore_backup(recovered, backup)
        restored = recovered.get(book_id)
        self.assertTrue(restored["deleted_at"])
        self.assertTrue(Path(restored["custom_cover"]).is_file())
        self.assertTrue(restored["available"])
        self.assertEqual(len(recovered.smart_shelves()), 1)
        self.assertEqual(len(recovered.rules()), 1)
        self.assertEqual(recovered.goals(2026)["books"], 30)
        self.assertTrue(restored["work_id"])
        recovered.restore_book(book_id)
        self.assertEqual(len(recovered.reading_stats(datetime.now().year)["sessions"]), 1)
        recovered.set_custom_cover(book_id, None)
        self.assertEqual(recovered.get(book_id)["custom_cover"], "")
        dialog.deleteLater()

    def test_editions_and_trash_native_actions_preserve_files(self):
        dialog = EditionsDialog(self.library, self.ids, self.window)
        dialog.changed.connect(self.window.reload)
        dialog.show()
        QTest.mouseClick(dialog.create_button, Qt.LeftButton)
        self.assertEqual(self.window.book_list.count(), 1)
        self.assertEqual(set(self.window.book_list.item(0).data(Qt.UserRole)["edition_ids"]), set(self.ids))
        self.screenshot(dialog, "editions.png")
        self.window.book_list.setCurrentRow(0)
        def choose_edition(chooser):
            chooser.read()
            return chooser.result()
        with patch.object(EditionsDialog, "exec_", choose_edition):
            self.window.open_book()
        self.assertEqual(len(self.window.readers), 1)
        reader = next(iter(self.window.readers))
        self.wait(lambda: reader.viewer.index >= 0)
        reader.close()
        QTest.mouseClick(dialog.separate_button, Qt.LeftButton)
        self.assertEqual(self.window.book_list.count(), 2)
        dialog.close()
        dialog.deleteLater()
        self.window.book_list.selectAll()
        with patch("desktop.QMessageBox.question", return_value=QMessageBox.Yes):
            self.window.trash_selection()
        self.assertEqual(self.window.book_list.count(), 0)
        trash = TrashDialog(self.library, self.window)
        trash.changed.connect(self.window.reload)
        trash.show()
        trash.items.selectAll()
        self.screenshot(trash, "trash.png")
        QTest.mouseClick(trash.restore_button, Qt.LeftButton)
        self.assertEqual(self.window.book_list.count(), 2)
        self.assertTrue(self.pdf.exists() and self.epub.exists())
        trash.close()
        trash.deleteLater()

    def test_reading_tracker_pauses_inactive_and_idle_time_and_saves_on_close(self):
        self.window.open_book(self.ids[0])
        reader = next(iter(self.window.readers))
        tracker = reader.tracker
        tracker.timer.stop()
        tick = tracker.last_tick
        tracker.tick(tick + 3, active=True)
        tracker.tick(tick + 5, active=False)
        self.assertEqual(tracker.pending, 3)
        tracker.last_activity = tick - 400
        tracker.tick(tick + 8, active=True)
        self.assertEqual(tracker.pending, 3)
        tracker.flush()
        reader.reject()
        self.app.processEvents()
        self.assertTrue(tracker.closed)
        stats = self.library.reading_stats(datetime.now().year)
        self.assertEqual(len(stats["sessions"]), 1)
        self.assertEqual(stats["seconds"], 3)
        self.library.update(self.ids[0], {"status": "finished"})
        stats_dialog = StatisticsDialog(self.library, self.window)
        stats_dialog.show()
        stats_dialog.books_goal.setValue(20)
        stats_dialog.minutes_goal.setValue(30)
        stats_dialog.save()
        self.assertEqual(self.library.goals(datetime.now().year)["daily_minutes"], 30)
        self.assertGreaterEqual(stats_dialog.book_progress.height(), 24)
        self.screenshot(stats_dialog, "reading-statistics.png")
        stats_dialog.close()
        stats_dialog.deleteLater()

    @unittest.skipUnless(shutil.which("tesseract"), "Requiere Tesseract")
    def test_real_scanned_pdf_ocr_search_selection_refresh_and_backup(self):
        path = self.root / "Escaneado.pdf"
        scanned_pdf(path)
        self.assertTrue(all(not page.strip() for page in pdf_pages(path)))
        self.library.add(path)
        book = next(book for book in self.library.listing()["books"] if book["path"] == str(path))
        self.assertEqual(book["title"], "Escaneado")
        dialog = OCRDialog(self.library, book, self.window)
        dialog.show()
        QTest.mouseClick(dialog.start_button, Qt.LeftButton)
        self.wait(lambda: dialog.worker and dialog.worker.isFinished(), seconds=30)
        self.app.processEvents()
        self.assertIn("1 página reconocida", dialog.status.text())
        self.assertEqual(len(self.library.search_text("Paraguay")), 1)
        self.assertTrue(self.library.ocr_words(book["id"], 0))
        self.screenshot(dialog, "ocr-completed.png")
        self.window.open_book(book["id"])
        reader = next(reader for reader in self.window.readers if reader.book["id"] == book["id"])
        self.wait(lambda: bool(reader.viewer.page.words))
        QTest.keyClick(reader.viewer.page, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(reader.viewer.page, Qt.Key_C, Qt.ControlModifier)
        self.assertIn("Paraguay", self.app.clipboard().text())
        reader.add_highlight()
        self.assertEqual(self.library.annotations(book["id"])[0]["kind"], "highlight")
        self.screenshot(reader, "ocr-reader-selection.png")
        reader.close()
        self.library.ingest(path, force=True)
        self.assertEqual(len(self.library.search_text("Paraguay")), 1)
        backup = self.root / "ocr.zip"
        create_backup(self.library, backup, True)
        path.unlink()
        recovered = Library(self.root / "recovered")
        restore_backup(recovered, backup)
        recovered.ingest(recovered.get(book["id"])["path"], force=True)
        self.assertIn("Paraguay", " ".join(word["text"] for word in recovered.ocr_words(book["id"], 0)))
        self.assertEqual(len(recovered.search_text("Paraguay")), 1)
        dialog.close()
        dialog.deleteLater()

    def test_ocr_cancel_before_start_keeps_the_index_and_original(self):
        book = next(book for book in self.library.listing()["books"] if book["format"] == "PDF")
        cancel = threading.Event()
        cancel.set()
        result = recognize_pdf(self.library, book["id"], cancel=cancel)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["done"], 0)
        self.assertEqual(len(self.library.search_text("Primera")), 1)
        self.assertTrue(self.pdf.exists())

    def test_new_dialogs_fit_small_windows_and_follow_dark_theme(self):
        self.window.set_theme("nogal")
        self.window.resize(680, 560)
        self.window.book_list.setCurrentRow(0)
        self.window.book_list.item(1).setSelected(True)
        self.app.processEvents()
        for widget in (self.window.detail_button, self.window.read_button):
            point = widget.mapTo(self.window, QPoint(0, 0))
            self.assertLessEqual(point.x() + widget.width(), 680)
        for dialog in (BatchEditDialog(self.library, self.ids, self.window), SmartShelvesDialog(self.library, {}, self.window), RulesDialog(self.library, self.window), StatisticsDialog(self.library, self.window)):
            dialog.show()
            self.app.processEvents()
            self.assertLessEqual(dialog.width(), 760)
            self.assertLessEqual(dialog.height(), 800)
            self.screenshot(dialog, type(dialog).__name__ + "-nogal.png")
            dialog.close()
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
