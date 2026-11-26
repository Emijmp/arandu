"""Herramientas nativas para organizar una biblioteca sin recargar el estante."""

from datetime import datetime
import sqlite3

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSpinBox, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from organizer import match_shelf, rule_matches
from taxonomy import TermEdit

STATUS_CHOICES = (("Todos", "all"), ("Por leer", "pending"), ("Leyendo", "reading"), ("Leídos", "finished"), ("Favoritos", "favorites"))
MONTHS = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre")


def label(text, muted=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    if muted:
        widget.setObjectName("muted")
    return widget


def combo(choices, selected=""):
    widget = QComboBox()
    for title, value in choices:
        widget.addItem(title, value)
    if selected and widget.findData(selected) < 0:
        widget.addItem(str(selected) + " (sin libros)", selected)
    widget.setCurrentIndex(max(0, widget.findData(selected)))
    widget.setMinimumHeight(widget.sizeHint().height())
    return widget


def buttons(dialog, layout, save="Guardar"):
    row = QDialogButtonBox()
    row.addButton("Cancelar", QDialogButtonBox.RejectRole)
    primary = row.addButton(save, QDialogButtonBox.AcceptRole)
    primary.setObjectName("primary")
    primary.setDefault(True)
    row.accepted.connect(dialog.save)
    row.rejected.connect(dialog.reject)
    layout.addWidget(row)


def scroll_form(layout):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    form = QFormLayout(body)
    form.setSpacing(12)
    scroll.setWidget(body)
    layout.addWidget(scroll, 1)
    return form


class BatchEditDialog(QDialog):
    def __init__(self, library, book_ids, parent=None):
        super().__init__(parent)
        self.library, self.book_ids = library, list(book_ids)
        self.setWindowTitle("Editar varios libros · Arandu")
        self.resize(570, 530)
        layout = QVBoxLayout(self)
        layout.addWidget(label(f"{len(book_ids)} libros. Solo se cambiarán los campos que marques."))
        form = scroll_form(layout)
        self.fields, self.checks = {}, {}
        self.fields["author"] = QLineEdit()
        self.fields["status"] = combo(STATUS_CHOICES[1:4], "pending")
        self.fields["favorite"] = combo((("Añadir a favoritos", True), ("Quitar de favoritos", False)), True)
        for kind, title in (("tags", "Etiquetas"), ("categories", "Categorías"), ("collections", "Colecciones")):
            names = library.collection_names() if kind == "collections" else library.taxonomy_names(kind)
            self.fields[kind] = TermEdit([], names)
            self.fields[kind].setPlaceholderText("Nombres separados por comas")
        for kind, title in (("author", "Autor"), ("status", "Estado"), ("favorite", "Favoritos"), ("tags", "Etiquetas"), ("categories", "Categorías"), ("collections", "Colecciones")):
            check = QCheckBox(title)
            field = self.fields[kind]
            field.setEnabled(False)
            field.setAccessibleName(title + " para los libros seleccionados")
            check.toggled.connect(field.setEnabled)
            self.checks[kind] = check
            form.addRow(check, field)
        self.mode = combo((("Añadir nombres", "add"), ("Reemplazar todos los nombres", "replace"), ("Quitar estos nombres", "remove")), "add")
        form.addRow("Etiquetas y grupos", self.mode)
        layout.addWidget(label("Añadir conserva los nombres existentes. Reemplazar con un campo vacío los borra de los libros seleccionados.", True))
        buttons(self, layout, "Aplicar cambios")

    def save(self):
        changes = {}
        for kind, check in self.checks.items():
            if check.isChecked():
                field = self.fields[kind]
                changes[kind] = field.currentData() if isinstance(field, QComboBox) else field.text()
                if kind in {"tags", "categories", "collections"}:
                    changes[kind] = changes[kind].split(",")
        try:
            self.library.batch_update(self.book_ids, changes, self.mode.currentData())
            self.accept()
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "No se pudieron aplicar los cambios", str(exc))


class ShelfEditorDialog(QDialog):
    def __init__(self, library, values=None, parent=None):
        super().__init__(parent)
        self.library, self.shelf = library, values or {}
        self.setWindowTitle("Estante inteligente · Arandu")
        self.resize(560, 620)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Un estante inteligente se actualiza cuando tus libros cumplen estos filtros."))
        form = scroll_form(layout)
        filters = self.shelf.get("filters", {})
        self.name = QLineEdit(self.shelf.get("name", ""))
        self.name.setMaxLength(80)
        self.query = QLineEdit(filters.get("query", ""))
        self.view = combo(STATUS_CHOICES, filters.get("view", "all"))
        self.format = combo((("Todos", "all"), ("PDF", "PDF"), ("EPUB", "EPUB")), filters.get("file_format", "all"))
        self.category = combo([("Todas", ""), *((name, name) for name in library.taxonomy_names("categories"))], filters.get("category", ""))
        self.tag = combo([("Todas", ""), *((name, name) for name in library.taxonomy_names("tags"))], filters.get("tag", ""))
        self.collection = combo([("Todas", ""), *((name, name) for name in library.collection_names())], filters.get("collection", ""))
        self.order = combo((("Más recientes", "recent"), ("Título", "title"), ("Autor", "author"), ("Progreso", "progress"), ("Serie", "series")), filters.get("order", "recent"))
        for title, field in (("Nombre", self.name), ("Buscar", self.query), ("Estado", self.view), ("Formato", self.format), ("Categoría", self.category), ("Etiqueta", self.tag), ("Colección", self.collection), ("Orden", self.order)):
            form.addRow(title, field)
        self.preview = label("", True)
        layout.addWidget(self.preview)
        for field in (self.view, self.format, self.category, self.tag, self.collection, self.order):
            field.currentIndexChanged.connect(self.update_preview)
        self.query.textChanged.connect(self.update_preview)
        self.update_preview()
        buttons(self, layout)

    def filters(self):
        return {"view": self.view.currentData(), "file_format": self.format.currentData(), "category": self.category.currentData(), "tag": self.tag.currentData(), "collection": self.collection.currentData(), "query": self.query.text(), "order": self.order.currentData()}

    def update_preview(self):
        count = len(match_shelf(self.library.listing()["books"], self.filters()))
        self.preview.setText(f"{count} {'libro cumple' if count == 1 else 'libros cumplen'} los filtros ahora.")

    def save(self):
        try:
            self.library.save_shelf(self.name.text(), self.filters(), self.shelf.get("id"))
            self.accept()
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "No se pudo guardar el estante", str(exc))


class SmartShelvesDialog(QDialog):
    def __init__(self, library, current_filters, parent=None):
        super().__init__(parent)
        self.library, self.current_filters = library, current_filters
        self.setWindowTitle("Estantes inteligentes · Arandu")
        self.resize(540, 450)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Guardá tus búsquedas habituales como estantes que se actualizan solos."))
        self.items = QListWidget()
        self.items.setAccessibleName("Estantes inteligentes y cantidad de libros")
        self.items.itemDoubleClicked.connect(lambda: self.edit())
        layout.addWidget(self.items, 1)
        self.empty = label("Todavía no hay estantes inteligentes. Guardá una búsqueda para crear el primero.", True)
        layout.addWidget(self.empty)
        row = QHBoxLayout()
        for title, callback in (("Nuevo", self.create), ("Editar", self.edit), ("Eliminar", self.remove)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        layout.addWidget(label("El nuevo estante parte de los filtros que tenías en la biblioteca. Elegilo luego en el selector de colecciones y estantes.", True))
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.reload()

    def reload(self):
        self.items.clear()
        books = self.library.listing()["books"]
        for shelf in self.library.smart_shelves():
            item = QListWidgetItem(f"{shelf['name']} · {len(match_shelf(books, shelf['filters']))} libros")
            item.setData(Qt.UserRole, shelf)
            self.items.addItem(item)
        self.empty.setVisible(self.items.count() == 0)

    def create(self):
        dialog = ShelfEditorDialog(self.library, {"filters": self.current_filters}, self)
        if dialog.exec_() == QDialog.Accepted:
            self.reload()
        dialog.deleteLater()

    def edit(self):
        if self.items.currentItem():
            dialog = ShelfEditorDialog(self.library, self.items.currentItem().data(Qt.UserRole), self)
            if dialog.exec_() == QDialog.Accepted:
                self.reload()
            dialog.deleteLater()

    def remove(self):
        item = self.items.currentItem()
        if item and QMessageBox.question(self, "Eliminar estante", "¿Eliminar este estante inteligente? Los libros se conservan.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.library.delete_shelf(item.data(Qt.UserRole)["id"])
            self.reload()


class RuleEditorDialog(QDialog):
    def __init__(self, library, rule=None, parent=None):
        super().__init__(parent)
        self.library, self.rule = library, rule or {}
        self.setWindowTitle("Regla de organización · Arandu")
        self.resize(600, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Si se cumplen todas las condiciones indicadas, se añaden los nombres elegidos. Los campos vacíos no limitan la regla."))
        form = scroll_form(layout)
        self.name = QLineEdit(self.rule.get("name", ""))
        self.name.setMaxLength(80)
        self.enabled = QCheckBox("Activa al incorporar o actualizar libros")
        self.enabled.setChecked(bool(self.rule.get("enabled", True)))
        condition = self.rule.get("conditions", {})
        self.folder = QLineEdit(condition.get("folder", ""))
        folder_widget = QWidget()
        row = QHBoxLayout(folder_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.folder, 1)
        choose = QPushButton("Elegir…")
        choose.clicked.connect(self.choose_folder)
        row.addWidget(choose)
        self.filename = QLineEdit(condition.get("filename", ""))
        self.author = QLineEdit(condition.get("author", ""))
        self.format = combo((("Cualquiera", ""), ("PDF", "PDF"), ("EPUB", "EPUB")), condition.get("format", ""))
        for title, field in (("Nombre", self.name), ("", self.enabled), ("Dentro de carpeta", folder_widget), ("Archivo contiene", self.filename), ("Autor contiene", self.author), ("Formato", self.format)):
            form.addRow(title, field)
        self.actions = {}
        for kind, title in (("tags", "Añadir etiquetas"), ("categories", "Añadir categorías"), ("collections", "Añadir a colecciones")):
            names = library.collection_names() if kind == "collections" else library.taxonomy_names(kind)
            field = TermEdit(self.rule.get("actions", {}).get(kind, []), names)
            field.setPlaceholderText("Nombres separados por comas")
            self.actions[kind] = field
            form.addRow(title, field)
        self.preview = label("", True)
        layout.addWidget(self.preview)
        for field in (self.folder, self.filename, self.author):
            field.textChanged.connect(self.update_preview)
        self.format.currentIndexChanged.connect(self.update_preview)
        self.update_preview()
        buttons(self, layout)

    def conditions(self):
        return {"folder": self.folder.text().strip(), "filename": self.filename.text().strip(), "author": self.author.text().strip(), "format": self.format.currentData()}

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Carpeta de la regla", self.folder.text())
        if path:
            self.folder.setText(path)

    def update_preview(self):
        conditions = self.conditions()
        count = sum(rule_matches(book, conditions) for book in self.library.listing()["books"]) if any(conditions.values()) else 0
        self.preview.setText(f"{count} {'libro coincide' if count == 1 else 'libros coinciden'} ahora. Al menos una condición es obligatoria.")

    def save(self):
        try:
            self.library.save_rule(self.name.text(), self.conditions(), {kind: field.text().split(",") for kind, field in self.actions.items()}, self.enabled.isChecked(), self.rule.get("id"))
            self.accept()
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "No se pudo guardar la regla", str(exc))


class RulesDialog(QDialog):
    applyRequested = pyqtSignal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("Reglas de organización · Arandu")
        self.resize(560, 450)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Organización automática al incorporar o actualizar archivos. Aplicar ahora también incluye los libros existentes."))
        self.items = QListWidget()
        self.items.setAccessibleName("Reglas de organización")
        self.items.itemDoubleClicked.connect(lambda: self.edit())
        layout.addWidget(self.items, 1)
        self.empty = label("Sin reglas todavía. Podés empezar por una carpeta o un autor.", True)
        layout.addWidget(self.empty)
        row = QHBoxLayout()
        for title, callback in (("Nueva", self.create), ("Editar", self.edit), ("Eliminar", self.remove)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        apply = QPushButton("Aplicar a la biblioteca")
        apply.setObjectName("primary")
        apply.clicked.connect(self.apply)
        layout.addWidget(apply)
        self.reload()

    def reload(self):
        self.items.clear()
        for rule in self.library.rules():
            item = QListWidgetItem(f"{rule['name']} · {'Activa' if rule['enabled'] else 'Pausada'}")
            item.setData(Qt.UserRole, rule)
            self.items.addItem(item)
        self.empty.setVisible(self.items.count() == 0)

    def create(self):
        dialog = RuleEditorDialog(self.library, parent=self)
        dialog.exec_()
        dialog.deleteLater()
        self.reload()

    def edit(self):
        if self.items.currentItem():
            dialog = RuleEditorDialog(self.library, self.items.currentItem().data(Qt.UserRole), self)
            dialog.exec_()
            dialog.deleteLater()
            self.reload()

    def remove(self):
        item = self.items.currentItem()
        if item and QMessageBox.question(self, "Eliminar regla", "¿Eliminar esta regla? Las clasificaciones ya asignadas se conservan.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.library.delete_rule(item.data(Qt.UserRole)["id"])
            self.reload()

    def apply(self):
        self.accept()
        self.applyRequested.emit()


class TrashDialog(QDialog):
    changed = pyqtSignal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("Papelera del catálogo · Arandu")
        self.resize(660, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Las fichas conservan sus notas, progreso y anotaciones. Los archivos originales permanecen en tu computadora."))
        self.items = QListWidget()
        self.items.setSelectionMode(QListWidget.ExtendedSelection)
        self.items.setAccessibleName("Libros en la papelera")
        layout.addWidget(self.items, 1)
        self.status = label("", True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.restore_button = QPushButton("Restaurar selección")
        self.restore_button.setObjectName("primary")
        self.restore_button.clicked.connect(self.restore)
        self.purge_button = QPushButton("Eliminar definitivamente")
        self.purge_button.setObjectName("danger")
        self.purge_button.clicked.connect(self.purge)
        row.addWidget(self.restore_button)
        row.addWidget(self.purge_button)
        layout.addLayout(row)
        self.items.itemSelectionChanged.connect(self.update_actions)
        self.reload()

    def reload(self):
        self.items.clear()
        for book in self.library.trash():
            item = QListWidgetItem(f"{book['title']} · {book['format']}\n{book['author']} · {datetime.fromtimestamp(book['deleted_at']):%d/%m/%Y}")
            item.setData(Qt.UserRole, book["id"])
            self.items.addItem(item)
        self.status.setText(f"{self.items.count()} fichas en la papelera." if self.items.count() else "La papelera está vacía.")
        self.update_actions()

    def update_actions(self):
        selected = bool(self.items.selectedItems())
        self.restore_button.setEnabled(selected)
        self.purge_button.setEnabled(selected)

    def restore(self):
        try:
            for item in self.items.selectedItems():
                self.library.restore_book(item.data(Qt.UserRole))
            self.reload()
            self.changed.emit()
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "No se pudo restaurar", str(exc))

    def purge(self):
        selected = [item.data(Qt.UserRole) for item in self.items.selectedItems()]
        if selected and QMessageBox.question(self, "Eliminar fichas definitivamente", f"¿Eliminar {len(selected)} fichas y todas sus notas y anotaciones? Esta acción no se puede deshacer.\n\nLos archivos originales se conservan.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            try:
                for book_id in selected:
                    self.library.purge_book(book_id)
                self.reload()
                self.changed.emit()
            except (OSError, ValueError, sqlite3.Error) as exc:
                QMessageBox.warning(self, "No se pudo eliminar", str(exc))


class EditionsDialog(QDialog):
    bookSelected = pyqtSignal(int)
    detailsRequested = pyqtSignal(int)
    changed = pyqtSignal()

    def __init__(self, library, selected_ids=(), parent=None):
        super().__init__(parent)
        self.library, self.selected_ids = library, list(selected_ids)
        self.setWindowTitle("Obras y ediciones · Arandu")
        self.resize(690, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Agrupá PDF, EPUB y otras ediciones en una sola ficha del estante. Cada archivo conserva su progreso y anotaciones."))
        self.works_combo = QComboBox()
        self.works_combo.setAccessibleName("Obra agrupada")
        self.works_combo.currentIndexChanged.connect(self.reload_members)
        layout.addWidget(self.works_combo)
        self.members = QListWidget()
        self.members.setAccessibleName("Ediciones de la obra")
        self.members.itemDoubleClicked.connect(lambda: self.read())
        self.members.currentItemChanged.connect(self.update_actions)
        layout.addWidget(self.members, 1)
        row = QHBoxLayout()
        self.read_button = QPushButton("Leer edición")
        self.read_button.clicked.connect(self.read)
        self.details_button = QPushButton("Ver ficha")
        self.details_button.clicked.connect(self.details)
        self.prefer_button = QPushButton("Preferir")
        self.prefer_button.clicked.connect(self.prefer)
        self.separate_button = QPushButton("Separar")
        self.separate_button.clicked.connect(self.separate)
        for button in (self.read_button, self.details_button, self.prefer_button, self.separate_button):
            row.addWidget(button)
        layout.addLayout(row)
        group_row = QHBoxLayout()
        self.create_button = QPushButton("Agrupar selección como obra nueva")
        self.create_button.setEnabled(len(self.selected_ids) >= 2)
        self.create_button.clicked.connect(self.create)
        self.attach_button = QPushButton("Añadir selección a esta obra")
        self.attach_button.clicked.connect(self.attach)
        group_row.addWidget(self.create_button)
        group_row.addWidget(self.attach_button)
        layout.addLayout(group_row)
        self.status = label(f"{len(self.selected_ids)} archivos seleccionados en el estante. Usá Ctrl o Shift para elegir varios.", True)
        layout.addWidget(self.status)
        self.reload()

    def reload(self, work_id=None):
        selected = work_id or self.works_combo.currentData()
        self.works_combo.blockSignals(True)
        self.works_combo.clear()
        for work in self.library.works():
            self.works_combo.addItem(work["title"], work["id"])
        self.works_combo.setCurrentIndex(max(0, self.works_combo.findData(selected)))
        self.works_combo.blockSignals(False)
        self.reload_members()

    def reload_members(self):
        self.members.clear()
        work_id = self.works_combo.currentData()
        preferred = next((work["preferred_book_id"] for work in self.library.works() if work["id"] == work_id), None)
        for book in self.library.listing()["books"]:
            if book["work_id"] == work_id:
                item = QListWidgetItem(f"{book['format']} · {book['title']}{' · Preferida' if book['id'] == preferred else ''}\n{book['path']}")
                item.setData(Qt.UserRole, book)
                self.members.addItem(item)
        if self.members.count():
            self.members.setCurrentRow(0)
        self.update_actions()

    def update_actions(self, *args):
        book = self.members.currentItem().data(Qt.UserRole) if self.members.currentItem() else None
        for button in (self.details_button, self.prefer_button, self.separate_button):
            button.setEnabled(book is not None)
        self.read_button.setEnabled(bool(book and book["available"]))
        self.attach_button.setEnabled(bool(self.selected_ids and self.works_combo.currentData()))

    def create(self):
        try:
            work_id = self.library.group_editions(self.selected_ids)
            self.reload(work_id)
            self.changed.emit()
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "No se pudo agrupar", str(exc))

    def attach(self):
        try:
            self.library.group_editions(self.selected_ids, self.works_combo.currentData())
            self.reload()
            self.changed.emit()
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "No se pudo agrupar", str(exc))

    def separate(self):
        if self.members.currentItem():
            try:
                self.library.ungroup_edition(self.members.currentItem().data(Qt.UserRole)["id"])
                self.reload()
                self.changed.emit()
            except (OSError, ValueError, sqlite3.Error) as exc:
                QMessageBox.warning(self, "No se pudo separar", str(exc))

    def prefer(self):
        if self.members.currentItem():
            try:
                self.library.prefer_edition(self.works_combo.currentData(), self.members.currentItem().data(Qt.UserRole)["id"])
                self.reload()
                self.changed.emit()
            except (OSError, ValueError, sqlite3.Error) as exc:
                QMessageBox.warning(self, "No se pudo elegir la edición", str(exc))

    def read(self):
        item = self.members.currentItem()
        if item and item.data(Qt.UserRole)["available"]:
            self.bookSelected.emit(item.data(Qt.UserRole)["id"])
            self.accept()

    def details(self):
        if self.members.currentItem():
            self.detailsRequested.emit(self.members.currentItem().data(Qt.UserRole)["id"])
            self.reload()


def duration(seconds):
    if 0 < seconds < 60:
        return "<1 min"
    minutes = int(seconds / 60)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


class StatisticsDialog(QDialog):
    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("Lecturas y objetivos · Arandu")
        self.resize(700, 720)
        layout = QVBoxLayout(self)
        self.year = QSpinBox()
        self.year.setRange(1900, 9998)
        self.year.setValue(datetime.now().year)
        self.year.setAccessibleName("Año de las estadísticas")
        row = QHBoxLayout()
        row.addWidget(label("Tu año de lectura"), 1)
        row.addWidget(self.year)
        layout.addLayout(row)
        self.summary = label("")
        self.summary.setObjectName("title")
        layout.addWidget(self.summary)
        self.months = QTreeWidget()
        self.months.setHeaderLabels(["Mes", "Libros", "Tiempo", "Sesiones"])
        self.months.setRootIsDecorated(False)
        layout.addWidget(self.months, 2)
        form = QFormLayout()
        self.books_goal = QSpinBox()
        self.books_goal.setRange(0, 10000)
        self.minutes_goal = QSpinBox()
        self.minutes_goal.setRange(0, 1440)
        self.minutes_goal.setSuffix(" min")
        form.addRow("Meta anual de libros", self.books_goal)
        form.addRow("Meta diaria de lectura", self.minutes_goal)
        layout.addLayout(form)
        self.book_progress = QProgressBar()
        self.minute_progress = QProgressBar()
        self.book_progress.setObjectName("goalProgress")
        self.minute_progress.setObjectName("goalProgress")
        self.book_progress.setAccessibleName("Progreso de la meta anual de libros")
        self.minute_progress.setAccessibleName("Progreso de la meta diaria de minutos")
        layout.addWidget(self.book_progress)
        layout.addWidget(self.minute_progress)
        self.recent = QListWidget()
        self.recent.setAccessibleName("Sesiones recientes de lectura")
        layout.addWidget(self.recent, 1)
        self.hint = label("", True)
        layout.addWidget(self.hint)
        save = QPushButton("Guardar objetivos")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        self.year.valueChanged.connect(self.reload)
        self.reload()

    def reload(self):
        stats = self.library.reading_stats(self.year.value())
        self.summary.setText(f"{stats['books']} {'libro' if stats['books'] == 1 else 'libros'} · {duration(stats['seconds'])}")
        self.months.clear()
        for month in stats["months"]:
            self.months.addTopLevelItem(QTreeWidgetItem([MONTHS[month["month"] - 1], str(month["books"]), duration(month["seconds"]), str(month["sessions"])]))
        current = self.months.topLevelItem(datetime.now().month - 1 if self.year.value() == datetime.now().year else 0)
        self.months.setCurrentItem(current)
        self.months.scrollToItem(current)
        self.books_goal.setValue(stats["goals"]["books"])
        self.minutes_goal.setValue(stats["goals"]["daily_minutes"])
        for bar, value, goal, title in (
            (self.book_progress, stats["books"], stats["goals"]["books"], "libros este año"),
            (self.minute_progress, int(stats["today_seconds"] / 60), stats["goals"]["daily_minutes"], "minutos hoy"),
        ):
            bar.setRange(0, max(1, goal))
            bar.setValue(min(value, max(1, goal)))
            bar.setFormat(f"{value} / {goal} {title}" if goal else "Objetivo desactivado")
        self.minute_progress.setVisible(self.year.value() == datetime.now().year)
        self.recent.clear()
        for session in stats["sessions"][:12]:
            self.recent.addItem(f"{datetime.fromtimestamp(session['started_at']):%d/%m %H:%M} · {session['title']} · {duration(session['seconds'])}")
        if not stats["sessions"]:
            item = QListWidgetItem("Todavía no hay sesiones de lectura registradas este año.")
            item.setFlags(Qt.NoItemFlags)
            self.recent.addItem(item)
        self.hint.setText("El tiempo se registra con el lector activo; se pausa al cambiar de ventana o tras 5 minutos sin interacción. Cero desactiva una meta." + (" Las fechas de libros terminados antes de esta función se estiman por su última modificación." if stats["estimated"] else ""))

    def save(self):
        try:
            self.library.save_goals(self.year.value(), self.books_goal.value(), self.minutes_goal.value())
            self.reload()
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "No se pudieron guardar los objetivos", str(exc))
