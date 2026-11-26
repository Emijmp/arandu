"""Duplicar, intercambiar y quitar paletas personales; tamaño de texto de la app."""

from dataclasses import asdict, replace
import json
from pathlib import Path
from uuid import uuid4

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QComboBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout

from themes import theme_from_dict


def unique_theme_name(name, themes):
    names = {theme.name.casefold() for theme in themes.values()}
    base = name.strip()[:40] or "Mi tema"
    result, number = base, 2
    while result.casefold() in names:
        result = f"{base} ({number})"
        number += 1
    return result


def export_theme(theme, target):
    exported = replace(theme, id="custom-" + uuid4().hex)
    target = Path(target)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps({"version": 1, "themes": [asdict(exported)]}, ensure_ascii=False, indent=2))
    temporary.replace(target)


def import_themes(source, existing):
    path = Path(source)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("El archivo de temas supera 1 MB.")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("themes"), list) or not data["themes"]:
        raise ValueError("Este archivo no contiene temas de Arandu compatibles.")
    available = dict(existing)
    added = []
    for entry in data["themes"]:
        theme = theme_from_dict(entry)
        theme = replace(theme, id="custom-" + uuid4().hex, name=unique_theme_name(theme.name, available))
        available[theme.id] = theme
        added.append(theme)
    return added


class ThemeManagerDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Gestionar temas · Arandu")
        self.resize(590, 500)
        layout = QVBoxLayout(self)
        self.items = QListWidget()
        layout.addWidget(self.items, 1)
        row = QHBoxLayout()
        for label, callback in (("Duplicar", self.duplicate), ("Importar…", self.import_file), ("Exportar…", self.export_file)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        self.remove_button = QPushButton("Eliminar")
        self.remove_button.clicked.connect(self.remove)
        row.addWidget(self.remove_button)
        layout.addLayout(row)
        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("Texto de la aplicación"))
        self.scale = QComboBox()
        self.scale.setAccessibleName("Tamaño del texto de la aplicación")
        for value in (90, 100, 110, 125):
            self.scale.addItem(f"{value} %", value)
        self.scale.setCurrentIndex(self.scale.findData(window.text_scale))
        self.scale.currentIndexChanged.connect(lambda: window.set_text_scale(self.scale.currentData()))
        scale_row.addWidget(self.scale)
        layout.addLayout(scale_row)
        self.status = QLabel("Las seis paletas originales siempre se conservan.")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.items.currentItemChanged.connect(self.selection_changed)
        self.reload()

    def reload(self):
        self.items.clear()
        for theme in self.window.available_themes.values():
            item = QListWidgetItem(theme.name + (" · personal" if theme.id.startswith("custom-") else ""))
            item.setData(Qt.UserRole, theme.id)
            self.items.addItem(item)
            if theme.id == self.window.theme.id:
                self.items.setCurrentItem(item)

    def selected(self):
        item = self.items.currentItem()
        return self.window.available_themes.get(item.data(Qt.UserRole)) if item else None

    def selection_changed(self, *args):
        theme = self.selected()
        self.remove_button.setEnabled(bool(theme and theme.id.startswith("custom-")))

    def duplicate(self):
        theme = self.selected()
        if not theme:
            return
        name, accepted = QInputDialog.getText(self, "Duplicar tema", "Nombre", text=unique_theme_name(theme.name + " copia", self.window.available_themes))
        if accepted:
            try:
                self.window.save_custom_theme(replace(theme, id="custom-" + uuid4().hex, name=name))
                self.reload()
            except (OSError, ValueError) as exc:
                self.status.setText(str(exc))

    def export_file(self):
        theme = self.selected()
        if theme:
            path, _ = QFileDialog.getSaveFileName(self, "Exportar tema", "tema-arandu.json", "Tema de Arandu (*.json)")
            if path:
                try:
                    export_theme(theme, path)
                    self.status.setText("Tema exportado.")
                except OSError as exc:
                    self.status.setText(str(exc))

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importar temas", "", "Tema de Arandu (*.json)")
        if path:
            try:
                added = import_themes(path, self.window.available_themes)
                self.window.store_custom_themes({**self.window.custom_themes, **{theme.id: theme for theme in added}}, added[0].id)
                self.reload()
                self.status.setText(f"{len(added)} temas importados.")
            except (OSError, ValueError) as exc:
                self.status.setText(str(exc))

    def remove(self):
        theme = self.selected()
        if theme and theme.id.startswith("custom-") and QMessageBox.question(self, "Eliminar tema", f"¿Eliminar «{theme.name}»? Podés exportarlo antes para conservar una copia.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            updated = {key: value for key, value in self.window.custom_themes.items() if key != theme.id}
            try:
                self.window.store_custom_themes(updated, "papel" if self.window.theme.id == theme.id else self.window.theme.id)
                self.reload()
            except (OSError, ValueError) as exc:
                self.status.setText(str(exc))
