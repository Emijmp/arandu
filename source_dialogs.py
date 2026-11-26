"""Selección de fuentes con vista previa y recuperación de libros ocultos."""

from pathlib import Path
import sqlite3

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from sources import canonical_path, exclusion_for, normalize_exclusions


def hint(text, muted=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    if muted:
        widget.setObjectName("muted")
    return widget


class SourcesDialog(QDialog):
    def __init__(self, library, parent=None, initial_folder=None):
        super().__init__(parent)
        self.library = library
        self.folders = list(library.listing()["folders"])
        if initial_folder:
            initial_folder = canonical_path(initial_folder)
            if initial_folder not in self.folders:
                self.folders.append(initial_folder)
        self.exclusions = normalize_exclusions(library.exclusions())
        self.setWindowTitle("Carpetas y archivos ignorados · Arandu")
        self.resize(800, 620)
        self.setMinimumSize(620, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(hint("Conectá una carpeta y elegí qué dejar afuera. Ignorar una subcarpeta también excluye todo su contenido."))
        self.tabs = QTabWidget()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Carpeta o archivo", "Uso como fuente"])
        self.tree.setColumnWidth(0, 475)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setAccessibleName("Carpetas conectadas, subcarpetas y archivos PDF o EPUB")
        self.tree.itemExpanded.connect(self.load_children)
        self.tree.itemSelectionChanged.connect(self.update_actions)
        self.ignored = QTreeWidget()
        self.ignored.setHeaderLabels(["Ruta ignorada", "Tipo"])
        self.ignored.setColumnWidth(0, 540)
        self.ignored.setRootIsDecorated(False)
        self.ignored.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.ignored.setAccessibleName("Exclusiones guardadas, incluidas las rutas que ya no existen")
        self.ignored.itemSelectionChanged.connect(self.update_actions)
        self.tabs.addTab(self.tree, "Fuentes")
        self.tabs.addTab(self.ignored, "Ignorados")
        self.tabs.currentChanged.connect(self.update_actions)
        layout.addWidget(self.tabs, 1)
        self.selection_hint = hint("Ctrl o Shift para elegir varias rutas.", True)
        layout.addWidget(self.selection_hint)
        row = QHBoxLayout()
        self.connect_button = QPushButton("Conectar carpeta…")
        self.connect_button.clicked.connect(self.connect_folder)
        self.disconnect_button = QPushButton("Desconectar")
        self.disconnect_button.clicked.connect(self.disconnect)
        self.ignore_button = QPushButton("Ignorar selección")
        self.ignore_button.clicked.connect(self.ignore_selection)
        self.include_button = QPushButton("Volver a incluir")
        self.include_button.clicked.connect(self.include_selection)
        for button in (self.connect_button, self.disconnect_button, self.ignore_button, self.include_button):
            row.addWidget(button)
        layout.addLayout(row)
        extra = QHBoxLayout()
        for title, callback in (("Ignorar archivo…", self.choose_ignored_file), ("Ignorar carpeta…", self.choose_ignored_folder)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            extra.addWidget(button)
        extra.addStretch()
        layout.addLayout(extra)
        layout.addWidget(hint("Los archivos y las fichas existentes se conservan. Desconectar una fuente conserva sus libros; ignorarla también los aparta del estante. Los cambios se aplican al guardar.", True))
        buttons = QDialogButtonBox()
        buttons.addButton("Cancelar", QDialogButtonBox.RejectRole)
        save = buttons.addButton("Guardar y actualizar", QDialogButtonBox.AcceptRole)
        save.setObjectName("primary")
        save.setDefault(True)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.rebuild()

    def is_internal(self, path):
        return Path(path).resolve().is_relative_to(self.library.data_dir)

    def make_item(self, path, kind, parent=None, root=False):
        path = Path(path)
        item = QTreeWidgetItem([str(path) if root else path.name, ""])
        value = {"path": canonical_path(path), "kind": kind, "root": root}
        item.setData(0, Qt.UserRole, value)
        item.setToolTip(0, str(path))
        if parent:
            parent.addChild(item)
        else:
            self.tree.addTopLevelItem(item)
        if kind == "folder" and not self.is_internal(path) and not path.is_symlink():
            item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        self.refresh_item(item)
        return item

    def refresh_item(self, item):
        value = item.data(0, Qt.UserRole)
        excluded = exclusion_for(value["path"], self.exclusions)
        if self.is_internal(value["path"]):
            state = "Datos de Arandu"
        elif excluded:
            state = "Ignorado" if excluded["path"] == value["path"] else "Ignorado por carpeta"
        else:
            state = "En uso" if Path(value["path"]).exists() else "Ruta no disponible"
        item.setText(1, state)
        item.setToolTip(1, "Excluido por: " + excluded["path"] if excluded else state)
        for index in range(item.childCount()):
            self.refresh_item(item.child(index))

    def load_children(self, item):
        if item.data(0, Qt.UserRole + 1):
            return
        value = item.data(0, Qt.UserRole)
        item.setData(0, Qt.UserRole + 1, True)
        path = Path(value["path"])
        try:
            children = sorted(path.iterdir(), key=lambda child: (not child.is_dir(), child.name.casefold()))
            for child in children:
                if child.is_dir():
                    self.make_item(child, "folder", item)
                elif child.suffix.lower() in {".pdf", ".epub"}:
                    self.make_item(child, "file", item)
            if not item.childCount():
                item.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
        except OSError as exc:
            item.setText(1, "No se pudo abrir")
            item.setToolTip(1, str(exc))

    def rebuild(self):
        self.tree.clear()
        for folder in self.folders:
            item = self.make_item(folder, "folder", root=True)
            if not self.is_internal(folder):
                item.setExpanded(True)
        self.refresh_exclusions()

    def refresh_exclusions(self):
        self.ignored.clear()
        for value in sorted(self.exclusions, key=lambda entry: entry["path"].casefold()):
            item = QTreeWidgetItem([value["path"], "Carpeta y contenido" if value["kind"] == "folder" else "Archivo"])
            item.setData(0, Qt.UserRole, value)
            item.setToolTip(0, value["path"])
            self.ignored.addTopLevelItem(item)
        self.tabs.setTabText(1, f"Ignorados ({len(self.exclusions)})")
        for index in range(self.tree.topLevelItemCount()):
            self.refresh_item(self.tree.topLevelItem(index))
        self.update_actions()

    def selected(self):
        tree = self.tree if self.tabs.currentIndex() == 0 else self.ignored
        return [item.data(0, Qt.UserRole) for item in tree.selectedItems()]

    def update_actions(self, *args):
        if not hasattr(self, "include_button"):
            return
        values = self.selected()
        exact = {value["path"] for value in self.exclusions}
        self.ignore_button.setEnabled(any(not self.is_internal(value["path"]) and not exclusion_for(value["path"], self.exclusions) for value in values))
        self.include_button.setEnabled(any(value["path"] in exact for value in values))
        self.disconnect_button.setEnabled(any(value.get("root") for value in values))
        if len(values) == 1:
            value = values[0]
            excluded = exclusion_for(value["path"], self.exclusions)
            text = value["path"]
            if excluded and excluded["path"] != value["path"]:
                text += "\nPara incluirlo, quitá primero la exclusión de «" + excluded["path"] + "»."
            self.selection_hint.setText(text)
        elif values:
            self.selection_hint.setText(f"{len(values)} rutas seleccionadas.")
        else:
            self.selection_hint.setText("Ctrl o Shift para elegir varias rutas." if self.folders else "Todavía no hay fuentes. Conectá una carpeta para revisar sus libros.")

    def connect_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Conectar una carpeta", "")
        if path:
            path = canonical_path(path)
            if path not in self.folders:
                self.folders.append(path)
                self.rebuild()

    def disconnect(self):
        removed = {value["path"] for value in self.selected() if value.get("root")}
        self.folders = [path for path in self.folders if path not in removed]
        self.rebuild()

    def stage_ignored(self, values):
        values = [value for value in values if not self.is_internal(value["path"])]
        self.exclusions = normalize_exclusions([*self.exclusions, *values])
        self.refresh_exclusions()

    def ignore_selection(self):
        self.stage_ignored(self.selected())

    def include_selection(self):
        included = {value["path"] for value in self.selected()}
        self.exclusions = [value for value in self.exclusions if value["path"] not in included]
        self.refresh_exclusions()

    def choose_ignored_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Ignorar archivos", "", "Libros (*.pdf *.epub *.PDF *.EPUB)")
        self.stage_ignored([{"path": canonical_path(path), "kind": "file"} for path in paths])

    def choose_ignored_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Ignorar una carpeta y todo su contenido", "")
        if path:
            self.stage_ignored([{"path": canonical_path(path), "kind": "folder"}])

    def save(self):
        try:
            self.library.configure_sources(self.folders, self.exclusions)
            self.accept()
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "No se pudieron guardar las fuentes", str(exc))


class HiddenBooksDialog(QDialog):
    changed = pyqtSignal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("Libros ocultos · Arandu")
        self.resize(620, 470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(hint("Estos libros conservan su ficha, notas y progreso. Ocultarlos no impide actualizar sus archivos desde las fuentes conectadas."))
        self.items = QListWidget()
        self.items.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.items.setAccessibleName("Libros ocultos. Ctrl o Shift para seleccionar varios.")
        self.items.itemSelectionChanged.connect(self.update_actions)
        layout.addWidget(self.items, 1)
        self.status = hint("", True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        self.restore_button = QPushButton("Mostrar selección")
        self.restore_button.setObjectName("primary")
        self.restore_button.clicked.connect(self.restore)
        buttons.addWidget(close)
        buttons.addWidget(self.restore_button)
        layout.addLayout(buttons)
        self.reload()

    def reload(self):
        self.items.clear()
        for book in self.library.hidden_books():
            suffix = " · Archivo ignorado además" if book["ignored"] else ""
            item = QListWidgetItem(f"{book['title']} · {book['format']}{suffix}\n{book['author']}")
            item.setData(Qt.UserRole, book["id"])
            item.setToolTip(book["path"])
            self.items.addItem(item)
        self.status.setText(f"{self.items.count()} {'ficha oculta' if self.items.count() == 1 else 'fichas ocultas'}. Si un archivo también está ignorado, volvé a incluir su ruta en Archivo → Carpetas e ignorados." if self.items.count() else "No hay libros ocultos. Tu estante está a la vista.")
        self.update_actions()

    def update_actions(self):
        if hasattr(self, "restore_button"):
            self.restore_button.setEnabled(bool(self.items.selectedItems()))

    def restore(self):
        ids = [item.data(Qt.UserRole) for item in self.items.selectedItems()]
        if ids:
            try:
                self.library.set_hidden(ids, False)
                self.reload()
                self.changed.emit()
            except (OSError, ValueError, sqlite3.Error) as exc:
                QMessageBox.warning(self, "No se pudieron mostrar los libros", str(exc))
