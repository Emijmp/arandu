"""Página PDF nativa con selección, copia y resaltados sobre coordenadas reales."""

import unicodedata

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QLabel

from themes import current_theme


def normalized(text):
    return "".join(char for char in unicodedata.normalize("NFD", text).casefold() if not unicodedata.combining(char))


class SelectablePDFPage(QLabel):
    selectionChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Abriendo PDF…", parent)
        self.words, self.annotations, self.matches = [], [], []
        self.anchor, self.end = -1, -1
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.IBeamCursor)
        self.setAccessibleName("Página PDF: seleccioná texto con el mouse o Ctrl+A; copiá con Ctrl+C")

    def set_words(self, words):
        self.words = words
        self.anchor = self.end = -1
        self.setAccessibleDescription(" ".join(word["text"] for word in words)[:30000] or "Esta página no contiene texto extraíble.")
        self.selectionChanged.emit()
        self.update()

    def selected_payload(self):
        if self.anchor < 0 or self.end < 0:
            return {"quote": "", "rectangles": [], "start_offset": -1, "end_offset": -1}
        start, end = sorted((self.anchor, self.end))
        selected = self.words[start:end + 1]
        return {"quote": " ".join(word["text"] for word in selected), "rectangles": [word["rect"] for word in selected], "start_offset": -1, "end_offset": -1}

    def image_rect(self):
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return QRectF()
        contents = self.contentsRect()
        ratio = pixmap.devicePixelRatioF()
        width, height = pixmap.width() / ratio, pixmap.height() / ratio
        return QRectF(contents.x() + (contents.width() - width) / 2, contents.y() + (contents.height() - height) / 2, width, height)

    def rect_for(self, rectangle):
        image = self.image_rect()
        x, y, width, height = rectangle
        return QRectF(image.x() + x * image.width(), image.y() + y * image.height(), width * image.width(), height * image.height())

    def nearest(self, point):
        if not self.words:
            return -1
        for index, word in enumerate(self.words):
            if self.rect_for(word["rect"]).contains(point):
                return index
        def distance(index):
            rect = self.rect_for(self.words[index]["rect"])
            dx = max(rect.left() - point.x(), 0, point.x() - rect.right())
            dy = max(rect.top() - point.y(), 0, point.y() - rect.bottom())
            return dx * dx + dy * dy
        return min(range(len(self.words)), key=distance)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.image_rect().contains(event.pos()):
            self.setFocus()
            self.anchor = self.end = self.nearest(event.pos())
            self.selectionChanged.emit()
            self.update()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self.anchor >= 0:
            self.end = self.nearest(event.pos())
            self.selectionChanged.emit()
            self.update()
        else:
            super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_A:
            self.anchor, self.end = (0, len(self.words) - 1) if self.words else (-1, -1)
        elif event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_C:
            QApplication.clipboard().setText(self.selected_payload()["quote"])
        elif event.modifiers() & Qt.ShiftModifier and event.key() in {Qt.Key_Left, Qt.Key_Right} and self.words:
            if self.anchor < 0:
                self.anchor = self.end = 0
            else:
                self.end = min(len(self.words) - 1, max(0, self.end + (1 if event.key() == Qt.Key_Right else -1)))
        else:
            super().keyPressEvent(event)
            return
        self.selectionChanged.emit()
        self.update()
        event.accept()

    def find_text(self, query):
        terms = normalized(query).split()
        self.matches = [word["rect"] for word in self.words if any(term.strip('"') in normalized(word["text"]) for term in terms)]
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.image_rect().isEmpty():
            return
        painter = QPainter(self)
        painter.setPen(Qt.NoPen)
        marked = [rect for item in self.annotations if item["kind"] == "highlight" for rect in item["rectangles"]]
        for rectangles, color in ((marked, QColor(234, 195, 78, 95)), (self.matches, QColor(112, 174, 231, 85)), (self.selected_payload()["rectangles"], QColor(89, 151, 221, 95))):
            painter.setBrush(color)
            for rect in rectangles:
                painter.drawRect(self.rect_for(rect))
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(current_theme().accent), 1, Qt.DashLine))
            painter.drawRect(self.image_rect().adjusted(-3, -3, 3, 3))
        painter.end()
