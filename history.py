"""Historial transaccional persistente, con protección frente a conflictos."""

from contextlib import contextmanager, nullcontext
import json
import math
import threading
import time

BOOK_FIELDS = {"id", "title", "author", "status", "favorite", "progress", "tags", "categories", "collections", "series", "series_number", "notes", "hidden"}
TABLE_KEYS = {"books": "id", "tags": "name", "categories": "name", "collections": "name", "smart_shelves": "id", "organization_rules": "id", "works": "id", "book_completions": "id", "annotations": "id"}


class HistoryStore:
    def initialize_history(self, db):
        self._history_local = threading.local()
        db.execute("CREATE TABLE IF NOT EXISTS change_history(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,changes TEXT NOT NULL,created_at REAL NOT NULL,state TEXT NOT NULL DEFAULT 'applied')")
        db.execute("CREATE TABLE IF NOT EXISTS reader_state(book_id INTEGER PRIMARY KEY,payload TEXT NOT NULL,updated_at REAL NOT NULL)")

    def _history_snapshot(self, db, book_ids):
        snapshot = {}
        for table, key in TABLE_KEYS.items():
            condition, params = "", []
            if book_ids is not None and table in {"books", "book_completions", "annotations"}:
                column = "id" if table == "books" else "book_id"
                condition = f" WHERE {column} IN ({','.join('?' for _ in book_ids) or 'NULL'})"
                params = book_ids
            rows = db.execute(f"SELECT * FROM {table}" + condition, params)
            snapshot[table] = {str(row[key]): {field: row[field] for field in row.keys() if table != "books" or field in BOOK_FIELDS} for row in rows}
        return snapshot

    @contextmanager
    def edit_transaction(self, title, db=None, book_ids=None, tracked=True):
        active = getattr(self._history_local, "db", None)
        if active is not None:
            yield active
            return
        with (self.connect() if db is None else nullcontext(db)) as connection:
            if not tracked:
                yield connection
                return
            ids = list(dict.fromkeys(book_ids)) if book_ids is not None else None
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            before = self._history_snapshot(connection, ids)
            self._history_local.db = connection
            try:
                yield connection
                after = self._history_snapshot(connection, ids)
                changes = []
                for table in TABLE_KEYS:
                    for key in sorted(before[table].keys() | after[table].keys()):
                        old, new = before[table].get(key), after[table].get(key)
                        if old == new:
                            continue
                        if old is not None and new is not None:
                            fields = {field for field in old if old[field] != new[field]}
                            old, new = ({field: old[field] for field in fields}, {field: new[field] for field in fields})
                        changes.append({"table": table, "key": key, "before": old, "after": new})
                if changes:
                    connection.execute("UPDATE change_history SET state='discarded' WHERE state='undone'")
                    connection.execute("INSERT INTO change_history(title,changes,created_at) VALUES(?,?,?)", (title, json.dumps(changes, ensure_ascii=False), time.time()))
            finally:
                self._history_local.db = None

    def history_entries(self, limit=250):
        with self.connect() as db:
            return [{**dict(row), "changes": json.loads(row["changes"])} for row in db.execute("SELECT * FROM change_history ORDER BY id DESC LIMIT ?", (min(2000, max(1, limit)),))]

    def history_heads(self):
        with self.connect() as db:
            return db.execute("SELECT max(id) FROM change_history WHERE state='applied'").fetchone()[0], db.execute("SELECT min(id) FROM change_history WHERE state='undone'").fetchone()[0]

    def history_step(self, redo=False, expected_id=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM change_history WHERE state=? ORDER BY id " + ("ASC" if redo else "DESC") + " LIMIT 1", ("undone" if redo else "applied",)).fetchone()
            if row is None:
                raise ValueError("No hay cambios para rehacer." if redo else "No hay cambios para deshacer.")
            if expected_id is not None and row["id"] != expected_id:
                raise ValueError("El historial cambió. Actualizá la lista antes de continuar.")
            operations = []
            for change in json.loads(row["changes"]):
                table, key = change["table"], TABLE_KEYS[change["table"]]
                expected = change["before"] if redo else change["after"]
                target = change["after"] if redo else change["before"]
                found = db.execute(f"SELECT * FROM {table} WHERE {key}=?", (change["key"],)).fetchone()
                current = dict(found) if found else None
                if expected is None:
                    if current is not None and not all(current.get(field) == value for field, value in target.items()):
                        raise ValueError("Hay datos posteriores con el mismo nombre. No se aplicó ningún cambio.")
                elif current is None or any(current.get(field) != value for field, value in expected.items()):
                    raise ValueError("Una ficha cambió después de esta acción. No se aplicó ningún cambio para conservar los datos actuales.")
                operations.append((table, key, change["key"], current, target))
            operations.sort(key=lambda item: item[0] != "books")
            for table, key, value, current, target in operations:
                if target is None:
                    if table in {"tags", "categories", "collections"} and any(value in json.loads(record[0]) for record in db.execute(f"SELECT {table} FROM books")):
                        continue
                    db.execute(f"DELETE FROM {table} WHERE {key}=?", (value,))
                elif current is None:
                    fields = list(target)
                    db.execute(f"INSERT INTO {table}({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", [target[field] for field in fields])
                else:
                    fields = [field for field in target if field != key]
                    if fields:
                        db.execute(f"UPDATE {table} SET {','.join(field+'=?' for field in fields)} WHERE {key}=?", [*(target[field] for field in fields), value])
            db.execute("UPDATE change_history SET state=? WHERE id=?", ("applied" if redo else "undone", row["id"]))
        return row["title"]

    def reading_state(self, book_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM reader_state WHERE book_id=?", (book_id,)).fetchone()
        try:
            value = json.loads(row[0]) if row else {}
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}

    def save_reading_state(self, book_id, payload):
        self.get(book_id)
        if not isinstance(payload, dict) or len(json.dumps(payload)) > 32000:
            raise ValueError("La posición de lectura no es válida.")
        for key, value in payload.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("La posición de lectura contiene un valor inválido.")
            if key in {"anchor", "chapter", "page"} and (type(value) is not int or not 0 <= value <= 100000000):
                raise ValueError("La posición de lectura contiene un valor inválido.")
            if key == "mode" and value not in {"continuous", "single", "double"}:
                raise ValueError("El modo de lectura no es válido.")
        with self.connect() as db:
            db.execute("INSERT INTO reader_state VALUES(?,?,?) ON CONFLICT(book_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at", (book_id, json.dumps(payload), time.time()))
