#!/usr/bin/env python3
"""Construye, verifica e instala Arandu para el usuario actual en Linux."""

import argparse
from contextlib import closing
import json
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("pdfinfo", "pdftoppm", "pdftotext", "tesseract")


def run(arguments, **kwargs):
    return subprocess.run([str(value) for value in arguments], check=True, **kwargs)


def build():
    missing = [program for program in TOOLS if not shutil.which(program)]
    if missing:
        raise SystemExit("Faltan herramientas para incluir en el paquete: " + ", ".join(missing))
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--noupx", "--onedir", "--name", "arandu", "--distpath", ROOT / "dist", "--workpath", ROOT / ".runtime" / "pyinstaller", "--specpath", ROOT / ".runtime", "--add-data", str(ROOT / "assets") + ":assets", "--runtime-hook", ROOT / "packaging" / "runtime_hook.py", "--hidden-import", "PyQt5.QtNetwork"]
    for program in TOOLS:
        command += ["--add-binary", str(Path(shutil.which(program)).resolve()) + ":support/bin"]
    command.append(ROOT / "arandu.py")
    run(command, cwd=ROOT)
    bundle = ROOT / "dist" / "arandu"
    manifest = {"sources": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in ROOT.glob("*.py")}, "python": sys.version.split()[0], "platform": "Linux x86_64", "native_tools": list(TOOLS)}
    (bundle / "BUILD-INFO.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return bundle


def sample_books(folder):
    pdf = folder / "Prueba local.pdf"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 600] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>", None, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    content = b"BT /F1 24 Tf 50 500 Td (Arandu local) Tj ET\n"
    objects[3] = b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream"
    output, offsets = bytearray(b"%PDF-1.4\n"), [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + value + b"\nendobj\n")
    start = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    pdf.write_bytes(output)
    epub = folder / "Lectura de prueba.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        archive.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Prueba del ejecutable</dc:title><dc:creator>Arandu</dc:creator></metadata><manifest><item id="one" href="one.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="one"/></spine></package>')
        archive.writestr("one.xhtml", '<html><body><h1>Lectura local</h1><p>La apertura EPUB funciona.</p></body></html>')
    return pdf, epub


def smoke(bundle):
    executable = bundle / "arandu"
    with tempfile.TemporaryDirectory(prefix="arandu-smoke-") as value:
        folder = Path(value)
        pdf, epub = sample_books(folder)
        environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "ARANDU_SMOKE_DELAY_MS": "1500"}
        process = subprocess.Popen([str(executable), "--data-dir", str(folder / "catalog"), "--smoke-test", str(pdf)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
        first = process.stdout.readline()
        if "iniciada" not in first:
            output, errors = process.communicate(timeout=35)
            raise RuntimeError("El ejecutable no inició: " + first + output + errors)
        # A second launch forwards its EPUB to the existing window.
        secondary = run([executable, "--data-dir", folder / "catalog", epub], capture_output=True, text=True, env=environment, timeout=20)
        output, errors = process.communicate(timeout=35)
        print(first + output, end="")
        if process.returncode or '"smoke_test": "ok"' not in output or '"books": 2' not in output or '"readers": 2' not in output:
            raise RuntimeError("Falló la apertura PDF/EPUB o la instancia única: " + output + errors + secondary.stderr)
        with closing(sqlite3.connect(folder / "catalog" / "library.sqlite3")) as db:
            if db.execute("SELECT count(*) FROM books").fetchone()[0] != 2:
                raise RuntimeError("No se conservaron los dos libros de prueba.")
            if not db.execute("SELECT 1 FROM book_text WHERE content LIKE '%Arandu local%'").fetchone():
                raise RuntimeError("Poppler incluido no produjo texto PDF.")
    # Exercise the bundled native tools, rather than the user's PATH.
    internal = bundle / "_internal"
    native_environment = {**os.environ, "LD_LIBRARY_PATH": str(internal), "PATH": str(internal / "support" / "bin")}
    for program in TOOLS:
        arguments = [internal / "support" / "bin" / program, "--version" if program == "tesseract" else "-v"]
        run(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=native_environment, timeout=15)
    return executable


def migrate_catalog(data_dir):
    source = ROOT / ".arandu"
    if data_dir.exists() or not (source / "library.sqlite3").is_file():
        return False
    staging = data_dir.with_name(data_dir.name + ".migrating")
    if staging.exists():
        raise RuntimeError("Existe una migración pendiente en " + str(staging))
    staging.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, staging, ignore=shutil.ignore_patterns("library.sqlite3", "library.sqlite3-wal", "library.sqlite3-shm"))
    with closing(sqlite3.connect(source / "library.sqlite3")) as original, closing(sqlite3.connect(staging / "library.sqlite3")) as copied:
        original.backup(copied)
        copied.row_factory = sqlite3.Row
        with copied:
            columns = {row[1] for row in copied.execute("PRAGMA table_info(books)")}
            for column in {"path", "cover_path", "custom_cover"} & columns:
                for row in copied.execute(f"SELECT id,{column} FROM books").fetchall():
                    path = Path(row[column]) if row[column] else None
                    if path and path.is_relative_to(source):
                        copied.execute(f"UPDATE books SET {column}=? WHERE id=?", (str(data_dir / path.relative_to(source)), row["id"]))
            if copied.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_exclusions'").fetchone():
                for row in copied.execute("SELECT path FROM source_exclusions").fetchall():
                    path = Path(row[0])
                    if path.is_relative_to(source):
                        copied.execute("UPDATE source_exclusions SET path=? WHERE path=?", (str(data_dir / path.relative_to(source)), str(path)))
    staging.rename(data_dir)
    return True


def desktop_quote(path):
    return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$') + '"'


def install(bundle):
    previous_handlers = {mime: subprocess.run(["xdg-mime", "query", "default", mime], capture_output=True, text=True, check=True).stdout.strip() for mime in ("application/pdf", "application/epub+zip")}
    data_home = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    prefix = Path.home() / ".local" / "opt" / "arandu"
    staged = prefix.with_name("arandu.new")
    if staged.exists():
        shutil.rmtree(staged)
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundle, staged)
    old = prefix.with_name("arandu.previous")
    if old.exists():
        shutil.rmtree(old)
    if prefix.exists():
        prefix.rename(old)
    staged.rename(prefix)
    executable = prefix / "arandu"
    commands = Path.home() / ".local" / "bin"
    commands.mkdir(parents=True, exist_ok=True)
    launcher = commands / "arandu"
    if launcher.is_symlink() or launcher.exists():
        launcher.unlink()
    launcher.symlink_to(executable)
    applications = data_home / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    icons = data_home / "icons" / "hicolor" / "scalable" / "apps"
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "assets" / "branding" / "themes" / "arandu-papel-icon.svg", icons / "arandu.svg")
    entry = applications / "arandu.desktop"
    entry.write_text("\n".join(("[Desktop Entry]", "Version=1.0", "Type=Application", "Name=Arandu", "GenericName=Biblioteca de libros", "Comment=Organizá y leé tus PDF y EPUB locales", "Exec=" + desktop_quote(executable) + " %F", "TryExec=" + str(executable), "Icon=arandu", "Terminal=false", "Categories=Office;Viewer;", "MimeType=application/pdf;application/epub+zip;", "StartupNotify=true", "StartupWMClass=Arandu", "Keywords=libros;lectura;biblioteca;PDF;EPUB;", "")), encoding="utf-8")
    run(["desktop-file-validate", entry])
    run(["update-desktop-database", applications])
    for mime, handler in previous_handlers.items():
        if handler and handler != "arandu.desktop":
            run(["xdg-mime", "default", handler, mime])
    if shutil.which("gtk-update-icon-cache"):
        run(["gtk-update-icon-cache", "--force", "--ignore-theme-index", data_home / "icons" / "hicolor"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    migrated = migrate_catalog(data_home / "arandu")
    result = {"executable": str(executable), "command": str(launcher), "desktop_entry": str(entry), "data_dir": str(data_home / "arandu"), "catalog_copied": migrated, "mime_types": ["application/pdf", "application/epub+zip"]}
    (ROOT / ".runtime" / "desktop-install.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--install", action="store_true")
    arguments = parser.parse_args()
    bundle = ROOT / "dist" / "arandu" if arguments.skip_build else build()
    smoke(bundle)
    if arguments.install:
        install(bundle)


if __name__ == "__main__":
    main()
