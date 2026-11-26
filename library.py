"""Catálogo local compartido por la aplicación nativa de Arandu."""

from contextlib import contextmanager
import hashlib
import json
import re
import subprocess
from pathlib import Path
import posixpath
import sqlite3
import threading
import time
import unicodedata
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

from extraction import html_text, pdf_cover, pdf_info, pdf_pages
from organizer import OrganizerStore
from sources import SourceStore
from history import BOOK_FIELDS, HistoryStore
from library_review import ReviewStore

ROOT = Path(__file__).resolve().parent
FORMATS = {".pdf", ".epub"}
STATUSES = {"pending", "reading", "finished"}
MAX_EPUB_ENTRY = 24 * 1024 * 1024
TAXONOMIES = {"tags": "etiquetas", "categories": "categorías"}


def term_name(value):
    return " ".join(unicodedata.normalize("NFC", value).split())[:60]


def term_key(value):
    return term_name(value).casefold()


def taxonomy_kind(kind):
    if kind not in TAXONOMIES:
        raise ValueError("Tipo de clasificación inválido.")
    return kind


def read_epub_entry(archive, entry):
    if archive.getinfo(entry).file_size > MAX_EPUB_ENTRY:
        raise ValueError("Este recurso del EPUB es demasiado grande.")
    return archive.read(entry)


def epub_package(path):
    with zipfile.ZipFile(path) as archive:
        container = ET.fromstring(read_epub_entry(archive, "META-INF/container.xml"))
        rootfile = container.find(".//{*}rootfile")
        if rootfile is None:
            raise ValueError("El EPUB no tiene un índice de contenido válido.")
        package_path = rootfile.attrib["full-path"]
        package = ET.fromstring(read_epub_entry(archive, package_path))
        base = posixpath.dirname(package_path)
        manifest = {}
        for item in package.findall(".//{*}manifest/{*}item"):
            href = unquote(urlsplit(item.attrib.get("href", "")).path)
            manifest[item.attrib["id"]] = {
                **item.attrib,
                "path": posixpath.normpath(posixpath.join(base, href)),
            }
        return package, manifest


def metadata(path):
    title, author, cover = path.stem.replace("_", " "), "Autor desconocido", ""
    if path.suffix.lower() == ".epub":
        package, manifest = epub_package(path)
        title_el = package.find(".//{*}metadata/{*}title")
        author_el = package.find(".//{*}metadata/{*}creator")
        if title_el is not None and title_el.text:
            title = title_el.text.strip()
        if author_el is not None and author_el.text:
            author = author_el.text.strip()
        for item in manifest.values():
            if "cover-image" in item.get("properties", "").split():
                cover = item["path"]
                break
        if not cover:
            for item in package.findall(".//{*}metadata/{*}meta"):
                if item.attrib.get("name") == "cover":
                    cover = manifest.get(item.attrib.get("content"), {}).get("path", "")
    elif path.suffix.lower() == ".pdf":
        try:
            info = pdf_info(path)
            title = info.get("Title") or title
            author = info.get("Author") or author
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return title[:500], author[:500], cover


class Library(OrganizerStore, SourceStore, HistoryStore, ReviewStore):
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.uploads = self.data_dir / "imports"
        self.uploads.mkdir(exist_ok=True)
        self.database = self.data_dir / "library.sqlite3"
        self.import_lock = threading.Lock()
        self.covers = self.data_dir / "covers"
        self.covers.mkdir(exist_ok=True)
        self.initialize()

    def initialize(self):
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS books (
                    id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL, author TEXT NOT NULL, format TEXT NOT NULL,
                    size INTEGER NOT NULL, cover TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending', favorite INTEGER NOT NULL DEFAULT 0,
                    progress INTEGER NOT NULL DEFAULT 0, chapter INTEGER NOT NULL DEFAULT 0,
                    tags TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '',
                    added_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS folders (path TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS annotations (
                    id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL,
                    position INTEGER NOT NULL, kind TEXT NOT NULL,
                    quote TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
                    rectangles TEXT NOT NULL DEFAULT '[]', start_offset INTEGER NOT NULL DEFAULT -1,
                    end_offset INTEGER NOT NULL DEFAULT -1, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS annotation_book ON annotations(book_id, position);
                CREATE TABLE IF NOT EXISTS collections (name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS tags (name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS categories (name TEXT PRIMARY KEY);
                CREATE VIRTUAL TABLE IF NOT EXISTS book_text USING fts5(
                    book_id UNINDEXED, position UNINDEXED, content,
                    tokenize='unicode61 remove_diacritics 2'
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(books)")}
            for name, declaration in {
                "file_hash": "TEXT NOT NULL DEFAULT ''", "mtime": "REAL NOT NULL DEFAULT 0",
                "cover_path": "TEXT NOT NULL DEFAULT ''", "indexed_mtime": "REAL NOT NULL DEFAULT -1",
                "index_error": "TEXT NOT NULL DEFAULT ''", "series": "TEXT NOT NULL DEFAULT ''",
                "series_number": "REAL NOT NULL DEFAULT 0", "collections": "TEXT NOT NULL DEFAULT '[]'",
                "categories": "TEXT NOT NULL DEFAULT '[]'",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE books ADD COLUMN {name} {declaration}")
            db.execute("CREATE INDEX IF NOT EXISTS book_digest ON books(file_hash)")
            self.initialize_organization(db)
            self.initialize_sources(db)
            self.initialize_history(db)
            # Incorporate classifications from older catalogs without altering their books.
            for kind in TAXONOMIES:
                names = {term_key(row[0]): row[0] for row in db.execute(f"SELECT name FROM {kind}")}
                for row in db.execute(f"SELECT {kind} FROM books"):
                    for value in json.loads(row[0]):
                        name = term_name(value)
                        if name and term_key(name) not in names:
                            db.execute(f"INSERT INTO {kind}(name) VALUES(?)", (name,))
                            names[term_key(name)] = name

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def serialize(row):
        book = dict(row)
        book["favorite"] = bool(book["favorite"])
        book["hidden"] = bool(book["hidden"])
        book["tags"] = json.loads(book["tags"])
        book["collections"] = json.loads(book["collections"])
        book["categories"] = json.loads(book["categories"])
        book["available"] = Path(book["path"]).is_file()
        return book

    def listing(self, include_deleted=False, include_hidden=False, include_ignored=False):
        with self.connect() as db:
            books = [self.serialize(row) for row in db.execute("SELECT * FROM books" + ("" if include_deleted else " WHERE deleted_at=0") + " ORDER BY added_at DESC, id DESC")]
            folders = [row[0] for row in db.execute("SELECT path FROM folders ORDER BY path")]
        exclusions = self.exclusions()
        for book in books:
            book["ignored"] = self.is_ignored(book["path"], exclusions)
        books = [book for book in books if (include_hidden or not book["hidden"]) and (include_ignored or not book["ignored"])]
        return {"books": books, "folders": folders}

    def get(self, book_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if row is None:
            raise FileNotFoundError("El libro ya no está en el catálogo.")
        book = self.serialize(row)
        book["ignored"] = self.is_ignored(book["path"])
        return book

    @staticmethod
    def digest(path):
        hasher = hashlib.sha256()
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def add(self, path):
        return self.ingest(path) == "added"

    def ingest(self, path, force=False):
        path = Path(path).expanduser().resolve()
        if self.is_ignored(path):
            return "ignored"
        if path.suffix.lower() not in FORMATS or not path.is_file():
            raise ValueError("Elegí un archivo PDF o EPUB válido.")
        stat = path.stat()
        with self.connect() as db:
            row = db.execute("SELECT * FROM books WHERE path = ?", (str(path),)).fetchone()
        if row and row["deleted_at"]:
            return "existing"
        if row and not force and row["file_hash"] and row["mtime"] == stat.st_mtime and row["size"] == stat.st_size and row["indexed_mtime"] == stat.st_mtime:
            return "existing"
        if path.suffix.lower() == ".pdf":
            with path.open("rb") as source:
                if b"%PDF-" not in source.read(1024):
                    raise ValueError("El archivo no parece ser un PDF válido.")
        digest = self.digest(path)
        status = "existing" if row else "added"
        if row is None:
            with self.connect() as db:
                candidates = db.execute("SELECT * FROM books WHERE file_hash = ? AND deleted_at=0", (digest,)).fetchall()
            row = next((candidate for candidate in candidates if not Path(candidate["path"]).is_file()), None)
            if row:
                status = "relinked"
        title, author, cover = metadata(path)
        now = time.time()
        with self.connect() as db:
            if row:
                book_id = row["id"]
                title = title if row["title"] in {Path(row["path"]).stem, Path(row["path"]).stem.replace("_", " ")} else row["title"]
                author = author if row["author"] in {"", "Autor desconocido"} else row["author"]
                db.execute("UPDATE books SET path=?,title=?,author=?,format=?,size=?,cover=?,file_hash=?,mtime=? WHERE id=?",
                           (str(path), title, author, path.suffix[1:].upper(), stat.st_size, cover, digest, stat.st_mtime, book_id))
            else:
                cursor = db.execute("INSERT INTO books(path,title,author,format,size,cover,file_hash,mtime,added_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                    (str(path), title, author, path.suffix[1:].upper(), stat.st_size, cover, digest, stat.st_mtime, now, now))
                book_id = cursor.lastrowid
        self.enrich(book_id)
        self.apply_rules(book_id)
        return status

    def enrich(self, book_id):
        book = self.get(book_id)
        path = Path(book["path"])
        pages, error, cover_path = [], "", book["cover_path"]
        try:
            if book["format"] == "PDF":
                pages = pdf_pages(path)
                target = self.covers / (book["file_hash"] + ".png")
                cover_path = str(target) if target.is_file() else pdf_cover(path, target)
            else:
                chapters = epub_chapters(path)
                with zipfile.ZipFile(path) as archive:
                    pages = [html_text(read_epub_entry(archive, chapter["path"]).decode("utf-8-sig", "replace")) for chapter in chapters]
                package, _ = epub_package(path)
                series = next((meta.text for meta in package.findall(".//{*}metadata/{*}meta") if meta.get("property") == "belongs-to-collection" and meta.text), "")
                number = next((meta.text for meta in package.findall(".//{*}metadata/{*}meta") if meta.get("property") == "group-position" and meta.text), "0")
                if not series:
                    series = next((meta.get("content") for meta in package.findall(".//{*}metadata/{*}meta") if meta.get("name") == "calibre:series"), "")
                    number = next((meta.get("content") for meta in package.findall(".//{*}metadata/{*}meta") if meta.get("name") == "calibre:series_index"), "0")
                if series and not book["series"]:
                    try:
                        self.update(book_id, {"series": series, "series_number": float(number)}, _record=False)
                    except ValueError:
                        self.update(book_id, {"series": series}, _record=False)
        except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
            error = str(exc)
        with self.connect() as db:
            db.execute("DELETE FROM ocr_pages WHERE book_id=? AND file_hash!=?", (book_id, book["file_hash"]))
            if book["format"] == "PDF":
                for row in db.execute("SELECT position,content FROM ocr_pages WHERE book_id=? AND file_hash=?", (book_id, book["file_hash"])):
                    if row["position"] < len(pages) and not pages[row["position"]].strip():
                        pages[row["position"]] = row["content"]
            db.execute("DELETE FROM book_text WHERE book_id = ?", (book_id,))
            db.executemany("INSERT INTO book_text(book_id,position,content) VALUES(?,?,?)", [(book_id, index, text) for index, text in enumerate(pages)])
            db.execute("UPDATE books SET cover_path=?,indexed_mtime=mtime,index_error=? WHERE id=?", (cover_path, error, book_id))

    def refresh_all(self):
        result = {"added": 0, "skipped": 0, "relinked": 0, "errors": []}
        for book in self.listing(include_hidden=True)["books"]:
            if book["available"]:
                try:
                    self.ingest(book["path"], force=True)
                    result["skipped"] += 1
                except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
                    result["errors"].append({"file": book["path"], "error": str(exc)})
        return result

    def needs_maintenance(self):
        exclusions = self.exclusions()
        with self.connect() as db:
            return any(not self.is_ignored(row[0], exclusions) for row in db.execute("SELECT path FROM books WHERE deleted_at=0 AND (file_hash='' OR indexed_mtime != mtime)"))

    def scan(self, folders, recursive=True):
        result = {"added": 0, "skipped": 0, "relinked": 0, "ignored": 0, "errors": []}
        for value in folders:
            folder = Path(value).expanduser().resolve()
            if self.is_ignored(folder) or folder.is_relative_to(self.data_dir):
                result["ignored"] += 1
                continue
            if not folder.is_dir():
                result["errors"].append({"file": str(folder), "error": "No se encontró la carpeta."})
                continue
            with self.connect() as db:
                db.execute("INSERT OR IGNORE INTO folders VALUES (?)", (str(folder),))
            try:
                def walk_error(exc):
                    result["errors"].append({"file": str(exc.filename or folder), "error": str(exc)})
                for directory, _, names, skipped in self.source_walk(folder, recursive, on_error=walk_error):
                    result["ignored"] += skipped
                    for name in names:
                        path = directory / name
                        if path.suffix.lower() not in FORMATS or not path.is_file():
                            continue
                        try:
                            status = self.ingest(path)
                            result[{"added":"added", "existing":"skipped", "relinked":"relinked", "ignored":"ignored"}[status]] += 1
                        except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
                            result["errors"].append({"file": str(path), "error": str(exc)})
            except OSError as exc:
                result["errors"].append({"file": str(folder), "error": str(exc)})
        return result

    def update(self, book_id, changes, _db=None, _record=True):
        previous = self.get(book_id)
        clean = {}
        for key in ("title", "author", "notes", "series"):
            if key in changes:
                if not isinstance(changes[key], str):
                    raise ValueError("Los campos de texto deben contener texto.")
                clean[key] = changes[key].strip()[:10000 if key == "notes" else 500]
        if "title" in clean and not clean["title"]:
            raise ValueError("El título no puede estar vacío.")
        if "status" in changes:
            if changes["status"] not in STATUSES:
                raise ValueError("Estado de lectura inválido.")
            clean["status"] = changes["status"]
            if clean["status"] == "finished":
                clean["progress"] = 100
        if "favorite" in changes:
            clean["favorite"] = int(bool(changes["favorite"]))
        if "hidden" in changes:
            if type(changes["hidden"]) is not bool:
                raise ValueError("El estado de visibilidad es inválido.")
            clean["hidden"] = int(changes["hidden"])
        for key, limit in (("progress", 100), ("chapter", 100000)):
            if key in changes:
                if type(changes[key]) is not int or not 0 <= changes[key] <= limit:
                    raise ValueError("El progreso de lectura es inválido.")
                clean[key] = changes[key]
        if clean.get("status") == "finished":
            clean["progress"] = 100
        memberships = {}
        for key in ("tags", "categories", "collections"):
            if key in changes:
                if not isinstance(changes[key], list) or not all(isinstance(tag, str) for tag in changes[key]):
                    raise ValueError("Las etiquetas, categorías y colecciones deben ser una lista de nombres.")
                values = {}
                for value in changes[key]:
                    name = term_name(value)
                    if name:
                        if key in TAXONOMIES and "," in name:
                            raise ValueError("Usá nombres sin comas para las etiquetas y categorías.")
                        values.setdefault(term_key(name) if key in TAXONOMIES else name, name)
                memberships[key] = list(values.values())[:30]
        if "series_number" in changes:
            number = changes["series_number"]
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not 0 <= number <= 100000:
                raise ValueError("El número de la serie es inválido.")
            clean["series_number"] = number
        clean["updated_at"] = time.time()
        tracked = _record and bool(set(changes) & (BOOK_FIELDS - {"id", "progress"}))
        with self.edit_transaction("Editar ficha", _db, [book_id], tracked) as db:
            for kind, values in memberships.items():
                known = {term_key(row[0]): row[0] for row in db.execute(f"SELECT name FROM {kind}")} if kind in TAXONOMIES else {}
                values = [known.get(term_key(name), name) for name in values]
                db.executemany(f"INSERT OR IGNORE INTO {kind}(name) VALUES(?)", [(name,) for name in values])
                clean[kind] = json.dumps(values, ensure_ascii=False)
            db.execute(f"UPDATE books SET {', '.join(key + ' = ?' for key in clean)} WHERE id = ?", [*clean.values(), book_id])
            if "title" in clean:
                db.execute("UPDATE works SET title=? WHERE preferred_book_id=?", (clean["title"], book_id))
            if clean.get("status") == "finished" and previous["status"] != "finished":
                db.execute("INSERT INTO book_completions(book_id,finished_at) VALUES(?,?)", (book_id, clean["updated_at"]))
            result = self.serialize(db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone())
        return result

    def taxonomy_counts(self, kind):
        kind = taxonomy_kind(kind)
        exclusions = self.exclusions()
        with self.connect() as db:
            names = {term_key(row[0]): row[0] for row in db.execute(f"SELECT name FROM {kind}")}
            counts = dict.fromkeys(names.values(), 0)
            for row in db.execute(f"SELECT path,{kind} FROM books WHERE deleted_at=0 AND hidden=0"):
                if self.is_ignored(row["path"], exclusions):
                    continue
                for key in {term_key(value) for value in json.loads(row[kind])}:
                    if key in names:
                        counts[names[key]] += 1
        return dict(sorted(counts.items(), key=lambda item: term_key(item[0])))

    def taxonomy_names(self, kind):
        kind = taxonomy_kind(kind)
        with self.connect() as db:
            return sorted((row[0] for row in db.execute(f"SELECT name FROM {kind}")), key=term_key)

    def save_taxonomy(self, kind, name, previous=None):
        kind = taxonomy_kind(kind)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60 or "," in name:
            raise ValueError("Escribí un nombre de hasta 60 caracteres, sin comas.")
        name = term_name(name)
        with self.edit_transaction("Renombrar clasificación" if previous else "Crear clasificación") as db:
            known = {term_key(row[0]): row[0] for row in db.execute(f"SELECT name FROM {kind}")}
            old = known.get(term_key(previous)) if previous is not None else None
            if previous is not None and old is None:
                raise ValueError("Ese nombre ya no existe. Cerrá y volvé a abrir la lista.")
            if term_key(name) in known and (old is None or term_key(old) != term_key(name)):
                raise ValueError("Ese nombre ya existe.")
            if old is not None:
                for row in db.execute(f"SELECT id, {kind} FROM books").fetchall():
                    values = json.loads(row[kind])
                    if any(term_key(value) == term_key(old) for value in values):
                        values = list(dict.fromkeys(name if term_key(value) == term_key(old) else value for value in values))
                        db.execute(f"UPDATE books SET {kind}=?, updated_at=? WHERE id=?", (json.dumps(values, ensure_ascii=False), time.time(), row["id"]))
                db.execute(f"DELETE FROM {kind} WHERE name=?", (old,))
                self.rename_classification_references(db, kind, old, name)
            db.execute(f"INSERT INTO {kind}(name) VALUES(?)", (name,))
        return name

    def remove_taxonomy(self, kind, name):
        kind = taxonomy_kind(kind)
        with self.edit_transaction("Eliminar clasificación") as db:
            for row in db.execute(f"SELECT id, {kind} FROM books").fetchall():
                values = json.loads(row[kind])
                remaining = [value for value in values if term_key(value) != term_key(name)]
                if remaining != values:
                    db.execute(f"UPDATE books SET {kind}=?, updated_at=? WHERE id=?", (json.dumps(remaining, ensure_ascii=False), time.time(), row["id"]))
            for row in db.execute(f"SELECT name FROM {kind}").fetchall():
                if term_key(row[0]) == term_key(name):
                    db.execute(f"DELETE FROM {kind} WHERE name=?", (row[0],))

    def delete(self, book_id):
        self.get(book_id)
        with self.connect() as db:
            db.execute("UPDATE books SET deleted_at=?,updated_at=? WHERE id=?", (time.time(), time.time(), book_id))

    def import_file(self, name, data):
        name = Path(name.replace("\\", "/")).name
        if Path(name).suffix.lower() not in FORMATS:
            raise ValueError("Solo se admiten archivos PDF y EPUB.")
        digest = hashlib.sha256(data).hexdigest()[:20]
        target = self.uploads / f"{digest}{Path(name).suffix.lower()}"
        with self.import_lock:
            created = not target.exists()
            if created:
                target.write_bytes(data)
            try:
                added = self.add(target)
                if added:
                    with self.connect() as db:
                        db.execute(
                            "UPDATE books SET title = ? WHERE path = ? AND title = ?",
                            (Path(name).stem.replace("_", " ")[:500], str(target), target.stem),
                        )
                return {"added": int(added), "skipped": int(not added)}
            except Exception:
                if created:
                    target.unlink(missing_ok=True)
                raise

    def search_text(self, query, book_id=None, limit=200):
        words = re.findall(r"\w+", str(query)[:500], re.UNICODE)
        if not words:
            return []
        match = ' AND '.join('"' + word + '"*' for word in words)
        if str(query).strip().startswith('"') and str(query).strip().endswith('"'):
            match = '"' + ' '.join(words) + '"'
        parameters = [match]
        condition = " AND books.deleted_at=0"
        if book_id is not None:
            condition += " AND books.id=?"
            parameters.append(book_id)
        else:
            condition += " AND books.hidden=0"
        limit = min(500, max(1, limit))
        exclusions = self.exclusions()
        result = []
        with self.connect() as db:
            rows = db.execute("SELECT books.id AS book_id,books.title,books.format,books.path,book_text.position,snippet(book_text,2,'','',' … ',24) AS excerpt FROM book_text JOIN books ON books.id=book_text.book_id WHERE book_text MATCH ?" + condition + " ORDER BY bm25(book_text)", parameters)
            for row in rows:
                if book_id is None and self.is_ignored(row["path"], exclusions):
                    continue
                match = dict(row)
                match.pop("path")
                result.append(match)
                if len(result) >= limit:
                    break
        return result

    def duplicates(self):
        books = self.listing()["books"]
        groups = {}
        for book in books:
            if book["file_hash"]:
                groups.setdefault(book["file_hash"], []).append(book)
        return [group for group in groups.values() if len(group) > 1]

    def relink(self, book_id, path):
        book = self.get(book_id)
        path = Path(path).expanduser().resolve()
        if not path.is_file() or path.suffix[1:].upper() != book["format"]:
            raise ValueError("Elegí un archivo del mismo formato que este libro.")
        if book["file_hash"] and self.digest(path) != book["file_hash"]:
            raise ValueError("Este archivo tiene otro contenido. Elegí el mismo libro en su nueva ubicación.")
        with self.connect() as db:
            existing = db.execute("SELECT id FROM books WHERE path=? AND id!=?", (str(path), book_id)).fetchone()
            if existing:
                raise ValueError("Ese archivo ya tiene una ficha en el catálogo.")
            db.execute("UPDATE books SET path=? WHERE id=?", (str(path), book_id))
        self.ingest(path, force=True)
        return self.get(book_id)

    def collection_names(self):
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT name FROM collections ORDER BY name COLLATE NOCASE")]

    def save_collection(self, name, previous=None):
        name = str(name).strip()[:60]
        if not name:
            raise ValueError("La colección necesita un nombre.")
        with self.edit_transaction("Renombrar colección" if previous else "Crear colección") as db:
            if name != previous and db.execute("SELECT 1 FROM collections WHERE name=?", (name,)).fetchone():
                raise ValueError("Ya existe una colección con ese nombre.")
            db.execute("INSERT OR IGNORE INTO collections(name) VALUES(?)", (name,))
            if previous and previous != name:
                for row in db.execute("SELECT id,collections FROM books").fetchall():
                    values = json.loads(row["collections"])
                    if previous in values:
                        values = list(dict.fromkeys(name if value == previous else value for value in values))
                        db.execute("UPDATE books SET collections=? WHERE id=?", (json.dumps(values), row["id"]))
                db.execute("DELETE FROM collections WHERE name=?", (previous,))
                self.rename_classification_references(db, "collections", previous, name)

    def remove_collection(self, name):
        with self.edit_transaction("Eliminar colección") as db:
            for row in db.execute("SELECT id,collections FROM books").fetchall():
                values = json.loads(row["collections"])
                if name in values:
                    db.execute("UPDATE books SET collections=? WHERE id=?", (json.dumps([value for value in values if value != name]), row["id"]))
            db.execute("DELETE FROM collections WHERE name=?", (name,))

    def annotations(self, book_id, position=None):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM annotations WHERE book_id=?" + (" AND position=?" if position is not None else "") + " ORDER BY position,created_at", [book_id, *([position] if position is not None else [])]).fetchall()
        return [{**dict(row), "rectangles": json.loads(row["rectangles"])} for row in rows]

    def add_annotation(self, book_id, position, kind, quote="", note="", rectangles=None, start_offset=-1, end_offset=-1):
        self.get(book_id)
        if kind not in {"bookmark", "highlight", "note"} or type(position) is not int or not 0 <= position <= 100000:
            raise ValueError("La anotación es inválida.")
        rectangles = rectangles or []
        if not isinstance(rectangles, list) or len(rectangles) > 5000:
            raise ValueError("El resaltado es demasiado grande.")
        for rect in rectangles:
            if not isinstance(rect, (list, tuple)) or len(rect) != 4 or not all(isinstance(value, (int, float)) and 0 <= value <= 1 for value in rect):
                raise ValueError("La posición del resaltado es inválida.")
        with self.edit_transaction("Guardar anotación", book_ids=[book_id]) as db:
            cursor = db.execute("INSERT INTO annotations(book_id,position,kind,quote,note,rectangles,start_offset,end_offset,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (book_id, position, kind, str(quote)[:20000], str(note)[:20000], json.dumps(rectangles), int(start_offset), int(end_offset), time.time()))
            return cursor.lastrowid

    def delete_annotation(self, annotation_id):
        with self.edit_transaction("Eliminar anotación") as db:
            db.execute("DELETE FROM annotations WHERE id=?", (annotation_id,))

    def update_annotation(self, annotation_id, note):
        with self.edit_transaction("Editar anotación") as db:
            db.execute("UPDATE annotations SET note=? WHERE id=?", (str(note)[:20000], annotation_id))

    def toggle_bookmark(self, book_id, position):
        existing = next((item for item in self.annotations(book_id, position) if item["kind"] == "bookmark"), None)
        if existing:
            self.delete_annotation(existing["id"])
            return False
        self.add_annotation(book_id, position, "bookmark")
        return True


def epub_chapters(path):
    package, manifest = epub_package(path)
    chapters = []
    with zipfile.ZipFile(path) as archive:
        for item in package.findall(".//{*}spine/{*}itemref"):
            entry = manifest.get(item.attrib.get("idref"))
            if not entry or item.attrib.get("linear") == "no":
                continue
            data = read_epub_entry(archive, entry["path"])
            title = f"Sección {len(chapters) + 1}"
            try:
                root = ET.fromstring(data)
                heading = root.find(".//{*}h1")
                if heading is None:
                    heading = root.find(".//{*}h2")
                if heading is None:
                    heading = root.find(".//{*}title")
                if heading is not None:
                    title = "".join(heading.itertext()).strip()[:150] or title
            except ET.ParseError:
                pass
            chapters.append({"title": title, "path": entry["path"]})
    if not chapters:
        raise ValueError("No se encontraron capítulos en este EPUB.")
    return chapters


def filter_books(books, view="all", query="", file_format="all", order="recent", category="", tag=""):
    def normalize(value):
        return "".join(char for char in unicodedata.normalize("NFD", str(value)).casefold() if not unicodedata.combining(char))

    query = normalize(query.strip())
    result = []
    for book in books:
        if book.get("hidden") or book.get("ignored"):
            continue
        if view == "favorites" and not book["favorite"]:
            continue
        if view not in {"all", "favorites"} and book["status"] != view:
            continue
        if file_format != "all" and book["format"] != file_format:
            continue
        if category and term_key(category) not in {term_key(value) for value in book.get("categories", [])}:
            continue
        if tag and term_key(tag) not in {term_key(value) for value in book["tags"]}:
            continue
        if query and query not in normalize(" ".join([book["title"], book["author"], book.get("series", ""), *book["tags"], *book.get("categories", []), *book.get("collections", [])])):
            continue
        result.append(book)
    if order in {"title", "author"}:
        result.sort(key=lambda book: normalize(book[order]))
    elif order == "series":
        result.sort(key=lambda book: (normalize(book.get("series", "")), book.get("series_number", 0), normalize(book["title"])))
    elif order == "progress":
        result.sort(key=lambda book: book["progress"], reverse=True)
    else:
        result.sort(key=lambda book: (book["added_at"], book["id"]), reverse=True)
    return result
