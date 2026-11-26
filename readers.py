"""Lectores nativos: PDF con Poppler y EPUB con QTextBrowser aislado."""

from pathlib import Path
import posixpath
import re
import shutil
import sqlite3
from urllib.parse import unquote
import zipfile
import xml.etree.ElementTree as ET

from PyQt5.QtCore import QByteArray, QProcess, QProcessEnvironment, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QImage, QPalette, QPixmap, QTextCharFormat, QTextCursor, QTextDocument, QTextBlockFormat
from PyQt5.QtWidgets import (
    QAction, QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QPushButton, QScrollArea, QTextEdit, QToolButton, QMenu,
    QSpinBox, QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from library import epub_chapters, read_epub_entry
from branding import app_icon
from themes import current_theme, font
from extraction import parse_pdf_words
from pdf_page import SelectablePDFPage
from library_dialogs import ContentSearchDialog
from reading_tools import AnnotationsDialog, DEFAULT_READING, ReadingSettingsDialog, reading_settings
from activity import ReadingTracker
from ocr import OCRDialog
from paged_text import PaginationMixin
from book_navigation import EPUBOutlinePane, PDFThumbnailPane


class ArchiveBrowser(PaginationMixin, QTextBrowser):
    """Only resolve resources from the open EPUB, never the disk or network."""
    pageChanged = pyqtSignal(int, int)
    pageTurnRequested = pyqtSignal(int)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.init_pagination()
        self.path = Path(path)
        self.theme = None
        self.content = ""
        self.reading = dict(DEFAULT_READING)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.document().setDocumentMargin(45)
        self.set_theme(current_theme())

    def set_theme(self, theme):
        if self.theme == theme:
            return
        position = self.verticalScrollBar().value()
        self.theme = theme
        self.setFont(QFont("Karla", self.reading["font_size"]))
        palette = self.palette()
        palette.setColor(QPalette.Base, QColor(theme.surface))
        palette.setColor(QPalette.Text, QColor(theme.text))
        self.setPalette(palette)
        self.document().setDefaultFont(QFont("Karla", self.reading["font_size"]))
        self.document().setDefaultStyleSheet(
            f"body {{color: {theme.text}; background-color: {theme.surface}; font-family: Karla; font-size: {self.reading['font_size']}pt;}}"
            "p {margin-top: 12px; margin-bottom: 16px;}"
            f"h1 {{font-family: Montserrat; font-size: {self.reading['font_size'] * 1.5}pt; font-weight: 700;}}"
            f"h2 {{font-family: Montserrat; font-size: {self.reading['font_size'] * 1.25}pt; font-weight: 600;}}"
            f"a {{color: {theme.accent};}} img {{max-width: 100%;}}"
        )
        if self.content:
            self.setHtml(self.content)
            self.apply_document_colors()
            self.verticalScrollBar().setValue(position)

    def set_content(self, content, base_url):
        self.content = content
        self.document().setBaseUrl(base_url)
        self.setHtml(content)
        self.apply_document_colors()
        self.layout_pages()

    def apply_document_colors(self):
        # EPUB inline colors must not leave white pages or unreadable black text
        # in a dark theme. Preserve emphasis, links, images and document layout.
        document = self.document()
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.Document)
        char_format = QTextCharFormat()
        char_format.setForeground(QColor(self.theme.text))
        char_format.setBackground(QBrush(Qt.NoBrush))
        char_format.setFontFamily("Karla")
        char_format.setFontPointSize(self.reading["font_size"])
        cursor.mergeCharFormat(char_format)
        frame_format = document.rootFrame().frameFormat()
        frame_format.setBackground(QColor(self.theme.surface))
        document.rootFrame().setFrameFormat(frame_format)
        block = document.begin()
        while block.isValid():
            block_format = block.blockFormat()
            block_format.setBackground(QBrush(Qt.NoBrush))
            block_format.setLineHeight(self.reading["line_spacing"], QTextBlockFormat.ProportionalHeight)
            cursor = QTextCursor(block)
            cursor.setBlockFormat(block_format)
            if block_format.headingLevel():
                cursor.select(QTextCursor.BlockUnderCursor)
                heading = QTextCharFormat()
                heading.setFontFamily("Montserrat")
                heading.setFontPointSize(self.reading["font_size"] * (1.5 if block_format.headingLevel() == 1 else 1.25))
                cursor.mergeCharFormat(heading)
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isAnchor():
                    cursor.setPosition(fragment.position())
                    cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.KeepAnchor)
                    link = QTextCharFormat()
                    link.setForeground(QColor(self.theme.accent))
                    link.setFontUnderline(True)
                    cursor.mergeCharFormat(link)
                iterator += 1
            block = block.next()

    def configure(self, values):
        state = self.anchor_state()
        self.reading = dict(values)
        self.document().setDocumentMargin(values["margin"])
        theme = self.theme or current_theme()
        self.theme = None
        self.set_theme(theme)
        self.layout_pages()
        self.restore_anchor(state)

    def loadResource(self, resource_type, url):
        if url.scheme() != "epub":
            return None
        entry = posixpath.normpath(unquote(url.path()).lstrip("/"))
        if entry.startswith("../") or entry in {"..", "."}:
            return None
        try:
            with zipfile.ZipFile(self.path) as archive:
                data = read_epub_entry(archive, entry)
            if resource_type == QTextDocument.ImageResource:
                image = QImage.fromData(data)
                return image if not image.isNull() else None
            if resource_type == QTextDocument.StyleSheetResource:
                return data.decode("utf-8", errors="replace")
            return QByteArray(data)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            return None


class EPUBReader(QWidget):
    positionChanged = pyqtSignal(int, int)
    stateChanged = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, book, parent=None, library=None):
        super().__init__(parent)
        self.library = library
        self.book = book
        self.resume = library.reading_state(book["id"]) if library else {}
        self.stopped = False
        self.chapters = epub_chapters(book["path"])
        self.index = min(book["chapter"], len(self.chapters) - 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.browser = ArchiveBrowser(book["path"], self)
        self.browser.setObjectName("epubPage")
        layout.addWidget(self.browser, 1)
        self.navigation = QWidget()
        controls = QHBoxLayout(self.navigation)
        controls.setContentsMargins(18, 12, 18, 12)
        self.previous = QPushButton("Anterior")
        self.next = QPushButton("Siguiente")
        self.chapter_box = QComboBox()
        self.chapter_box.setAccessibleName("Sección del libro")
        self.chapter_box.setMinimumWidth(200)
        self.chapter_box.addItems([chapter["title"] for chapter in self.chapters])
        self.counter = QLabel()
        controls.addWidget(self.previous)
        controls.addWidget(self.chapter_box, 1)
        controls.addWidget(self.counter)
        controls.addWidget(self.next)
        layout.addWidget(self.navigation)
        self.previous.clicked.connect(lambda: self.turn_page(-1))
        self.next.clicked.connect(lambda: self.turn_page(1))
        self.chapter_box.activated[int].connect(self.navigate)
        self.browser.anchorClicked.connect(self.follow_link)
        self.browser.verticalScrollBar().valueChanged.connect(lambda: self.stateChanged.emit())
        self.browser.pageChanged.connect(self.page_changed)
        self.browser.pageTurnRequested.connect(self.turn_page)
        QTimer.singleShot(0, lambda: self.navigate(self.index, restore=True))

    def navigate(self, index, restore=False, fragment="", last_page=False):
        if self.stopped or not 0 <= index < len(self.chapters):
            return
        try:
            entry = self.chapters[index]["path"]
            with zipfile.ZipFile(self.book["path"]) as archive:
                content = read_epub_entry(archive, entry).decode("utf-8-sig", errors="replace")
            self.index = index
            self.browser.set_content(content, QUrl("epub:///" + entry))
            self.browser.verticalScrollBar().setValue(0)
            self.browser.set_page(0)
            if restore:
                self.browser.set_page_mode(self.resume.get("mode", "continuous"))
                if self.resume.get("chapter") == index:
                    self.browser.restore_anchor(self.resume)
                    QTimer.singleShot(0, lambda: self.restore_initial(index))
            if last_page and self.browser.page_mode != "continuous":
                self.browser.set_page(self.browser.page_count - 1)
            if fragment:
                self.browser.scrollToAnchor(fragment)
            self.chapter_box.setCurrentIndex(index)
            self.update_counter()
            self.positionChanged.emit(index, len(self.chapters))
            self.stateChanged.emit()
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            self.error.emit(str(exc))

    def restore_initial(self, index):
        if not self.stopped and self.index == index:
            self.browser.restore_anchor(self.resume)

    def page_changed(self, page, total):
        self.update_counter()
        self.stateChanged.emit()

    def update_counter(self):
        paged = self.browser.page_mode != "continuous"
        page, total = self.browser.page_index, self.browser.page_count
        self.counter.setText(f"{page + 1}–{min(total, page + self.browser.spread_size)} / {total}" if paged and self.browser.spread_size == 2 and page + 1 < total else f"{page + 1} / {total}" if paged else f"{self.index + 1} / {len(self.chapters)}")
        self.previous.setEnabled(self.index > 0 or (paged and page > 0))
        self.next.setEnabled(self.index < len(self.chapters) - 1 or (paged and page + self.browser.spread_size < total))

    def turn_page(self, direction):
        browser = self.browser
        if browser.page_mode == "continuous":
            self.navigate(self.index + direction)
            return
        target = browser.page_index + direction * browser.spread_size
        if target < 0:
            self.navigate(self.index - 1, last_page=True)
        elif target >= browser.page_count:
            self.navigate(self.index + 1)
        else:
            browser.set_page(target)

    def set_mode(self, mode):
        self.browser.set_page_mode(mode)
        self.update_counter()
        self.stateChanged.emit()

    def state(self):
        return {**self.browser.anchor_state(), "chapter": self.index, "mode": self.browser.page_mode}

    def follow_link(self, url):
        if url.scheme() not in {"", "epub"}:
            return
        path = posixpath.normpath(unquote(url.path()).lstrip("/"))
        if not url.path():
            self.browser.scrollToAnchor(url.fragment())
            return
        for index, chapter in enumerate(self.chapters):
            if path == chapter["path"]:
                self.navigate(index)
                if url.fragment():
                    self.browser.scrollToAnchor(url.fragment())
                break

    def stop(self):
        self.stopped = True


class PDFReader(QWidget):
    positionChanged = pyqtSignal(int, int)
    stateChanged = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, book, parent=None, library=None):
        super().__init__(parent)
        self.library = library
        self.book = book
        self.resume = library.reading_state(book["id"]) if library else {}
        self._restore_page = book["chapter"]
        self.index = book["chapter"]
        self.page_count = 0
        self.last_image = QImage()
        self.displayed_index = None
        self._render_scroll = None
        self.stopped = False
        self.words_cache = {}
        self.pending_query = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("pdfScroll")
        self.scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.page = SelectablePDFPage(self)
        self.page.setAlignment(Qt.AlignCenter)
        self.page.setContentsMargins(20, 20, 20, 20)
        self.scroll.setWidget(self.page)
        layout.addWidget(self.scroll, 1)
        self.navigation = QWidget()
        controls = QHBoxLayout(self.navigation)
        controls.setContentsMargins(18, 12, 18, 12)
        self.previous = QPushButton("Anterior")
        self.next = QPushButton("Siguiente")
        self.page_box = QSpinBox()
        self.page_box.setMinimum(1)
        self.page_box.setMaximum(1)
        self.page_box.setAccessibleName("Número de página")
        self.total_label = QLabel("/ …")
        self.zoom = QComboBox()
        self.zoom.setAccessibleName("Ampliación del PDF")
        self.zoom.addItem("Ajustar al ancho", "fit")
        for percent in (75, 100, 125, 150, 200):
            self.zoom.addItem(f"{percent} %", percent / 100)
        controls.addWidget(self.previous)
        controls.addStretch()
        controls.addWidget(QLabel("Página"))
        controls.addWidget(self.page_box)
        controls.addWidget(self.total_label)
        controls.addStretch()
        controls.addWidget(self.zoom)
        controls.addWidget(self.next)
        layout.addWidget(self.navigation)
        self.info_process = QProcess(self)
        self.render_process = QProcess(self)
        self.text_process = QProcess(self)
        self.text_process.finished.connect(self.text_finished)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("LC_ALL", "C")
        self.info_process.setProcessEnvironment(environment)
        self.render_process.setProcessEnvironment(environment)
        self.text_process.setProcessEnvironment(environment)
        self.info_process.finished.connect(self.info_finished)
        self.render_process.finished.connect(self.render_finished)
        self.info_process.errorOccurred.connect(self.process_error)
        self.render_process.errorOccurred.connect(self.process_error)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.timed_out)
        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.render)
        self.previous.clicked.connect(lambda: self.navigate(self.index - 1))
        self.next.clicked.connect(lambda: self.navigate(self.index + 1))
        self.page_box.valueChanged.connect(lambda page: self.navigate(page - 1))
        self.zoom.currentIndexChanged.connect(self.render)
        stored_zoom = self.zoom.findData(self.resume.get("zoom", "fit"))
        if stored_zoom >= 0:
            self.zoom.setCurrentIndex(stored_zoom)
        self.zoom.currentIndexChanged.connect(lambda: self.stateChanged.emit())
        self.scroll.verticalScrollBar().valueChanged.connect(lambda: self.stateChanged.emit())
        self.scroll.horizontalScrollBar().valueChanged.connect(lambda: self.stateChanged.emit())
        self.set_controls(False)
        QTimer.singleShot(0, self.load)

    def set_controls(self, enabled):
        self.page_box.setEnabled(enabled)
        self.zoom.setEnabled(enabled)
        self.previous.setEnabled(enabled and self.index > 0)
        self.next.setEnabled(enabled and self.index < self.page_count - 1)

    def load(self):
        if self.stopped:
            return
        if not shutil.which("pdfinfo") or not shutil.which("pdftoppm"):
            self.fail("Para leer PDF, instalá Poppler (pdfinfo y pdftoppm).")
            return
        self.info_process.start("pdfinfo", ["-enc", "UTF-8", self.book["path"]])
        self.timeout.start(20000)

    def info_finished(self, code, status):
        self.timeout.stop()
        if self.stopped:
            return
        text = bytes(self.info_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        match = re.search(r"^Pages:\s+(\d+)", text, re.MULTILINE)
        if code != 0 or not match:
            self.fail("No se pudo abrir este PDF. Puede estar dañado o protegido con contraseña.")
            return
        self.page_count = int(match[1])
        self.page_box.blockSignals(True)
        self.page_box.setMaximum(self.page_count)
        self.page_box.blockSignals(False)
        self.total_label.setText(f"/ {self.page_count}")
        self.navigate(min(self.index, self.page_count - 1))

    def navigate(self, index):
        if not 0 <= index < self.page_count or self.stopped:
            return
        self.index = index
        self.page.set_words([])
        self.page_box.blockSignals(True)
        self.page_box.setValue(index + 1)
        self.page_box.blockSignals(False)
        self.set_controls(True)
        self.render()

    def render(self):
        if self.stopped or not self.page_count:
            return
        self._render_scroll = self.state() if self.displayed_index == self.index else None
        self.timeout.stop()
        if self.render_process.state() != QProcess.NotRunning:
            self.render_process.blockSignals(True)
            self.render_process.kill()
            self.render_process.waitForFinished(1000)
            self.render_process.blockSignals(False)
        self.render_process.readAllStandardOutput()
        self.render_process.readAllStandardError()
        scale = self.zoom.currentData()
        width = max(300, min(2600, self.scroll.viewport().width() - 60 if scale == "fit" else int(850 * scale)))
        self.render_process.start("pdftoppm", [
            "-f", str(self.index + 1), "-l", str(self.index + 1), "-singlefile",
            "-scale-to-x", str(width), "-scale-to-y", "-1", "-png", self.book["path"],
        ])
        self.timeout.start(30000)

    def render_finished(self, code, status):
        self.timeout.stop()
        if self.stopped:
            return
        image = QImage.fromData(bytes(self.render_process.readAllStandardOutput()))
        if code != 0 or image.isNull():
            self.fail("No se pudo mostrar esta página del PDF.")
            return
        self.last_image = image
        self.page.setPixmap(QPixmap.fromImage(image))
        self.page.adjustSize()
        self.scroll.verticalScrollBar().setValue(0)
        if self.resume.get("chapter") == self.index and self._restore_page == self.index:
            QTimer.singleShot(0, self.restore_scroll)
        elif self._render_scroll:
            saved = dict(self._render_scroll)
            QTimer.singleShot(0, lambda: self.restore_ratios(saved))
        self.displayed_index = self.index
        self.positionChanged.emit(self.index, self.page_count)
        self.request_words()

    def restore_scroll(self):
        if self.stopped or self.index != self._restore_page:
            return
        self.restore_ratios(self.resume)
        self._restore_page = None

    def restore_ratios(self, state):
        if self.stopped:
            return
        for key, bar in (("scroll_y", self.scroll.verticalScrollBar()), ("scroll_x", self.scroll.horizontalScrollBar())):
            dimension = self.last_image.height() if key == "scroll_y" else self.last_image.width()
            extent = dimension if state.get("position_basis") == "page" else bar.maximum()
            bar.setValue(round(float(state.get(key, 0)) * extent))

    def state(self):
        return {"chapter": self.index, "zoom": self.zoom.currentData(), "position_basis": "page", "scroll_y": self.scroll.verticalScrollBar().value() / max(1, self.last_image.height()), "scroll_x": self.scroll.horizontalScrollBar().value() / max(1, self.last_image.width())}

    def request_words(self):
        if self.library:
            recognized = self.library.ocr_words(self.book["id"], self.index)
            if recognized:
                self.words_cache[self.index] = recognized
        if self.index in self.words_cache:
            self.page.set_words(self.words_cache[self.index])
            self.page.find_text(self.pending_query)
            return
        if self.text_process.state() != QProcess.NotRunning:
            self.text_process.blockSignals(True)
            self.text_process.kill()
            self.text_process.waitForFinished(1000)
            self.text_process.blockSignals(False)
        self.text_process.readAllStandardOutput()
        self.text_position = self.index
        self.text_process.start("pdftotext", ["-f", str(self.index + 1), "-l", str(self.index + 1), "-enc", "UTF-8", "-bbox-layout", self.book["path"], "-"])

    def text_finished(self, code, status):
        if self.stopped or self.text_position != self.index or code:
            return
        try:
            words = parse_pdf_words(bytes(self.text_process.readAllStandardOutput()))
        except (ValueError, ET.ParseError):
            return
        self.words_cache[self.index] = words
        if len(self.words_cache) > 10:
            self.words_cache.pop(next(iter(self.words_cache)))
        self.page.set_words(words)
        self.page.find_text(self.pending_query)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.zoom.currentData() == "fit" and self.page_count and not self.stopped:
            self.resize_timer.start(180)

    def process_error(self, error):
        if error == QProcess.FailedToStart and not self.stopped:
            self.fail("No se pudo ejecutar Poppler. Comprobá que pdfinfo y pdftoppm estén instalados.")

    def timed_out(self):
        for process in (self.info_process, self.render_process, self.text_process):
            process.blockSignals(True)
            process.kill()
            process.waitForFinished(1000)
            process.blockSignals(False)
        self.fail("El PDF tardó demasiado en abrirse. Probá con otro archivo.")

    def fail(self, message):
        self.page.setText(message)
        self.page.adjustSize()
        self.error.emit(message)

    def stop(self):
        self.stopped = True
        self.timeout.stop()
        self.resize_timer.stop()
        for process in (self.info_process, self.render_process, self.text_process):
            process.blockSignals(True)
            if process.state() != QProcess.NotRunning:
                process.kill()
                process.waitForFinished(1000)


class ReaderWindow(QDialog):
    libraryChanged = pyqtSignal()

    def __init__(self, library, book, parent=None):
        super().__init__(parent, Qt.Window)
        self.library, self.book = library, book
        self._finished = False
        self.reading = reading_settings(library.data_dir)
        self.setWindowTitle(f"{book['title']} · Arandu")
        self.resize(1100, 820)
        self.setMinimumSize(620, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = QWidget()
        header = QHBoxLayout(self.header)
        header.setContentsMargins(22, 15, 22, 12)
        heading = QVBoxLayout()
        title = QLabel(book["title"])
        title.setObjectName("readerTitle")
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        self.title_label = title
        author = QLabel(book["author"])
        author.setObjectName("muted")
        author.setTextFormat(Qt.PlainText)
        self.author_label = author
        heading.addWidget(title)
        heading.addWidget(author)
        header.addLayout(heading, 1)
        self.finish_button = QPushButton("Marcar leído")
        self.finish_button.setObjectName("primary")
        self.finish_button.clicked.connect(self.mark_finished)
        header.addWidget(self.finish_button)
        layout.addWidget(self.header)
        self.tools = QWidget()
        toolbar = QHBoxLayout(self.tools)
        toolbar.setContentsMargins(16, 0, 16, 8)
        self.search_button = QPushButton("Buscar")
        self.search_button.clicked.connect(self.open_search)
        self.bookmark_button = QPushButton("Marcador")
        self.bookmark_button.setCheckable(True)
        self.bookmark_button.clicked.connect(self.toggle_bookmark)
        self.highlight_button = QPushButton("Resaltar")
        self.highlight_button.setEnabled(False)
        self.highlight_button.clicked.connect(self.add_highlight)
        self.note_button = QPushButton("Nota")
        self.note_button.clicked.connect(self.add_note)
        self.annotations_button = QPushButton("Anotaciones")
        self.annotations_button.clicked.connect(self.open_annotations)
        for button in (self.search_button, self.bookmark_button, self.highlight_button, self.note_button, self.annotations_button):
            toolbar.addWidget(button)
        toolbar.addStretch()
        self.reading_button = QToolButton()
        self.reading_button.setText("Lectura")
        self.reading_button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self)
        fullscreen = menu.addAction("Pantalla completa")
        fullscreen.setShortcut("F11")
        fullscreen.triggered.connect(self.toggle_fullscreen)
        self.addAction(fullscreen)
        if book["format"] == "EPUB":
            menu.addAction("Letra, márgenes e interlineado…", self.open_reading_settings)
        else:
            menu.addAction("Reconocer texto (OCR)…", self.open_ocr)
        menu.addAction("Exportar anotaciones…", self.export_annotations)
        self.sidebar_action = menu.addAction("Mostrar navegación lateral")
        self.sidebar_action.setCheckable(True)
        self.sidebar_action.setShortcut("Ctrl+T")
        self.addAction(self.sidebar_action)
        self.reading_button.setMenu(menu)
        toolbar.addWidget(self.reading_button)
        layout.addWidget(self.tools)
        self.viewer = PDFReader(book, self, library) if book["format"] == "PDF" else EPUBReader(book, self, library)
        if isinstance(self.viewer, EPUBReader):
            self.viewer.browser.configure(self.reading)
            self.viewer.browser.selectionChanged.connect(self.selection_changed)
        else:
            self.viewer.page.selectionChanged.connect(self.selection_changed)
        self.viewer.positionChanged.connect(self.save_position)
        self.viewer.error.connect(self.show_error)
        self.splitter = QSplitter(Qt.Horizontal)
        self.navigator = PDFThumbnailPane(book["path"], self) if book["format"] == "PDF" else EPUBOutlinePane(book["path"], self)
        self.navigator.setMinimumWidth(145)
        self.navigator.setMaximumWidth(340)
        self.splitter.addWidget(self.navigator)
        self.splitter.addWidget(self.viewer)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([215, 885])
        layout.addWidget(self.splitter, 1)
        self.sidebar_wanted = library.reading_state(book["id"]).get("sidebar", True)
        self.sidebar_action.setChecked(self.sidebar_wanted)
        self.sidebar_action.toggled.connect(self.toggle_sidebar)
        self.navigator.positionSelected.connect(self.viewer.navigate if book["format"] == "PDF" else lambda index, anchor: self.viewer.navigate(index, fragment=anchor))
        self.viewer.positionChanged.connect(self.navigator.set_current)
        self.state_timer = QTimer(self)
        self.state_timer.setSingleShot(True)
        self.state_timer.setInterval(350)
        self.state_timer.timeout.connect(self.save_state)
        self.viewer.stateChanged.connect(lambda: self.state_timer.start())
        self.page_mode_actions = {}
        if book["format"] == "EPUB":
            from PyQt5.QtWidgets import QActionGroup
            group = QActionGroup(self)
            group.setExclusive(True)
            submenu = menu.addMenu("Presentación EPUB")
            for mode, label in (("continuous", "Desplazamiento continuo"), ("single", "Página individual"), ("double", "Doble página")):
                action = submenu.addAction(label)
                action.setCheckable(True)
                action.setChecked(mode == self.viewer.resume.get("mode", "continuous"))
                action.triggered.connect(lambda checked, value=mode: self.viewer.set_mode(value))
                group.addAction(action)
                self.page_mode_actions[mode] = action
        self.message = QLabel("La posición, los marcadores y las notas se guardan en tu equipo.")
        self.message.setObjectName("readerMessage")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        for key, callback in (("Ctrl+F", self.open_search), ("Ctrl+M", self.toggle_bookmark), ("Ctrl+H", self.add_highlight), ("Ctrl+Shift+N", self.add_note), ("PgDown", lambda: self.step_page(1)), ("PgUp", lambda: self.step_page(-1))):
            action = QAction(self)
            action.setShortcut(key)
            action.triggered.connect(callback)
            self.addAction(action)
        if book["status"] == "finished":
            self.finish_button.setText("Leído")
            self.finish_button.setEnabled(False)
        self.tracker = ReadingTracker(library, book["id"], self)
        self.adapt_reader()

    def toggle_sidebar(self, visible):
        self.sidebar_wanted = visible
        self.adapt_reader()
        self.state_timer.start()

    def adapt_reader(self):
        if hasattr(self, "navigator"):
            self.navigator.setVisible(self.sidebar_wanted and self.width() >= 850 and not self.isFullScreen())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.adapt_reader()

    def step_page(self, direction):
        self.viewer.turn_page(direction) if isinstance(self.viewer, EPUBReader) else self.go_to(self.viewer.index + direction)

    def save_state(self):
        loaded = bool(self.viewer.browser.content) if isinstance(self.viewer, EPUBReader) else not self.viewer.last_image.isNull()
        if loaded:
            try:
                self.library.save_reading_state(self.book["id"], {**self.viewer.state(), "sidebar": self.sidebar_wanted})
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.show_error("No se pudo guardar el punto de lectura: " + str(exc))

    def refresh_from_library(self):
        self.book = self.library.get(self.book["id"])
        self.title_label.setText(self.book["title"])
        self.author_label.setText(self.book["author"])
        self.finish_button.setText("Leído" if self.book["status"] == "finished" else "Marcar leído")
        self.finish_button.setEnabled(self.book["status"] != "finished")
        self.refresh_annotations()

    def show_error(self, message):
        self.message.setText(message)

    def set_theme(self, theme):
        self.setWindowIcon(app_icon(theme))
        if isinstance(self.viewer, EPUBReader):
            self.viewer.browser.set_theme(theme)
        self.refresh_annotations()
        self.update()

    def selected_payload(self):
        if isinstance(self.viewer, PDFReader):
            return self.viewer.page.selected_payload()
        cursor = self.viewer.browser.textCursor()
        return {"quote": cursor.selectedText().replace('\u2029', '\n'), "rectangles": [], "start_offset": cursor.selectionStart() if cursor.hasSelection() else -1, "end_offset": cursor.selectionEnd() if cursor.hasSelection() else -1}

    def selection_changed(self):
        self.highlight_button.setEnabled(bool(self.selected_payload()["quote"]))

    def save_position(self, index, total):
        try:
            current = self.library.get(self.book["id"])
            changes = {"chapter": index}
            if current["status"] != "finished":
                changes.update(status="reading", progress=min(99, round(index / total * 100)))
            self.library.update(self.book["id"], changes, _record=False)
            self.refresh_annotations()
            self.libraryChanged.emit()
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.message.setText(f"No se pudo guardar la posición: {exc}")

    def refresh_annotations(self):
        entries = self.library.annotations(self.book["id"], self.viewer.index)
        self.bookmark_button.setChecked(any(item["kind"] == "bookmark" for item in entries))
        if isinstance(self.viewer, PDFReader):
            self.viewer.page.annotations = entries
            self.viewer.page.update()
        else:
            browser = self.viewer.browser
            selections = []
            for item in entries:
                if item["kind"] != "highlight":
                    continue
                cursor = QTextCursor(browser.document())
                if 0 <= item["start_offset"] < item["end_offset"] < browser.document().characterCount():
                    cursor.setPosition(item["start_offset"])
                    cursor.setPosition(item["end_offset"], QTextCursor.KeepAnchor)
                else:
                    cursor = browser.document().find(item["quote"])
                if not cursor.isNull() and cursor.hasSelection():
                    selection = QTextEdit.ExtraSelection()
                    selection.cursor = cursor
                    selection.format.setBackground(QColor(223, 184, 74, 95))
                    selections.append(selection)
            browser.setExtraSelections(selections)

    def toggle_bookmark(self):
        self.library.toggle_bookmark(self.book["id"], self.viewer.index)
        self.refresh_annotations()
        self.message.setText("Marcador guardado." if self.bookmark_button.isChecked() else "Marcador quitado.")

    def add_highlight(self):
        payload = self.selected_payload()
        if not payload["quote"]:
            self.message.setText("Seleccioná un fragmento del libro para resaltarlo.")
            return
        self.library.add_annotation(self.book["id"], self.viewer.index, "highlight", **payload)
        self.refresh_annotations()
        self.message.setText("Resaltado guardado. El archivo original no se modifica.")

    def add_note(self):
        note, accepted = QInputDialog.getMultiLineText(self, "Nota de lectura", f"Nota para {'página' if self.book['format']=='PDF' else 'sección'} {self.viewer.index + 1}")
        if accepted and note.strip():
            self.library.add_annotation(self.book["id"], self.viewer.index, "note", note=note, **self.selected_payload())
            self.message.setText("Nota guardada en Anotaciones.")

    def open_annotations(self):
        dialog = AnnotationsDialog(self.library, self.book, self)
        dialog.positionSelected.connect(self.go_to)
        dialog.changed.connect(self.refresh_annotations)
        dialog.exec_()
        dialog.deleteLater()

    def open_ocr(self):
        dialog = OCRDialog(self.library, self.book, self)
        dialog.changed.connect(self.reload_ocr)
        dialog.exec_()
        dialog.deleteLater()

    def reload_ocr(self):
        if isinstance(self.viewer, PDFReader):
            self.viewer.words_cache.clear()
            self.viewer.request_words()

    def export_annotations(self):
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "Exportar anotaciones del libro", "notas-arandu.md", "Markdown (*.md);;Texto (*.txt)")
        if path:
            try:
                self.library.export_annotations(path, [self.book["id"]])
                self.message.setText("Anotaciones exportadas: " + path)
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.show_error(str(exc))

    def go_to(self, position, query=""):
        if position < 0:
            return
        if isinstance(self.viewer, PDFReader):
            self.viewer.pending_query = query
            if self.viewer.page_count:
                self.viewer.navigate(position)
            else:
                self.viewer.index = position
        else:
            self.viewer.navigate(position)
            if query:
                self.viewer.browser.find(query.strip('"'))

    def open_search(self):
        dialog = ContentSearchDialog(self.library, self.book["id"], self)
        dialog.resultSelected.connect(lambda book_id, position, query: self.go_to(position, query))
        dialog.exec_()
        dialog.deleteLater()

    def open_annotation(self, annotation):
        if annotation["position"] is None:
            return
        self.go_to(annotation["position"], annotation.get("quote", ""))
        if isinstance(self.viewer, EPUBReader):
            start, end = annotation.get("start_offset", -1), annotation.get("end_offset", -1)
            if 0 <= start < end < self.viewer.browser.document().characterCount():
                cursor = QTextCursor(self.viewer.browser.document())
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.KeepAnchor)
                self.viewer.browser.setTextCursor(cursor)
                if self.viewer.browser.page_mode == "continuous":
                    self.viewer.browser.ensureCursorVisible()
                else:
                    self.viewer.browser.set_page(int(self.viewer.browser.document_y(start) / self.viewer.browser.page_height))

    def open_reading_settings(self):
        dialog = ReadingSettingsDialog(self.reading, self.library.data_dir, self)
        def update(values):
            self.reading = dict(values)
            self.viewer.browser.configure(values)
            self.refresh_annotations()
        dialog.settingsChanged.connect(update)
        dialog.exec_()
        dialog.deleteLater()

    def toggle_fullscreen(self):
        entering = not self.isFullScreen()
        self.header.setVisible(not entering)
        self.tools.setVisible(not entering)
        self.message.setVisible(not entering)
        self.viewer.navigation.setVisible(not entering)
        self.showFullScreen() if entering else self.showNormal()
        self.adapt_reader()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen()
        else:
            super().keyPressEvent(event)

    def mark_finished(self):
        try:
            self.library.update(self.book["id"], {"status": "finished"})
            self.finish_button.setText("Leído")
            self.finish_button.setEnabled(False)
            self.message.setText("Libro marcado como leído.")
            self.libraryChanged.emit()
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.message.setText(str(exc))

    def closeEvent(self, event):
        self.finish_reader()
        super().closeEvent(event)

    def done(self, result):
        self.finish_reader()
        super().done(result)

    def finish_reader(self):
        if self._finished:
            return
        self._finished = True
        self.state_timer.stop()
        self.save_state()
        self.tracker.close()
        self.navigator.stop()
        self.viewer.stop()
