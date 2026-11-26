"""Metadatos, miniaturas y texto local usando EPUB y Poppler."""

from html.parser import HTMLParser
import os
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET


def poppler(program, arguments, timeout=45):
    executable = shutil.which(program)
    if not executable:
        raise ValueError(f"Falta {program}. Instalá Poppler para procesar PDF.")
    result = subprocess.run([executable, *map(str, arguments)], capture_output=True, timeout=timeout, env={**os.environ, "LC_ALL": "C"})
    if result.returncode:
        raise ValueError("No se pudo procesar el PDF. Puede estar dañado o protegido con contraseña.")
    return result.stdout


def pdf_info(path):
    text = poppler("pdfinfo", ["-enc", "UTF-8", path]).decode("utf-8", "replace")
    return {match[1].strip(): match[2].strip() for match in re.finditer(r"^([^:\r\n]+):[ \t]*([^\r\n]*)", text, re.MULTILINE)}


def pdf_pages(path):
    text = poppler("pdftotext", ["-enc", "UTF-8", "-layout", path, "-"], timeout=120).decode("utf-8", "replace")
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return [page.strip() for page in pages]


def pdf_words(path, position):
    data = poppler("pdftotext", ["-f", position + 1, "-l", position + 1, "-enc", "UTF-8", "-bbox-layout", path, "-"])
    return parse_pdf_words(data)


def parse_pdf_words(data):
    root = ET.fromstring(data)
    page = root.find(".//{*}page")
    if page is None:
        return []
    width, height = float(page.get("width")), float(page.get("height"))
    words = []
    for line_index, line in enumerate(page.findall(".//{*}line")):
        for word in line.findall("{*}word"):
            words.append({
                "text": "".join(word.itertext()), "line": line_index,
                "rect": [float(word.get("xMin")) / width, float(word.get("yMin")) / height,
                         (float(word.get("xMax")) - float(word.get("xMin"))) / width,
                         (float(word.get("yMax")) - float(word.get("yMin"))) / height],
            })
    return words


def pdf_cover(path, output):
    output = Path(output)
    prefix = output.with_suffix("")
    poppler("pdftoppm", ["-f", 1, "-l", 1, "-singlefile", "-scale-to", 420, "-png", path, prefix])
    return str(output) if output.is_file() else ""


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self.hidden += 1
        if tag in {"p", "div", "br", "h1", "h2", "h3", "li", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_text(content):
    parser = PlainText()
    parser.feed(content)
    return re.sub(r"[ \t]+", " ", "".join(parser.parts)).strip()
