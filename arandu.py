#!/usr/bin/env python3
"""Punto de entrada de Arandu, una aplicación nativa de escritorio."""

import argparse
from pathlib import Path
import sys

from library import ROOT, Library


def main():
    parser = argparse.ArgumentParser(description="Arandu · Tu biblioteca de escritorio.")
    parser.add_argument("--data-dir", type=Path, help="Carpeta del catálogo SQLite.")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("files", nargs="*", type=Path, help="PDF y EPUB que querés abrir con Arandu.")
    args = parser.parse_args()
    try:
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QApplication
        from branding import app_icon
        from desktop import MainWindow
        from themes import DEFAULT_THEME, THEMES
        from desktop_runtime import DesktopInstance, FileOpenController, desktop_data_dir
    except ImportError:
        parser.exit(1, "Falta PyQt5. Instalá las dependencias con: python3 -m pip install -r requirements.txt\n")
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Arandu")
    app.setDesktopFileName("arandu")
    app.setOrganizationName("Arandu")
    app.setWindowIcon(app_icon(THEMES[DEFAULT_THEME]))
    app.setStyle("Fusion")
    args.data_dir = args.data_dir or desktop_data_dir(ROOT, getattr(sys, "frozen", False))
    instance = DesktopInstance(args.data_dir, app)
    if instance.forward(args.files):
        return 0
    instance.listen()
    try:
        window = MainWindow(Library(args.data_dir))
    except OSError as exc:
        parser.exit(1, f"No se pudo abrir el catálogo: {exc}\n")
    window.show()
    controller = FileOpenController(window)
    instance.requested.connect(controller.request)
    if args.files:
        controller.request(args.files)
    if args.smoke_test:
        controller.start_smoke_test(app)
    print("Arandu · Aplicación de escritorio iniciada.", flush=True)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
