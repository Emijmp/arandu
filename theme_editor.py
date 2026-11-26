"""Editor nativo de paletas, con vista previa reversible y colores validados."""

from uuid import uuid4

from PyQt5.QtCore import QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from themes import EDITABLE_COLORS, contrast_issues, contrast_ratio, current_theme, fix_placeholder_colors, make_custom_theme, normalize_color


class ColorField(QWidget):
    changed = pyqtSignal()

    def __init__(self, label, color, parent=None):
        super().__init__(parent)
        self.label = label
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.swatch = QPushButton()
        self.swatch.setFixedSize(36, 34)
        self.swatch.setAccessibleName(f"Elegir color de {label.lower()}")
        self.swatch.setToolTip(f"Elegir color de {label.lower()}")
        self.swatch.clicked.connect(self.choose_color)
        row.addWidget(self.swatch)
        self.hex_edit = QLineEdit(color)
        self.hex_edit.setMaxLength(7)
        self.hex_edit.setAccessibleName(f"Color de {label.lower()} en hexadecimal")
        self.hex_edit.setPlaceholderText("#RRGGBB")
        self.hex_edit.textChanged.connect(self.text_changed)
        row.addWidget(self.hex_edit, 1)
        self.setFocusProxy(self.hex_edit)
        self.refresh_swatch()

    def text_changed(self):
        self.refresh_swatch()
        self.changed.emit()

    def set_color(self, value):
        self.hex_edit.setText(value)
        self.refresh_swatch()

    def refresh_swatch(self):
        try:
            value = normalize_color(self.hex_edit.text())
        except ValueError:
            value = current_theme().surface
        focus_color = max(("#000000", "#ffffff"), key=lambda color: contrast_ratio(color, value))
        self.swatch.setStyleSheet(
            f"QPushButton {{background: {value}; border: 1px solid {current_theme().border}; padding: 0; min-width: 0; min-height: 0;}}"
            f"QPushButton:focus {{border: 2px solid {focus_color};}}"
        )

    def choose_color(self):
        try:
            initial = normalize_color(self.hex_edit.text())
        except ValueError:
            initial = current_theme().surface
        color = QColorDialog.getColor(QColor(initial), self, f"Color de {self.label.lower()}")
        if color.isValid():
            self.set_color(color.name())


class ThemeEditorDialog(QDialog):
    previewRequested = pyqtSignal(object)

    def __init__(self, original, available_themes, save_callback, parent=None):
        super().__init__(parent)
        self.original = original
        self.available_themes = dict(available_themes)
        self.save_callback = save_callback
        self.saved_theme = None
        self.closed = False
        self.draft = None
        self.theme_id = original.id if original.id.startswith("custom-") else "custom-" + uuid4().hex
        self.setWindowTitle("Personalizar tema · Arandu")
        self.setMinimumSize(450, 440)
        screen = self.screen()
        self.resize(610, min(740, max(440, screen.availableGeometry().height() - 80)) if screen else 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(14)
        title = QLabel("Personalizar tema")
        title.setObjectName("title")
        title.setTextFormat(Qt.PlainText)
        layout.addWidget(title)
        description = QLabel("Partí de una paleta y ajustá sus colores.")
        description.setObjectName("muted")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("themeFields")
        self.scroll.setWidgetResizable(True)
        form_widget = QWidget()
        form_widget.setObjectName("themeForm")
        form = QFormLayout(form_widget)
        form.setContentsMargins(0, 3, 10, 3)
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(48)
        self.name_edit.setAccessibleName("Nombre del tema personalizado")
        existing_names = {theme.name.casefold() for theme in available_themes.values()}
        name = "Mi tema"
        suffix = 2
        while name.casefold() in existing_names:
            name = f"Mi tema {suffix}"
            suffix += 1
        self.name_edit.setText(original.name if original.id.startswith("custom-") else name)
        form.addRow("Nombre", self.name_edit)
        self.base_combo = QComboBox()
        self.base_combo.setAccessibleName("Paleta de partida")
        for theme in available_themes.values():
            self.base_combo.addItem(theme.name, theme.id)
        self.base_combo.setCurrentIndex(self.base_combo.findData(original.id))
        form.addRow("Partir de", self.base_combo)
        self.color_fields = {}
        for key, label in EDITABLE_COLORS:
            field = ColorField(label, getattr(original, key))
            field.changed.connect(self.schedule_preview)
            self.color_fields[key] = field
            form.addRow(label, field)
        self.scroll.setWidget(form_widget)
        layout.addWidget(self.scroll, 1)
        self.preview_check = QCheckBox("Ver cambios en la aplicación")
        self.preview_check.setChecked(True)
        self.preview_check.toggled.connect(self.refresh_preview)
        layout.addWidget(self.preview_check)
        self.validation = QLabel()
        self.validation.setObjectName("themeValidation")
        self.validation.setAccessibleName("Estado de los colores y el contraste")
        self.validation.setTextFormat(Qt.PlainText)
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)
        actions = QHBoxLayout()
        self.reset_button = QPushButton("Restablecer")
        self.reset_button.setToolTip("Recuperar los colores de la paleta de partida")
        self.reset_button.clicked.connect(self.reset_colors)
        actions.addWidget(self.reset_button)
        actions.addStretch()
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.save_button = QPushButton("Guardar tema")
        self.save_button.setObjectName("primary")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self.timer = QTimer(self)
        self.timer.setInterval(140)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh_preview)
        self.name_edit.textChanged.connect(self.schedule_preview)
        self.base_combo.currentIndexChanged.connect(self.reset_colors)
        self.refresh_preview()

    def schedule_preview(self):
        self.timer.start(140)

    def reset_colors(self):
        base = self.available_themes[self.base_combo.currentData()]
        for key, field in self.color_fields.items():
            field.set_color(getattr(base, key))
        self.timer.stop()
        self.refresh_preview()

    def refresh_preview(self):
        if self.closed:
            return
        self.timer.stop()
        if not self.preview_check.isChecked():
            self.previewRequested.emit(self.original)
        self.draft = None
        self.save_button.setEnabled(False)
        try:
            colors = {key: field.hex_edit.text() for key, field in self.color_fields.items()}
            base = self.available_themes[self.base_combo.currentData()]
            theme = make_custom_theme(base, self.theme_id, self.name_edit.text(), colors)
        except ValueError as exc:
            self.validation.setText(str(exc))
            return
        issues = contrast_issues(theme)
        if issues:
            label, ratio = issues[0]
            self.validation.setText(f"{label}: {ratio:.1f}:1. Necesita al menos 4,5:1. La vista previa conserva la última paleta legible.")
            return
        self.draft = theme
        self.save_button.setEnabled(True)
        self.validation.setText("Contraste adecuado. El logo y los lectores siguen la paleta.")
        self.previewRequested.emit(theme if self.preview_check.isChecked() else self.original)
        for field in self.color_fields.values():
            field.refresh_swatch()
        fix_placeholder_colors(self)

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start(0)

    def save(self):
        self.refresh_preview()
        if self.draft is None:
            return
        try:
            self.save_callback(self.draft)
        except (OSError, ValueError) as exc:
            self.validation.setText(f"No se pudo guardar el tema: {exc}")
            return
        self.saved_theme = self.draft
        self.accept()

    def done(self, result):
        self.closed = True
        self.timer.stop()
        if result != QDialog.Accepted:
            self.previewRequested.emit(self.original)
        super().done(result)
