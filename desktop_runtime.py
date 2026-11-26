"""Inicio desde el escritorio, apertura de libros e instancia única por catálogo."""

import hashlib
import json
import os
from pathlib import Path
import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtNetwork import QLocalServer, QLocalSocket


def desktop_data_dir(source_root, frozen=False):
    configured = os.environ.get("ARANDU_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    if frozen:
        return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "arandu"
    return Path(source_root) / ".arandu"


class DesktopInstance(QObject):
    requested = pyqtSignal(list)

    def __init__(self, data_dir, parent=None):
        super().__init__(parent)
        digest = hashlib.sha256(str(Path(data_dir).resolve()).encode()).hexdigest()[:24]
        user_id = os.getuid() if hasattr(os, "getuid") else 0
        self.name = f"arandu-{user_id}-{digest}"
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self.receive)
        self.connections = set()

    def forward(self, paths):
        socket = QLocalSocket(self)
        socket.connectToServer(self.name)
        if not socket.waitForConnected(400):
            socket.deleteLater()
            return False
        socket.write((json.dumps([str(path) for path in paths], ensure_ascii=False) + "\n").encode())
        socket.flush()
        socket.waitForBytesWritten(1500)
        socket.disconnectFromServer()
        socket.deleteLater()
        return True

    def listen(self):
        if not self.server.listen(self.name):
            QLocalServer.removeServer(self.name)
            if not self.server.listen(self.name):
                raise OSError("No se pudo preparar la apertura de archivos de Arandu.")

    def receive(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.setParent(self)
            socket.payload = bytearray()
            self.connections.add(socket)
            def read(connection=socket):
                connection.payload.extend(bytes(connection.readAll()))
                if len(connection.payload) > 1024 * 1024:
                    connection.abort()
                    return
                if b"\n" not in connection.payload:
                    return
                try:
                    paths = json.loads(bytes(connection.payload).split(b"\n", 1)[0])
                    if isinstance(paths, list) and all(isinstance(path, str) for path in paths):
                        self.requested.emit(paths)
                except (ValueError, UnicodeError):
                    pass
                connection.disconnectFromServer()
            def done(connection=socket):
                self.connections.discard(connection)
                connection.deleteLater()
            socket.readyRead.connect(read)
            socket.disconnected.connect(done)
            read()


class FileOpenController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.pending, self.inflight, self.errors = [], False, []
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.process)
        self.timer.start()

    def request(self, paths):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        self.pending.extend(str(Path(path).expanduser().resolve()) for path in paths)

    def process(self):
        if self.window.busy or self.inflight or not self.pending:
            return
        paths = list(dict.fromkeys(self.pending))
        self.pending.clear()
        self.inflight = True
        def operation():
            ids, errors = [], []
            for value in paths:
                try:
                    path = Path(value)
                    status = self.window.library.ingest(path)
                    if status == "ignored":
                        raise ValueError("El archivo está ignorado. Volvé a incluirlo desde Carpetas e ignorados.")
                    with self.window.library.connect() as db:
                        row = db.execute("SELECT id,deleted_at FROM books WHERE path=?", (str(path),)).fetchone()
                    if row is None:
                        raise ValueError("No se encontró la ficha del libro.")
                    if row["deleted_at"]:
                        raise ValueError("El libro está en la papelera. Restaurá su ficha antes de abrirlo.")
                    ids.append(row["id"])
                except Exception as exc:
                    errors.append(Path(value).name + ": " + str(exc))
            return ids, errors
        def completed(result):
            ids, errors = result
            self.errors.extend(errors)
            self.inflight = False
            self.window.reload()
            for book_id in ids:
                self.window.open_book(book_id)
            if errors:
                self.window.show_error("\n".join(errors))
            elif ids:
                self.window.notice.announce("success", f"{len(ids)} {'libro abierto' if len(ids) == 1 else 'libros abiertos'}. Todo se guarda en tu biblioteca.")
        self.window.run_background(operation, completed, "Abriendo libros…")

    def start_smoke_test(self, app):
        self.smoke_started = time.monotonic()
        self.smoke_timer = QTimer(self)
        self.smoke_timer.setInterval(100)
        self.smoke_timer.timeout.connect(lambda: self.check_smoke(app))
        self.smoke_timer.start()

    def check_smoke(self, app):
        minimum = max(500, min(10000, int(os.environ.get("ARANDU_SMOKE_DELAY_MS", "500")))) / 1000
        if time.monotonic() - self.smoke_started < minimum:
            return
        busy = self.window.busy or self.pending or self.inflight
        readers = list(self.window.readers)
        loaded = all(bool(reader.viewer.browser.content) if hasattr(reader.viewer, "browser") else not reader.viewer.last_image.isNull() for reader in readers)
        if (busy or not loaded) and time.monotonic() - self.smoke_started < 25:
            return
        self.smoke_timer.stop()
        result = {"smoke_test": "ok" if not busy and loaded and not self.errors else "failed", "books": len(self.window.library.listing()["books"]), "readers": len(readers), "errors": self.errors}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        for reader in readers:
            reader.close()
        self.window.close()
        app.exit(0 if result["smoke_test"] == "ok" else 1)
