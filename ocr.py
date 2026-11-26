"""Reconocimiento de páginas escaneadas sin modificar el PDF original."""

import csv
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from extraction import pdf_info, pdf_pages, poppler

TESSDATA = Path(__file__).resolve().parent / "assets" / "ocr" / "tessdata"


def languages():
    return {code: title for code, title in (("spa", "Español"), ("eng", "Inglés")) if (TESSDATA / (code + ".traineddata")).is_file()}


def tsv_words(data):
    rows = list(csv.DictReader(io.StringIO(data), delimiter="\t", quoting=csv.QUOTE_NONE))
    page = next((row for row in rows if row["level"] == "1"), None)
    if page is None:
        return [], ""
    width, height = float(page["width"]), float(page["height"])
    if width <= 0 or height <= 0:
        raise ValueError("El OCR devolvió un tamaño de página inválido.")
    lines, words = {}, []
    for row in rows:
        if row["level"] != "5" or not row.get("text", "").strip():
            continue
        key = tuple(row[field] for field in ("page_num", "block_num", "par_num", "line_num"))
        line = lines.setdefault(key, len(lines))
        rect = [float(row["left"]) / width, float(row["top"]) / height, float(row["width"]) / width, float(row["height"]) / height]
        words.append({"text": row["text"], "line": line, "rect": [max(0, min(1, value)) for value in rect]})
    content = []
    for line in range(len(lines)):
        content.append(" ".join(word["text"] for word in words if word["line"] == line))
    return words, "\n".join(content)


def recognize_pdf(library, book_id, language="spa", cancel=None, progress=None):
    executable = shutil.which("tesseract")
    if not executable:
        raise ValueError("Falta Tesseract. Instalá el motor OCR con el gestor de paquetes del sistema.")
    if language not in languages():
        raise ValueError("No está disponible el modelo de ese idioma.")
    book = library.get(book_id)
    if book["format"] != "PDF" or not book["available"] or book["deleted_at"]:
        raise ValueError("Elegí un PDF disponible en tu biblioteca.")
    before = Path(book["path"]).stat()
    native = pdf_pages(book["path"])
    count = int(pdf_info(book["path"]).get("Pages", len(native)))
    done, skipped = 0, 0
    with tempfile.TemporaryDirectory(dir=library.data_dir) as folder:
        for position in range(count):
            if cancel and cancel.is_set():
                return {"done": done, "skipped": skipped, "cancelled": True}
            if progress:
                progress(position, count)
            if (position < len(native) and native[position].strip()) or library.ocr_words(book_id, position):
                skipped += 1
                continue
            image = Path(folder) / "page"
            poppler("pdftoppm", ["-f", position + 1, "-l", position + 1, "-singlefile", "-scale-to", 2800, "-png", book["path"], image], timeout=120)
            result = subprocess.run([executable, str(image) + ".png", "stdout", "--tessdata-dir", str(TESSDATA), "-l", language, "--oem", "1", "--psm", "3", "-c", "tessedit_create_tsv=1"], capture_output=True, timeout=120, env={**os.environ, "OMP_THREAD_LIMIT": "2"})
            if result.returncode:
                raise ValueError("Tesseract no pudo reconocer esta página: " + result.stderr.decode("utf-8", "replace")[-300:])
            words, content = tsv_words(result.stdout.decode("utf-8", "replace"))
            current = Path(book["path"]).stat()
            if (current.st_size, current.st_mtime_ns) != (before.st_size, before.st_mtime_ns) or library.get(book_id)["file_hash"] != book["file_hash"]:
                raise ValueError("El PDF cambió durante el reconocimiento. Actualizá el catálogo antes de repetirlo.")
            with library.connect() as db:
                db.execute("INSERT OR REPLACE INTO ocr_pages VALUES(?,?,?,?,?,?)", (book_id, position, book["file_hash"], content, json.dumps(words, ensure_ascii=False), language))
                db.execute("DELETE FROM book_text WHERE book_id=? AND position=?", (book_id, position))
                db.execute("INSERT INTO book_text VALUES(?,?,?)", (book_id, position, content))
            done += 1
        if progress:
            progress(count, count)
    return {"done": done, "skipped": skipped, "cancelled": False}


class OCRThread(QThread):
    progress = pyqtSignal(int, int)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, library, book_id, language, parent):
        super().__init__(parent)
        self.library, self.book_id, self.language = library, book_id, language
        self.cancel = threading.Event()

    def run(self):
        try:
            self.completed.emit(recognize_pdf(self.library, self.book_id, self.language, self.cancel, self.progress.emit))
        except Exception as exc:
            self.failed.emit(str(exc))


class OCRDialog(QDialog):
    changed = pyqtSignal()

    def __init__(self, library, book, parent=None):
        super().__init__(parent)
        self.library, self.book = library, book
        self.worker = None
        self.setWindowTitle("Reconocer texto · Arandu")
        self.resize(520, 330)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        description = QLabel("El OCR reconoce las páginas sin texto del PDF. Después podrás buscar, copiar y resaltar sus palabras. El original se conserva.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.language = QComboBox()
        for code, title in languages().items():
            self.language.addItem(title, code)
        self.language.setAccessibleName("Idioma del PDF escaneado")
        layout.addWidget(self.language)
        self.status = QLabel("Todo el procesamiento se hace en tu equipo.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        self.start_button = QPushButton("Reconocer texto")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("Detener")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.close_button = QPushButton("Cerrar")
        self.close_button.clicked.connect(self.reject)
        for button in (self.start_button, self.cancel_button, self.close_button):
            row.addWidget(button)
        layout.addLayout(row)
        if not shutil.which("tesseract") or not self.language.count():
            self.start_button.setEnabled(False)
            self.status.setText("Falta Tesseract o sus modelos. Consultá las dependencias OCR en el README de Arandu.")

    def start(self):
        self.worker = OCRThread(self.library, self.book["id"], self.language.currentData(), self)
        self.worker.progress.connect(self.show_progress)
        self.worker.completed.connect(self.completed)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.stopped)
        self.start_button.setEnabled(False)
        self.language.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.close_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status.setText("Preparando las páginas…")
        self.worker.start()

    def show_progress(self, position, total):
        self.progress.setRange(0, total)
        self.progress.setValue(position)
        self.status.setText(f"Página {min(position + 1, total)} de {total} · reconocimiento local")

    def completed(self, result):
        self.status.setText(f"{'Proceso detenido' if result['cancelled'] else 'Listo'}: {result['done']} {'página reconocida' if result['done'] == 1 else 'páginas reconocidas'}; {result['skipped']} {'ya tenía' if result['skipped'] == 1 else 'ya tenían'} texto. Las páginas procesadas quedan guardadas.")
        self.changed.emit()

    def failed(self, message):
        self.status.setText(message)
        self.changed.emit()

    def stopped(self):
        self.progress.setRange(0, max(1, self.progress.maximum()))
        self.start_button.setEnabled(True)
        self.language.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.close_button.setEnabled(True)

    def cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel.set()
            self.status.setText("Se detendrá al terminar la página actual.")
            self.cancel_button.setEnabled(False)

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
