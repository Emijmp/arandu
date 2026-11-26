"""Copias locales verificadas; restauración con copia de seguridad previa."""

from datetime import datetime, timezone
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import zipfile
from uuid import uuid4

from themes import load_custom_themes
from sources import exclusion_for


SETTINGS = ("window.json", "themes.json", "reader.json")


def create_backup(library, target, include_books=False):
    target = Path(target).expanduser().resolve()
    if target == library.database or target.suffix.lower() != ".zip":
        raise ValueError("Elegí un archivo .zip para guardar la copia.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=library.data_dir) as folder:
        snapshot = Path(folder) / "catalog.sqlite3"
        with library.connect() as source, closing(sqlite3.connect(snapshot)) as destination:
            source.backup(destination)
        manifest = {"format": "arandu-backup", "version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "files": {}, "covers": {}}
        # Read exactly the books represented by this snapshot, including the trash.
        with closing(sqlite3.connect(snapshot)) as snapshot_db:
            snapshot_db.row_factory = sqlite3.Row
            books = [library.serialize(row) for row in snapshot_db.execute("SELECT * FROM books")]
        temporary = Path(folder) / "backup.zip"
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, "catalog.sqlite3")
            for name in SETTINGS:
                path = library.data_dir / name
                if path.is_file():
                    archive.write(path, name)
            written = set()
            for book in books:
                if book["custom_cover"] and Path(book["custom_cover"]).is_file():
                    digest = library.digest(book["custom_cover"])
                    entry = f"covers/{digest}.png"
                    manifest["covers"][str(book["id"])] = entry
                    if entry not in written:
                        archive.write(book["custom_cover"], entry)
                        written.add(entry)
            if include_books:
                for book in books:
                    if not book["available"]:
                        continue
                    before = Path(book["path"]).stat()
                    digest = library.digest(book["path"])
                    entry = f"books/{digest}.{book['format'].lower()}"
                    manifest["files"][str(book["id"])] = entry
                    if entry not in written:
                        archive.write(book["path"], entry)
                        after = Path(book["path"]).stat()
                        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                            raise ValueError("Un libro cambió mientras se copiaba. Volvé a crear la copia.")
                        written.add(entry)
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        # Copy next to the chosen destination, then replace it atomically.
        staged_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as staged:
                staged_path = Path(staged.name)
                with temporary.open("rb") as source:
                    shutil.copyfileobj(source, staged)
            os.replace(staged_path, target)
        finally:
            if staged_path:
                staged_path.unlink(missing_ok=True)
    return str(target)


def daily_backup(library):
    if not library.listing(include_deleted=True, include_hidden=True, include_ignored=True)["books"] and not library.exclusions():
        return None
    target = library.data_dir / "backups" / f"automatico-{datetime.now():%Y-%m-%d}.zip"
    return str(target) if target.exists() else create_backup(library, target)


def restore_backup(library, source):
    source = Path(source).expanduser().resolve()
    with tempfile.TemporaryDirectory(dir=library.data_dir) as folder:
        stage = Path(folder)
        with zipfile.ZipFile(source) as archive:
            if archive.getinfo("manifest.json").file_size > 4 * 1024 * 1024:
                raise ValueError("El manifiesto de la copia es demasiado grande.")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "arandu-backup" or manifest.get("version") != 1 or not isinstance(manifest.get("files"), dict):
                raise ValueError("Este archivo no es una copia de Arandu compatible.")
            if not all(isinstance(entry, str) and re.fullmatch(r"books/[a-f0-9]{64}\.(pdf|epub)", entry) for entry in manifest["files"].values()):
                raise ValueError("La copia contiene una ruta de libro no permitida.")
            covers = manifest.get("covers", {})
            if not isinstance(covers, dict) or not all(isinstance(entry, str) and re.fullmatch(r"covers/[a-f0-9]{64}\.png", entry) for entry in covers.values()):
                raise ValueError("La copia contiene una ruta de portada no permitida.")
            allowed = {"manifest.json", "catalog.sqlite3", *SETTINGS, *manifest["files"].values(), *covers.values()}
            for name in archive.namelist():
                if name not in allowed or (name.startswith("books/") and not re.fullmatch(r"books/[a-f0-9]{64}\.(pdf|epub)", name)):
                    raise ValueError("La copia contiene una ruta no permitida.")
                if archive.getinfo(name).file_size > 20 * 1024 * 1024 * 1024:
                    raise ValueError("Un archivo de la copia excede el tamaño permitido.")
                destination = stage / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as incoming, destination.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
        snapshot = stage / "catalog.sqlite3"
        if not snapshot.is_file():
            raise ValueError("La copia no contiene el catálogo.")
        to_recover = {}
        covers_to_recover = {}
        with closing(sqlite3.connect(snapshot)) as db, db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("El catálogo de la copia está dañado.")
            columns = {row[1] for row in db.execute("PRAGMA table_info(books)")}
            if not {"id", "path", "title", "author", "notes", "progress"}.issubset(columns):
                raise ValueError("El catálogo de la copia no tiene un formato compatible.")
            source_exclusions = []
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_exclusions'").fetchone():
                source_exclusions = [{"path": row[0], "kind": row[1]} for row in db.execute("SELECT path,kind FROM source_exclusions")]
            for book_id, entry in manifest["files"].items():
                if not str(book_id).isdigit() or not (stage / entry).is_file():
                    raise ValueError("La copia contiene un libro incompleto.")
                expected = Path(entry).stem
                if library.digest(stage / entry) != expected:
                    raise ValueError("Un libro de la copia no supera la verificación de integridad.")
                existing = db.execute("SELECT path FROM books WHERE id=?", (int(book_id),)).fetchone()
                if existing is None:
                    raise ValueError("La copia contiene una referencia de libro desconocida.")
                original = Path(existing[0])
                if not original.is_file() or library.digest(original) != expected:
                    recovered = library.uploads / "recovered" / f"{book_id}-{Path(entry).name}"
                    if recovered.exists() and library.digest(recovered) != expected:
                        recovered = recovered.with_name(f"{book_id}-{expected}-{uuid4().hex[:8]}{Path(entry).suffix}")
                    if not recovered.exists():
                        to_recover[recovered] = entry
                    db.execute("UPDATE books SET path=? WHERE id=?", (str(recovered), int(book_id)))
                    if exclusion_for(original, source_exclusions):
                        # A relocated copy must not reappear simply because its path changed.
                        db.execute("INSERT OR IGNORE INTO source_exclusions VALUES(?,?,?)", (str(recovered), "file", datetime.now().timestamp()))
            if "cover_path" in columns:
                db.execute("UPDATE books SET cover_path=''")
            if "custom_cover" in columns:
                db.execute("UPDATE books SET custom_cover=''")
                for book_id, entry in covers.items():
                    if not str(book_id).isdigit() or not (stage / entry).is_file() or library.digest(stage / entry) != Path(entry).stem or not db.execute("SELECT 1 FROM books WHERE id=?", (int(book_id),)).fetchone():
                        raise ValueError("La copia contiene una portada incompleta o dañada.")
                    destination = library.covers / f"custom-{book_id}-{Path(entry).stem}-{uuid4().hex[:8]}.png"
                    covers_to_recover[destination] = entry
                    db.execute("UPDATE books SET custom_cover=? WHERE id=?", (str(destination), int(book_id)))
            if "indexed_mtime" in columns:
                db.execute("UPDATE books SET indexed_mtime=-1")
        for name in SETTINGS:
            if (stage / name).exists() and not isinstance(json.loads((stage / name).read_text()), dict):
                raise ValueError("La copia contiene preferencias inválidas.")
        load_custom_themes(stage)
        for name in SETTINGS:
            if not (stage / name).exists():
                (stage / name).write_text(json.dumps({"version": 1, "themes": []} if name == "themes.json" else {}))
        safety = library.data_dir / "backups" / f"antes-de-restaurar-{datetime.now():%Y%m%d-%H%M%S-%f}.zip"
        create_backup(library, safety)
        recovered_dir = library.uploads / "recovered"
        recovered_dir.mkdir(exist_ok=True)
        for destination, entry in to_recover.items():
            shutil.copyfile(stage / entry, destination)
        for destination, entry in covers_to_recover.items():
            shutil.copyfile(stage / entry, destination)
        for name in SETTINGS:
            if (stage / name).is_file():
                os.replace(stage / name, library.data_dir / name)
        os.replace(snapshot, library.database)
        library.initialize()
    return str(safety)
