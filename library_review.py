"""Consulta global de anotaciones y diagnóstico con reparación verificable."""

from pathlib import Path
import unicodedata
import subprocess
import zipfile
import xml.etree.ElementTree as ET

from extraction import pdf_info


def normalized(value):
    return "".join(char for char in unicodedata.normalize("NFD", str(value)).casefold() if not unicodedata.combining(char))


class ReviewStore:
    def annotation_overview(self, query="", kind="all", book_id=None, include_apart=False):
        books = self.listing(include_hidden=include_apart, include_ignored=include_apart)["books"]
        books = {book["id"]: book for book in books if book_id is None or book["id"] == book_id}
        query = normalized(query.strip())
        result = []
        with self.connect() as db:
            for row in db.execute("SELECT * FROM annotations ORDER BY created_at DESC"):
                book = books.get(row["book_id"])
                if book and (kind == "all" or kind == row["kind"]):
                    item = {**dict(row), "title": book["title"], "author": book["author"], "format": book["format"]}
                    if not query or query in normalized(" ".join((book["title"], book["author"], item["quote"], item["note"]))):
                        result.append(item)
        if kind in {"all", "general"}:
            for book in books.values():
                if book["notes"] and (not query or query in normalized(" ".join((book["title"], book["author"], book["notes"])))):
                    result.append({"id": None, "book_id": book["id"], "kind": "general", "position": None, "quote": "", "note": book["notes"], "title": book["title"], "author": book["author"], "format": book["format"], "created_at": book["updated_at"]})
        return sorted(result, key=lambda item: item["created_at"], reverse=True)

    def diagnose(self, cancel=None, progress=None):
        from library import epub_chapters
        books = self.listing(include_hidden=True)["books"]
        issues = []
        for index, book in enumerate(books):
            if cancel and cancel.is_set():
                break
            if progress:
                progress(index + 1, len(books), book["title"])
            def add(kind, message):
                issues.append({"book_id": book["id"], "title": book["title"], "format": book["format"], "path": book["path"], "kind": kind, "message": message})
            if not book["available"]:
                add("missing", "No se encontró el archivo. Podés buscar su nueva ubicación por lotes.")
                continue
            try:
                if book["format"] == "PDF":
                    info = pdf_info(book["path"])
                    if "copy:no" in info.get("Encrypted", "").lower():
                        add("read_error", "El PDF restringe la extracción de texto.")
                else:
                    epub_chapters(book["path"])
                stat = Path(book["path"]).stat()
                if stat.st_mtime != book["mtime"] or stat.st_size != book["size"] or book["indexed_mtime"] != book["mtime"] or not book["file_hash"]:
                    add("stale_index", "El archivo o su índice cambió. Reindexá la ficha.")
                if book["index_error"]:
                    add("index_error", book["index_error"])
                else:
                    with self.connect() as db:
                        text = db.execute("SELECT 1 FROM book_text WHERE book_id=? AND length(trim(content))>0 LIMIT 1", (book["id"],)).fetchone()
                    if text is None:
                        add("empty_text", "No hay texto para buscar. Un PDF escaneado puede reconocerse con OCR." if book["format"] == "PDF" else "No hay texto indexado. Probá reindexar el EPUB.")
            except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
                add("read_error", str(exc))
        return issues

    def repair_missing(self, book_ids, folder, cancel=None, progress=None):
        ids = list(dict.fromkeys(book_ids))
        targets = [self.get(book_id) for book_id in ids]
        targets = [book for book in targets if not book["available"] and not book["ignored"] and not book["deleted_at"]]
        folder = Path(folder).expanduser().resolve()
        if not folder.is_dir():
            raise ValueError("Elegí una carpeta disponible para buscar los archivos.")
        wanted = {book["file_hash"] for book in targets if book["file_hash"]}
        used = {book["path"] for book in self.listing(include_deleted=True, include_hidden=True, include_ignored=True)["books"] if book["available"]}
        candidates, errors = {}, []
        for directory, _, names, _ in self.source_walk(folder):
            for name in names:
                if cancel and cancel.is_set():
                    return {"ok": 0, "errors": errors, "cancelled": True}
                path = directory / name
                if path.suffix.lower() not in {".pdf", ".epub"} or str(path.resolve()) in used:
                    continue
                if progress:
                    progress(0, 0, "Buscando: " + name)
                try:
                    digest = self.digest(path)
                    if digest in wanted:
                        candidates.setdefault((digest, path.suffix[1:].upper()), []).append(path)
                except OSError as exc:
                    errors.append(str(exc))
        done = 0
        for index, book in enumerate(targets):
            if cancel and cancel.is_set():
                return {"ok": done, "errors": errors, "cancelled": True}
            if progress:
                progress(index + 1, len(targets), book["title"])
            matches = candidates.get((book["file_hash"], book["format"]), [])
            if not matches:
                errors.append(book["title"] + ": no se encontró una copia con el mismo contenido.")
                continue
            try:
                self.relink(book["id"], matches.pop(0))
                done += 1
            except (OSError, ValueError) as exc:
                errors.append(book["title"] + ": " + str(exc))
        return {"ok": done, "errors": errors, "cancelled": False}

    def repair_indexes(self, book_ids, cancel=None, progress=None):
        ids = list(dict.fromkeys(book_ids))
        result = {"ok": 0, "errors": [], "cancelled": False}
        for index, book_id in enumerate(ids):
            if cancel and cancel.is_set():
                result["cancelled"] = True
                break
            book = self.get(book_id)
            if progress:
                progress(index + 1, len(ids), book["title"])
            try:
                if not book["available"] or book["ignored"] or book["deleted_at"]:
                    raise ValueError("El archivo no está disponible como fuente.")
                self.ingest(book["path"], force=True)
                if self.get(book_id)["index_error"]:
                    raise ValueError(self.get(book_id)["index_error"])
                result["ok"] += 1
            except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
                result["errors"].append(book["title"] + ": " + str(exc))
        return result
