"""Preferencias de lectura y lista de anotaciones, guardadas localmente."""

import json
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QInputDialog, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QVBoxLayout


DEFAULT_READING = {"font_size": 18, "margin": 40, "line_spacing": 150}
LIMITS = {"font_size": (12, 32), "margin": (10, 120), "line_spacing": (110, 220)}


def reading_settings(data_dir):
    try:
        saved = json.loads((Path(data_dir) / "reader.json").read_text())
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    return {key: min(LIMITS[key][1], max(LIMITS[key][0], saved[key])) if type(saved.get(key)) is int else value for key, value in DEFAULT_READING.items()}


def save_reading_settings(data_dir, values):
    values = {key: int(min(LIMITS[key][1], max(LIMITS[key][0], values[key]))) for key in DEFAULT_READING}
    target = Path(data_dir) / "reader.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(values))
    temporary.replace(target)


class ReadingSettingsDialog(QDialog):
    settingsChanged = pyqtSignal(object)

    def __init__(self, values, data_dir, parent=None):
        super().__init__(parent)
        self.original, self.data_dir = dict(values), data_dir
        self.setWindowTitle("Ajustes de lectura · Arandu")
        self.resize(420, 270)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.fields = {}
        for key, title, suffix in (("font_size", "Tamaño de letra", " pt"), ("margin", "Márgenes", " px"), ("line_spacing", "Interlineado", " %")):
            spin = QSpinBox()
            spin.setRange(*LIMITS[key])
            spin.setSuffix(suffix)
            spin.setValue(values[key])
            spin.setAccessibleName(title)
            spin.valueChanged.connect(lambda: self.settingsChanged.emit(self.values()))
            self.fields[key] = spin
            form.addRow(title, spin)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Guardar")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def values(self):
        return {key: field.value() for key, field in self.fields.items()}

    def save(self):
        try:
            save_reading_settings(self.data_dir, self.values())
            self.accept()
        except OSError as exc:
            QMessageBox.warning(self, "No se pudieron guardar los ajustes", str(exc))

    def done(self, result):
        if result != QDialog.Accepted:
            self.settingsChanged.emit(self.original)
        super().done(result)


class AnnotationsDialog(QDialog):
    positionSelected = pyqtSignal(int)
    changed = pyqtSignal()

    def __init__(self, library, book, parent=None):
        super().__init__(parent)
        self.library, self.book = library, book
        self.setWindowTitle("Marcadores y anotaciones · Arandu")
        self.resize(620, 470)
        layout = QVBoxLayout(self)
        self.items = QListWidget()
        self.items.setWordWrap(True)
        self.items.itemActivated.connect(self.go)
        layout.addWidget(self.items, 1)
        row = QHBoxLayout()
        for title, callback in (("Ir a la página", self.go), ("Editar nota", self.edit), ("Eliminar", self.remove)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.reload()

    def reload(self):
        self.items.clear()
        labels = {"bookmark": "Marcador", "highlight": "Resaltado", "note": "Nota"}
        location = "Página" if self.book["format"] == "PDF" else "Sección"
        for annotation in self.library.annotations(self.book["id"]):
            text = f"{location} {annotation['position'] + 1} · {labels[annotation['kind']]}"
            if annotation["quote"]:
                text += "\n" + annotation["quote"][:220]
            if annotation["note"]:
                text += "\n" + annotation["note"][:300]
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, annotation)
            self.items.addItem(item)

    def go(self, *args):
        item = self.items.currentItem()
        if item:
            self.positionSelected.emit(item.data(Qt.UserRole)["position"])
            self.accept()

    def edit(self):
        item = self.items.currentItem()
        if item:
            annotation = item.data(Qt.UserRole)
            note, accepted = QInputDialog.getMultiLineText(self, "Editar nota", "Nota", annotation["note"])
            if accepted:
                self.library.update_annotation(annotation["id"], note)
                self.reload()
                self.changed.emit()

    def remove(self):
        item = self.items.currentItem()
        if item and QMessageBox.question(self, "Eliminar anotación", "¿Quitar esta anotación del catálogo? El libro no se modifica.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.library.delete_annotation(item.data(Qt.UserRole)["id"])
            self.reload()
            self.changed.emit()
