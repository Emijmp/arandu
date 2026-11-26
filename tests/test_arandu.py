from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from library import Library, epub_chapters, filter_books, read_epub_entry


PDF = b"%PDF-1.4\nA small local test document.\n%%EOF\n"


def make_epub(path):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        archive.writestr("OEBPS/content.opf", '''<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Un libro de prueba</dc:title><dc:creator>Autora local</dc:creator></metadata><manifest><item id="one" href="one.xhtml" media-type="application/xhtml+xml"/><item id="two" href="cap%C3%ADtulo.xhtml" media-type="application/xhtml+xml"/><item id="cover" href="cover.svg" media-type="image/svg+xml" properties="cover-image"/></manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>''')
        archive.writestr("OEBPS/one.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Inicio</title></head><body><h1>Primer capítulo</h1><script>window.parent.alert(1)</script><p>Una historia.</p></body></html>')
        archive.writestr("OEBPS/capítulo.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Segundo capítulo</h1><p>Continuará.</p></body></html>')
        archive.writestr("OEBPS/cover.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="green"/></svg>')


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.books = self.root / "books"
        self.books.mkdir()
        self.library = Library(self.root / "data")

    def tearDown(self):
        self.temp.cleanup()

    def test_recursive_scan_metadata_deduplication_and_invalid_files(self):
        (self.books / "Mi_libro.PDF").write_bytes(PDF)
        (self.books / "nested").mkdir()
        make_epub(self.books / "nested" / "book.epub")
        (self.books / "broken.epub").write_text("broken")
        (self.books / "ignore.txt").write_text("not a book")
        first = self.library.scan([str(self.books)])
        self.assertEqual((first["added"], first["skipped"], len(first["errors"])), (2, 0, 1))
        books = self.library.listing()["books"]
        epub = next(book for book in books if book["format"] == "EPUB")
        self.assertEqual(epub["title"], "Un libro de prueba")
        self.assertEqual(epub["author"], "Autora local")
        self.assertEqual(epub["cover"], "OEBPS/cover.svg")
        second = self.library.scan([str(self.books)])
        self.assertEqual((second["added"], second["skipped"]), (0, 2))
        self.assertEqual(self.library.listing()["folders"], [str(self.books)])

    def test_nonrecursive_scan_and_missing_folder(self):
        nested = self.books / "nested"
        nested.mkdir()
        (nested / "one.pdf").write_bytes(PDF)
        self.assertEqual(self.library.scan([str(self.books)], False)["added"], 0)
        self.assertEqual(len(self.library.scan([str(self.root / "missing")])["errors"]), 1)

    def test_catalog_changes_persist_and_rescan_preserves_them(self):
        path = self.books / "one.pdf"
        path.write_bytes(PDF)
        self.library.add(path)
        book_id = self.library.listing()["books"][0]["id"]
        self.library.update(book_id, {"title": "Mi título", "author": "Mi autora", "tags": [" Novela ", "Novela", ""], "notes": "Una nota", "favorite": True, "status": "reading", "progress": 42, "chapter": 1})
        self.library.scan([str(self.books)])
        reopened = Library(self.root / "data").get(book_id)
        self.assertEqual((reopened["title"], reopened["tags"], reopened["progress"], reopened["favorite"]), ("Mi título", ["Novela"], 42, True))
        self.assertEqual(self.library.update(book_id, {"status": "finished", "progress": 12})["progress"], 100)

    def test_deletion_and_missing_file_do_not_destroy_original(self):
        path = self.books / "one.pdf"
        path.write_bytes(PDF)
        self.library.add(path)
        book_id = self.library.listing()["books"][0]["id"]
        path.rename(self.books / "moved.pdf")
        self.assertFalse(self.library.get(book_id)["available"])
        self.library.delete(book_id)
        self.assertTrue((self.books / "moved.pdf").exists())
        self.assertEqual(self.library.listing()["books"], [])

    def test_upload_is_content_deduplicated_and_validated(self):
        self.assertEqual(self.library.import_file("../../Un libro.pdf", PDF)["added"], 1)
        self.assertEqual(self.library.import_file("otra copia.PDF", PDF)["skipped"], 1)
        book = self.library.listing()["books"][0]
        self.assertEqual(book["title"], "Un libro")
        self.assertEqual(Path(book["path"]).parent, self.library.uploads)
        with self.assertRaises(ValueError):
            self.library.import_file("fake.pdf", b"Not a PDF")
        with self.assertRaises(ValueError):
            self.library.import_file("program.exe", PDF)
        self.assertEqual(len(list(self.library.uploads.iterdir())), 1)

    def test_invalid_updates_are_rejected(self):
        self.library.import_file("test.pdf", PDF)
        book_id = self.library.listing()["books"][0]["id"]
        for changes in ({"title": " "}, {"progress": 101}, {"progress": "4"}, {"status": "invalid"}, {"tags": "wrong"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.library.update(book_id, changes)

    def test_epub_resource_size_limit(self):
        path = self.books / "large.epub"
        make_epub(path)
        with zipfile.ZipFile(path) as archive, patch("library.MAX_EPUB_ENTRY", 3):
            with self.assertRaises(ValueError):
                read_epub_entry(archive, "OEBPS/one.xhtml")

    def test_epub_chapters_preserve_reading_order_and_unicode_paths(self):
        path = self.books / "book.epub"
        make_epub(path)
        chapters = epub_chapters(path)
        self.assertEqual([chapter["title"] for chapter in chapters], ["Primer capítulo", "Segundo capítulo"])
        self.assertEqual(chapters[1]["path"], "OEBPS/capítulo.xhtml")

    def test_search_filters_accents_tags_and_status(self):
        (self.books / "Árbol.pdf").write_bytes(PDF)
        make_epub(self.books / "other.epub")
        self.library.scan([str(self.books)])
        books = self.library.listing()["books"]
        pdf = next(book for book in books if book["format"] == "PDF")
        self.library.update(pdf["id"], {"tags": ["Ficción"], "favorite": True, "status": "reading"})
        books = self.library.listing()["books"]
        self.assertEqual(len(filter_books(books, query="arbol")), 1)
        self.assertEqual(len(filter_books(books, query="ficcion", view="favorites")), 1)
        self.assertEqual(len(filter_books(books, query="ficcion", file_format="EPUB")), 0)
        self.assertEqual(len(filter_books(books, view="reading")), 1)


if __name__ == "__main__":
    unittest.main()
