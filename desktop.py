"""Ventana y controles nativos de Arandu (Qt Widgets)."""

from functools import lru_cache
import json
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
import zipfile

from PyQt5.QtCore import QByteArray, QFileSystemWatcher, QItemSelectionModel, QObject, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPalette, QPen, QPixmap
from PyQt5.QtWidgets import (
    QAction, QActionGroup, QAbstractItemView, QApplication, QBoxLayout, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListView, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget,
    QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from library import FORMATS, ROOT, filter_books, read_epub_entry, term_key
from branding import BrandMark, app_icon
from readers import ReaderWindow
from theme_editor import ThemeEditorDialog
from theme_manager import ThemeManagerDialog
from library_dialogs import ContentSearchDialog, CollectionsDialog, DuplicatesDialog
from taxonomy import TermEdit, TaxonomyFilterDialog, TaxonomyManagerDialog
from organization_dialogs import BatchEditDialog, EditionsDialog, RulesDialog, SmartShelvesDialog, StatisticsDialog, TrashDialog
from organizer import match_shelf
from ocr import OCRDialog
from cover_tools import cover_image
from source_dialogs import HiddenBooksDialog, SourcesDialog
from review_dialogs import DiagnosticsDialog, GlobalAnnotationsDialog, HistoryDialog
from backups import create_backup, daily_backup, restore_backup
from themes import DEFAULT_THEME, THEMES, Theme, apply_theme, current_theme, fix_placeholder_colors, font, load_custom_themes, load_fonts, save_custom_themes, scaled_px, ui_scale


VIEWS = {"all": "Todos los libros", "reading": "Leyendo", "pending": "Por leer", "finished": "Leídos", "favorites": "Favoritos"}
def base_font(family="Karla", size=11, weight=QFont.Normal):
    return font(family, size, weight, scaled=False)


STATUS = {"pending": "Por leer", "reading": "Leyendo", "finished": "Leído"}


def text_label(text="", name=None):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    if name:
        label.setObjectName(name)
    return label


class WorkerSignals(QObject):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    finished = pyqtSignal()


class Worker(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.succeeded.emit(self.function())
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        finally:
            self.signals.finished.emit()


class IndexButton(QPushButton):
    """An index entry with a real catalog count, exposed as a native button."""

    def __init__(self, title, short_title, parent=None):
        super().__init__(title, parent)
        self.title, self.short_title = title, short_title
        self.count, self.compact = 0, False
        self.theme = current_theme()
        self.setCheckable(True)
        self.setMinimumHeight(42)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(title)

    def sizeHint(self):
        return QSize(58 if self.compact else 156, scaled_px(42))

    def minimumSizeHint(self):
        return self.sizeHint()

    def set_count(self, count):
        self.count = count
        self.setAccessibleName(f"{self.title}, {count} libros")
        self.update()

    def paintEvent(self, event):
        t = self.theme
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        active = self.isChecked()
        if self.underMouse():
            painter.fillRect(rect, QColor(t.hover))
        if active:
            painter.fillRect(0, 11, 2, self.height() - 22, QColor(t.accent))
        painter.setPen(QColor(t.accent if active else t.muted))
        painter.setFont(font("Karla", 11, QFont.DemiBold if active else QFont.Normal))
        if self.compact:
            painter.drawText(rect, Qt.AlignCenter, painter.fontMetrics().elidedText(self.short_title, Qt.ElideRight, rect.width() - 6))
        else:
            painter.drawText(rect.adjusted(11, 0, -28, 0), Qt.AlignVCenter | Qt.AlignLeft, painter.fontMetrics().elidedText(self.title, Qt.ElideRight, rect.width() - 40))
            painter.setFont(font("Karla", 10))
            painter.drawText(rect.adjusted(0, 0, -7, 0), Qt.AlignVCenter | Qt.AlignRight, str(self.count))
        if self.hasFocus():
            painter.setPen(QPen(QColor(t.accent), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect.adjusted(3, 3, -4, -4))
        painter.end()


class NoticeBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notice")
        self.setAccessibleName("Estado de la biblioteca")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(5)
        self.message = text_label("", "noticeText")
        self.message.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Importación en curso")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.message)
        layout.addWidget(self.progress)
        self.dismiss_timer = QTimer(self)
        self.dismiss_timer.setSingleShot(True)
        self.dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def announce(self, state, message):
        self.setProperty("state", state)
        self.dismiss_timer.stop()
        if state == "success":
            self.dismiss_timer.start(5000)
        self.message.setText(message)
        self.setAccessibleDescription(message)
        self.progress.setVisible(state == "loading")
        self.style().unpolish(self)
        self.style().polish(self)
        self.show()


class BookDelegate(QStyledItemDelegate):
    def __init__(self, parent):
        super().__init__(parent)
        self.list_mode = False
        self.theme = current_theme()

    def sizeHint(self, option, index):
        return QSize(scaled_px(194), scaled_px(106 if self.list_mode else 326))

    @lru_cache(maxsize=128)
    def cover(self, book_id, title, author, path, cover_entry, cover_file, theme):
        if cover_file and Path(cover_file).is_file():
            image = QPixmap(cover_file)
            if not image.isNull():
                return image.scaled(300, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if cover_entry:
            try:
                with zipfile.ZipFile(path) as archive:
                    image = QPixmap()
                    if image.loadFromData(read_epub_entry(archive, cover_entry)):
                        return image.scaled(300, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                pass
        t = theme
        background, foreground = t.inset, t.text
        image = QPixmap(300, 420)
        image.fill(QColor(background))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(0, 0, 5, 420, QColor(t.accent))
        painter.setPen(QColor(foreground))
        painter.setFont(base_font("Montserrat", 18, QFont.DemiBold))
        painter.drawText(QRect(32, 64, 236, 250), Qt.AlignVCenter | Qt.TextWordWrap, title[:200])
        painter.setFont(base_font("Karla", 12))
        painter.drawText(QRect(32, 344, 236, 44), Qt.AlignLeft | Qt.TextWordWrap, author[:100])
        painter.end()
        return image

    def paint(self, painter, option, index):
        book = index.data(Qt.UserRole)
        if not book:
            return
        t = self.theme
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(ui_scale(), ui_scale())
        selected = bool(option.state & QStyle.State_Selected)
        focused = bool(option.state & QStyle.State_HasFocus)
        rect = QRect(round(option.rect.x() / ui_scale()) + 4, round(option.rect.y() / ui_scale()) + 4, round(option.rect.width() / ui_scale()) - 8, round(option.rect.height() / ui_scale()) - 8)
        if selected and not self.list_mode:
            painter.fillRect(QRect(rect.x(), rect.y() + 8, 3, 198), QColor(t.accent))
        elif selected or option.state & QStyle.State_MouseOver:
            painter.fillRect(rect, QColor(t.selected if selected else t.hover))
        if focused:
            painter.setPen(QPen(QColor(t.accent), 1, Qt.DashLine))
            painter.drawRect(rect.adjusted(3, 3, -3, -3))
        pixmap = self.cover(book["id"], book["title"], book["author"], book["path"], book["cover"], book.get("custom_cover") or book.get("cover_path", ""), t)
        if self.list_mode:
            cover_rect = QRect(rect.x() + 12, rect.y() + 8, 42, 62)
            text_x, text_width = rect.x() + 70, max(70, rect.width() - 174)
            title_y, author_y, status_y = rect.y() + 23, rect.y() + 44, rect.y() + 65
        else:
            cover_rect = QRect(rect.x() + 14, rect.y() + 8, rect.width() - 28, 198)
            text_x, text_width = rect.x() + 14, rect.width() - 28
            title_y, author_y, status_y = rect.y() + 229, rect.y() + 253, rect.y() + 277
        scaled = pixmap.scaled(cover_rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter.drawPixmap(cover_rect.x() + (cover_rect.width() - scaled.width()) // 2, cover_rect.y(), scaled)
        painter.setPen(QColor(t.text))
        painter.setFont(base_font("Montserrat", 10, QFont.DemiBold))
        painter.drawText(text_x, title_y, painter.fontMetrics().elidedText(book["title"], Qt.ElideRight, text_width))
        painter.setFont(base_font("Karla", 11))
        painter.setPen(QColor(t.muted))
        painter.drawText(text_x, author_y, painter.fontMetrics().elidedText(book["author"], Qt.ElideRight, text_width))
        painter.setPen(QColor(t.error if not book["available"] else t.muted))
        status = "Archivo no disponible" if not book["available"] else STATUS[book["status"]]
        if book["status"] == "reading" and book["available"]:
            status += f" · {book['progress']} %"
        painter.setFont(base_font("Karla", 10))
        formats = book.get("edition_formats", book["format"])
        status_text = status if self.list_mode else f"{formats} · {status}"
        painter.drawText(text_x, status_y, painter.fontMetrics().elidedText(status_text, Qt.ElideRight, text_width - (34 if book["favorite"] and not self.list_mode else 0)))
        classification = " · ".join([*book.get("categories", []), *("#" + tag for tag in book["tags"])])
        painter.setPen(QColor(t.muted))
        painter.drawText(text_x, rect.y() + (87 if self.list_mode else 299), painter.fontMetrics().elidedText(classification, Qt.ElideRight, text_width))
        if self.list_mode:
            painter.setPen(QColor(t.muted))
            painter.drawText(rect.right() - 85, rect.y() + 49, formats)
        if book["favorite"]:
            painter.setFont(base_font("Montserrat", 8, QFont.Bold))
            painter.setPen(QColor(t.accent))
            painter.drawText(rect.right() - 43, rect.y() + (66 if self.list_mode else 277), "FAV")
        if book["status"] == "reading" and not self.list_mode:
            track = QRect(rect.x() + 14, rect.bottom() - 9, rect.width() - 28, 3)
            painter.fillRect(track, QColor(t.border))
            painter.fillRect(QRect(track.x(), track.y(), int(track.width() * book["progress"] / 100), 3), QColor(t.accent))
        painter.restore()


class DetailDialog(QDialog):
    def __init__(self, library, book, parent=None):
        super().__init__(parent)
        self.library, self.book = library, book
        self.setWindowTitle("Detalles del libro · Arandu")
        self.resize(620, 700)
        self.pending_path = None
        self.pending_cover = None
        self.cover_changed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 25, 28, 25)
        layout.setSpacing(17)
        title = text_label("Detalles del libro", "title")
        layout.addWidget(title)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setSpacing(13)
        self.title_edit = QLineEdit(book["title"])
        self.title_edit.setMaxLength(500)
        self.author_edit = QLineEdit(book["author"])
        self.author_edit.setMaxLength(500)
        self.status_combo = QComboBox()
        for value, label in STATUS.items():
            self.status_combo.addItem(label, value)
        self.status_combo.setCurrentIndex(self.status_combo.findData(book["status"]))
        self.categories_edit = TermEdit(book.get("categories", []), library.taxonomy_names("categories"))
        self.categories_edit.setPlaceholderText("Novela, ensayo, historia…")
        self.categories_edit.setAccessibleName("Categorías, separadas por comas")
        self.tags_edit = TermEdit(book["tags"], library.taxonomy_names("tags"))
        self.tags_edit.setPlaceholderText("Paraguay, investigar, recomendado…")
        self.tags_edit.setAccessibleName("Etiquetas o tags, separadas por comas")
        self.progress_spin = QSpinBox()
        self.progress_spin.setRange(0, 100)
        self.progress_spin.setSuffix(" %")
        self.progress_spin.setValue(book["progress"])
        self.favorite_check = QCheckBox("Guardar en favoritos")
        self.favorite_check.setChecked(book["favorite"])
        self.hidden_check = QCheckBox("Ocultar del estante habitual")
        self.hidden_check.setChecked(book.get("hidden", False))
        self.collections_edit = QLineEdit(", ".join(book.get("collections", [])))
        self.collections_edit.setPlaceholderText("Colecciones separadas por comas")
        self.series_edit = QLineEdit(book.get("series", ""))
        self.series_edit.setPlaceholderText("Nombre de la serie")
        self.series_number = QDoubleSpinBox()
        self.series_number.setRange(0, 100000)
        self.series_number.setDecimals(2)
        self.series_number.setValue(book.get("series_number", 0))
        self.series_number.setAccessibleName("Número de orden en la serie")
        series_widget = QWidget()
        series_row = QHBoxLayout(series_widget)
        series_row.setContentsMargins(0, 0, 0, 0)
        series_row.addWidget(self.series_edit, 1)
        series_row.addWidget(self.series_number)
        self.notes_edit = QPlainTextEdit(book["notes"])
        self.notes_edit.setPlaceholderText("Una idea, una cita, algo para recordar…")
        self.notes_edit.setMinimumHeight(90)
        for name, widget in (("Título", self.title_edit), ("Autor", self.author_edit), ("Categorías", self.categories_edit), ("Etiquetas (tags)", self.tags_edit), ("Colecciones", self.collections_edit), ("Serie / orden", series_widget), ("Estado", self.status_combo), ("Progreso", self.progress_spin), ("", self.favorite_check), ("Visibilidad", self.hidden_check), ("Mis notas", self.notes_edit)):
            form.addRow(name, widget)
        hint = text_label("Separá las categorías y etiquetas con comas.", "muted")
        hint.setWordWrap(True)
        form.insertRow(4, "", hint)
        cover_row = QWidget()
        cover_layout = QHBoxLayout(cover_row)
        cover_layout.setContentsMargins(0, 0, 0, 0)
        self.cover_preview = text_label("Personalizada" if book.get("custom_cover") else "Del archivo", "muted")
        self.cover_preview.setAccessibleName("Vista previa de la portada")
        cover_layout.addWidget(self.cover_preview)
        choose_cover = QPushButton("Cambiar…")
        choose_cover.clicked.connect(self.choose_cover)
        cover_layout.addWidget(choose_cover)
        reset_cover = QPushButton("Del archivo")
        reset_cover.clicked.connect(self.reset_cover)
        cover_layout.addWidget(reset_cover)
        form.insertRow(5, "Portada", cover_row)
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setWidget(form_widget)
        layout.addWidget(self.form_scroll, 1)
        path = text_label(f"{book['format']} · {book['size'] / 1024 / 1024:.1f} MB\n{book['path']}", "muted")
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.file_label = path
        layout.addWidget(path)
        locate = QPushButton("Localizar archivo…")
        locate.clicked.connect(self.locate)
        layout.addWidget(locate)
        buttons = QHBoxLayout()
        remove = QPushButton("Quitar del catálogo")
        remove.setObjectName("danger")
        remove.clicked.connect(self.remove)
        buttons.addWidget(remove)
        buttons.addStretch()
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton("Guardar")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self.save)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self.status_combo.currentIndexChanged.connect(self.status_changed)

    def status_changed(self):
        if self.status_combo.currentData() == "finished":
            self.progress_spin.setValue(100)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: fix_placeholder_colors(self))

    def locate(self):
        path, _ = QFileDialog.getOpenFileName(self, "Localizar el libro", str(Path.home()), "Libros (*.pdf *.epub *.PDF *.EPUB)")
        if path:
            self.pending_path = path
            self.file_label.setText(path + "\nLa ubicación se actualiza al guardar.")

    def focus_classification(self):
        self.form_scroll.ensureWidgetVisible(self.categories_edit)
        self.categories_edit.setFocus(Qt.OtherFocusReason)

    def choose_cover(self):
        path, _ = QFileDialog.getOpenFileName(self, "Elegir portada", "", "Imágenes (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            try:
                self.pending_cover = cover_image(path)
                self.cover_changed = True
                pixmap = QPixmap()
                pixmap.loadFromData(self.pending_cover)
                self.cover_preview.setPixmap(pixmap.scaled(64, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.cover_preview.setToolTip(Path(path).name)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "No se pudo cambiar la portada", str(exc))

    def reset_cover(self):
        self.cover_changed, self.pending_cover = True, None
        self.cover_preview.clear()
        self.cover_preview.setText("Del archivo")

    def save(self):
        try:
            if not self.title_edit.text().strip():
                raise ValueError("El título no puede estar vacío.")
            if self.pending_path:
                self.library.relink(self.book["id"], self.pending_path)
            self.library.update(self.book["id"], {
                "title": self.title_edit.text(), "author": self.author_edit.text(),
                "status": self.status_combo.currentData(), "tags": self.tags_edit.text().split(","),
                "categories": self.categories_edit.text().split(","),
                "progress": self.progress_spin.value(), "notes": self.notes_edit.toPlainText(),
                "favorite": self.favorite_check.isChecked(),
                "hidden": self.hidden_check.isChecked(),
                "collections": self.collections_edit.text().split(","),
                "series": self.series_edit.text(), "series_number": self.series_number.value(),
            })
            if self.cover_changed:
                self.library.set_custom_cover(self.book["id"], self.pending_cover)
            self.accept()
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "No se pudo guardar", str(exc))

    def remove(self):
        answer = QMessageBox.question(self, "Mover a la papelera", "¿Mover esta ficha a la papelera del catálogo?\n\nPodrás recuperar sus notas, etiquetas y progreso desde Biblioteca → Papelera. El archivo seguirá en tu computadora.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            try:
                self.library.delete(self.book["id"])
                self.accept()
            except (OSError, sqlite3.Error) as exc:
                QMessageBox.warning(self, "No se pudo quitar", str(exc))


class MainWindow(QMainWindow):
    def __init__(self, library):
        super().__init__()
        load_fonts()
        self.library = library
        self.theme_load_error = None
        try:
            self.custom_themes = load_custom_themes(library.data_dir)
        except (OSError, ValueError) as exc:
            self.custom_themes = {}
            self.theme_load_error = str(exc)
        self.available_themes = {**THEMES, **self.custom_themes}
        self.books, self.folders = [], []
        self.retained_books = []
        self.view, self.layout_mode = "all", "grid"
        self.category_filter, self.tag_filter = "", ""
        self.compact_catalog = False
        self.text_scale = 100
        self.auto_sync = True
        self.source_editor_open = False
        self.maintenance_open = False
        self.watch_pending = False
        self.theme = THEMES[DEFAULT_THEME]
        self.busy = False
        self.theme_editor = None
        self.workers, self.readers = set(), set()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.setWindowTitle("Arandu · Mi biblioteca")
        self.setWindowIcon(app_icon(self.theme))
        self.resize(1280, 870)
        self.setMinimumSize(680, 560)
        self.setAcceptDrops(True)
        shell = QWidget()
        self.setCentralWidget(shell)
        row = QHBoxLayout(shell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(192)
        self.sidebar_layout = QVBoxLayout(self.sidebar)
        self.sidebar_layout.setSpacing(2)
        self.sidebar_layout.setContentsMargins(18, 25, 18, 20)
        self.brand = BrandMark()
        self.sidebar_layout.addWidget(self.brand)
        self.nav_buttons = {}
        shorts = {"all": "Todo", "reading": "Ley.", "pending": "Pend.", "finished": "Leídos", "favorites": "Fav."}
        for view, label in VIEWS.items():
            button = IndexButton(label, shorts[view])
            button.clicked.connect(lambda checked, value=view: self.set_view(value))
            self.sidebar_layout.addWidget(button)
            self.nav_buttons[view] = button
        self.folders_heading = text_label("Carpetas", "sidebarHeading")
        self.sidebar_layout.addWidget(self.folders_heading)
        self.folder_label = text_label()
        self.folder_label.setWordWrap(True)
        self.sidebar_layout.addWidget(self.folder_label)
        self.sidebar_layout.addStretch()
        row.addWidget(self.sidebar)
        content = QWidget()
        content.setObjectName("content")
        self.content_layout = QVBoxLayout(content)
        column = self.content_layout
        column.setContentsMargins(38, 26, 38, 22)
        column.setSpacing(19)
        self.theme_combo = QComboBox()
        self.theme_combo.setObjectName("themeChooser")
        self.theme_combo.setAccessibleName("Tema de la aplicación")
        self.theme_combo.setToolTip("Cambiar tema. La elección se guarda automáticamente.")
        self.update_theme_choices()
        self.theme_combo.setMaximumWidth(150)
        self.heading_row = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(8)
        self.title_label = text_label("Mi biblioteca", "title")
        heading.addWidget(self.title_label)
        self.heading_row.addLayout(heading, 1)
        self.heading_actions = QHBoxLayout()
        self.heading_actions.addWidget(self.theme_combo)
        self.folder_button = QPushButton("Conectar carpeta")
        self.folder_button.clicked.connect(lambda: self.manage_sources() if self.folders else self.choose_folder())
        self.folder_button.setObjectName("sourceButton")
        self.sidebar_layout.addWidget(self.folder_button)
        self.add_button = QPushButton("Añadir libros")
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(self.choose_files)
        self.heading_actions.addWidget(self.add_button)
        self.heading_row.addLayout(self.heading_actions)
        column.addLayout(self.heading_row)
        self.filters_layout = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("catalogSearch")
        self.search.setAccessibleName("Buscar por título, autor, etiqueta o categoría")
        self.search.setPlaceholderText("Buscar libros…")
        self.search.setClearButtonEnabled(True)
        self.filters_layout.addWidget(self.search, 1)
        self.filter_options = QWidget(content)
        filter_options = QHBoxLayout(self.filter_options)
        filter_options.setContentsMargins(0, 0, 0, 0)
        self.format_combo = QComboBox()
        self.format_combo.addItem("Todos los formatos", "all")
        self.format_combo.addItem("PDF", "PDF")
        self.format_combo.addItem("EPUB", "EPUB")
        self.format_combo.setAccessibleName("Filtrar por formato")
        filter_options.addWidget(self.format_combo, 1)
        self.sort_combo = QComboBox()
        for label, value in (("Más recientes", "recent"), ("Título: A–Z", "title"), ("Autor: A–Z", "author"), ("Progreso", "progress"), ("Serie", "series")):
            self.sort_combo.addItem(label, value)
        self.sort_combo.setAccessibleName("Ordenar libros")
        filter_options.addWidget(self.sort_combo, 1)
        self.taxonomy_filter_button = QPushButton("Filtros…")
        self.taxonomy_filter_button.setAccessibleName("Filtrar por etiquetas y categorías")
        self.taxonomy_filter_button.clicked.connect(self.filter_classification)
        filter_options.addWidget(self.taxonomy_filter_button)
        self.filter_options.hide()
        self.notice = NoticeBar()
        column.addWidget(self.notice)
        self.catalog_toolbar = QFrame()
        self.catalog_toolbar.setObjectName("catalogToolbar")
        self.catalog_toolbar.setLayout(self.filters_layout)
        self.filters_layout.setContentsMargins(0, 0, 0, 12)
        self.catalog_controls = QWidget()
        collection_row = QHBoxLayout(self.catalog_controls)
        collection_row.setContentsMargins(0, 0, 0, 0)
        collection_row.setSpacing(6)
        self.count_label = text_label("", "muted")
        heading.addWidget(self.count_label)
        self.collection_combo = QComboBox()
        self.collection_combo.setObjectName("shelfChooser")
        self.collection_combo.setMinimumContentsLength(9)
        self.collection_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.collection_combo.setAccessibleName("Filtrar por colección")
        self.collection_combo.setMaximumWidth(180)
        self.collection_combo.addItem("Colecciones", "")
        self.collection_combo.setToolTip("Filtrar por colección")
        self.collection_combo.currentIndexChanged.connect(self.render)
        collection_row.addWidget(self.collection_combo)
        filter_options.removeWidget(self.taxonomy_filter_button)
        self.taxonomy_filter_button.setParent(self.catalog_controls)
        collection_row.addWidget(self.taxonomy_filter_button)
        self.content_button = QPushButton("En el texto")
        self.content_button.setObjectName("quietButton")
        self.content_button.setToolTip("Buscar texto dentro de todos los PDF y EPUB")
        self.content_button.clicked.connect(self.open_content_search)
        collection_row.addWidget(self.content_button)
        self.grid_button = QPushButton("Estante")
        self.list_button = QPushButton("Lista")
        self.view_switcher = QFrame()
        self.view_switcher.setObjectName("viewSwitcher")
        switch_row = QHBoxLayout(self.view_switcher)
        switch_row.setContentsMargins(0, 0, 0, 0)
        switch_row.setSpacing(0)
        for button, mode in ((self.grid_button, "grid"), (self.list_button, "list")):
            button.setObjectName("viewSegment")
            button.setCheckable(True)
            button.clicked.connect(lambda checked, value=mode: self.set_layout(value))
            switch_row.addWidget(button)
        collection_row.addWidget(self.view_switcher)
        self.grid_button.setAccessibleName("Vista de portadas en el estante")
        self.list_button.setAccessibleName("Vista de índice en lista")
        self.filters_layout.addWidget(self.catalog_controls)
        column.addWidget(self.catalog_toolbar)
        column.removeWidget(self.notice)
        column.addWidget(self.notice)
        self.stack = QStackedWidget()
        self.book_list = QListWidget()
        self.book_list.setObjectName("books")
        self.book_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.book_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.book_list.setResizeMode(QListView.Adjust)
        self.book_list.setMovement(QListView.Static)
        self.book_list.setUniformItemSizes(True)
        self.book_list.setMouseTracking(True)
        self.book_list.setSpacing(9)
        self.book_list.setAccessibleName("Catálogo de libros. Doble clic para leer.")
        self.delegate = BookDelegate(self.book_list)
        self.book_list.setItemDelegate(self.delegate)
        self.book_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.book_list.customContextMenuRequested.connect(self.context_menu)
        self.book_list.itemDoubleClicked.connect(lambda item: self.open_book())
        self.book_list.currentItemChanged.connect(self.selection_changed)
        self.book_list.itemSelectionChanged.connect(self.selection_changed)
        self.book_list.setToolTip("Ctrl o Shift para seleccionar varios libros. Clic derecho para organizarlos.")
        self.stack.addWidget(self.book_list)
        self.empty_widget = QFrame()
        self.empty_widget.setObjectName("emptyPanel")
        empty = QVBoxLayout(self.empty_widget)
        empty.setContentsMargins(2, 16, 2, 16)
        empty.setSpacing(14)
        empty.addStretch(1)
        self.empty_title = text_label("Sin libros", "emptyTitle")
        self.empty_title.setWordWrap(True)
        empty.addWidget(self.empty_title)
        self.empty_body = text_label("Añadí PDF o EPUB para empezar.", "emptyBody")
        self.empty_body.setWordWrap(True)
        empty.addWidget(self.empty_body)
        self.empty_button = QPushButton("Añadir libros")
        self.empty_button.setObjectName("primary")
        self.empty_button.clicked.connect(self.empty_action)
        empty.addWidget(self.empty_button, 0, Qt.AlignLeft)
        empty.addStretch(2)
        self.stack.addWidget(self.empty_widget)
        column.addWidget(self.stack, 1)
        self.selection_bar = QFrame()
        self.selection_bar.setObjectName("selectionBar")
        selection = QHBoxLayout(self.selection_bar)
        selection.setContentsMargins(15, 13, 15, 13)
        self.selection_label = text_label("Elegí un libro del estante.", "muted")
        selection.addWidget(self.selection_label, 1)
        self.favorite_button = QPushButton("Favorito")
        self.favorite_button.setCheckable(True)
        self.favorite_button.clicked.connect(self.toggle_favorite)
        self.detail_button = QPushButton("Ficha y etiquetas")
        self.detail_button.setToolTip("Editar categorías, etiquetas, notas y datos del libro")
        self.detail_button.clicked.connect(self.show_details)
        self.read_button = QPushButton("Leer libro")
        self.read_button.setObjectName("primary")
        self.read_button.clicked.connect(lambda: self.open_book())
        for button in (self.favorite_button, self.detail_button, self.read_button):
            selection.addWidget(button)
        column.addWidget(self.selection_bar)
        row.addWidget(content, 1)
        self.search.textChanged.connect(self.render)
        self.format_combo.currentIndexChanged.connect(self.render)
        self.sort_combo.currentIndexChanged.connect(self.render)
        self.create_menus()
        self.restore_preferences()
        self.set_theme(self.theme.id, persist=False)
        self.theme_combo.currentIndexChanged.connect(self.theme_selected)
        self.set_layout(self.layout_mode)
        self.reload()
        self.statusBar().hide()
        self.setup_watcher()
        if self.library.needs_maintenance():
            QTimer.singleShot(0, self.refresh_index)
        self.adapt_layout()
        if self.theme_load_error:
            self.notice.announce("error", f"No se pudieron cargar tus temas personalizados: {self.theme_load_error}")

    def create_menus(self):
        file_menu = self.menuBar().addMenu("Archivo")
        for title, shortcut, callback in (("Añadir libros…", "Ctrl+O", self.choose_files), ("Conectar carpeta…", "Ctrl+Shift+O", self.choose_folder), ("Actualizar carpetas", "F5", self.rescan), ("Salir", "Ctrl+Q", self.close)):
            action = QAction(title, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            file_menu.addAction(action)
        sources_action = file_menu.addAction("Carpetas e ignorados…")
        sources_action.setShortcut("Ctrl+Shift+E")
        sources_action.triggered.connect(self.manage_sources)
        edit_menu = self.menuBar().addMenu("Edición")
        for title, shortcut, callback in (("Deshacer cambio del catálogo", "Ctrl+Z", lambda: self.undo_change(False)), ("Rehacer cambio del catálogo", "Ctrl+Shift+Z", lambda: self.undo_change(True)), ("Historial de cambios…", "Ctrl+Alt+H", self.open_history)):
            action = edit_menu.addAction(title)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
        library_menu = self.menuBar().addMenu("Biblioteca")
        for title, shortcut, callback in (("Buscar", "Ctrl+F", self.search.setFocus), ("Leer libro seleccionado", "Return", lambda: self.open_book()), ("Detalles del libro", "Ctrl+I", self.show_details)):
            action = QAction(title, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            library_menu.addAction(action)
        reveal = QAction("Abrir carpeta de datos", self)
        reveal.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.data_dir))))
        library_menu.addAction(reveal)
        organize_menu = library_menu.addMenu("Organizar")
        reading_menu = library_menu.addMenu("Lectura y notas")
        maintenance_menu = library_menu.addMenu("Mantenimiento")
        organize_callbacks = {self.manage_classification, self.edit_selection, self.manage_shelves, self.manage_rules, self.manage_editions, self.manage_collections, self.open_hidden_books, self.open_trash}
        reading_callbacks = {self.open_content_search, self.open_statistics, self.open_global_annotations, self.export_annotations}
        for label, shortcut, callback in (
            ("Buscar en el contenido…", "Ctrl+Shift+F", self.open_content_search),
            ("Etiquetas y categorías…", "", self.manage_classification),
            ("Editar selección…", "Ctrl+Shift+I", self.edit_selection),
            ("Estantes inteligentes…", "", self.manage_shelves),
            ("Reglas de organización…", "", self.manage_rules),
            ("Obras y ediciones…", "", self.manage_editions),
            ("Lecturas y objetivos…", "", self.open_statistics),
            ("Todas las anotaciones…", "Ctrl+Alt+N", self.open_global_annotations),
            ("Diagnóstico y reparación…", "", self.open_diagnostics),
            ("Reconocer texto (OCR)…", "", self.open_ocr),
            ("Exportar anotaciones…", "", self.export_annotations),
            ("Papelera del catálogo…", "", self.open_trash),
            ("Libros ocultos…", "", self.open_hidden_books),
            ("Colecciones…", "", self.manage_collections),
            ("Revisar duplicados…", "", self.review_duplicates),
            ("Actualizar portadas e índice", "", self.refresh_index),
        ):
            target_menu = organize_menu if callback in organize_callbacks else reading_menu if callback in reading_callbacks else maintenance_menu
            action = target_menu.addAction(label)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(callback)
        self.sync_action = QAction("Sincronizar carpetas automáticamente", self, checkable=True)
        self.sync_action.setChecked(True)
        self.sync_action.toggled.connect(self.set_auto_sync)
        maintenance_menu.addAction(self.sync_action)
        data_menu = self.menuBar().addMenu("Datos")
        data_menu.addAction("Crear copia de seguridad…", self.backup_library)
        data_menu.addAction("Restaurar copia…", self.restore_library)
        appearance_menu = self.menuBar().addMenu("Apariencia")
        self.theme_actions = {}
        self.theme_action_group = QActionGroup(self)
        self.theme_action_group.setExclusive(True)
        for dark, label in ((False, "Temas claros"), (True, "Temas oscuros")):
            group_menu = appearance_menu.addMenu(label)
            for theme in THEMES.values():
                if theme.dark != dark:
                    continue
                action = QAction(theme.name, self, checkable=True)
                action.setStatusTip(theme.description)
                action.triggered.connect(lambda checked, value=theme.id: self.set_theme(value))
                self.theme_action_group.addAction(action)
                self.theme_actions[theme.id] = action
                group_menu.addAction(action)
        self.custom_theme_menu = appearance_menu.addMenu("Mis temas")
        self.update_custom_theme_actions()
        appearance_menu.addSeparator()
        self.customize_theme_action = appearance_menu.addAction("Personalizar tema…")
        self.customize_theme_action.triggered.connect(self.customize_theme)
        appearance_menu.addAction("Gestionar temas y tamaño de texto…", self.manage_themes)
        choose_theme = appearance_menu.addAction("Elegir tema…")
        choose_theme.setShortcut("Ctrl+Shift+T")
        choose_theme.triggered.connect(self.theme_combo.setFocus)

    @staticmethod
    def theme_swatch(theme):
        swatch = QPixmap(20, 20)
        swatch.fill(QColor(theme.background))
        painter = QPainter(swatch)
        painter.fillRect(0, 0, 7, 20, QColor(theme.accent))
        painter.setPen(QColor(theme.border))
        painter.drawRect(0, 0, 19, 19)
        painter.end()
        return QIcon(swatch)

    def update_theme_choices(self):
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        for theme in self.available_themes.values():
            self.theme_combo.addItem(self.theme_swatch(theme), theme.name, theme.id)
        self.theme_combo.insertSeparator(self.theme_combo.count())
        self.theme_combo.addItem("Personalizar…", "__customize__")
        index = self.theme_combo.findData(self.theme.id)
        self.theme_combo.setCurrentIndex(index if index >= 0 else 0)
        self.theme_combo.blockSignals(False)
        if hasattr(self, "custom_theme_menu"):
            self.update_custom_theme_actions()

    def update_custom_theme_actions(self):
        self.custom_theme_menu.menuAction().setVisible(bool(self.custom_themes))
        for theme_id in list(self.theme_actions):
            if theme_id.startswith("custom-") and theme_id not in self.custom_themes:
                action = self.theme_actions.pop(theme_id)
                self.custom_theme_menu.removeAction(action)
                self.theme_action_group.removeAction(action)
                action.deleteLater()
        for theme in self.custom_themes.values():
            action = self.theme_actions.get(theme.id)
            if action is None:
                action = QAction(self)
                action.setCheckable(True)
                action.triggered.connect(lambda checked, value=theme.id: self.set_theme(value))
                self.theme_action_group.addAction(action)
                self.custom_theme_menu.addAction(action)
                self.theme_actions[theme.id] = action
            action.setText(theme.name.replace("&", "&&"))
            action.setIcon(self.theme_swatch(theme))

    def theme_selected(self):
        theme_id = self.theme_combo.currentData()
        if theme_id == "__customize__":
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(self.theme_combo.findData(self.theme.id))
            self.theme_combo.blockSignals(False)
            QTimer.singleShot(0, self.customize_theme)
        elif theme_id:
            self.set_theme(theme_id)

    def customize_theme(self):
        if self.theme_editor is not None:
            self.theme_editor.raise_()
            self.theme_editor.activateWindow()
            return
        original = self.theme
        dialog = ThemeEditorDialog(original, self.available_themes, self.save_custom_theme, self)
        self.theme_editor = dialog
        dialog.previewRequested.connect(lambda theme: self.set_theme(theme, persist=False))
        try:
            if dialog.exec_() != QDialog.Accepted:
                self.set_theme(original, persist=False)
        finally:
            self.theme_editor = None
            dialog.deleteLater()

    def save_custom_theme(self, theme):
        if self.theme_load_error:
            self.custom_themes = load_custom_themes(self.library.data_dir)
            self.theme_load_error = None
        updated = {**self.custom_themes, theme.id: theme}
        self.store_custom_themes(updated, theme.id)
        self.notice.announce("success", "Tema guardado.")

    def store_custom_themes(self, updated, active_id):
        save_custom_themes(self.library.data_dir, updated)
        self.custom_themes = dict(updated)
        self.available_themes = {**THEMES, **self.custom_themes}
        self.update_theme_choices()
        self.set_theme(active_id)

    def manage_themes(self):
        dialog = ThemeManagerDialog(self)
        dialog.exec_()
        dialog.deleteLater()

    def set_text_scale(self, value):
        self.text_scale = min(125, max(90, int(value)))
        QApplication.instance().setProperty("aranduTextScale", self.text_scale)
        self.set_theme(self.theme, persist=False)
        self.adapt_layout()
        self.book_list.doItemsLayout()
        self.save_preferences()

    def restore_preferences(self):
        try:
            settings = json.loads((self.library.data_dir / "window.json").read_text())
            self.layout_mode = "list" if settings.get("layout") == "list" else "grid"
            self.text_scale = min(125, max(90, int(settings.get("text_scale", 100))))
            self.auto_sync = bool(settings.get("auto_sync", True))
            self.sync_action.blockSignals(True)
            self.sync_action.setChecked(self.auto_sync)
            self.sync_action.blockSignals(False)
            self.theme = self.available_themes.get(settings.get("theme"), THEMES[DEFAULT_THEME])
            if settings.get("geometry"):
                self.restoreGeometry(QByteArray.fromBase64(settings["geometry"].encode()))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        QApplication.instance().setProperty("aranduTextScale", self.text_scale)

    def save_preferences(self):
        target = self.library.data_dir / "window.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "layout": self.layout_mode,
            "theme": self.theme.id,
            "text_scale": self.text_scale,
            "auto_sync": self.auto_sync,
            "geometry": bytes(self.saveGeometry().toBase64()).decode(),
        }))
        temporary.replace(target)

    def set_theme(self, theme_id, persist=True):
        self.theme = theme_id if isinstance(theme_id, Theme) else self.available_themes.get(theme_id, THEMES[DEFAULT_THEME])
        apply_theme(self.theme)
        self.brand.set_theme(self.theme)
        icon = app_icon(self.theme)
        self.setWindowIcon(icon)
        QApplication.instance().setWindowIcon(icon)
        self.theme_combo.blockSignals(True)
        choice = self.theme_combo.findData(self.theme.id)
        if choice >= 0:
            self.theme_combo.setCurrentIndex(choice)
        self.theme_combo.blockSignals(False)
        self.theme_combo.setAccessibleDescription(f"Tema {'oscuro' if self.theme.dark else 'claro'}. {self.theme.description}")
        self.theme_combo.setToolTip(f"{self.theme.name} · Cambiar tema")
        for key, action in self.theme_actions.items():
            action.setChecked(key == self.theme.id)
        for button in self.nav_buttons.values():
            button.theme = self.theme
            button.update()
        self.delegate.theme = self.theme
        fix_placeholder_colors(self)
        self.book_list.viewport().update()
        for reader in self.readers:
            reader.set_theme(self.theme)
        if persist:
            try:
                self.save_preferences()
            except OSError as exc:
                self.notice.announce("error", f"El tema está aplicado, pero no pudimos guardar tu elección: {exc}")

    def adapt_layout(self):
        if not hasattr(self, "selection_label"):
            return
        compact = self.width() < 900
        short = self.height() < 700
        self.sidebar.setFixedWidth(76 if compact else 192)
        self.sidebar_layout.setContentsMargins(8 if compact else 18, 22, 8 if compact else 18, 18)
        self.brand.set_compact(compact)
        show_folders = bool(self.folders) and not compact
        self.folders_heading.setVisible(show_folders)
        self.folder_label.setVisible(show_folders)
        for button in self.nav_buttons.values():
            button.compact = compact
            button.updateGeometry()
            button.update()
        narrow = self.width() < 780
        self.heading_row.setDirection(QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight)
        self.heading_row.setSpacing(12)
        self.folder_button.setText("Fuentes" if compact else "Gestionar fuentes…" if self.folders else "Conectar carpeta…")
        self.folder_button.setToolTip("Gestionar carpetas e ignorados" if self.folders else "Conectar una carpeta de PDF y EPUB")
        self.add_button.setText("Añadir libros")
        self.add_button.setToolTip("Añadir libros desde tu computadora")
        self.filters_layout.setDirection(QBoxLayout.TopToBottom if self.width() < 1120 else QBoxLayout.LeftToRight)
        self.filters_layout.setSpacing(9)
        self.content_layout.setContentsMargins(20 if compact else 32, 22 if compact else 28, 20 if compact else 32, 18)
        self.content_layout.setSpacing(12 if short else 18)
        self.selection_bar.layout().setContentsMargins(0, 10, 0, 0)
        self.selection_label.setVisible(self.width() >= 900)
        self.collection_combo.setMaximumWidth(150 if compact else 180)
        self.title_label.setStyleSheet(f"font: 600 {scaled_px(28)}px 'Montserrat';")
        self.empty_title.setStyleSheet(f"font: 600 {scaled_px(22)}px 'Montserrat';")
        self.compact_catalog = (self.width() < 850 and short) or self.height() < 620
        self.set_layout(self.layout_mode)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.adapt_layout()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: fix_placeholder_colors(self))

    def selected_book(self):
        item = self.book_list.currentItem()
        if not item or not item.isSelected():
            selected = self.book_list.selectedItems()
            item = selected[0] if selected else None
        return item.data(Qt.UserRole) if item else None

    def selected_ids(self):
        ids = []
        for item in self.book_list.selectedItems():
            book = item.data(Qt.UserRole)
            if book:
                ids.extend(book.get("edition_ids", [book["id"]]))
        return list(dict.fromkeys(ids))

    def reload(self):
        try:
            data = self.library.listing()
            self.books, self.folders = data["books"], data["folders"]
            self.retained_books = self.library.listing(include_hidden=True, include_ignored=True)["books"]
            active_ids = {book["id"] for book in self.retained_books}
            for reader in list(self.readers):
                if reader.book["id"] not in active_ids:
                    reader.close()
            self.folder_label.setText("\n\n".join(Path(folder).name or folder for folder in self.folders) or "")
            self.folder_label.setToolTip("\n".join(self.folders))
            for view, button in self.nav_buttons.items():
                count = sum(view == "all" or (book["favorite"] if view == "favorites" else book["status"] == view) for book in self.books)
                button.set_count(count)
            collection = self.collection_combo.currentData()
            self.collection_combo.blockSignals(True)
            self.collection_combo.clear()
            self.collection_combo.addItem("Estantes", "")
            for name in self.library.collection_names():
                self.collection_combo.addItem(name, name)
            shelves = self.library.smart_shelves()
            if shelves:
                self.collection_combo.insertSeparator(self.collection_combo.count())
            for shelf in shelves:
                self.collection_combo.addItem("↳ " + shelf["name"], "@smart:" + str(shelf["id"]))
            self.collection_combo.setCurrentIndex(max(0, self.collection_combo.findData(collection)))
            self.collection_combo.blockSignals(False)
            if self.category_filter not in self.library.taxonomy_names("categories"):
                self.category_filter = ""
            if self.tag_filter not in self.library.taxonomy_names("tags"):
                self.tag_filter = ""
            self.adapt_layout()
            self.render()
            if hasattr(self, "watcher"):
                self.update_watches()
        except (OSError, sqlite3.Error) as exc:
            self.show_error(str(exc))

    def render(self):
        selected = self.selected_book()
        scroll_position = self.book_list.verticalScrollBar().value()
        selected_id = selected["id"] if selected else None
        selected_ids = set(self.selected_ids())
        books = filter_books(self.books, self.view, self.search.text(), self.format_combo.currentData(), self.sort_combo.currentData(), category=self.category_filter, tag=self.tag_filter)
        active = [label + name for label, name in (("Categoría: ", self.category_filter), ("Etiqueta: ", self.tag_filter)) if name]
        if self.format_combo.currentData() != "all":
            active.insert(0, "Formato: " + self.format_combo.currentData())
        self.taxonomy_filter_button.setText(f"Filtros ({len(active)})" if active else "Filtros…")
        self.taxonomy_filter_button.setToolTip("\n".join(active) if active else "Filtrar por formato, etiquetas y categorías; ordenar libros")
        collection = self.collection_combo.currentData() or ""
        shelf = next((shelf for shelf in self.library.smart_shelves() if collection == "@smart:" + str(shelf["id"])), None)
        if shelf:
            books = match_shelf(books, shelf["filters"])
        elif collection:
            books = [book for book in books if collection in book["collections"]]
        file_count = len(books)
        books = self.library.catalog_entries(books)
        self.book_list.clear()
        for book in books:
            item = QListWidgetItem(f"{book['title']} — {book['author']}")
            item.setData(Qt.UserRole, book)
            classification = f"Categorías: {', '.join(book['categories']) or 'Sin categoría'}\nEtiquetas: {', '.join(book['tags']) or 'Sin etiquetas'}"
            item.setData(Qt.AccessibleTextRole, f"{book['title']} — {book['author']}. {classification}")
            item.setToolTip(f"{book['title']}\n{book['author']}\n{classification}\n{book['path']}\n\nDoble clic para leer. Clic derecho para organizar.")
            if len(book.get("edition_ids", [])) > 1:
                item.setToolTip(item.toolTip() + f"\n{len(book['edition_ids'])} ediciones · {book['edition_formats']}")
            self.book_list.addItem(item)
            if book["id"] == selected_id:
                self.book_list.setCurrentItem(item, QItemSelectionModel.NoUpdate)
            if selected_ids.intersection(book.get("edition_ids", [book["id"]])):
                item.setSelected(True)
        self.book_list.verticalScrollBar().setValue(scroll_position)
        self.count_label.setText(f"{len(books)} {'libro' if len(books) == 1 else 'libros'}")
        if file_count != len(books):
            self.count_label.setText(f"{len(books)} fichas · {file_count} archivos")
        self.title_label.setText(shelf["name"] if shelf and self.view == "all" else "Mi biblioteca" if self.view == "all" else VIEWS[self.view])
        for view, button in self.nav_buttons.items():
            button.setChecked(view == self.view)
        self.stack.setCurrentWidget(self.book_list if books else self.empty_widget)
        if self.books:
            self.empty_title.setText("Sin resultados")
            self.empty_body.setText("Probá otra búsqueda o quitá los filtros de categorías y etiquetas.")
            self.empty_button.setText("Ver todos los libros")
        else:
            if self.retained_books:
                has_hidden = any(book["hidden"] for book in self.retained_books)
                self.empty_title.setText("Tu estante está vacío")
                self.empty_body.setText("Tus fichas siguen guardadas. Podés recuperar los libros ocultos o volver a incluir las rutas ignoradas.")
                self.empty_button.setText("Revisar libros ocultos" if has_hidden else "Revisar ignorados")
            else:
                self.empty_title.setText("Todavía no hay libros")
                self.empty_body.setText("Soltá un PDF o EPUB en esta ventana, o añadilo desde tu carpeta.")
                self.empty_button.setText("Añadir libros")
        self.selection_changed()
        if not self.busy:
            self.statusBar().showMessage(f"{len(self.books)} libros en tu biblioteca · Todo se guarda en tu computadora.")

    def set_view(self, view):
        self.view = view
        self.render()

    def set_layout(self, mode):
        self.layout_mode = mode
        show_list = mode == "list" or self.compact_catalog
        self.delegate.list_mode = show_list
        self.book_list.setViewMode(QListView.ListMode if show_list else QListView.IconMode)
        self.book_list.setGridSize(QSize() if show_list else QSize(scaled_px(194), scaled_px(326)))
        self.grid_button.setChecked(not show_list)
        self.list_button.setChecked(show_list)
        self.grid_button.setEnabled(not self.compact_catalog)
        self.grid_button.setToolTip("Ampliá la ventana para ver las portadas en el estante." if self.compact_catalog else "Ver portadas en el estante")
        self.book_list.doItemsLayout()

    def selection_changed(self, *args):
        book = self.selected_book()
        ids = self.selected_ids()
        self.selection_bar.setVisible(bool(ids))
        for button in (self.favorite_button, self.detail_button, self.read_button):
            button.setEnabled(bool(ids))
        self.read_button.setEnabled(bool(book and book["available"] and len(self.book_list.selectedItems()) == 1))
        self.selection_label.setText(f"{len(ids)} archivos seleccionados" if len(ids) > 1 else self.fontMetrics().elidedText(book["title"], Qt.ElideRight, 230) if book else "Elegí un libro del estante.")
        favorite = bool(ids) and all(book["favorite"] for book in self.books if book["id"] in ids)
        self.favorite_button.setChecked(favorite)
        self.favorite_button.setAccessibleName("Quitar de favoritos" if favorite else "Añadir a favoritos")
        self.detail_button.setText(f"Editar {len(ids)} libros" if len(ids) > 1 else "Ficha y etiquetas")

    def empty_action(self):
        if not self.books:
            if self.retained_books:
                self.open_hidden_books() if any(book["hidden"] for book in self.retained_books) else self.manage_sources()
            else:
                self.choose_files()
        else:
            self.category_filter, self.tag_filter = "", ""
            self.search.clear()
            self.format_combo.setCurrentIndex(0)
            self.collection_combo.setCurrentIndex(0)
            self.set_view("all")

    def choose_files(self):
        if self.busy:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Añadir libros a Arandu", str(Path.home()), "Libros (*.pdf *.epub *.PDF *.EPUB)")
        if files:
            self.import_paths(files)

    def choose_folder(self):
        if self.busy:
            return
        folder = QFileDialog.getExistingDirectory(self, "Conectar una carpeta de libros", str(Path.home()))
        if folder:
            self.manage_sources(initial_folder=folder)

    def manage_sources(self, checked=False, initial_folder=None):
        if self.busy:
            self.notice.announce("error", "Esperá a que termine la tarea actual para cambiar las fuentes.")
            return
        self.source_editor_open = True
        self.sync_timer.stop()
        dialog = None
        try:
            dialog = SourcesDialog(self.library, self, initial_folder)
            accepted = dialog.exec_() == QDialog.Accepted
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.show_error(str(exc))
            return
        finally:
            self.source_editor_open = False
            if dialog is not None:
                dialog.deleteLater()
        if accepted:
            self.reload()
            if self.folders:
                self.import_paths(self.folders)
            else:
                self.notice.announce("success", "Fuentes e ignorados guardados. Las fichas existentes se conservan.")
        else:
            self.queue_sync()

    def import_paths(self, paths, automatic=False):
        if self.busy:
            return
        def operation():
            result = {"added": 0, "skipped": 0, "relinked": 0, "ignored": 0, "errors": []}
            for value in paths:
                path = Path(value)
                if path.is_dir():
                    scanned = self.library.scan([str(path)])
                    result["added"] += scanned["added"]
                    result["skipped"] += scanned["skipped"]
                    result["relinked"] += scanned.get("relinked", 0)
                    result["ignored"] += scanned.get("ignored", 0)
                    result["errors"].extend(scanned["errors"])
                else:
                    try:
                        status = self.library.ingest(path)
                        result[{"added": "added", "existing": "skipped", "relinked": "relinked", "ignored": "ignored"}[status]] += 1
                    except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
                        result["errors"].append({"file": str(path), "error": str(exc)})
            return result
        self.run_background(operation, self.import_finished, "Buscando libros…", automatic=automatic)

    def run_background(self, operation, completed, message, automatic=False, locked=False):
        if self.busy:
            return
        self.busy, self.automatic_task = True, automatic
        self.locked_task = locked
        for button in (self.add_button, self.folder_button, self.empty_button, self.content_button):
            button.setEnabled(False)
        if locked:
            self.stack.setEnabled(False)
            self.selection_bar.setEnabled(False)
            self.menuBar().setEnabled(False)
            self.theme_combo.setEnabled(False)
        if not automatic:
            self.notice.announce("loading", message)
        worker = Worker(operation)
        self.workers.add(worker)
        worker.signals.succeeded.connect(completed)
        worker.signals.failed.connect(self.show_error)
        worker.signals.finished.connect(lambda: self.worker_finished(worker))
        self.pool.start(worker)

    def import_finished(self, result):
        self.reload()
        count = result["added"]
        if not self.automatic_task or count or result.get("relinked"):
            message = f"{count} libros añadidos · {result.get('relinked', 0)} ubicaciones recuperadas · {result['skipped']} ya estaban en el catálogo."
            if result.get("ignored"):
                message += f" {result['ignored']} rutas ignoradas; se gestionan en Archivo → Carpetas e ignorados."
            self.notice.announce("success", message)
        self.statusBar().showMessage(f"{result['added']} libros añadidos · {result['skipped']} ya estaban en tu biblioteca.", 15000)
        if result["errors"]:
            self.notice.announce("error", f"{len(result['errors'])} archivos no se pudieron leer. Los {count} libros incorporados ya están en el estante.")
            if self.automatic_task:
                return
            message = QMessageBox(QMessageBox.Warning, "Algunos archivos no se pudieron leer", f"Se añadieron {result['added']} libros.\n{len(result['errors'])} archivos o carpetas no se pudieron leer.", QMessageBox.Ok, self)
            message.setDetailedText("\n".join(f"{entry['file']}: {entry['error']}" for entry in result["errors"]))
            message.exec_()

    def worker_finished(self, worker):
        self.workers.discard(worker)
        self.busy = False
        for button in (self.add_button, self.folder_button, self.empty_button, self.content_button):
            button.setEnabled(True)
        self.stack.setEnabled(True)
        self.selection_bar.setEnabled(True)
        self.menuBar().setEnabled(True)
        self.theme_combo.setEnabled(True)
        if getattr(self, "pending_refresh", False):
            self.pending_refresh = False
            QTimer.singleShot(0, self.refresh_index)
        elif self.watch_pending:
            self.watch_pending = False
            self.queue_sync()

    def rescan(self):
        if self.busy:
            return
        if self.folders:
            self.import_paths(self.folders)
        else:
            self.choose_folder()

    def toggle_favorite(self):
        ids = self.selected_ids()
        if ids:
            try:
                favorite = not all(book["favorite"] for book in self.books if book["id"] in ids)
                self.library.batch_update(ids, {"favorite": favorite})
                self.reload()
                self.notice.announce("success", "Favoritos actualizados.")
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.show_error(str(exc))

    def show_details(self, checked=False, classification=False):
        if len(self.selected_ids()) > 1:
            self.edit_selection()
            return
        book = self.selected_book()
        if book:
            dialog = DetailDialog(self.library, book, self)
            if classification:
                QTimer.singleShot(0, dialog.focus_classification)
            if dialog.exec_() == QDialog.Accepted:
                self.reload()
                self.notice.announce("success", "Ficha, etiquetas y categorías guardadas.")
            dialog.deleteLater()

    def open_book(self, book_id=None, position=None, query=""):
        selected = self.selected_book()
        if book_id is None and selected is None:
            return
        if book_id is None and len(self.book_list.selectedItems()) > 1:
            return
        if book_id is None and len(selected.get("edition_ids", [])) > 1:
            self.manage_editions()
            return
        try:
            book = self.library.get(book_id if book_id is not None else selected["id"])
            if book["deleted_at"]:
                raise ValueError("Restaurá este libro desde la papelera antes de abrirlo.")
            if not book["available"]:
                raise FileNotFoundError("El archivo se movió o ya no existe. Añadilo desde su ubicación actual.")
            for reader in self.readers:
                if reader.book["id"] == book["id"] and reader.isVisible():
                    if position is not None:
                        reader.go_to(position, query)
                    reader.raise_()
                    reader.activateWindow()
                    return
            if position is not None:
                book = {**book, "chapter": position}
            reader = ReaderWindow(self.library, book, self)
            if position is not None:
                if book["format"] == "EPUB":
                    reader.viewer.resume.pop("chapter", None)
                else:
                    reader.viewer._restore_page = None
            if query and book["format"] == "PDF":
                reader.viewer.pending_query = query
            elif query:
                QTimer.singleShot(0, lambda: reader.viewer.browser.find(query.strip('"')))
            reader.set_theme(self.theme)
            reader.libraryChanged.connect(self.reload)
            reader.finished.connect(lambda result: self.reader_finished(reader))
            self.readers.add(reader)
            reader.show()
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError, sqlite3.Error) as exc:
            self.show_error(str(exc))

    def reader_finished(self, reader):
        reader.viewer.stop()
        self.readers.discard(reader)
        reader.deleteLater()

    def context_menu(self, position):
        item = self.book_list.itemAt(position)
        if not item:
            return
        if not item.isSelected():
            self.book_list.setCurrentItem(item)
        book = item.data(Qt.UserRole)
        menu = QMenu(self)
        read = menu.addAction("Leer libro")
        read.setEnabled(book["available"] and len(self.book_list.selectedItems()) == 1)
        read.triggered.connect(lambda: self.open_book())
        menu.addAction("Quitar de favoritos" if book["favorite"] else "Añadir a favoritos", self.toggle_favorite)
        menu.addAction("Detalles y notas…", self.show_details)
        menu.addAction("Etiquetas y categorías…", lambda: self.show_details(classification=True))
        menu.addAction("Editar selección…", self.edit_selection)
        menu.addAction("Obras y ediciones…", self.manage_editions)
        menu.addAction("Reconocer texto (OCR)…", self.open_ocr)
        menu.addAction("Exportar anotaciones…", self.export_annotations)
        menu.addAction("Ocultar del estante", self.hide_selection)
        menu.addAction("Ignorar estos archivos", self.ignore_selection)
        menu.addAction("Mover a la papelera", self.trash_selection)
        folder = menu.addAction("Mostrar carpeta del archivo")
        folder.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(book["path"]).parent))))
        menu.exec_(self.book_list.viewport().mapToGlobal(position))

    def open_content_search(self):
        if self.busy:
            return
        dialog = ContentSearchDialog(self.library, parent=self)
        dialog.resultSelected.connect(lambda book_id, position, query: self.open_book(book_id, position, query))
        dialog.exec_()
        dialog.deleteLater()

    def history_changed(self):
        self.reload()
        for reader in self.readers:
            reader.refresh_from_library()

    def undo_change(self, redo=False):
        if self.busy:
            self.notice.announce("error", "Esperá a que termine la tarea actual para cambiar el historial.")
            return
        try:
            title = self.library.history_step(redo)
            self.history_changed()
            self.notice.announce("success", ("Rehecho: " if redo else "Deshecho: ") + title)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.show_error(str(exc))

    def open_history(self):
        if self.busy:
            self.notice.announce("error", "Esperá a que termine la tarea actual para abrir el historial.")
            return
        self.maintenance_open = True
        self.sync_timer.stop()
        try:
            dialog = HistoryDialog(self.library, self)
            dialog.changed.connect(self.history_changed)
            dialog.exec_()
            dialog.deleteLater()
        finally:
            self.maintenance_open = False
            self.queue_sync()

    def open_global_annotations(self):
        dialog = GlobalAnnotationsDialog(self.library, self)
        dialog.annotationSelected.connect(self.open_annotation)
        dialog.changed.connect(self.history_changed)
        dialog.exec_()
        dialog.deleteLater()

    def open_annotation(self, annotation):
        self.open_book(annotation["book_id"], annotation["position"], annotation.get("quote", ""))
        reader = next((reader for reader in self.readers if reader.book["id"] == annotation["book_id"]), None)
        if reader:
            QTimer.singleShot(0, lambda: reader.open_annotation(annotation))

    def open_diagnostics(self):
        if self.busy:
            self.notice.announce("error", "Esperá a que termine la tarea actual para revisar la biblioteca.")
            return
        self.maintenance_open = True
        self.sync_timer.stop()
        try:
            dialog = DiagnosticsDialog(self.library, self)
            dialog.changed.connect(self.reload)
            dialog.detailsRequested.connect(lambda book_id: self.edition_details(book_id, dialog))
            dialog.ocrRequested.connect(lambda book_id: self.ocr_for_book(book_id, dialog))
            dialog.exec_()
            dialog.deleteLater()
        finally:
            self.maintenance_open = False
            self.queue_sync()

    def ocr_for_book(self, book_id, parent=None):
        dialog = OCRDialog(self.library, self.library.get(book_id), parent or self)
        dialog.changed.connect(self.ocr_updated)
        dialog.exec_()
        dialog.deleteLater()

    def manage_collections(self):
        dialog = CollectionsDialog(self.library, self)
        dialog.exec_()
        dialog.deleteLater()
        self.reload()

    def edit_selection(self):
        ids = self.selected_ids()
        if not ids:
            self.notice.announce("error", "Elegí uno o varios libros. Usá Ctrl o Shift para seleccionar más de uno.")
            return
        dialog = BatchEditDialog(self.library, ids, self)
        if dialog.exec_() == QDialog.Accepted:
            self.reload()
            self.notice.announce("success", f"{len(ids)} libros actualizados.")
        dialog.deleteLater()

    def current_filters(self):
        collection = self.collection_combo.currentData() or ""
        shelf = next((item for item in self.library.smart_shelves() if collection == "@smart:" + str(item["id"])), None)
        values = {"view": self.view, "query": self.search.text(), "file_format": self.format_combo.currentData(), "order": self.sort_combo.currentData(), "category": self.category_filter, "tag": self.tag_filter, "collection": "" if shelf else collection}
        if shelf:
            values = {**shelf["filters"], **{key: value for key, value in values.items() if value and value not in {"all", "recent"}}}
        return values

    def manage_shelves(self):
        dialog = SmartShelvesDialog(self.library, self.current_filters(), self)
        dialog.exec_()
        dialog.deleteLater()
        self.reload()

    def manage_rules(self):
        if self.busy:
            self.notice.announce("error", "Esperá a que termine la tarea actual para aplicar reglas.")
            return
        dialog = RulesDialog(self.library, self)
        dialog.applyRequested.connect(lambda: self.run_background(self.library.apply_all_rules, lambda count: self.rules_applied(count), "Organizando libros…", locked=True))
        dialog.exec_()
        dialog.deleteLater()

    def rules_applied(self, count):
        self.reload()
        self.notice.announce("success", f"Reglas aplicadas: {count} libros actualizados.")

    def manage_editions(self):
        ids = self.selected_ids()
        dialog = EditionsDialog(self.library, ids, self)
        book = self.selected_book()
        if book and book.get("work_id"):
            dialog.reload(book["work_id"])
        dialog.bookSelected.connect(lambda book_id: self.open_book(book_id))
        dialog.detailsRequested.connect(lambda book_id: self.edition_details(book_id, dialog))
        dialog.changed.connect(self.reload)
        dialog.exec_()
        dialog.deleteLater()

    def edition_details(self, book_id, parent):
        details = DetailDialog(self.library, self.library.get(book_id), parent)
        if details.exec_() == QDialog.Accepted:
            self.reload()
        details.deleteLater()

    def open_statistics(self):
        for reader in self.readers:
            reader.tracker.tick()
            reader.tracker.flush()
        dialog = StatisticsDialog(self.library, self)
        dialog.exec_()
        dialog.deleteLater()

    def open_ocr(self):
        book = self.selected_book()
        if not book or book["format"] != "PDF" or not book["available"] or len(self.book_list.selectedItems()) != 1:
            self.notice.announce("error", "Seleccioná un PDF disponible para reconocer sus páginas escaneadas.")
            return
        dialog = OCRDialog(self.library, book, self)
        dialog.changed.connect(self.ocr_updated)
        dialog.exec_()
        dialog.deleteLater()

    def ocr_updated(self):
        for reader in self.readers:
            reader.reload_ocr()
        self.reload()

    def export_annotations(self):
        ids = self.selected_ids() or None
        path, _ = QFileDialog.getSaveFileName(self, "Exportar anotaciones de la selección" if ids else "Exportar todas las anotaciones", "notas-arandu.md", "Markdown (*.md);;Texto (*.txt)")
        if path:
            try:
                self.library.export_annotations(path, ids)
                self.notice.announce("success", "Anotaciones exportadas: " + path)
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.show_error(str(exc))

    def trash_selection(self):
        ids = self.selected_ids()
        if ids and QMessageBox.question(self, "Mover a la papelera", f"¿Mover {len(ids)} fichas a la papelera? Podrás recuperar sus notas y progreso. Los archivos se conservan.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            for reader in list(self.readers):
                if reader.book["id"] in ids:
                    reader.close()
            for book_id in ids:
                self.library.delete(book_id)
            self.reload()
            self.notice.announce("success", "Fichas movidas a la papelera.")

    def open_trash(self):
        dialog = TrashDialog(self.library, self)
        dialog.changed.connect(self.reload)
        dialog.exec_()
        dialog.deleteLater()

    def hide_selection(self):
        ids = self.selected_ids()
        if ids:
            try:
                self.library.set_hidden(ids, True)
                self.reload()
                self.notice.announce("success", "Libros ocultos. Podés recuperarlos en Biblioteca → Libros ocultos.")
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.show_error(str(exc))

    def ignore_selection(self):
        if self.busy:
            self.notice.announce("error", "Esperá a que termine la tarea actual para cambiar las exclusiones.")
            return
        ids = self.selected_ids()
        if ids:
            try:
                self.library.ignore_paths([{"path": self.library.get(book_id)["path"], "kind": "file"} for book_id in ids])
                self.reload()
                self.notice.announce("success", "Archivos ignorados. Para incluirlos, abrí Archivo → Carpetas e ignorados.")
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.show_error(str(exc))

    def open_hidden_books(self):
        dialog = HiddenBooksDialog(self.library, self)
        dialog.changed.connect(self.reload)
        dialog.exec_()
        dialog.deleteLater()

    def filter_classification(self):
        dialog = TaxonomyFilterDialog(self.library, self.category_filter, self.tag_filter, self)
        dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(self.format_combo.currentData()))
        dialog.sort_combo.setCurrentIndex(dialog.sort_combo.findData(self.sort_combo.currentData()))
        if dialog.exec_() == QDialog.Accepted:
            self.category_filter = dialog.category_combo.currentData()
            self.tag_filter = dialog.tag_combo.currentData()
            self.format_combo.setCurrentIndex(self.format_combo.findData(dialog.format_combo.currentData()))
            self.sort_combo.setCurrentIndex(self.sort_combo.findData(dialog.sort_combo.currentData()))
            self.render()
        dialog.deleteLater()

    def manage_classification(self):
        dialog = TaxonomyManagerDialog(self.library, self)
        dialog.renamed.connect(self.classification_renamed)
        dialog.removed.connect(self.classification_removed)
        dialog.changed.connect(self.reload)
        dialog.exec_()
        dialog.deleteLater()

    def classification_renamed(self, kind, previous, name):
        attribute = "tag_filter" if kind == "tags" else "category_filter"
        if term_key(getattr(self, attribute)) == term_key(previous):
            setattr(self, attribute, name)

    def classification_removed(self, kind, name):
        attribute = "tag_filter" if kind == "tags" else "category_filter"
        if term_key(getattr(self, attribute)) == term_key(name):
            setattr(self, attribute, "")

    def review_duplicates(self):
        dialog = DuplicatesDialog(self.library, self)
        def show(book_id):
            details = DetailDialog(self.library, self.library.get(book_id), dialog)
            details.exec_()
            details.deleteLater()
        dialog.bookSelected.connect(show)
        dialog.exec_()
        dialog.deleteLater()
        self.reload()

    def refresh_index(self):
        if self.busy:
            self.pending_refresh = True
            return
        self.run_background(self.library.refresh_all, self.import_finished, "Actualizando metadatos, portadas e índice de búsqueda…")

    def setup_watcher(self):
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self.queue_sync)
        self.watcher.fileChanged.connect(self.queue_sync)
        self.sync_timer = QTimer(self)
        self.sync_timer.setSingleShot(True)
        self.sync_timer.timeout.connect(self.sync_now)
        self.periodic_sync = QTimer(self)
        self.periodic_sync.setInterval(60000)
        self.periodic_sync.timeout.connect(self.queue_sync)
        if self.auto_sync:
            self.periodic_sync.start()
        self.update_watches()

    def set_auto_sync(self, enabled):
        self.auto_sync = bool(enabled)
        if hasattr(self, "periodic_sync"):
            self.periodic_sync.start() if enabled else self.periodic_sync.stop()
            if not enabled:
                self.sync_timer.stop()
            self.update_watches()
            self.save_preferences()

    def update_watches(self):
        desired = set()
        if self.auto_sync:
            exclusions = self.library.exclusions()
            for folder in self.folders:
                root = Path(folder)
                if self.library.is_ignored(root, exclusions) or root.is_relative_to(self.library.data_dir):
                    continue
                if root.is_dir():
                    try:
                        for directory, _, _, _ in self.library.source_walk(root, exclusions=exclusions):
                            desired.add(str(directory))
                    except OSError:
                        pass
                elif root.parent.is_dir():
                    desired.add(str(root.parent))
            desired.update(book["path"] for book in self.library.listing(include_hidden=True)["books"] if book["available"] and not Path(book["path"]).is_relative_to(self.library.data_dir))
        current = set(self.watcher.directories() + self.watcher.files())
        if current - desired:
            self.watcher.removePaths(list(current - desired))
        if desired - current:
            self.watcher.addPaths(list(desired - current))

    def queue_sync(self, *args):
        if self.auto_sync and self.isVisible() and not self.source_editor_open and not self.maintenance_open:
            self.sync_timer.start(1200)

    def sync_now(self):
        if not self.auto_sync or not self.isVisible() or self.source_editor_open or self.maintenance_open:
            return
        if self.busy:
            self.watch_pending = True
            return
        if self.folders:
            self.import_paths(self.folders, automatic=True)
        else:
            self.reload()

    def backup_library(self):
        if self.busy:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Crear copia de seguridad", "arandu-backup.zip", "Copia de Arandu (*.zip)")
        if not path:
            return
        answer = QMessageBox.question(self, "Incluir los libros", "¿Incluir también los archivos PDF y EPUB?\n\nSí: copia portable con los libros disponibles.\nNo: solo catálogo, notas, progreso, temas y ajustes.", QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.No)
        if answer != QMessageBox.Cancel:
            self.save_preferences()
            self.run_background(lambda: create_backup(self.library, path, answer == QMessageBox.Yes), lambda result: self.notice.announce("success", f"Copia guardada: {result}"), "Creando copia de seguridad…")

    def restore_library(self):
        if self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Restaurar copia", "", "Copia de Arandu (*.zip)")
        if not path:
            return
        if QMessageBox.question(self, "Restaurar catálogo", "Se recuperará el catálogo de la copia. Antes guardaremos una copia del estado actual. Los archivos originales no se borran.\n\n¿Continuar?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        for reader in list(self.readers):
            reader.close()
        self.sync_timer.stop()
        self.run_background(lambda: restore_backup(self.library, path), self.backup_restored, "Verificando y restaurando la copia…", locked=True)

    def backup_restored(self, safety):
        self.custom_themes = load_custom_themes(self.library.data_dir)
        self.available_themes = {**THEMES, **self.custom_themes}
        self.update_theme_choices()
        self.restore_preferences()
        self.set_theme(self.theme, persist=False)
        self.delegate.cover.cache_clear()
        self.reload()
        self.notice.announce("success", f"Copia restaurada. El estado anterior se conserva en {safety}")
        self.pending_refresh = True

    def show_error(self, message):
        self.notice.announce("error", message)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self.busy:
            if any(url.isLocalFile() and (Path(url.toLocalFile()).is_dir() or Path(url.toLocalFile()).suffix.lower() in FORMATS) for url in event.mimeData().urls()):
                event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        self.import_paths(paths)
        event.acceptProposedAction()

    def closeEvent(self, event):
        if self.busy:
            self.statusBar().showMessage("Esperá a que termine la importación antes de cerrar.")
            event.ignore()
            return
        if self.theme_editor is not None:
            self.theme_editor.reject()
        if hasattr(self, "sync_timer"):
            self.sync_timer.stop()
            self.periodic_sync.stop()
        for reader in list(self.readers):
            reader.close()
        try:
            self.save_preferences()
            daily_backup(self.library)
        except (OSError, ValueError, sqlite3.Error):
            pass
        super().closeEvent(event)
