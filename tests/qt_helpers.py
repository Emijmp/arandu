"""Limpieza de widgets Qt al ejecutar pruebas sin app.exec()."""

from PyQt5.QtCore import QCoreApplication, QEvent


def drain_qt_events(app):
    """Eliminar widgets pendientes antes de retirar sus catálogos temporales."""
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
