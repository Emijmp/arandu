"""Búsqueda de texto, colecciones y revisión de duplicados."""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)


class ContentSearchDialog(QDialog):
    resultSelected = pyqtSignal(int, int, str)

    def __init__(self, library, book_id=None, parent=None):
        super().__init__(parent)
        self.library, self.book_id = library, book_id
        self.setWindowTitle("Buscar en los libros · Arandu")
        self.resize(820, 520)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setAccessibleName("Palabra o frase dentro de los libros")
        self.query.setPlaceholderText('Palabras o "una frase exacta"…')
        self.query.returnPressed.connect(self.search)
        button = QPushButton("Buscar")
        button.setObjectName("primary")
        button.clicked.connect(self.search)
        row.addWidget(self.query, 1)
        row.addWidget(button)
        layout.addLayout(row)
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["Libro", "Ubicación", "Coincidencia"])
        self.results.setRootIsDecorated(False)
        self.results.setColumnWidth(0, 180)
        self.results.setColumnWidth(1, 110)
        self.results.itemActivated.connect(self.open_result)
        layout.addWidget(self.results, 1)
        self.status = QLabel("El texto se busca en tu equipo. Los PDF escaneados necesitan una capa de texto.")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        layout.addWidget(self.status)
        open_button = QPushButton("Abrir coincidencia")
        open_button.clicked.connect(lambda: self.open_result(self.results.currentItem()))
        layout.addWidget(open_button)

    def search(self):
        self.results.clear()
        try:
            matches = self.library.search_text(self.query.text(), self.book_id)
            for match in matches:
                location = "Página" if match["format"] == "PDF" else "Sección"
                item = QTreeWidgetItem([match["title"], f"{location} {int(match['position']) + 1}", match["excerpt"]])
                item.setData(0, Qt.UserRole, match)
                self.results.addTopLevelItem(item)
            self.status.setText(f"{len(matches)} {'coincidencia' if len(matches) == 1 else 'coincidencias'}." if matches else "Sin coincidencias. Probá otra palabra o actualizá el índice desde Biblioteca.")
            if matches:
                self.results.setCurrentItem(self.results.topLevelItem(0))
        except Exception as exc:
            self.status.setText(str(exc))

    def open_result(self, item):
        if item is None:
            return
        match = item.data(0, Qt.UserRole)
        self.accept()
        self.resultSelected.emit(int(match["book_id"]), int(match["position"]), self.query.text())


class CollectionsDialog(QDialog):
    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("Colecciones · Arandu")
        self.resize(440, 420)
        layout = QVBoxLayout(self)
        description = QLabel("Organizá tus estantes. Los libros pueden pertenecer a varias colecciones.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.items = QListWidget()
        layout.addWidget(self.items, 1)
        row = QHBoxLayout()
        for label, callback in (("Nueva", self.create), ("Renombrar", self.rename), ("Eliminar", self.remove)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.reload()

    def reload(self):
        self.items.clear()
        self.items.addItems(self.library.collection_names())

    def create(self):
        name, accepted = QInputDialog.getText(self, "Nueva colección", "Nombre")
        if accepted:
            self.save(name)

    def rename(self):
        item = self.items.currentItem()
        if item:
            name, accepted = QInputDialog.getText(self, "Renombrar colección", "Nombre", text=item.text())
            if accepted:
                self.save(name, item.text())

    def save(self, name, previous=None):
        try:
            self.library.save_collection(name, previous)
            self.reload()
        except Exception as exc:
            QMessageBox.warning(self, "Colecciones", str(exc))

    def remove(self):
        item = self.items.currentItem()
        if item and QMessageBox.question(self, "Eliminar colección", "Se quitará esta colección. Sus libros y notas se conservan.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.library.remove_collection(item.text())
            self.reload()


class DuplicatesDialog(QDialog):
    bookSelected = pyqtSignal(int)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Revisar duplicados · Arandu")
        self.resize(800, 460)
        layout = QVBoxLayout(self)
        label = QLabel("Mismo contenido, aunque cambie el nombre. No se borra ningún archivo automáticamente.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Libro", "Archivo"])
        self.tree.setColumnWidth(0, 250)
        groups = library.duplicates()
        for group in groups:
            parent_item = QTreeWidgetItem([f"{len(group)} copias", ""])
            for book in group:
                item = QTreeWidgetItem([book["title"], book["path"]])
                item.setData(0, Qt.UserRole, book["id"])
                parent_item.addChild(item)
            self.tree.addTopLevelItem(parent_item)
            parent_item.setExpanded(True)
        layout.addWidget(self.tree, 1)
        layout.addWidget(QLabel(f"{len(groups)} grupos de duplicados."))
        button = QPushButton("Ver ficha seleccionada")
        button.clicked.connect(self.open_selected)
        layout.addWidget(button)

    def open_selected(self):
        item = self.tree.currentItem()
        if item and item.data(0, Qt.UserRole) is not None:
            self.bookSelected.emit(item.data(0, Qt.UserRole))
