"""Paletas, fuentes reales, persistencia y adaptación de la ventana nativa."""

import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, Qt, QUrl
from PyQt5.QtGui import QFont, QFontDatabase, QFontInfo, QPalette
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from qt_helpers import drain_qt_events

from desktop import DetailDialog, MainWindow
from library import Library
from test_arandu import PDF, make_epub
from themes import DEFAULT_THEME, THEMES, contrast_ratio


class PaletteTests(unittest.TestCase):
    def test_three_light_and_three_dark_classic_palettes(self):
        self.assertEqual(len(THEMES), 6)
        self.assertEqual(sum(theme.dark for theme in THEMES.values()), 3)
        self.assertEqual(len({theme.accent for theme in THEMES.values()}), 6)

    def test_text_contrast_meets_wcag_aa_in_all_six_themes(self):
        for theme in THEMES.values():
            for role, foreground, background in (
                ("principal", theme.text, theme.background),
                ("superficie", theme.text, theme.surface),
                ("selección", theme.text, theme.selected),
                ("secundario", theme.muted, theme.background),
                ("metadatos", theme.muted, theme.surface),
                ("metadatos seleccionados", theme.muted, theme.selected),
                ("aviso", theme.text, theme.inset),
                ("botón", theme.on_accent, theme.accent),
                ("índice", theme.sidebar_text, theme.sidebar),
                ("índice secundario", theme.sidebar_muted, theme.sidebar),
                ("error", theme.error, theme.background),
                ("éxito", theme.success, theme.background),
            ):
                with self.subTest(theme=theme.name, role=role):
                    self.assertGreaterEqual(contrast_ratio(foreground, background), 4.5)

    def test_focus_color_has_nontext_contrast(self):
        for theme in THEMES.values():
            for background in (theme.background, theme.surface, theme.selected):
                with self.subTest(theme=theme.name, background=background):
                    self.assertGreaterEqual(contrast_ratio(theme.accent, background), 3)


class ThemeWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data")
        self.window = MainWindow(self.library)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        drain_qt_events(self.app)
        self.temp.cleanup()

    def screenshot(self, name, widget=None):
        target = os.environ.get("ARANDU_TEST_ARTIFACTS")
        if target:
            folder = Path(target)
            folder.mkdir(parents=True, exist_ok=True)
            self.app.processEvents()
            self.assertTrue((widget or self.window).grab().save(str(folder / name)))

    def populate(self):
        for title, author in (("La memoria del río", "Ana Ríos"), ("Cuaderno del lector", "Mateo Vera"), ("El último invierno", "Lucía Valle")):
            path = self.root / f"{title}.pdf"
            path.write_bytes(PDF)
            self.library.add(path)
            book = next(book for book in self.library.listing()["books"] if book["path"] == str(path))
            self.library.update(book["id"], {"author": author})
        self.window.reload()

    def test_bundled_montserrat_and_karla_include_real_regular_and_bold_faces(self):
        for family in ("Montserrat", "Karla"):
            self.assertIn(family, QFontDatabase().families())
            self.assertIn("Regular", QFontDatabase().styles(family))
            self.assertIn("Bold", QFontDatabase().styles(family))
            self.assertEqual(QFontInfo(QFont(family, 12, QFont.Bold)).family(), family)
            self.assertEqual(QFontInfo(QFont(family, 12, QFont.Bold)).weight(), QFont.Bold)
        self.assertEqual(self.window.title_label.font().family(), "Montserrat")
        self.assertEqual(self.window.search.font().family(), "Karla")

    def test_theme_selector_updates_every_palette_and_saves_immediately(self):
        self.populate()
        self.window.book_list.setCurrentRow(0)
        selected_id = self.window.selected_book()["id"]
        for theme in THEMES.values():
            with self.subTest(theme=theme.name):
                self.window.theme_combo.setCurrentIndex(self.window.theme_combo.findData(theme.id))
                self.assertEqual(self.window.theme.id, theme.id)
                self.assertEqual(self.app.palette().color(QPalette.Window).name(), theme.background)
                self.assertEqual(self.window.delegate.theme.id, theme.id)
                self.assertTrue(self.window.theme_actions[theme.id].isChecked())
                self.assertEqual(self.window.selected_book()["id"], selected_id)
                self.assertEqual(self.window.search.palette().color(QPalette.PlaceholderText).name(), theme.muted)
                self.assertEqual(self.window.search.palette().color(QPalette.PlaceholderText).alpha(), 255)
                self.screenshot(f"theme-{theme.id}.png")
                self.screenshot(f"logo-{theme.id}.png", self.window.brand)
                artifact_folder = os.environ.get("ARANDU_TEST_ARTIFACTS")
                if artifact_folder:
                    self.window.windowIcon().pixmap(256, 256).save(str(Path(artifact_folder) / f"icon-{theme.id}.png"))
        settings = json.loads((self.library.data_dir / "window.json").read_text())
        self.assertEqual(settings["theme"], "tinta")

    def test_theme_and_layout_survive_restart_and_old_preferences_are_valid(self):
        self.window.set_theme("nogal")
        self.window.set_layout("list")
        self.window.close()
        self.window = MainWindow(self.library)
        self.assertEqual((self.window.theme.id, self.window.layout_mode), ("nogal", "list"))
        self.window.close()
        (self.library.data_dir / "window.json").write_text('{"layout":"grid"}')
        self.window = MainWindow(self.library)
        self.assertEqual(self.window.theme.id, DEFAULT_THEME)

    def test_menu_changes_theme_and_keeps_search_and_selection(self):
        self.populate()
        self.window.search.setText("rio")
        self.window.book_list.setCurrentRow(0)
        selected_id = self.window.selected_book()["id"]
        self.window.theme_actions["grafito"].trigger()
        self.assertEqual(self.window.theme_combo.currentData(), "grafito")
        self.assertEqual(self.window.search.text(), "rio")
        self.assertEqual(self.window.selected_book()["id"], selected_id)

    def test_theme_changes_open_epub_reader_without_losing_chapter(self):
        path = self.root / "book.epub"
        make_epub(path)
        self.library.add(path)
        book = self.library.listing()["books"][0]
        self.window.open_book(book["id"])
        self.app.processEvents()
        reader = next(iter(self.window.readers))
        reader.viewer.navigate(1)
        self.window.set_theme("grafito")
        self.app.processEvents()
        self.assertEqual(reader.viewer.browser.palette().color(QPalette.Base).name(), THEMES["grafito"].surface)
        self.assertIn("Continuará.", reader.viewer.browser.toPlainText())
        self.assertEqual(reader.viewer.index, 1)
        self.assertEqual(self.library.get(book["id"])["chapter"], 1)
        first = reader.viewer.browser.document().begin().begin().fragment()
        self.assertEqual(first.charFormat().foreground().color().name(), THEMES["grafito"].text)
        self.screenshot("reader-grafito.png", reader)

    def test_dark_epub_theme_overrides_book_inline_background_and_text_colors(self):
        from readers import ArchiveBrowser
        path = self.root / "book.epub"
        make_epub(path)
        browser = ArchiveBrowser(path)
        browser.set_content('<html><body style="background:white;color:black"><h1>Título</h1><p style="color:black">Una página legible.</p></body></html>', QUrl("epub:///OEBPS/one.xhtml"))
        browser.set_theme(THEMES["tinta"])
        self.assertEqual(browser.document().rootFrame().frameFormat().background().color().name(), THEMES["tinta"].surface)
        fragment = browser.document().lastBlock().begin().fragment()
        self.assertEqual(fragment.charFormat().foreground().color().name(), THEMES["tinta"].text)
        self.assertEqual(fragment.charFormat().fontFamily(), "Karla")
        browser.deleteLater()

    def test_detail_dialog_uses_current_theme_and_heading_font(self):
        self.populate()
        self.window.set_theme("nogal")
        book = self.library.listing()["books"][0]
        dialog = DetailDialog(self.library, book, self.window)
        dialog.show()
        self.app.processEvents()
        self.assertEqual(dialog.palette().color(QPalette.Window).name(), THEMES["nogal"].background)
        self.assertEqual(dialog.title_edit.font().family(), "Karla")
        self.screenshot("detail-nogal.png", dialog)
        dialog.close()
        dialog.deleteLater()

    def test_small_windows_keep_primary_actions_inside_the_window(self):
        self.populate()
        self.window.book_list.setCurrentRow(0)
        for width, height in ((680, 560), (900, 650), (1280, 870)):
            self.window.resize(width, height)
            self.app.processEvents()
            with self.subTest(size=(width, height)):
                self.assertEqual(self.window.width(), width)
                for widget in (self.window.theme_combo, self.window.add_button, self.window.folder_button, self.window.search, self.window.read_button, self.window.detail_button, self.window.list_button, self.window.taxonomy_filter_button):
                    self.assertTrue(widget.isVisible())
                    top_left = widget.mapTo(self.window, QPoint(0, 0))
                    self.assertGreaterEqual(top_left.x(), 0)
                    self.assertGreaterEqual(top_left.y(), 0)
                    self.assertLessEqual(top_left.x() + widget.width(), width)
                    self.assertLessEqual(top_left.y() + widget.height(), height)
                self.screenshot(f"desktop-{width}.png")
        self.window.set_theme("tinta")
        self.window.resize(680, 560)
        self.app.processEvents()
        self.window.book_list.setFocus()
        self.window.book_list.setCurrentRow(0)
        QTest.keyClick(self.window.book_list, Qt.Key_End)
        self.assertEqual(self.window.book_list.currentRow(), self.window.book_list.count() - 1)
        self.screenshot("desktop-compact-tinta.png")

    def test_empty_loading_error_and_success_states(self):
        self.assertEqual(self.window.stack.currentWidget(), self.window.empty_widget)
        for theme_id in ("papel", "grafito"):
            self.window.set_theme(theme_id)
            self.screenshot(f"state-empty-{theme_id}.png")
            for state, message in (("loading", "Buscando PDF y EPUB en tu carpeta Libros…"), ("error", "No pudimos leer un EPUB. Los demás libros ya están en su lugar."), ("success", "Tres libros encontraron su lugar en el estante.")):
                self.window.notice.announce(state, message)
                self.assertEqual(self.window.notice.progress.isVisible(), state == "loading")
                self.assertEqual(self.window.notice.property("state"), state)
                self.screenshot(f"state-{state}-{theme_id}.png")
            self.window.notice.hide()


if __name__ == "__main__":
    unittest.main()
