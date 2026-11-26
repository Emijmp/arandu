"""Historial, posición exacta, EPUB paginado, índices y reparación verificable."""

import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QTextCursor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog

from backups import create_backup, restore_backup
from book_navigation import epub_outline
from qt_helpers import drain_qt_events

from desktop import MainWindow
from library import Library
from review_dialogs import DiagnosticsDialog, GlobalAnnotationsDialog, HistoryDialog
from test_arandu import make_epub
from test_desktop import make_pdf


def long_epub(path, ncx=False):
    paragraphs = "".join(f'<p>Párrafo {index:03d}. Una biblioteca reúne historias, preguntas y recuerdos. Leer con calma permite encontrar ideas para volver a ellas después.</p>' for index in range(80))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>')
        nav = '<item id="nav" href="toc.ncx" media-type="application/x-dtbncx+xml"/>' if ncx else '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        archive.writestr("OEBPS/book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Lecturas para volver</dc:title><dc:creator>Autora local</dc:creator></metadata><manifest><item id="one" href="one.xhtml" media-type="application/xhtml+xml"/><item id="two" href="two.xhtml" media-type="application/xhtml+xml"/>' + nav + '</manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>')
        archive.writestr("OEBPS/one.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Parte primera</h1>' + paragraphs + '<h2 id="mitad">Otra mirada</h2>' + paragraphs + '</body></html>')
        archive.writestr("OEBPS/two.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Parte segunda</h1>' + paragraphs + '</body></html>')
        if ncx:
            archive.writestr("OEBPS/toc.ncx", '<ncx><navMap><navPoint><navLabel><text>Parte primera</text></navLabel><content src="one.xhtml"/><navPoint><navLabel><text>Otra mirada</text></navLabel><content src="one.xhtml#mitad"/></navPoint></navPoint><navPoint><navLabel><text>Parte segunda</text></navLabel><content src="two.xhtml"/></navPoint></navMap></ncx>')
        else:
            archive.writestr("OEBPS/nav.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol><li><a href="one.xhtml">Parte primera</a><ol><li><a href="one.xhtml#mitad">Otra mirada</a></li></ol></li><li><a href="two.xhtml">Parte segunda</a></li><li><a href="https://example.invalid">Enlace externo omitido</a></li></ol></nav></body></html>')


class ReadingStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data")
        self.pdf, self.epub = self.root / "Historia.pdf", self.root / "Novela.epub"
        make_pdf(self.pdf, title="Documento original")
        make_epub(self.epub)
        self.library.add(self.pdf)
        self.library.add(self.epub)
        self.ids = [book["id"] for book in self.library.listing()["books"]]

    def tearDown(self):
        self.temp.cleanup()

    def test_batch_history_is_one_atomic_action_and_redo_survives_restart(self):
        self.library.batch_update(self.ids, {"tags": ["Investigar"], "categories": ["Historia"], "status": "finished"})
        self.assertEqual(len(self.library.history_entries()), 1)
        self.library.history_step()
        self.assertTrue(all(not book["tags"] and book["status"] == "pending" and book["progress"] == 0 for book in self.library.listing()["books"]))
        self.assertEqual(self.library.reading_stats(2026)["books"], 0)
        reopened = Library(self.library.data_dir)
        reopened.history_step(redo=True)
        self.assertTrue(all(book["tags"] == ["Investigar"] and book["status"] == "finished" for book in reopened.listing()["books"]))

    def test_taxonomy_undo_restores_memberships_shelf_and_rule_references(self):
        book_id = self.ids[0]
        self.library.update(book_id, {"tags": ["Antes"]})
        self.library.save_shelf("Buscar etiqueta", {"tag": "Antes"})
        self.library.save_rule("Documentos", {"format": "PDF"}, {"tags": ["Antes"]})
        self.library.save_taxonomy("tags", "Después", "Antes")
        self.library.update(book_id, {"chapter": 1, "progress": 24}, _record=False)
        self.library.history_step()
        self.assertEqual(self.library.get(book_id)["tags"], ["Antes"])
        self.assertEqual(self.library.smart_shelves()[0]["filters"]["tag"], "Antes")
        self.assertEqual(self.library.rules()[0]["actions"]["tags"], ["Antes"])
        self.assertEqual(self.library.get(book_id)["chapter"], 1)
        self.assertEqual(self.library.get(book_id)["progress"], 24)

    def test_rules_apply_as_one_action_and_undo_does_not_disable_the_rule(self):
        self.library.save_rule("Todos", {"folder": str(self.root)}, {"tags": ["Automática"]})
        self.assertEqual(self.library.apply_all_rules(), 2)
        self.assertEqual(len(self.library.history_entries()), 1)
        self.library.history_step()
        self.assertTrue(all(book["tags"] == [] for book in self.library.listing()["books"]))
        self.assertEqual(len(self.library.rules()), 1)
        self.library.history_step(True)
        self.assertTrue(all(book["tags"] == ["Automática"] for book in self.library.listing()["books"]))

    def test_conflict_blocks_entire_undo_and_new_edit_discards_redo(self):
        self.library.batch_update(self.ids, {"author": "Nombre nuevo"})
        with self.library.connect() as db:
            db.execute("UPDATE books SET author='Cambio externo' WHERE id=?", (self.ids[1],))
        with self.assertRaises(ValueError):
            self.library.history_step()
        self.assertEqual(self.library.get(self.ids[0])["author"], "Nombre nuevo")
        with self.library.connect() as db:
            db.execute("UPDATE books SET author='Nombre nuevo' WHERE id=?", (self.ids[1],))
        self.library.history_step()
        self.library.update(self.ids[0], {"tags": ["Otra decisión"]})
        with self.assertRaises(ValueError):
            self.library.history_step(True)
        self.assertTrue(any(entry["state"] == "discarded" for entry in self.library.history_entries()))

    def test_undo_annotation_and_backup_preserve_history_and_exact_reading_state(self):
        book_id = self.ids[0]
        annotation_id = self.library.add_annotation(book_id, 0, "note", quote="Cita", note="Nota")
        self.library.history_step()
        self.assertEqual(self.library.annotations(book_id), [])
        self.library.history_step(True)
        self.assertEqual(self.library.annotations(book_id)[0]["id"], annotation_id)
        payload = {"chapter": 0, "mode": "double", "anchor": 1200, "anchor_offset": -2}
        self.library.save_reading_state(book_id, payload)
        backup = self.root / "reading.zip"
        create_backup(self.library, backup)
        restored = Library(self.root / "restored")
        restore_backup(restored, backup)
        self.assertEqual(restored.reading_state(book_id), payload)
        self.assertEqual(len(restored.history_entries()), 1)
        restored.history_step()
        self.assertEqual(restored.annotations(book_id), [])

    def test_global_annotations_searches_quotes_notes_and_fiche_with_visibility_filters(self):
        self.library.add_annotation(self.ids[0], 0, "highlight", quote="Historia del Paraguay", note="Investigar después")
        self.library.update(self.ids[1], {"notes": "Una idea general"})
        self.assertEqual(len(self.library.annotation_overview()), 2)
        self.assertEqual(len(self.library.annotation_overview("despues", "highlight")), 1)
        self.assertEqual(len(self.library.annotation_overview("idea", "general")), 1)
        self.library.set_hidden([self.ids[0]])
        self.assertEqual(len(self.library.annotation_overview()), 1)
        self.assertEqual(len(self.library.annotation_overview(include_apart=True)), 2)

    def test_diagnostics_detects_missing_changed_and_invalid_files_and_reindexes(self):
        self.pdf.rename(self.root / "Movido.pdf")
        issues = self.library.diagnose()
        self.assertEqual([issue["kind"] for issue in issues], ["missing"])
        self.pdf.write_bytes((self.root / "Movido.pdf").read_bytes())
        os.utime(self.epub, (time.time() + 5, time.time() + 5))
        self.assertIn("stale_index", [issue["kind"] for issue in self.library.diagnose()])
        self.assertEqual(self.library.repair_indexes(self.ids)["ok"], 2)
        self.assertEqual(self.library.diagnose(), [])
        self.pdf.write_text("PDF dañado")
        self.assertIn("read_error", [issue["kind"] for issue in self.library.diagnose()])

    def test_batch_relink_finds_exact_contents_and_keeps_notes_annotations_and_state(self):
        for book_id in self.ids:
            self.library.update(book_id, {"notes": "Conservar", "tags": ["Mi etiqueta"]})
            self.library.add_annotation(book_id, 0, "note", note="Idea")
            self.library.save_reading_state(book_id, {"chapter": 0, "anchor": 42})
        moved = self.root / "Nueva ubicación"
        moved.mkdir()
        self.pdf.rename(moved / "Otro nombre.pdf")
        self.epub.rename(moved / "Otro nombre.epub")
        result = self.library.repair_missing(self.ids, moved)
        self.assertEqual((result["ok"], result["errors"]), (2, []))
        self.assertEqual(self.library.diagnose(), [])
        for book_id in self.ids:
            self.assertEqual(self.library.get(book_id)["notes"], "Conservar")
            self.assertEqual(self.library.annotations(book_id)[0]["note"], "Idea")
            self.assertEqual(self.library.reading_state(book_id)["anchor"], 42)

    def test_epub_outline_preserves_nav_and_ncx_hierarchy_and_anchors(self):
        for ncx in (False, True):
            path = self.root / f"outline-{ncx}.epub"
            long_epub(path, ncx)
            outline = epub_outline(path)
            self.assertEqual(len(outline), 2)
            self.assertEqual(outline[0]["children"][0]["target"], {"chapter": 0, "anchor": "mitad"})
            self.assertEqual(outline[1]["target"]["chapter"], 1)


class ReadingWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data")
        self.epub, self.pdf = self.root / "Lecturas.epub", self.root / "Documento.pdf"
        long_epub(self.epub)
        make_pdf(self.pdf)
        self.library.add(self.epub)
        self.library.add(self.pdf)
        self.window = MainWindow(self.library)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.wait(lambda: not self.window.busy)
        self.window.close()
        self.window.deleteLater()
        drain_qt_events(self.app)
        self.temp.cleanup()

    def wait(self, condition, seconds=12):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(20)
        self.fail("La operación no terminó a tiempo.")

    def open_reader(self, kind):
        book = next(book for book in self.library.listing()["books"] if book["format"] == kind)
        self.window.open_book(book["id"])
        reader = next(reader for reader in self.window.readers if reader.book["id"] == book["id"])
        if kind == "EPUB":
            self.wait(lambda: bool(reader.viewer.browser.content))
        else:
            self.wait(lambda: bool(reader.viewer.page.words))
        return reader

    def screenshot(self, widget, name):
        folder = os.environ.get("ARANDU_TEST_ARTIFACTS")
        if folder:
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            self.app.processEvents()
            self.assertTrue(widget.grab().save(str(target / name)))

    def test_epub_reopens_at_exact_scroll_and_reading_does_not_create_history(self):
        reader = self.open_reader("EPUB")
        bar = reader.viewer.browser.verticalScrollBar()
        self.assertGreater(bar.maximum(), 4000)
        bar.setValue(1735)
        self.app.processEvents()
        saved = reader.viewer.state()
        reader.close()
        self.app.processEvents()
        self.assertEqual(self.library.history_entries(), [])
        reopened = self.open_reader("EPUB")
        self.wait(lambda: reopened.viewer.browser.verticalScrollBar().value() > 1000)
        self.assertLessEqual(abs(reopened.viewer.browser.verticalScrollBar().value() - saved["scroll_y"]), 3)

    def test_pdf_restores_zoom_scroll_and_thumbnail_navigation(self):
        reader = self.open_reader("PDF")
        viewer = reader.viewer
        viewer.zoom.setCurrentIndex(viewer.zoom.findData(1.5))
        self.wait(lambda: viewer.last_image.width() == 1275 and viewer.render_process.state() == 0)
        self.app.processEvents()
        viewer.scroll.verticalScrollBar().setValue(round(viewer.scroll.verticalScrollBar().maximum() * 0.45))
        expected = viewer.state()["scroll_y"]
        reader.close()
        self.app.processEvents()
        reopened = self.open_reader("PDF")
        self.wait(lambda: reopened.viewer._restore_page is None)
        self.assertEqual(reopened.viewer.zoom.currentData(), 1.5)
        self.assertLess(abs(reopened.viewer.state()["scroll_y"] - expected), 0.02)
        reopened.resize(1100, 980)
        self.app.processEvents()
        self.assertLess(abs(reopened.viewer.state()["scroll_y"] - expected), 0.02)
        self.wait(lambda: not reopened.navigator.items.item(0).icon().isNull())
        QTest.mouseClick(reopened.navigator.items.viewport(), Qt.LeftButton, pos=reopened.navigator.items.visualItemRect(reopened.navigator.items.item(1)).center())
        self.wait(lambda: reopened.viewer.index == 1 and bool(reopened.viewer.page.words))
        self.screenshot(reopened, "pdf-thumbnails.png")

    def test_epub_single_double_pages_selection_highlights_and_resume(self):
        reader = self.open_reader("EPUB")
        viewer, browser = reader.viewer, reader.viewer.browser
        reader.page_mode_actions["single"].trigger()
        browser.set_page(0)
        self.assertGreater(browser.page_count, 4)
        QTest.mouseClick(viewer.next, Qt.LeftButton)
        self.assertEqual((viewer.index, browser.page_index), (0, 1))
        self.screenshot(reader, "epub-single.png")
        reader.page_mode_actions["double"].trigger()
        browser.set_page(0)
        QTest.mouseClick(viewer.next, Qt.LeftButton)
        self.assertEqual(browser.page_index, 2)
        cursor = browser.document().find("Párrafo 010")
        browser.setTextCursor(cursor)
        browser.set_page(int(browser.document_y(cursor.position()) / browser.page_height))
        QTest.keyClick(browser, Qt.Key_C, Qt.ControlModifier)
        self.assertIn("Párrafo 010", self.app.clipboard().text())
        reader.add_highlight()
        self.assertEqual(self.library.annotations(reader.book["id"])[0]["kind"], "highlight")
        page = browser.page_index
        self.screenshot(reader, "epub-double-selected.png")
        reader.close()
        self.app.processEvents()
        reopened = self.open_reader("EPUB")
        self.wait(lambda: reopened.viewer.browser.page_mode == "double")
        self.assertEqual(reopened.viewer.browser.page_index, page)
        reopened.page_mode_actions["continuous"].trigger()
        self.assertGreater(reopened.viewer.browser.verticalScrollBar().maximum(), 1000)

    def test_hierarchical_outline_opens_nested_anchor_and_sidebar_hides_on_small_windows(self):
        reader = self.open_reader("EPUB")
        nested = reader.navigator.topLevelItem(0).child(0)
        reader.navigator.activate(nested)
        self.assertEqual(reader.viewer.index, 0)
        self.assertGreater(reader.viewer.browser.verticalScrollBar().value(), 3000)
        reader.page_mode_actions["single"].trigger()
        reader.viewer.browser.set_page(0)
        reader.navigator.activate(nested)
        self.assertGreater(reader.viewer.browser.page_index, 4)
        self.assertEqual(reader.navigator.currentItem(), nested)
        reader.resize(680, 560)
        self.app.processEvents()
        self.assertFalse(reader.navigator.isVisible())
        self.screenshot(reader, "epub-small.png")
        reader.resize(1100, 820)
        self.app.processEvents()
        self.assertTrue(reader.navigator.isVisible())

    def test_history_dialog_undo_redo_and_global_annotations_open_the_location(self):
        book = next(book for book in self.library.listing()["books"] if book["format"] == "EPUB")
        self.library.batch_update([book["id"]], {"tags": ["Revisar"]})
        dialog = HistoryDialog(self.library, self.window)
        dialog.changed.connect(self.window.history_changed)
        dialog.show()
        QTest.mouseClick(dialog.undo_button, Qt.LeftButton)
        self.assertEqual(self.library.get(book["id"])["tags"], [])
        QTest.mouseClick(dialog.redo_button, Qt.LeftButton)
        self.assertEqual(self.library.get(book["id"])["tags"], ["Revisar"])
        self.screenshot(dialog, "change-history.png")
        dialog.close()
        dialog.deleteLater()
        self.library.add_annotation(book["id"], 1, "note", quote="Párrafo 010", note="Nota para volver")
        annotations = GlobalAnnotationsDialog(self.library, self.window)
        annotations.annotationSelected.connect(self.window.open_annotation)
        annotations.show()
        annotations.query.setText("volver")
        annotations.reload()
        self.assertEqual(annotations.items.topLevelItemCount(), 1)
        self.screenshot(annotations, "global-annotations.png")
        QTest.mouseClick(annotations.open_button, Qt.LeftButton)
        self.assertFalse(annotations.timer.isActive())
        self.wait(lambda: bool(self.window.readers))
        reader = next(iter(self.window.readers))
        self.wait(lambda: reader.viewer.index == 1)
        self.assertEqual(reader.viewer.browser.textCursor().selectedText(), "Párrafo 010")
        annotations.deleteLater()

    def test_diagnostic_dialog_repairs_multiple_missing_files_in_background(self):
        moved = self.root / "Movidos"
        moved.mkdir()
        self.epub.rename(moved / self.epub.name)
        self.pdf.rename(moved / self.pdf.name)
        dialog = DiagnosticsDialog(self.library, self.window)
        dialog.changed.connect(self.window.reload)
        dialog.show()
        QTest.mouseClick(dialog.scan_button, Qt.LeftButton)
        self.wait(lambda: dialog.worker.isFinished())
        self.app.processEvents()
        self.assertEqual(dialog.items.topLevelItemCount(), 2)
        dialog.items.selectAll()
        self.screenshot(dialog, "library-diagnostics.png")
        with patch("review_dialogs.QFileDialog.getExistingDirectory", return_value=str(moved)):
            QTest.mouseClick(dialog.locate_button, Qt.LeftButton)
        self.wait(lambda: dialog.worker.isFinished())
        self.app.processEvents()
        self.assertEqual(dialog.items.topLevelItemCount(), 0)
        self.assertTrue(all(book["available"] for book in self.library.listing()["books"]))
        dialog.close()
        dialog.deleteLater()

    def test_paged_mouse_selection_and_dark_theme_keep_text_readable(self):
        reader = self.open_reader("EPUB")
        reader.page_mode_actions["single"].trigger()
        browser = reader.viewer.browser
        cursor = browser.document().find("Párrafo 001")
        y = browser.document_y(cursor.selectionStart())
        browser.set_page(int(y / browser.page_height))
        rect = browser.page_rect(0)
        point = QPoint(round(rect.left() + browser.document().documentMargin() + 35), round(rect.top() + y - browser.page_index * browser.page_height + 12))
        QTest.mouseDClick(browser.viewport(), Qt.LeftButton, pos=point)
        self.assertTrue(browser.textCursor().hasSelection())
        QTest.keyClick(browser, Qt.Key_C, Qt.ControlModifier)
        self.assertTrue(self.app.clipboard().text())
        self.window.set_theme("nogal")
        reader.page_mode_actions["double"].trigger()
        self.screenshot(reader, "epub-double-nogal.png")

    def test_cancelled_diagnostic_repair_keeps_unverified_issues_visible(self):
        self.pdf.unlink()
        dialog = DiagnosticsDialog(self.library, self.window)
        dialog.completed({"issues": self.library.diagnose()})
        count = dialog.items.topLevelItemCount()
        dialog.completed({"issues": None, "report": {"ok": 0, "errors": [], "cancelled": True}})
        self.assertEqual(dialog.items.topLevelItemCount(), count)
        self.assertIn("detenido", dialog.status.text())
        dialog.close()
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
