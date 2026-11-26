"""Registro discreto de lectura: ventana activa, sin contar la inactividad."""

from datetime import datetime
import time
import sqlite3

from PyQt5.QtCore import QEvent, QObject, QTimer
from PyQt5.QtWidgets import QApplication


class ReadingTracker(QObject):
    def __init__(self, library, book_id, window):
        super().__init__(window)
        self.library, self.book_id, self.window = library, book_id, window
        self.last_tick = self.last_activity = time.monotonic()
        self.pending = 0.0
        self.session_id = None
        self.day = datetime.now().date()
        self.closed = False
        QApplication.instance().installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.Wheel, QEvent.WindowActivate}:
            if watched.isWidgetType() and (watched is self.window or self.window.isAncestorOf(watched)):
                self.last_activity = time.monotonic()
        return False

    def tick(self, now=None, active=None, wall_time=None):
        if self.closed:
            return
        now = time.monotonic() if now is None else now
        wall_time = time.time() if wall_time is None else wall_time
        elapsed = min(5.0, max(0.0, now - self.last_tick))
        today = datetime.fromtimestamp(wall_time).date()
        if today != self.day:
            self.flush(wall_time - elapsed)
            self.day, self.session_id = today, None
        if active is None:
            active = self.window.isActiveWindow() and self.window.isVisible() and not self.window.isMinimized()
        if active and now - self.last_activity < 300:
            self.pending += elapsed
        self.last_tick = now
        if self.pending >= 15:
            self.flush(wall_time)

    def flush(self, wall_time=None):
        if self.pending <= 0:
            return
        try:
            self.session_id = self.library.record_reading(self.book_id, self.pending, self.session_id, wall_time)
            self.pending = 0
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.window.show_error("No se pudo guardar el tiempo de lectura: " + str(exc))

    def close(self):
        if self.closed:
            return
        self.tick()
        self.flush()
        self.closed = True
        self.timer.stop()
        QApplication.instance().removeEventFilter(self)
