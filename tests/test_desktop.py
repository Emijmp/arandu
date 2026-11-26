"""Pruebas de la aplicación Qt con documentos reales y plataforma offscreen."""

import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QFont, QTextDocument
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog

from qt_helpers import drain_qt_events

from desktop import DetailDialog, MainWindow
from library import Library
from readers import ArchiveBrowser, EPUBReader, PDFReader, ReaderWindow
from test_arandu import make_epub


def make_pdf(path, title=None, author=None):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 600] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>",
        None,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 600] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index, page_title in ((3, "Primera pagina"), (5, "Segunda pagina")):
        content = f"BT /F1 24 Tf 50 500 Td ({page_title}) Tj ET\n".encode()
        objects[index] = b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream"
    info_reference = ""
    if title or author:
        entries = []
        for key, value in (("Title", title), ("Author", author)):
            if value:
                encoded = (b"\xfe\xff" + value.encode("utf-16be")).hex()
                entries.append(f"/{key} <{encoded}>")
        objects.append(("<< " + " ".join(entries) + " >>").encode())
        info_reference = f" /Info {len(objects)} 0 R"
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R{info_reference} >>\nstartxref\n{start}\n%%EOF\n".encode())
    path.write_bytes(output)


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")
        cls.app.setFont(QFont("Sans Serif", 11))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data")
        self.epub = self.root / "Libro.epub"
        self.pdf = self.root / "Mi PDF.pdf"
        make_epub(self.epub)
        make_pdf(self.pdf)
        self.window = MainWindow(self.library)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.wait_until(lambda: not self.window.busy)
        self.window.close()
        self.window.deleteLater()
        drain_qt_events(self.app)
        self.temp.cleanup()

    def wait_until(self, condition, seconds=8):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(20)
        self.fail("La operación de la interfaz no terminó a tiempo.")

    def screenshot(self, name):
        folder = os.environ.get("ARANDU_TEST_ARTIFACTS")
        if folder:
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            self.app.processEvents()
            self.assertTrue(self.window.grab().save(str(target / name)))

    def test_empty_window_native_file_import_search_and_favorites(self):
        self.assertEqual(self.window.stack.currentWidget(), self.window.empty_widget)
        self.screenshot("desktop-empty.png")
        with patch("desktop.QFileDialog.getOpenFileNames", return_value=([str(self.epub), str(self.pdf)], "")):
            QTest.mouseClick(self.window.add_button, Qt.LeftButton)
        self.wait_until(lambda: not self.window.busy)
        self.assertEqual(self.window.book_list.count(), 2)
        self.window.search.setText("autora")
        self.assertEqual(self.window.book_list.count(), 1)
        self.window.book_list.setCurrentRow(0)
        QTest.mouseClick(self.window.favorite_button, Qt.LeftButton)
        self.window.set_view("favorites")
        self.assertEqual(self.window.book_list.count(), 1)
        self.assertTrue(self.library.listing()["books"][1]["favorite"])
        self.window.search.clear()
        self.window.set_view("all")
        self.screenshot("desktop-library.png")
        self.assertTrue(self.epub.exists())
        self.assertTrue(self.pdf.exists())

    def test_detail_dialog_saves_metadata_notes_and_completion(self):
        self.library.add(self.epub)
        book = self.library.listing()["books"][0]
        dialog = DetailDialog(self.library, book, self.window)
        dialog.show()
        dialog.title_edit.setText("Una historia corregida")
        dialog.notes_edit.setPlainText("Una nota para recordar.")
        dialog.tags_edit.setText("Ficción, Paraguay")
        dialog.categories_edit.setText("Novela, Literatura paraguaya")
        dialog.status_combo.setCurrentIndex(dialog.status_combo.findData("finished"))
        dialog.save()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        saved = Library(self.root / "data").get(book["id"])
        self.assertEqual((saved["title"], saved["status"], saved["progress"]), ("Una historia corregida", "finished", 100))
        self.assertEqual(saved["tags"], ["Ficción", "Paraguay"])
        self.assertEqual(saved["categories"], ["Novela", "Literatura paraguaya"])
        self.assertEqual(saved["notes"], "Una nota para recordar.")
        dialog.deleteLater()

    def test_epub_reader_navigation_and_resume_are_persistent(self):
        self.library.add(self.epub)
        book = self.library.listing()["books"][0]
        self.window.open_book(book["id"])
        reader = next(iter(self.window.readers))
        self.wait_until(lambda: "Una historia." in reader.viewer.browser.toPlainText())
        self.assertNotIn("window.parent", reader.viewer.browser.toPlainText())
        QTest.mouseClick(reader.viewer.next, Qt.LeftButton)
        self.assertIn("Continuará.", reader.viewer.browser.toPlainText())
        self.assertEqual(self.library.get(book["id"])["chapter"], 1)
        reader.close()
        self.app.processEvents()
        self.window.open_book(book["id"])
        reader = next(iter(self.window.readers))
        self.wait_until(lambda: "Continuará." in reader.viewer.browser.toPlainText())
        QTest.mouseClick(reader.finish_button, Qt.LeftButton)
        self.assertEqual(self.library.get(book["id"])["status"], "finished")

    def test_epub_resources_never_load_network_or_local_files(self):
        browser = ArchiveBrowser(self.epub)
        self.assertIsNone(browser.loadResource(QTextDocument.ImageResource, QUrl("https://example.com/test.png")))
        self.assertIsNone(browser.loadResource(QTextDocument.HtmlResource, QUrl.fromLocalFile("/etc/passwd")))
        self.assertIsNone(browser.loadResource(QTextDocument.HtmlResource, QUrl("epub:///../secret")))
        self.assertIsNotNone(browser.loadResource(QTextDocument.HtmlResource, QUrl("epub:///OEBPS/one.xhtml")))
        browser.deleteLater()

    @unittest.skipUnless(shutil.which("pdfinfo") and shutil.which("pdftoppm"), "Poppler no está instalado")
    def test_pdf_reader_renders_pages_and_restores_last_page(self):
        self.library.add(self.pdf)
        book = self.library.listing()["books"][0]
        self.window.open_book(book["id"])
        reader = next(iter(self.window.readers))
        self.wait_until(lambda: not reader.viewer.last_image.isNull())
        self.assertEqual(reader.viewer.page_count, 2)
        QTest.mouseClick(reader.viewer.next, Qt.LeftButton)
        self.wait_until(lambda: self.library.get(book["id"])["chapter"] == 1)
        reader.close()
        self.app.processEvents()
        self.window.open_book(book["id"])
        reader = next(iter(self.window.readers))
        self.wait_until(lambda: not reader.viewer.last_image.isNull())
        self.assertEqual(reader.viewer.index, 1)
        self.assertEqual(reader.viewer.page_box.value(), 2)
        original_page_color = reader.viewer.last_image.pixelColor(10, 10)
        self.window.set_theme("grafito")
        self.app.processEvents()
        self.assertEqual(reader.viewer.index, 1)
        self.assertEqual(reader.viewer.last_image.pixelColor(10, 10), original_page_color)
        self.assertEqual(self.library.get(book["id"])["chapter"], 1)

    def test_list_layout_survives_restart(self):
        self.window.set_layout("list")
        self.window.close()
        self.window = MainWindow(self.library)
        self.assertEqual(self.window.layout_mode, "list")


if __name__ == "__main__":
    unittest.main()
