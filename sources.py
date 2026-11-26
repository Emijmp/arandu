"""Fuentes locales, exclusiones persistentes y fichas ocultas."""

import os
from pathlib import Path
import time


def canonical_path(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("Elegí una ruta válida.")
    return str(Path(value).expanduser().resolve())


def normalize_exclusions(values):
    if not isinstance(values, (list, tuple)):
        raise ValueError("Las exclusiones deben ser una lista de rutas.")
    result = {}
    for value in values:
        if not isinstance(value, dict) or value.get("kind") not in {"file", "folder"}:
            raise ValueError("Cada exclusión debe indicar un archivo o una carpeta.")
        path = canonical_path(value.get("path"))
        result[path] = {"path": path, "kind": value["kind"]}
    return list(result.values())


def exclusion_for(path, exclusions):
    path = Path(path).expanduser().resolve()
    # An explicit exclusion takes priority over an inherited one in the UI.
    exact = next((entry for entry in exclusions if Path(entry["path"]) == path), None)
    if exact:
        return exact
    parents = [entry for entry in exclusions if entry["kind"] == "folder" and path.is_relative_to(Path(entry["path"]))]
    return max(parents, key=lambda entry: len(Path(entry["path"]).parts)) if parents else None


class SourceStore:
    def initialize_sources(self, db):
        if "hidden" not in {row[1] for row in db.execute("PRAGMA table_info(books)")}:
            db.execute("ALTER TABLE books ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
        db.execute("CREATE TABLE IF NOT EXISTS source_exclusions(path TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('file','folder')), created_at REAL NOT NULL)")

    def exclusions(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM source_exclusions ORDER BY path COLLATE NOCASE")]

    def is_ignored(self, path, exclusions=None):
        return exclusion_for(path, self.exclusions() if exclusions is None else exclusions) is not None

    def ignore_paths(self, values):
        values = normalize_exclusions(values)
        with self.connect() as db:
            db.executemany("INSERT INTO source_exclusions VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET kind=excluded.kind", [(entry["path"], entry["kind"], time.time()) for entry in values])
        return len(values)

    def include_paths(self, paths):
        paths = [canonical_path(path) for path in paths]
        with self.connect() as db:
            db.executemany("DELETE FROM source_exclusions WHERE path=?", [(path,) for path in paths])

    def configure_sources(self, folders, exclusions):
        folders = list(dict.fromkeys(canonical_path(path) for path in folders))
        exclusions = normalize_exclusions(exclusions)
        with self.connect() as db:
            db.execute("DELETE FROM folders")
            db.executemany("INSERT INTO folders VALUES(?)", [(path,) for path in folders])
            old = {row["path"]: row["created_at"] for row in db.execute("SELECT * FROM source_exclusions")}
            db.execute("DELETE FROM source_exclusions")
            db.executemany("INSERT INTO source_exclusions VALUES(?,?,?)", [(entry["path"], entry["kind"], old.get(entry["path"], time.time())) for entry in exclusions])

    def source_walk(self, root, recursive=True, exclusions=None, on_error=None):
        """Yield (directory, subdirectories, files, skipped) without entering exclusions."""
        root = Path(root).expanduser().resolve()
        exclusions = self.exclusions() if exclusions is None else exclusions
        if root.is_relative_to(self.data_dir) or self.is_ignored(root, exclusions):
            yield root, [], [], 1
            return
        for directory, names, files in os.walk(root, topdown=True, onerror=on_error, followlinks=False):
            directory = Path(directory)
            kept_dirs, kept_files, skipped = [], [], 0
            for name in names:
                child = directory / name
                if child.resolve().is_relative_to(self.data_dir) or self.is_ignored(child, exclusions):
                    skipped += 1
                elif not child.is_symlink():
                    kept_dirs.append(name)
            names[:] = kept_dirs if recursive else []
            for name in files:
                if self.is_ignored(directory / name, exclusions):
                    skipped += 1
                else:
                    kept_files.append(name)
            yield directory, kept_dirs, kept_files, skipped
            if not recursive:
                break

    def set_hidden(self, book_ids, hidden=True):
        if type(hidden) is not bool:
            raise ValueError("El estado de visibilidad debe ser verdadero o falso.")
        return self.batch_update(book_ids, {"hidden": hidden})

    def hidden_books(self):
        return [book for book in self.listing(include_hidden=True, include_ignored=True)["books"] if book["hidden"]]
