"""The supplied Arandu logo, recolored from each application's palette."""

from functools import lru_cache
from pathlib import Path
import xml.etree.ElementTree as ET

from PyQt5.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt5.QtGui import QIcon, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QSizePolicy, QWidget

from themes import current_theme


ASSETS = Path(__file__).resolve().parent / "assets" / "branding"
ET.register_namespace("", "http://www.w3.org/2000/svg")


@lru_cache(maxsize=24)
def logo_svg(theme, variant="logo"):
    if variant not in {"logo", "mark", "icon", "stacked"}:
        raise ValueError("Variante de logo desconocida.")
    root = ET.fromstring((ASSETS / f"{variant}.svg").read_bytes())
    colors = {"symbol": theme.accent, "wordmark": theme.text, "background": theme.background}
    for element in root.iter():
        if element.get("id") in colors:
            element.set("fill", colors[element.get("id")])
    return ET.tostring(root, encoding="utf-8")


@lru_cache(maxsize=6)
def app_icon(theme):
    renderer = QSvgRenderer(QByteArray(logo_svg(theme, "icon")))
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class BrandMark(QWidget):
    """Compact, accessible vector lockup, without changing the supplied letters."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme = current_theme()
        self.compact = False
        self.renderer = QSvgRenderer(self)
        self.setFixedHeight(52)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName("Arandu")
        self.setAccessibleDescription("Logo de Arandu: una A que forma un libro abierto.")
        self.setToolTip("Arandu")
        self.refresh()

    def sizeHint(self):
        return QSize(36 if self.compact else 148, 52)

    def set_theme(self, theme):
        self.theme = theme
        self.refresh()

    def set_compact(self, compact):
        if self.compact != compact:
            self.compact = compact
            self.refresh()
            self.updateGeometry()

    def refresh(self):
        self.renderer.load(QByteArray(logo_svg(self.theme, "mark" if self.compact else "logo")))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width = min(self.width(), 36 if self.compact else 148)
        height = width if self.compact else width * 34 / 141
        left = (self.width() - width) / 2 if self.compact else 0
        self.renderer.render(painter, QRectF(left, 0, width, height))
        painter.end()
