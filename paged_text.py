"""Páginas EPUB nativas con selección y anotaciones sobre QTextDocument."""

import math
from PyQt5.QtCore import QPoint, QPointF, QRectF, QSizeF, Qt
from PyQt5.QtGui import QAbstractTextDocumentLayout, QColor, QPainter, QPalette, QTextCursor, QTextDocument


class PaginationMixin:
    def init_pagination(self):
        self.page_mode = "continuous"
        self.page_index = 0
        self.page_width = self.page_height = 1.0
        self._laying_out = False
        self._drag_anchor = None

    @property
    def spread_size(self):
        return 2 if self.page_mode == "double" else 1

    @property
    def page_count(self):
        return max(1, self.document().pageCount()) if self.page_mode != "continuous" else 1

    def anchor_state(self):
        if self.page_mode == "continuous":
            cursor = self.cursorForPosition(QPoint(round(self.document().documentMargin()) + 2, 0))
            return {"anchor": cursor.position(), "anchor_offset": self.cursorRect(cursor).top(), "scroll_y": self.verticalScrollBar().value()}
        point = QPointF(self.document().documentMargin() + 2, self.page_index * self.page_height + self.document().documentMargin() + 2)
        position = self.document().documentLayout().hitTest(point, Qt.FuzzyHit)
        return {"anchor": max(0, position), "anchor_offset": 0, "page": self.page_index}

    def document_y(self, position):
        cursor = QTextCursor(self.document())
        cursor.setPosition(max(0, min(int(position), self.document().characterCount() - 1)))
        block = cursor.block()
        line = block.layout().lineForTextPosition(cursor.positionInBlock())
        return self.document().documentLayout().blockBoundingRect(block).top() + (line.y() if line.isValid() else 0)

    def restore_anchor(self, state):
        position = max(0, min(int(state.get("anchor", 0)), self.document().characterCount() - 1))
        if self.page_mode == "continuous":
            cursor = QTextCursor(self.document())
            cursor.setPosition(position)
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + self.cursorRect(cursor).top() - int(state.get("anchor_offset", 0)))
        else:
            self.set_page(int(self.document_y(position) / self.page_height))

    def set_page_mode(self, mode):
        if mode not in {"continuous", "single", "double"}:
            return
        state = self.anchor_state()
        self.page_mode = mode
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if mode == "continuous" else Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.layout_pages()
        self.restore_anchor(state)
        self.viewport().update()

    def layout_pages(self):
        if self._laying_out:
            return
        self._laying_out = True
        try:
            if self.page_mode == "continuous":
                self.document().setPageSize(QSizeF(max(100, self.viewport().width()), -1))
            else:
                self.page_width = max(100.0, (self.viewport().width() - 24 - (16 if self.spread_size == 2 else 0)) / self.spread_size)
                self.page_height = max(120.0, self.viewport().height() - 16)
                self.document().setPageSize(QSizeF(self.page_width, self.page_height))
                self.page_index = min(self.page_index, max(0, self.page_count - 1))
            self.viewport().update()
        finally:
            self._laying_out = False
        self.pageChanged.emit(self.page_index, self.page_count)

    def set_page(self, index):
        count = self.spread_size
        self.page_index = max(0, min(int(index), self.page_count - 1))
        self.page_index -= self.page_index % count
        self.viewport().update()
        self.pageChanged.emit(self.page_index, self.page_count)

    def page_rect(self, column):
        return QRectF(12 + column * (self.page_width + 16), 8, self.page_width, self.page_height)

    def point_in_document(self, point):
        for column in range(self.spread_size):
            rect = self.page_rect(column)
            page = self.page_index + column
            if page < self.page_count and rect.contains(QPointF(point)):
                return QPointF(point.x() - rect.x(), point.y() - rect.y() + page * self.page_height)
        return None

    def paintEvent(self, event):
        if self.page_mode == "continuous":
            return super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor(self.theme.inset))
        selections = []
        for item in self.extraSelections():
            selection = QAbstractTextDocumentLayout.Selection()
            selection.cursor, selection.format = item.cursor, item.format
            selections.append(selection)
        if self.textCursor().hasSelection():
            selection = QAbstractTextDocumentLayout.Selection()
            selection.cursor = self.textCursor()
            selection.format.setBackground(QColor(self.theme.selected))
            selection.format.setForeground(QColor(self.theme.text))
            selections.append(selection)
        for column in range(self.spread_size):
            page = self.page_index + column
            if page >= self.page_count:
                break
            rect = self.page_rect(column)
            painter.save()
            painter.fillRect(rect, QColor(self.theme.surface))
            painter.setClipRect(rect)
            painter.translate(rect.x(), rect.y() - page * self.page_height)
            context = QAbstractTextDocumentLayout.PaintContext()
            context.clip = QRectF(0, page * self.page_height, self.page_width, self.page_height)
            context.palette = self.palette()
            context.palette.setColor(QPalette.Text, QColor(self.theme.text))
            context.selections = selections
            self.document().documentLayout().draw(painter, context)
            painter.restore()
        painter.end()

    def mousePressEvent(self, event):
        if self.page_mode == "continuous":
            return super().mousePressEvent(event)
        point = self.point_in_document(event.pos())
        if point is not None and event.button() == Qt.LeftButton:
            self.setFocus()
            self._drag_anchor = self.document().documentLayout().hitTest(point, Qt.FuzzyHit)
            cursor = QTextCursor(self.document())
            cursor.setPosition(max(0, self._drag_anchor))
            self.setTextCursor(cursor)
            self.viewport().update()

    def mouseMoveEvent(self, event):
        if self.page_mode == "continuous":
            return super().mouseMoveEvent(event)
        point = self.point_in_document(event.pos())
        if point is not None and event.buttons() & Qt.LeftButton and self._drag_anchor is not None:
            cursor = QTextCursor(self.document())
            cursor.setPosition(max(0, self._drag_anchor))
            cursor.setPosition(max(0, self.document().documentLayout().hitTest(point, Qt.FuzzyHit)), QTextCursor.KeepAnchor)
            self.setTextCursor(cursor)
            self.viewport().update()

    def mouseReleaseEvent(self, event):
        if self.page_mode == "continuous":
            return super().mouseReleaseEvent(event)
        point = self.point_in_document(event.pos())
        if point is not None and not self.textCursor().hasSelection():
            href = self.document().documentLayout().anchorAt(point)
            if href:
                from PyQt5.QtCore import QUrl
                self.anchorClicked.emit(self.document().baseUrl().resolved(QUrl(href)))
        self._drag_anchor = None

    def mouseDoubleClickEvent(self, event):
        if self.page_mode == "continuous":
            return super().mouseDoubleClickEvent(event)
        self.mousePressEvent(event)
        cursor = self.textCursor()
        cursor.select(QTextCursor.WordUnderCursor)
        self.setTextCursor(cursor)
        self.viewport().update()

    def wheelEvent(self, event):
        if self.page_mode == "continuous":
            return super().wheelEvent(event)
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            self.pageTurnRequested.emit(-1 if delta > 0 else 1)
        event.accept()

    def keyPressEvent(self, event):
        if self.page_mode != "continuous" and event.key() in {Qt.Key_PageDown, Qt.Key_PageUp, Qt.Key_Space} and not event.modifiers() & Qt.ControlModifier:
            self.pageTurnRequested.emit(-1 if event.key() == Qt.Key_PageUp or event.modifiers() & Qt.ShiftModifier else 1)
            return
        super().keyPressEvent(event)
        self.viewport().update()

    def find(self, expression, options=QTextDocument.FindFlags()):
        found = super().find(expression, options)
        if found and self.page_mode != "continuous":
            self.set_page(math.floor(self.document_y(self.textCursor().selectionStart()) / self.page_height))
        return found

    def scrollToAnchor(self, name):
        if self.page_mode == "continuous":
            return super().scrollToAnchor(name)
        block = self.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and name in fragment.charFormat().anchorNames():
                    self.set_page(int(self.document_y(fragment.position()) / self.page_height))
                    return
                iterator += 1
            block = block.next()

    def resizeEvent(self, event):
        state = self.anchor_state() if hasattr(self, "page_mode") else None
        super().resizeEvent(event)
        if state is not None:
            self.layout_pages()
            self.restore_anchor(state)
