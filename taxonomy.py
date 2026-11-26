"""Clasificación local: sugerencias, filtros y administración de nombres."""

import sqlite3

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QCompleter, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from library import TAXONOMIES


class CommaCompleter(QCompleter):
    def splitPath(self, path):
        return [path.rsplit(",", 1)[-1].strip()]

    def pathFromIndex(self, index):
        name = super().pathFromIndex(index)
        text = self.widget().text()
        return text.rsplit(",", 1)[0].rstrip() + ", " + name if "," in text else name


class TermEdit(QLineEdit):
    def __init__(self, values, suggestions, parent=None):
        super().__init__(", ".join(values), parent)
        completer = CommaCompleter(suggestions, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.setCompleter(completer)
        self.setClearButtonEnabled(True)
        self.setToolTip("Separá los nombres con comas. Hasta 30 por libro y 60 caracteres por nombre.")


class TaxonomyFilterDialog(QDialog):
    def __init__(self, library, category="", tag="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Filtrar y ordenar · Arandu")
        self.resize(520, 430)
        self.setMinimumSize(440, 400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        description = QLabel("Elegí qué libros querés ver y en qué orden. Los filtros se combinan entre sí.")
        description.setWordWrap(True)
        layout.addWidget(description)
        form = QFormLayout()
        form.setSpacing(12)
        self.format_combo = QComboBox()
        for title, value in (("Todos los formatos", "all"), ("PDF", "PDF"), ("EPUB", "EPUB")):
            self.format_combo.addItem(title, value)
        self.format_combo.setAccessibleName("Formato de los libros")
        self.format_combo.setMinimumHeight(self.format_combo.sizeHint().height())
        form.addRow("Formato", self.format_combo)
        self.category_combo, self.tag_combo = QComboBox(), QComboBox()
        for kind, combo, label, selected in (
            ("categories", self.category_combo, "Categoría", category),
            ("tags", self.tag_combo, "Etiqueta", tag),
        ):
            combo.addItem("Todas las " + TAXONOMIES[kind], "")
            for name, count in library.taxonomy_counts(kind).items():
                combo.addItem(f"{name} ({count})", name)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.setAccessibleName("Filtrar por " + label.lower())
            combo.setMinimumContentsLength(16)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumHeight(combo.sizeHint().height())
            form.addRow(label, combo)
        self.sort_combo = QComboBox()
        for title, value in (("Más recientes", "recent"), ("Título: A–Z", "title"), ("Autor: A–Z", "author"), ("Progreso", "progress"), ("Serie", "series")):
            self.sort_combo.addItem(title, value)
        self.sort_combo.setAccessibleName("Orden de los libros")
        self.sort_combo.setMinimumHeight(self.sort_combo.sizeHint().height())
        form.addRow("Orden", self.sort_combo)
        layout.addLayout(form)
        hint = QLabel("Se conserva la búsqueda y el estante que tenías seleccionados.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox()
        clear = buttons.addButton("Limpiar filtros", QDialogButtonBox.ResetRole)
        clear.clicked.connect(self.clear)
        buttons.addButton("Cancelar", QDialogButtonBox.RejectRole)
        apply = buttons.addButton("Aplicar", QDialogButtonBox.AcceptRole)
        apply.setObjectName("primary")
        apply.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def clear(self):
        self.format_combo.setCurrentIndex(0)
        self.sort_combo.setCurrentIndex(0)
        self.category_combo.setCurrentIndex(0)
        self.tag_combo.setCurrentIndex(0)


class TaxonomyManagerDialog(QDialog):
    changed = pyqtSignal()
    renamed = pyqtSignal(str, str, str)
    removed = pyqtSignal(str, str)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("Etiquetas y categorías · Arandu")
        self.resize(540, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        description = QLabel("Categorías para ordenar tus libros. Etiquetas para cruzar temas, lugares o ideas.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.tabs = QTabWidget()
        self.lists = {}
        for kind, label in (("tags", "Etiquetas"), ("categories", "Categorías")):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            items = QListWidget()
            items.setAccessibleName(label + " y cantidad de libros")
            items.itemDoubleClicked.connect(lambda item: self.rename())
            self.lists[kind] = items
            page_layout.addWidget(items)
            self.tabs.addTab(page, label)
        layout.addWidget(self.tabs, 1)
        self.empty = QLabel()
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        row = QHBoxLayout()
        self.new_button = QPushButton("Nueva")
        self.rename_button = QPushButton("Renombrar")
        self.remove_button = QPushButton("Eliminar")
        self.new_button.clicked.connect(self.create)
        self.rename_button.clicked.connect(self.rename)
        self.remove_button.clicked.connect(self.remove)
        for button in (self.new_button, self.rename_button, self.remove_button):
            row.addWidget(button)
        layout.addLayout(row)
        hint = QLabel("Para asignarlas, elegí un libro y abrí «Ficha y etiquetas». Separá los nombres con comas.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        layout.addWidget(close, 0, Qt.AlignRight)
        self.tabs.currentChanged.connect(self.update_actions)
        for items in self.lists.values():
            items.currentItemChanged.connect(self.update_actions)
        self.reload()

    @property
    def kind(self):
        return "tags" if self.tabs.currentIndex() == 0 else "categories"

    def update_actions(self, *args):
        items = self.lists[self.kind]
        selected = items.currentItem() is not None
        self.rename_button.setEnabled(selected)
        self.remove_button.setEnabled(selected)
        self.empty.setText("Elegí un nombre para renombrarlo o quitarlo de todas las fichas." if items.count() else "Todavía no hay " + TAXONOMIES[self.kind] + ". Creá la primera aquí o desde la ficha de un libro.")

    def reload(self, selected=None):
        for kind, items in self.lists.items():
            previous = selected if kind == self.kind and selected else (items.currentItem().data(Qt.UserRole) if items.currentItem() else None)
            items.clear()
            for name, count in self.library.taxonomy_counts(kind).items():
                item = QListWidgetItem(f"{name}  ·  {count} {'libro' if count == 1 else 'libros'}")
                item.setData(Qt.UserRole, name)
                items.addItem(item)
                if name == previous:
                    items.setCurrentItem(item)
        self.update_actions()

    def create(self):
        singular = "etiqueta" if self.kind == "tags" else "categoría"
        name, accepted = QInputDialog.getText(self, "Nueva " + singular, "Nombre")
        if accepted:
            self.save(name)

    def rename(self):
        item = self.lists[self.kind].currentItem()
        if item:
            previous = item.data(Qt.UserRole)
            name, accepted = QInputDialog.getText(self, "Renombrar", "Se actualizará en todos sus libros.", text=previous)
            if accepted:
                self.save(name, previous)

    def save(self, name, previous=None):
        try:
            name = self.library.save_taxonomy(self.kind, name, previous)
            if previous is not None:
                self.renamed.emit(self.kind, previous, name)
            self.reload(name)
            self.changed.emit()
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "No se pudo guardar", str(exc))

    def remove(self):
        item = self.lists[self.kind].currentItem()
        if not item:
            return
        name = item.data(Qt.UserRole)
        if QMessageBox.question(self, "Eliminar nombre", f"¿Querés quitar «{name}» de todas las fichas?\n\nLos libros, archivos y notas se conservan.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.library.remove_taxonomy(self.kind, name)
            self.removed.emit(self.kind, name)
            self.reload()
            self.changed.emit()
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "No se pudo eliminar", str(exc))
