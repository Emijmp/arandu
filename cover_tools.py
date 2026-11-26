"""Carga acotada de imágenes y normalización de portadas a PNG."""

from pathlib import Path
from PyQt5.QtCore import QBuffer, QIODevice, Qt
from PyQt5.QtGui import QImageReader


def cover_image(path):
    path = Path(path)
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Elegí una imagen de hasta 25 MB.")
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 40000000:
        raise ValueError("La imagen no es válida o supera los 40 megapíxeles.")
    image = reader.read()
    if image.isNull():
        raise ValueError("No se pudo leer la imagen elegida.")
    image = image.scaled(1000, 1400, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError("No se pudo preparar la portada.")
    return bytes(buffer.data())
