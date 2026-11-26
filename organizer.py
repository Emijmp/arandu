"""Organización, lecturas y recuperación del catálogo. Todo permanece local."""

from datetime import datetime, timedelta
import json
import math
import re
import time
from pathlib import Path
from uuid import uuid4


def clean_names(values):
    from library import term_key, term_name
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("Escribí una lista de nombres.")
    result = {}
    for value in values:
        name = term_name(value)
        if "," in name:
            raise ValueError("Los nombres no pueden contener comas.")
        if name:
            result.setdefault(term_key(name), name)
    return list(result.values())[:30]


def shelf_filters(values):
    if not isinstance(values, dict):
        raise ValueError("Los filtros no son válidos.")
    result = {key: str(values.get(key, ""))[:500] for key in ("query", "category", "tag", "collection")}
    for key, default, choices in (
        ("view", "all", {"all", "pending", "reading", "finished", "favorites"}),
        ("file_format", "all", {"all", "PDF", "EPUB"}),
        ("order", "recent", {"recent", "title", "author", "progress", "series"}),
    ):
        result[key] = values.get(key, default)
        if result[key] not in choices:
            raise ValueError("Los filtros no son válidos.")
    return result


def match_shelf(books, values):
    from library import filter_books
    values = shelf_filters(values)
    collection = values.pop("collection")
    result = filter_books(books, **values)
    return [book for book in result if not collection or collection in book["collections"]]


def rule_matches(book, conditions):
    folder = conditions.get("folder", "")
    if folder:
        try:
            if not Path(book["path"]).resolve().is_relative_to(Path(folder).expanduser().resolve()):
                return False
        except (OSError, ValueError):
            return False
    for field in ("author", "filename"):
        value = book["author"] if field == "author" else Path(book["path"]).name
        if conditions.get(field) and conditions[field].casefold() not in value.casefold():
            return False
    return not conditions.get("format") or book["format"] == conditions["format"]


class OrganizerStore:
    def rename_classification_references(self, db, kind, previous, name):
        from library import term_key
        field = {"tags": "tag", "categories": "category", "collections": "collection"}[kind]
        for row in db.execute("SELECT * FROM smart_shelves").fetchall():
            filters = json.loads(row["filters"])
            if term_key(filters.get(field, "")) == term_key(previous):
                filters[field] = name
                db.execute("UPDATE smart_shelves SET filters=? WHERE id=?", (json.dumps(filters), row["id"]))
        for row in db.execute("SELECT * FROM organization_rules").fetchall():
            actions = json.loads(row["actions"])
            values = actions.get(kind, [])
            if any(term_key(value) == term_key(previous) for value in values):
                actions[kind] = clean_names([name if term_key(value) == term_key(previous) else value for value in values])
                db.execute("UPDATE organization_rules SET actions=? WHERE id=?", (json.dumps(actions), row["id"]))

    def initialize_organization(self, db):
        columns = {row[1] for row in db.execute("PRAGMA table_info(books)")}
        for key, declaration in {
            "deleted_at": "REAL NOT NULL DEFAULT 0", "custom_cover": "TEXT NOT NULL DEFAULT ''",
            "work_id": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if key not in columns:
                db.execute(f"ALTER TABLE books ADD COLUMN {key} {declaration}")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS smart_shelves(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, filters TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS organization_rules(id INTEGER PRIMARY KEY, name TEXT NOT NULL, conditions TEXT NOT NULL, actions TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS works(id INTEGER PRIMARY KEY, title TEXT NOT NULL, preferred_book_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS reading_sessions(id TEXT PRIMARY KEY, book_id INTEGER NOT NULL, started_at REAL NOT NULL, ended_at REAL NOT NULL, seconds REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS book_completions(id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL, finished_at REAL NOT NULL, estimated INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS reading_goals(year INTEGER PRIMARY KEY, books INTEGER NOT NULL DEFAULT 12, daily_minutes INTEGER NOT NULL DEFAULT 20);
            CREATE TABLE IF NOT EXISTS ocr_pages(book_id INTEGER NOT NULL, position INTEGER NOT NULL, file_hash TEXT NOT NULL, content TEXT NOT NULL, words TEXT NOT NULL, language TEXT NOT NULL, PRIMARY KEY(book_id,position));
            CREATE INDEX IF NOT EXISTS reading_book ON reading_sessions(book_id);
            CREATE INDEX IF NOT EXISTS completions_book ON book_completions(book_id);
        """)
        db.execute("INSERT INTO book_completions(book_id,finished_at,estimated) SELECT id,updated_at,1 FROM books WHERE status='finished' AND NOT EXISTS(SELECT 1 FROM book_completions WHERE book_id=books.id)")

    def batch_update(self, book_ids, changes, mode="add"):
        if mode not in {"add", "replace", "remove"}:
            raise ValueError("Elegí cómo aplicar las etiquetas y categorías.")
        allowed = {"author", "status", "favorite", "hidden", "tags", "categories", "collections"}
        if not changes or set(changes) - allowed:
            raise ValueError("Elegí al menos un campo para editar.")
        book_ids = list(dict.fromkeys(book_ids))
        books = [self.get(book_id) for book_id in book_ids]
        if not books or any(book["deleted_at"] for book in books):
            raise ValueError("Seleccioná libros que estén en la biblioteca.")
        from library import term_key
        with self.edit_transaction("Editar varios libros", book_ids=book_ids) as db:
            for book in books:
                update = dict(changes)
                for kind in ("tags", "categories", "collections"):
                    if kind in update:
                        values = clean_names(update[kind])
                        if mode == "add":
                            values = clean_names([*book[kind], *values])
                        elif mode == "remove":
                            keys = {term_key(name) for name in values}
                            values = [name for name in book[kind] if term_key(name) not in keys]
                        update[kind] = values
                self.update(book["id"], update, _db=db)
        return len(books)

    def trash(self):
        with self.connect() as db:
            return [self.serialize(row) for row in db.execute("SELECT * FROM books WHERE deleted_at>0 ORDER BY deleted_at DESC")]

    def restore_book(self, book_id):
        self.get(book_id)
        with self.connect() as db:
            db.execute("UPDATE books SET deleted_at=0,updated_at=? WHERE id=?", (time.time(), book_id))

    def purge_book(self, book_id):
        if not self.get(book_id)["deleted_at"]:
            raise ValueError("Primero mové el libro a la papelera.")
        with self.connect() as db:
            for table in ("annotations", "book_text", "reading_sessions", "book_completions", "ocr_pages", "reader_state"):
                db.execute(f"DELETE FROM {table} WHERE book_id=?", (book_id,))
            db.execute("DELETE FROM books WHERE id=?", (book_id,))
            db.execute("DELETE FROM works WHERE NOT EXISTS(SELECT 1 FROM books WHERE work_id=works.id)")

    def set_custom_cover(self, book_id, data):
        self.get(book_id)
        path = ""
        if data is not None:
            if not isinstance(data, bytes) or not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > 25 * 1024 * 1024:
                raise ValueError("Elegí una imagen PNG válida de hasta 25 MB.")
            target = self.covers / f"custom-{book_id}-{uuid4().hex}.png"
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(target)
            path = str(target)
        with self.connect() as db:
            db.execute("UPDATE books SET custom_cover=?,updated_at=? WHERE id=?", (path, time.time(), book_id))
        return path

    def smart_shelves(self):
        with self.connect() as db:
            return [{**dict(row), "filters": json.loads(row["filters"])} for row in db.execute("SELECT * FROM smart_shelves ORDER BY name COLLATE NOCASE")]

    def save_shelf(self, name, filters, shelf_id=None):
        name = str(name).strip()[:80]
        if not name:
            raise ValueError("El estante necesita un nombre.")
        filters = shelf_filters(filters)
        with self.connect() as db:
            if db.execute("SELECT 1 FROM smart_shelves WHERE name=? COLLATE NOCASE AND id!=?", (name, shelf_id or 0)).fetchone():
                raise ValueError("Ya existe un estante con ese nombre.")
            if shelf_id is None:
                return db.execute("INSERT INTO smart_shelves(name,filters) VALUES(?,?)", (name, json.dumps(filters))).lastrowid
            if not db.execute("UPDATE smart_shelves SET name=?,filters=? WHERE id=?", (name, json.dumps(filters), shelf_id)).rowcount:
                raise ValueError("El estante ya no existe.")
        return shelf_id

    def delete_shelf(self, shelf_id):
        with self.connect() as db:
            db.execute("DELETE FROM smart_shelves WHERE id=?", (shelf_id,))

    def rules(self):
        with self.connect() as db:
            return [{**dict(row), "conditions": json.loads(row["conditions"]), "actions": json.loads(row["actions"])} for row in db.execute("SELECT * FROM organization_rules ORDER BY id")]

    def save_rule(self, name, conditions, actions, enabled=True, rule_id=None):
        name = str(name).strip()[:80]
        if not name or not isinstance(conditions, dict) or not isinstance(actions, dict):
            raise ValueError("La regla necesita un nombre, una condición y una acción.")
        if set(conditions) - {"folder", "filename", "author", "format"} or set(actions) - {"tags", "categories", "collections"}:
            raise ValueError("La regla contiene un campo inválido.")
        conditions = {key: str(value).strip()[:1000] for key, value in conditions.items() if value}
        if not conditions or (conditions.get("format") and conditions["format"] not in {"PDF", "EPUB"}):
            raise ValueError("Definí al menos una condición válida.")
        if "folder" in conditions:
            conditions["folder"] = str(Path(conditions["folder"]).expanduser().resolve())
        actions = {key: clean_names(value) for key, value in actions.items() if value}
        if not any(actions.values()):
            raise ValueError("Definí al menos una etiqueta, categoría o colección.")
        with self.connect() as db:
            values = (name, json.dumps(conditions), json.dumps(actions), int(bool(enabled)))
            if rule_id is None:
                return db.execute("INSERT INTO organization_rules(name,conditions,actions,enabled) VALUES(?,?,?,?)", values).lastrowid
            if not db.execute("UPDATE organization_rules SET name=?,conditions=?,actions=?,enabled=? WHERE id=?", (*values, rule_id)).rowcount:
                raise ValueError("La regla ya no existe.")
        return rule_id

    def delete_rule(self, rule_id):
        with self.connect() as db:
            db.execute("DELETE FROM organization_rules WHERE id=?", (rule_id,))

    def apply_rules(self, book_id):
        book = self.get(book_id)
        if book["deleted_at"]:
            return False
        changes = {}
        for rule in self.rules():
            if rule["enabled"] and rule_matches(book, rule["conditions"]):
                for kind, names in rule["actions"].items():
                    values = clean_names([*changes.get(kind, book[kind]), *names])
                    if values != book[kind]:
                        changes[kind] = values
        if changes:
            with self.edit_transaction("Aplicar reglas", book_ids=[book_id]) as db:
                self.update(book_id, changes, _db=db)
        return bool(changes)

    def apply_all_rules(self):
        books = self.listing(include_hidden=True)["books"]
        with self.edit_transaction("Aplicar reglas a la biblioteca", book_ids=[book["id"] for book in books]):
            return sum(self.apply_rules(book["id"]) for book in books)

    def group_editions(self, book_ids, work_id=None):
        ids = list(dict.fromkeys(book_ids))
        books = [self.get(book_id) for book_id in ids]
        if not books or any(book["deleted_at"] for book in books) or (work_id is None and len(ids) < 2):
            raise ValueError("Seleccioná al menos dos libros para crear una obra.")
        with self.connect() as db:
            if work_id is None:
                work_id = db.execute("INSERT INTO works(title,preferred_book_id) VALUES(?,?)", (books[0]["title"], ids[0])).lastrowid
            elif not db.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
                raise ValueError("La obra ya no existe.")
            db.executemany("UPDATE books SET work_id=? WHERE id=?", [(work_id, book_id) for book_id in ids])
            db.execute("DELETE FROM works WHERE NOT EXISTS(SELECT 1 FROM books WHERE work_id=works.id)")
        return work_id

    def ungroup_edition(self, book_id):
        with self.connect() as db:
            db.execute("UPDATE books SET work_id=0 WHERE id=?", (book_id,))
            db.execute("DELETE FROM works WHERE NOT EXISTS(SELECT 1 FROM books WHERE work_id=works.id)")

    def works(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM works WHERE EXISTS(SELECT 1 FROM books WHERE work_id=works.id AND deleted_at=0) ORDER BY title COLLATE NOCASE")]

    def prefer_edition(self, work_id, book_id):
        book = self.get(book_id)
        if book["work_id"] != work_id or book["deleted_at"]:
            raise ValueError("Elegí una edición de esta obra.")
        with self.connect() as db:
            db.execute("UPDATE works SET preferred_book_id=?,title=? WHERE id=?", (book_id, book["title"], work_id))

    def catalog_entries(self, books):
        preferred = {work["id"]: work["preferred_book_id"] for work in self.works()}
        groups, entries = {}, []
        for book in books:
            if not book["work_id"]:
                entries.append(book)
            else:
                if book["work_id"] not in groups:
                    groups[book["work_id"]] = []
                    entries.append(groups[book["work_id"]])
                groups[book["work_id"]].append(book)
        result = []
        for entry in entries:
            if isinstance(entry, dict):
                result.append(entry)
                continue
            primary = next((book for book in entry if book["id"] == preferred.get(book["work_id"]) and book["available"]), next((book for book in entry if book["available"]), entry[0]))
            result.append({**primary, "edition_ids": [book["id"] for book in entry], "edition_formats": " / ".join(dict.fromkeys(book["format"] for book in entry))})
        return result

    def record_reading(self, book_id, seconds, session_id=None, ended_at=None):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 < seconds <= 86400:
            raise ValueError("La duración de lectura es inválida.")
        self.get(book_id)
        ended_at = time.time() if ended_at is None else float(ended_at)
        if not math.isfinite(ended_at):
            raise ValueError("La fecha de lectura es inválida.")
        session_id = session_id or uuid4().hex
        with self.connect() as db:
            old = db.execute("SELECT book_id FROM reading_sessions WHERE id=?", (session_id,)).fetchone()
            if old and old[0] != book_id:
                raise ValueError("La sesión pertenece a otro libro.")
            db.execute("INSERT INTO reading_sessions(id,book_id,started_at,ended_at,seconds) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET ended_at=excluded.ended_at,seconds=reading_sessions.seconds+excluded.seconds", (session_id, book_id, ended_at - seconds, ended_at, seconds))
        return session_id

    def goals(self, year):
        with self.connect() as db:
            row = db.execute("SELECT * FROM reading_goals WHERE year=?", (year,)).fetchone()
        return dict(row) if row else {"year": year, "books": 12, "daily_minutes": 20}

    def save_goals(self, year, books, daily_minutes):
        if any(type(value) is not int for value in (year, books, daily_minutes)) or not 1900 <= year <= 9998 or not 0 <= books <= 10000 or not 0 <= daily_minutes <= 1440:
            raise ValueError("Revisá el año y las metas de lectura.")
        with self.connect() as db:
            db.execute("INSERT INTO reading_goals VALUES(?,?,?) ON CONFLICT(year) DO UPDATE SET books=excluded.books,daily_minutes=excluded.daily_minutes", (year, books, daily_minutes))

    def reading_stats(self, year, now=None):
        now = datetime.now() if now is None else now
        begin, end = datetime(year, 1, 1).timestamp(), datetime(year + 1, 1, 1).timestamp()
        today, tomorrow = datetime(now.year, now.month, now.day), datetime(now.year, now.month, now.day) + timedelta(days=1)
        with self.connect() as db:
            sessions = [dict(row) for row in db.execute("SELECT s.*,b.title,b.format FROM reading_sessions s JOIN books b ON b.id=s.book_id WHERE b.deleted_at=0 AND s.started_at>=? AND s.started_at<? ORDER BY s.started_at DESC", (begin, end))]
            completions = [dict(row) for row in db.execute("SELECT c.*,b.work_id FROM book_completions c JOIN books b ON b.id=c.book_id WHERE b.deleted_at=0 AND c.finished_at>=? AND c.finished_at<?", (begin, end))]
        months = [{"month": month, "seconds": 0, "sessions": 0, "books": set()} for month in range(1, 13)]
        for session in sessions:
            month = months[datetime.fromtimestamp(session["started_at"]).month - 1]
            month["seconds"] += session["seconds"]
            month["sessions"] += 1
        for item in completions:
            months[datetime.fromtimestamp(item["finished_at"]).month - 1]["books"].add(("work", item["work_id"]) if item["work_id"] else ("book", item["book_id"]))
        for month in months:
            month["books"] = len(month["books"])
        return {"months": months, "seconds": sum(item["seconds"] for item in sessions), "sessions": sessions,
                "books": len({("work", item["work_id"]) if item["work_id"] else ("book", item["book_id"]) for item in completions}), "estimated": any(item["estimated"] for item in completions),
                "today_seconds": sum(item["seconds"] for item in sessions if today.timestamp() <= item["started_at"] < tomorrow.timestamp()), "goals": self.goals(year)}

    def ocr_words(self, book_id, position):
        with self.connect() as db:
            row = db.execute("SELECT o.words FROM ocr_pages o JOIN books b ON b.id=o.book_id WHERE o.book_id=? AND o.position=? AND o.file_hash=b.file_hash", (book_id, position)).fetchone()
        return json.loads(row[0]) if row else []

    def annotation_export(self, book_ids=None, markdown=True):
        ids = set(book_ids) if book_ids is not None else None
        books = [book for book in self.listing(include_hidden=ids is not None, include_ignored=ids is not None)["books"] if ids is None or book["id"] in ids]
        lines = ["# Notas de Arandu" if markdown else "NOTAS DE ARANDU", ""]
        def escaped(value):
            return re.sub(r"([\\`*_\[\]<>#])", r"\\\1", value) if markdown else value
        for book in books:
            lines += [("## " if markdown else "") + escaped(book["title"].replace("\n", " ")), f"{escaped(book['author'])} · {book['format']}", f"Archivo: {escaped(Path(book['path']).name)}", ""]
            if book["notes"]:
                lines += ["Mis notas:", escaped(book["notes"]), ""]
            annotations = self.annotations(book["id"])
            for item in annotations:
                location = "Página" if book["format"] == "PDF" else "Sección"
                kind = {"bookmark": "Marcador", "highlight": "Resaltado", "note": "Nota"}[item["kind"]]
                lines += [f"{location} {item['position'] + 1} · {kind}"]
                if item["quote"]:
                    lines.extend(("> " if markdown else "    ") + escaped(line) for line in item["quote"].splitlines())
                if item["note"]:
                    lines.append(escaped(item["note"]))
                lines.append("")
            if not annotations and not book["notes"]:
                lines += ["Sin anotaciones.", ""]
        return "\n".join(lines).rstrip() + "\n"

    def export_annotations(self, target, book_ids=None):
        target = Path(target).expanduser().resolve()
        if target.suffix.lower() not in {".md", ".txt"} or str(target) in {book["path"] for book in self.listing(include_deleted=True, include_hidden=True, include_ignored=True)["books"]}:
            raise ValueError("Elegí un archivo nuevo con extensión .md o .txt.")
        text = self.annotation_export(book_ids, target.suffix.lower() == ".md")
        temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return str(target)
