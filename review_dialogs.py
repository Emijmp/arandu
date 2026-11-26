"""Historial, anotaciones globales y diagnóstico en ventanas nativas."""

from datetime import datetime
import json
import sqlite3
import threading

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QLineEdit,
)

KINDS = {"all": "Todas", "general": "Notas generales", "note": "Notas de lectura", "highlight": "Resaltados", "bookmark": "Marcadores"}
ISSUES = {"missing": "Archivo faltante", "read_error": "Error de lectura", "stale_index": "Índice pendiente", "index_error": "Error de indexación", "empty_text": "Sin texto para buscar"}


def text_label(text=""):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    return label


class HistoryDialog(QDialog):
    changed = pyqtSignal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("Historial de cambios · Arandu")
        self.resize(820, 620)
        layout = QVBoxLayout(self)
        layout.addWidget(text_label("Cada edición por lotes y aplicación de reglas se revierte como una sola acción. La posición de lectura se conserva. Deshacer una aplicación no desactiva su regla."))
        splitter = QSplitter(Qt.Vertical)
        self.items = QTreeWidget()
        self.items.setHeaderLabels(["Fecha", "Cambio", "Estado"])
        self.items.setRootIsDecorated(False)
        self.items.setColumnWidth(0, 160)
        self.items.setColumnWidth(1, 350)
        self.items.setAccessibleName("Historial de cambios del catálogo")
        self.items.currentItemChanged.connect(self.describe)
        splitter.addWidget(self.items)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Valores anteriores y nuevos del cambio seleccionado")
        splitter.addWidget(self.details)
        splitter.setSizes([320, 160])
        layout.addWidget(splitter, 1)
        self.status = text_label()
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.undo_button = QPushButton("Deshacer último")
        self.redo_button = QPushButton("Rehacer")
        self.undo_button.clicked.connect(lambda: self.step(False))
        self.redo_button.clicked.connect(lambda: self.step(True))
        refresh = QPushButton("Actualizar")
        refresh.clicked.connect(self.reload)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        for button in (self.undo_button, self.redo_button, refresh, close):
            row.addWidget(button)
        layout.addLayout(row)
        self.reload()

    def reload(self):
        self.items.clear()
        entries = self.library.history_entries()
        states = {"applied": "Aplicado", "undone": "Deshecho", "discarded": "Reemplazado"}
        self.undo_id, self.redo_id = self.library.history_heads()
        for entry in entries:
            item = QTreeWidgetItem([datetime.fromtimestamp(entry["created_at"]).strftime("%d/%m/%Y %H:%M"), entry["title"], states[entry["state"]]])
            item.setData(0, Qt.UserRole, entry)
            self.items.addTopLevelItem(item)
        if self.items.topLevelItemCount():
            self.items.setCurrentItem(self.items.topLevelItem(0))
        else:
            self.details.setPlainText("Todavía no hay cambios registrados. Las próximas ediciones aparecerán aquí.")
        self.undo_button.setEnabled(self.undo_id is not None)
        self.redo_button.setEnabled(self.redo_id is not None)
        self.status.setText(f"{len(entries)} acciones recientes. Deshacer recorre el historial desde la última acción aplicada.")

    def describe(self, item, previous=None):
        if not item:
            return
        names = {"books": "Libro", "tags": "Etiqueta", "categories": "Categoría", "collections": "Colección", "smart_shelves": "Estante inteligente", "organization_rules": "Regla", "works": "Obra", "book_completions": "Lectura terminada", "annotations": "Anotación"}
        fields = {"title": "Título", "author": "Autor", "status": "Estado", "favorite": "Favorito", "hidden": "Oculto", "progress": "Progreso", "tags": "Etiquetas", "categories": "Categorías", "collections": "Colecciones", "notes": "Notas", "series": "Serie", "series_number": "Orden en la serie", "name": "Nombre", "quote": "Cita", "note": "Nota", "position": "Página o sección", "kind": "Tipo", "filters": "Filtros", "actions": "Acciones", "conditions": "Condiciones", "enabled": "Activa", "finished_at": "Fecha"}
        values = {"pending": "Por leer", "reading": "Leyendo", "finished": "Leído", "highlight": "Resaltado", "bookmark": "Marcador", "note": "Nota"}
        def display(field, value):
            if field in {"favorite", "hidden", "enabled"}:
                return "Sí" if value else "No"
            if field == "position":
                return str(value + 1)
            if field == "finished_at":
                return datetime.fromtimestamp(value).strftime("%d/%m/%Y %H:%M")
            if field == "progress":
                return str(value) + " %"
            if field in {"tags", "categories", "collections"}:
                return ", ".join(json.loads(value)) or "Sin nombres"
            if field in {"filters", "actions", "conditions"}:
                data = json.loads(value)
                return "; ".join(fields.get(key, {"query": "Buscar", "view": "Estado", "file_format": "Formato", "tag": "Etiqueta", "category": "Categoría", "collection": "Colección", "folder": "Carpeta", "filename": "Archivo"}.get(key, "Criterio")) + ": " + (", ".join(item) if isinstance(item, list) else values.get(str(item), str(item))) for key, item in data.items() if item and item != "all") or "Sin filtros"
            return values.get(str(value), str(value)) or "Vacío"
        lines = []
        for change in item.data(0, Qt.UserRole)["changes"]:
            title = names[change["table"]]
            if change["table"] == "books":
                try:
                    title += ": " + self.library.get(int(change["key"]))["title"]
                except OSError:
                    title += " (ficha eliminada)"
            lines.append(title)
            old, new = change["before"], change["after"]
            if old is None or new is None:
                lines.append("Se creó este elemento." if old is None else "Se quitó este elemento.")
                for field, value in (new or old).items():
                    if field in fields:
                        lines.append(fields[field] + ": " + display(field, value))
            else:
                for field in old:
                    if field in fields:
                        lines += [fields[field], "Antes: " + display(field, old[field]), "Después: " + display(field, new[field])]
            lines.append("")
        self.details.setPlainText("\n".join(lines))

    def step(self, redo):
        try:
            title = self.library.history_step(redo, self.redo_id if redo else self.undo_id)
            self.reload()
            self.status.setText(("Rehecho: " if redo else "Deshecho: ") + title)
            self.changed.emit()
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "No se pudo cambiar el historial", str(exc))
            self.reload()


class GlobalAnnotationsDialog(QDialog):
    annotationSelected = pyqtSignal(object)
    changed = pyqtSignal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("Todas las anotaciones · Arandu")
        self.resize(930, 680)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Buscar en libros, citas y notas…")
        self.query.setClearButtonEnabled(True)
        self.query.setAccessibleName("Buscar en todas las anotaciones")
        self.kind = QComboBox()
        for key, title in KINDS.items():
            self.kind.addItem(title, key)
        row.addWidget(self.query, 1)
        row.addWidget(self.kind)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.book_filter = QComboBox()
        self.book_filter.setMinimumContentsLength(20)
        self.book_filter.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.book_filter.addItem("Todos los libros", None)
        for book in library.listing(include_hidden=True, include_ignored=True)["books"]:
            self.book_filter.addItem(book["title"] + " · " + book["format"], book["id"])
        self.book_filter.setAccessibleName("Filtrar anotaciones por libro")
        self.include_apart = QCheckBox("Incluir ocultos e ignorados")
        row.addWidget(self.book_filter, 1)
        row.addWidget(self.include_apart)
        layout.addLayout(row)
        splitter = QSplitter(Qt.Vertical)
        self.items = QTreeWidget()
        self.items.setHeaderLabels(["Libro", "Tipo", "Ubicación", "Cita o nota"])
        self.items.setRootIsDecorated(False)
        self.items.setColumnWidth(0, 220)
        self.items.setColumnWidth(1, 130)
        self.items.setColumnWidth(2, 110)
        self.items.setAccessibleName("Notas, citas y resaltados de la biblioteca")
        self.items.currentItemChanged.connect(self.describe)
        self.items.itemActivated.connect(lambda item: self.open())
        splitter.addWidget(self.items)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Cita y nota completas")
        splitter.addWidget(self.details)
        splitter.setSizes([320, 180])
        layout.addWidget(splitter, 1)
        self.status = text_label()
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.open_button = QPushButton("Abrir en el lector")
        self.open_button.setObjectName("primary")
        self.open_button.clicked.connect(self.open)
        self.edit_button = QPushButton("Editar nota")
        self.edit_button.clicked.connect(self.edit)
        self.delete_button = QPushButton("Eliminar")
        self.delete_button.clicked.connect(self.remove)
        for button in (self.open_button, self.edit_button, self.delete_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self.reload)
        self.query.textChanged.connect(lambda: self.timer.start())
        self.kind.currentIndexChanged.connect(self.reload)
        self.book_filter.currentIndexChanged.connect(self.reload)
        self.include_apart.toggled.connect(self.reload)
        self.reload()

    def reload(self):
        self.timer.stop()
        self.items.clear()
        try:
            values = self.library.annotation_overview(self.query.text(), self.kind.currentData(), self.book_filter.currentData(), self.include_apart.isChecked())
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.status.setText("No se pudieron consultar las anotaciones: " + str(exc))
            self.details.clear()
            self.describe(None)
            return
        for value in values:
            location = "Ficha" if value["position"] is None else ("Página " if value["format"] == "PDF" else "Sección ") + str(value["position"] + 1)
            item = QTreeWidgetItem([value["title"], KINDS[value["kind"]], location, (value["quote"] or value["note"]).replace("\n", " ")[:200]])
            item.setData(0, Qt.UserRole, value)
            self.items.addTopLevelItem(item)
        self.status.setText(f"{len(values)} {'anotación' if len(values) == 1 else 'anotaciones'}." if values else "Sin anotaciones para estos filtros. Tus próximas notas y resaltados aparecerán aquí.")
        if values:
            self.items.setCurrentItem(self.items.topLevelItem(0))
        else:
            self.details.clear()
        self.describe(self.items.currentItem())

    def describe(self, item, previous=None):
        for button in (self.open_button, self.edit_button, self.delete_button):
            button.setEnabled(item is not None)
        if item:
            value = item.data(0, Qt.UserRole)
            self.open_button.setEnabled(self.library.get(value["book_id"])["available"])
            self.details.setPlainText(value["title"] + "\n" + value["author"] + "\n\n" + ("Cita:\n" + value["quote"] + "\n\n" if value["quote"] else "") + "Nota:\n" + (value["note"] or "Sin nota añadida."))

    def current(self):
        return self.items.currentItem().data(0, Qt.UserRole) if self.items.currentItem() else None

    def open(self):
        if self.current():
            self.annotationSelected.emit(self.current())
            self.accept()

    def edit(self):
        value = self.current()
        if value:
            note, accepted = QInputDialog.getMultiLineText(self, "Editar nota", value["title"], value["note"])
            if accepted:
                try:
                    self.library.update(value["book_id"], {"notes": note}) if value["kind"] == "general" else self.library.update_annotation(value["id"], note)
                    self.reload()
                    self.changed.emit()
                except (OSError, ValueError, sqlite3.Error) as exc:
                    QMessageBox.warning(self, "No se pudo guardar la nota", str(exc))

    def remove(self):
        value = self.current()
        if value and QMessageBox.question(self, "Eliminar anotación", "¿Eliminar esta anotación? El cambio queda en el historial para poder deshacerlo.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            try:
                self.library.update(value["book_id"], {"notes": ""}) if value["kind"] == "general" else self.library.delete_annotation(value["id"])
                self.reload()
                self.changed.emit()
            except (OSError, ValueError, sqlite3.Error) as exc:
                QMessageBox.warning(self, "No se pudo eliminar la anotación", str(exc))

    def done(self, result):
        self.timer.stop()
        super().done(result)


class ReviewWorker(QThread):
    result = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)

    def __init__(self, function, parent):
        super().__init__(parent)
        self.function, self.cancel = function, threading.Event()

    def run(self):
        try:
            self.result.emit(self.function(self.cancel, self.progress.emit))
        except Exception as exc:
            self.failed.emit(str(exc))


class DiagnosticsDialog(QDialog):
    changed = pyqtSignal()
    detailsRequested = pyqtSignal(int)
    ocrRequested = pyqtSignal(int)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library, self.worker = library, None
        self.setWindowTitle("Diagnóstico y reparación · Arandu")
        self.resize(900, 610)
        layout = QVBoxLayout(self)
        layout.addWidget(text_label("Revisá archivos faltantes, problemas de lectura e índices pendientes. La búsqueda de archivos movidos solo enlaza copias con el mismo contenido."))
        self.items = QTreeWidget()
        self.items.setHeaderLabels(["Libro", "Problema", "Detalle"])
        self.items.setRootIsDecorated(False)
        self.items.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.items.setColumnWidth(0, 210)
        self.items.setColumnWidth(1, 170)
        self.items.setAccessibleName("Problemas de la biblioteca. Ctrl o Shift para seleccionar varios.")
        layout.addWidget(self.items, 1)
        self.status = text_label("Pulsá Revisar para comprobar tu biblioteca. Las rutas ignoradas quedan fuera del diagnóstico.")
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        self.scan_button = QPushButton("Revisar")
        self.scan_button.clicked.connect(self.scan)
        self.locate_button = QPushButton("Buscar archivos movidos…")
        self.locate_button.clicked.connect(self.locate)
        self.index_button = QPushButton("Reindexar selección")
        self.index_button.clicked.connect(self.reindex)
        for button in (self.scan_button, self.locate_button, self.index_button):
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.details_button = QPushButton("Abrir ficha")
        self.details_button.clicked.connect(self.details)
        self.ocr_button = QPushButton("Reconocer texto (OCR)…")
        self.ocr_button.clicked.connect(self.ocr)
        self.cancel_button = QPushButton("Detener")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.reject)
        for button in (self.details_button, self.ocr_button, self.cancel_button, close):
            row.addWidget(button)
        layout.addLayout(row)
        self.items.itemSelectionChanged.connect(self.update_actions)
        self.update_actions()

    def selected_ids(self):
        return list(dict.fromkeys(item.data(0, Qt.UserRole)["book_id"] for item in self.items.selectedItems()))

    def update_actions(self):
        busy = bool(self.worker and self.worker.isRunning())
        ids = self.selected_ids()
        self.scan_button.setEnabled(not busy)
        self.locate_button.setEnabled(not busy and any(not self.library.get(book_id)["available"] for book_id in ids))
        self.index_button.setEnabled(not busy and bool(ids))
        self.details_button.setEnabled(not busy and len(ids) == 1)
        self.ocr_button.setEnabled(not busy and len(ids) == 1 and self.library.get(ids[0])["format"] == "PDF" and self.library.get(ids[0])["available"])
        self.cancel_button.setEnabled(busy)

    def start(self, function):
        if self.worker and self.worker.isRunning():
            return
        self.worker = ReviewWorker(function, self)
        self.worker.progress.connect(self.show_progress)
        self.worker.result.connect(self.completed)
        self.worker.failed.connect(self.status.setText)
        self.worker.finished.connect(self.stopped)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText("Revisando la biblioteca…")
        self.worker.start()
        self.update_actions()

    def show_progress(self, position, total, title):
        self.progress.setRange(0, total)
        self.progress.setValue(position)
        self.status.setText(title if not total else f"{position} de {total} · {title}")

    def scan(self):
        self.start(lambda cancel, progress: {"issues": self.library.diagnose(cancel, progress), "cancelled": cancel.is_set()})

    def completed(self, result):
        if result["issues"] is not None:
            self.items.clear()
            for value in result["issues"]:
                item = QTreeWidgetItem([value["title"], ISSUES[value["kind"]], value["message"]])
                item.setToolTip(2, value["message"] + "\n" + value["path"])
                item.setData(0, Qt.UserRole, value)
                self.items.addTopLevelItem(item)
        report = result.get("report")
        if report:
            self.status.setText(f"{report['ok']} fichas reparadas · {len(report['errors'])} sin resolver." + (" Proceso detenido; pulsá Revisar para comprobar lo que queda pendiente." if report["cancelled"] else f" {len(result['issues'])} problemas detectados."))
            self.status.setToolTip("\n".join(report["errors"]))
            self.changed.emit()
        else:
            self.status.setText("Revisión detenida. Se muestran los resultados comprobados." if result.get("cancelled") else f"{len(result['issues'])} problemas detectados." if result["issues"] else "Todo en su sitio: archivos legibles e índices al día.")

    def stopped(self):
        self.progress.hide()
        self.update_actions()

    def locate(self):
        ids = self.selected_ids()
        folder = QFileDialog.getExistingDirectory(self, "Buscar copias de los archivos faltantes", "")
        if folder:
            def job(cancel, progress):
                report = self.library.repair_missing(ids, folder, cancel, progress)
                return {"report": report, "issues": None if report["cancelled"] else self.library.diagnose(cancel, progress)}
            self.start(job)

    def reindex(self):
        ids = self.selected_ids()
        def job(cancel, progress):
            report = self.library.repair_indexes(ids, cancel, progress)
            return {"report": report, "issues": None if report["cancelled"] else self.library.diagnose(cancel, progress)}
        self.start(job)

    def details(self):
        ids = self.selected_ids()
        if len(ids) == 1:
            self.detailsRequested.emit(ids[0])

    def ocr(self):
        ids = self.selected_ids()
        if len(ids) == 1:
            self.ocrRequested.emit(ids[0])

    def cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel.set()
            self.status.setText("Se detendrá al terminar el archivo actual.")

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.cancel()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.cancel()
            event.ignore()
        else:
            super().closeEvent(event)
