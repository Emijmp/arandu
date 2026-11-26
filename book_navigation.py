"""Índice EPUB jerárquico y miniaturas PDF generadas según la vista visible."""

from collections import OrderedDict
import posixpath
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

from PyQt5.QtCore import QProcess, QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtWidgets import QLabel, QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from library import epub_chapters, epub_package, read_epub_entry


def epub_outline(path):
    package, manifest = epub_package(path)
    chapters = epub_chapters(path)
    indexes = {chapter["path"]: index for index, chapter in enumerate(chapters)}

    def target(href, base):
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc:
            return None
        entry = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(parsed.path)))
        if entry not in indexes:
            return None
        return {"chapter": indexes[entry], "anchor": unquote(parsed.fragment)}

    with zipfile.ZipFile(path) as archive:
        nav = next((item for item in manifest.values() if "nav" in item.get("properties", "").split()), None)
        if nav:
            try:
                document = ET.fromstring(read_epub_entry(archive, nav["path"]))
                nodes = document.findall(".//{*}nav")
                toc = next((node for node in nodes if "toc" in node.get("{http://www.idpf.org/2007/ops}type", "").split()), nodes[0] if nodes else document)
                def html_nodes(ordered, depth=0):
                    result = []
                    if ordered is None or depth > 40:
                        return result
                    for li in ordered.findall("{*}li"):
                        link = li.find("{*}a")
                        title = link if link is not None else li.find("{*}span")
                        children = html_nodes(li.find("{*}ol"), depth + 1)
                        value = target(link.get("href", ""), nav["path"]) if link is not None else None
                        text = "".join(title.itertext()).strip()[:300] if title is not None else "Sección"
                        if value or children:
                            result.append({"title": text, "target": value, "children": children})
                    return result
                result = html_nodes(toc.find("{*}ol"))
                if result:
                    return result
            except (ET.ParseError, KeyError):
                pass
        ncx = next((item for item in manifest.values() if item.get("media-type") == "application/x-dtbncx+xml"), None)
        if ncx:
            try:
                document = ET.fromstring(read_epub_entry(archive, ncx["path"]))
                def ncx_nodes(parent, depth=0):
                    result = []
                    if parent is None or depth > 40:
                        return result
                    for node in parent.findall("{*}navPoint"):
                        text = node.find("{*}navLabel/{*}text")
                        content = node.find("{*}content")
                        value = target(content.get("src", ""), ncx["path"]) if content is not None else None
                        children = ncx_nodes(node, depth + 1)
                        if value or children:
                            result.append({"title": "".join(text.itertext()).strip()[:300] if text is not None else "Sección", "target": value, "children": children})
                    return result
                result = ncx_nodes(document.find(".//{*}navMap"))
                if result:
                    return result
            except (ET.ParseError, KeyError):
                pass
    return [{"title": chapter["title"], "target": {"chapter": index, "anchor": ""}, "children": []} for index, chapter in enumerate(chapters)]


class EPUBOutlinePane(QTreeWidget):
    positionSelected = pyqtSignal(int, str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setAccessibleName("Índice jerárquico del EPUB")
        self.setWordWrap(True)
        def append(nodes, parent_item=None):
            for node in nodes:
                item = QTreeWidgetItem([node["title"]])
                item.setData(0, Qt.UserRole, node["target"])
                item.setToolTip(0, node["title"])
                parent_item.addChild(item) if parent_item is not None else self.addTopLevelItem(item)
                append(node["children"], item)
        append(epub_outline(path))
        self.expandToDepth(1)
        self.itemActivated.connect(self.activate)
        self.itemClicked.connect(self.activate)

    def activate(self, item, column=0):
        target = item.data(0, Qt.UserRole)
        if target:
            self.setCurrentItem(item)
            self.positionSelected.emit(target["chapter"], target["anchor"])

    def set_current(self, position, total=0):
        current = self.currentItem()
        if current:
            target = current.data(0, Qt.UserRole)
            if target and target["chapter"] == position:
                return
        def visit(item):
            target = item.data(0, Qt.UserRole)
            if target and target["chapter"] == position:
                self.setCurrentItem(item)
                return True
            return any(visit(item.child(index)) for index in range(item.childCount()))
        for index in range(self.topLevelItemCount()):
            if visit(self.topLevelItem(index)):
                break

    def stop(self):
        pass


class PDFThumbnailPane(QWidget):
    positionSelected = pyqtSignal(int)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path, self.current, self.total = str(path), 0, 0
        self.cache, self.failed = OrderedDict(), set()
        self.stopped = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.items = QListWidget()
        self.items.setAccessibleName("Miniaturas PDF. Elegí una página para abrirla.")
        self.items.setIconSize(QSize(110, 150))
        self.items.setSpacing(4)
        self.items.itemClicked.connect(lambda item: self.positionSelected.emit(item.data(Qt.UserRole)))
        self.items.itemActivated.connect(lambda item: self.positionSelected.emit(item.data(Qt.UserRole)))
        layout.addWidget(self.items, 1)
        self.status = QLabel("Las miniaturas se cargan a medida que avanzás.")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        layout.addWidget(self.status)
        self.process = QProcess(self)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.failed_to_start)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.load_visible)
        self.items.verticalScrollBar().valueChanged.connect(lambda: self.timer.start(100))
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.timed_out)

    def set_current(self, position, total):
        if total != self.total:
            self.items.clear()
            self.total = total
            for index in range(total):
                item = QListWidgetItem(f"Página {index + 1}")
                item.setData(Qt.UserRole, index)
                item.setSizeHint(QSize(170, 164))
                self.items.addItem(item)
        self.current = position
        self.items.setCurrentRow(position)
        self.items.scrollToItem(self.items.item(position))
        self.timer.start(0)

    def load_visible(self):
        if self.stopped or not self.isVisible() or self.process.state() != QProcess.NotRunning or not self.total:
            return
        first = self.items.indexAt(QPoint(10, 10)).row()
        first = max(0, first)
        last = min(self.total, first + max(2, self.items.viewport().height() // 164 + 2))
        for index in range(first, last):
            if index in self.cache:
                self.items.item(index).setIcon(QIcon(self.cache[index]))
                self.cache.move_to_end(index)
                continue
            if index in self.failed:
                continue
            self.rendering = index
            self.process.start("pdftoppm", ["-f", str(index + 1), "-l", str(index + 1), "-singlefile", "-scale-to", "150", "-png", self.path])
            self.timeout.start(20000)
            return

    def finished(self, code, status):
        self.timeout.stop()
        if self.stopped:
            return
        pixmap = QPixmap()
        data = bytes(self.process.readAllStandardOutput())
        self.process.readAllStandardError()
        if code == 0 and pixmap.loadFromData(data):
            self.cache[self.rendering] = pixmap
            self.items.item(self.rendering).setIcon(QIcon(pixmap))
            while len(self.cache) > 32:
                old, _ = self.cache.popitem(last=False)
                self.items.item(old).setIcon(QIcon())
        else:
            self.failed.add(self.rendering)
            self.status.setText("Alguna miniatura no se pudo generar. Podés abrir la página por su número.")
        self.timer.start(0)

    def failed_to_start(self, error):
        if error == QProcess.FailedToStart:
            self.timeout.stop()
            self.failed.add(self.rendering)
            self.status.setText("No se pudo ejecutar Poppler para las miniaturas.")

    def timed_out(self):
        self.failed.add(self.rendering)
        self.process.kill()

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start(0)

    def stop(self):
        self.stopped = True
        self.timer.stop()
        self.timeout.stop()
        self.process.blockSignals(True)
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)
