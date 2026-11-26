"""Seis paletas clásicas y tipografías locales para la interfaz editorial."""

from dataclasses import asdict, dataclass, fields, replace
import json
from pathlib import Path
import re

from PyQt5.QtGui import QColor, QFont, QFontDatabase, QPalette
from PyQt5.QtWidgets import QApplication, QLineEdit, QPlainTextEdit


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    dark: bool
    description: str
    background: str
    surface: str
    inset: str
    text: str
    muted: str
    border: str
    accent: str
    on_accent: str
    sidebar: str
    sidebar_text: str
    sidebar_muted: str
    selected: str
    hover: str
    disabled: str
    success: str
    error: str


THEMES = {theme.id: theme for theme in (
    Theme("papel", "Papel", False, "Marfil y granate, como una primera edición.",
          "#f3eee3", "#fffaf1", "#eae1d3", "#2b241f", "#6b594c", "#a39582",
          "#7c2835", "#fff8eb", "#50202b", "#f8ead6", "#ddc6b7", "#e5d7c5", "#e9dfd0", "#8e8275", "#28543f", "#9c302b"),
    Theme("porcelana", "Porcelana", False, "Blanco de archivo y azul de tinta.",
          "#f4f4ef", "#ffffff", "#e6ebed", "#1e2d37", "#516473", "#99a8ad",
          "#25475e", "#ffffff", "#1d3344", "#eff3ec", "#c0cfda", "#dbe5e8", "#e5ecee", "#82919a", "#28543f", "#a7392f"),
    Theme("lino", "Lino", False, "Papel natural y verde de encuadernación.",
          "#eeeee2", "#fafbee", "#e0e5d4", "#24392e", "#52644f", "#93a081",
          "#315841", "#fffdf2", "#273f31", "#f3f2dc", "#c7d1b7", "#d5dec4", "#dce3cb", "#809078", "#2a573e", "#903a2a"),
    # Paleta 1: códigos indicados en la primera referencia del usuario.
    Theme(
        id="nogal", name="Nogal", dark=True,
        description="Paleta 1: negro cálido, nogal, arena y marfil.",
        background="#0a0a0c", surface="#1f1c1a", inset="#1f1c1a",
        text="#f3efe9", muted="#c8b29a", border="#5c5048",
        accent="#c8b29a", on_accent="#0a0a0c",
        sidebar="#0a0a0c", sidebar_text="#f3efe9", sidebar_muted="#c8b29a",
        selected="#1f1c1a", hover="#1f1c1a", disabled="#5c5048",
        success="#c8b29a", error="#f3efe9",
    ),
    # Paleta 2: códigos indicados en la segunda referencia del usuario.
    Theme(
        id="grafito", name="Grafito", dark=True,
        description="Paleta 2: carbón frío, grafito y gris plata.",
        background="#101216", surface="#2a2f37", inset="#2a2f37",
        text="#f3f5f8", muted="#b7bec8", border="#474f5b",
        accent="#b7bec8", on_accent="#101216",
        sidebar="#101216", sidebar_text="#f3f5f8", sidebar_muted="#b7bec8",
        selected="#2a2f37", hover="#2a2f37", disabled="#474f5b",
        success="#b7bec8", error="#f3f5f8",
    ),
    # Paleta 3: colores muestreados del centro de las cinco franjas del PNG.
    Theme(
        id="tinta", name="Tinta", dark=True,
        description="Paleta 3: azul noche, azul de tinta y azul bruma.",
        background="#0e1a2b", surface="#1b3454", inset="#1b3454",
        text="#a4b7d7", muted="#a4b7d7", border="#5c7aa3",
        accent="#a4b7d7", on_accent="#0e1a2b",
        sidebar="#0e1a2b", sidebar_text="#a4b7d7", sidebar_muted="#a4b7d7",
        selected="#274472", hover="#274472", disabled="#5c7aa3",
        success="#a4b7d7", error="#a4b7d7",
    ),
)}
DEFAULT_THEME = "papel"
CUSTOM_THEME_FILE = "themes.json"
EDITABLE_COLORS = (
    ("background", "Fondo"), ("surface", "Superficies"),
    ("text", "Texto"), ("muted", "Texto secundario"),
    ("accent", "Acento"), ("selected", "Selección"), ("border", "Bordes"),
)
COLOR_FIELDS = tuple(field.name for field in fields(Theme) if field.name not in {"id", "name", "dark", "description"})


def load_fonts():
    app = QApplication.instance()
    if app.property("aranduFontsLoaded"):
        return
    folder = Path(__file__).resolve().parent / "assets" / "fonts"
    for family in ("Montserrat", "Karla"):
        for weight in ("Regular", "SemiBold", "Bold"):
            font_id = QFontDatabase.addApplicationFont(str(folder / f"{family}-{weight}.ttf"))
            if font_id < 0 or family not in QFontDatabase.applicationFontFamilies(font_id):
                raise RuntimeError(f"No se pudo cargar la tipografía local {family} {weight}.")
    app.setFont(QFont("Karla", 11))
    app.setProperty("aranduFontsLoaded", True)


def ui_scale():
    app = QApplication.instance()
    return (app.property("aranduTextScale") or 100) / 100 if app else 1


def scaled_px(value):
    return round(value * ui_scale())


def font(family="Karla", size=11, weight=QFont.Normal, scaled=True):
    result = QFont(family, size, weight)
    if scaled:
        result.setPointSizeF(size * ui_scale())
    return result


def palette(theme):
    result = QPalette()
    for role, value in (
        (QPalette.Window, theme.background), (QPalette.WindowText, theme.text),
        (QPalette.Base, theme.surface), (QPalette.AlternateBase, theme.inset),
        (QPalette.Text, theme.text), (QPalette.Button, theme.surface),
        (QPalette.ButtonText, theme.text), (QPalette.Highlight, theme.accent),
        (QPalette.HighlightedText, theme.on_accent), (QPalette.Link, theme.accent),
        (QPalette.ToolTipBase, theme.sidebar), (QPalette.ToolTipText, theme.sidebar_text),
        (QPalette.Light, theme.surface), (QPalette.Mid, theme.border),
        (QPalette.Dark, theme.inset), (QPalette.Shadow, theme.inset),
    ):
        result.setColor(role, QColor(value))
    result.setColor(QPalette.Disabled, QPalette.Text, QColor(theme.disabled))
    result.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(theme.disabled))
    result.setColor(QPalette.PlaceholderText, QColor(theme.muted))
    return result


def apply_theme(theme):
    app = QApplication.instance()
    load_fonts()
    app.setPalette(palette(theme))
    css = re.sub(r"(font-size:\s*|font:\s*\d+\s+)(\d+)px", lambda match: match[1] + str(scaled_px(int(match[2]))) + "px", stylesheet(theme))
    app.setStyleSheet(css)
    app.setProperty("aranduTheme", theme.id)
    app.arandu_theme = theme


def current_theme():
    app = QApplication.instance()
    return getattr(app, "arandu_theme", THEMES[DEFAULT_THEME]) if app else THEMES[DEFAULT_THEME]


def fix_placeholder_colors(root):
    # Qt 5's stylesheet polish can restore a half-transparent placeholder.
    # Apply an opaque, contrast-checked color after the widgets are shown.
    color = QColor(current_theme().muted)
    for field in (*root.findChildren(QLineEdit), *root.findChildren(QPlainTextEdit)):
        field_palette = field.palette()
        field_palette.setColor(QPalette.PlaceholderText, color)
        field.setPalette(field_palette)


def stylesheet(t):
    return f"""
    QWidget {{color: {t.text}; font-family: 'Karla'; font-size: 16px;}}
    QMainWindow, QDialog, QWidget#content {{background: {t.background};}}
    QLabel {{background: transparent;}}
    QFrame#sidebar {{background: {t.background}; border: none; border-right: 1px solid {t.inset};}}
    QFrame#sidebar QLabel {{color: {t.muted}; font-size: 14px;}}
    QFrame#sidebar QLabel#sidebarHeading {{font: 500 13px 'Karla'; padding: 22px 0 8px;}}
    QFrame#catalogToolbar {{border: none; border-bottom: 1px solid {t.border};}}
    QFrame#viewSwitcher {{border: 1px solid {t.border};}}
    QPushButton#viewSegment {{border: none; border-bottom: 2px solid transparent; border-radius: 0; padding: 6px 11px; color: {t.muted};}}
    QPushButton#viewSegment:checked {{color: {t.text}; background: {t.inset}; border-bottom-color: {t.accent};}}
    QPushButton#viewSegment:focus {{border: 2px solid {t.accent}; padding: 4px 9px;}}
    QPushButton#viewSegment:disabled {{color: {t.disabled};}}
    QPushButton#sourceButton {{text-align: left; padding: 6px 4px; font-size: 13px; border-top: 1px solid {t.border}; border-radius: 0;}}
    QPushButton#sourceButton:focus {{border: 2px solid {t.accent}; padding: 5px 3px;}}
    QPushButton#quietButton {{color: {t.muted};}}
    QPushButton#quietButton:focus {{color: {t.text};}}
    QLabel#title {{font: 600 26px 'Montserrat'; color: {t.text};}}
    QLabel#subtitle {{font-size: 16px; color: {t.muted};}}
    QLabel#muted, QLabel#readerMessage {{font-size: 14px; color: {t.muted};}}
    QLabel#collectionLabel {{font: 600 14px 'Montserrat';}}
    QLabel#readerTitle {{font: 600 18px 'Montserrat';}}
    QLabel#readerMessage {{padding: 10px 22px; background: {t.inset};}}
    QPushButton {{background: transparent; color: {t.text}; border: 1px solid transparent; border-radius: 2px; padding: 8px 12px; min-height: 20px; font-size: 14px;}}
    QPushButton:hover {{background: {t.hover};}}
    QPushButton:pressed, QPushButton:checked {{background: {t.inset}; color: {t.text};}}
    QPushButton:focus {{border: 2px solid {t.accent}; padding: 7px 11px;}}
    QPushButton:disabled {{color: {t.disabled};}}
    QPushButton#primary {{background: {t.accent}; border-color: {t.accent}; color: {t.on_accent}; font-weight: 600;}}
    QPushButton#primary:hover {{border: 2px solid {t.text}; padding: 7px 11px;}}
    QPushButton#primary:focus {{border: 2px solid {t.text}; padding: 7px 11px;}}
    QPushButton#primary:disabled {{background: {t.inset}; color: {t.disabled}; border-color: {t.inset};}}
    QPushButton#danger {{color: {t.error}; border-color: {t.error};}}
    QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{background: {t.surface}; color: {t.text}; border: 1px solid transparent; border-bottom: 1px solid {t.border}; border-radius: 0; padding: 8px 10px; min-height: 20px; selection-background-color: {t.accent}; selection-color: {t.on_accent};}}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{border: 2px solid {t.accent}; padding: 7px 9px;}}
    QLineEdit#catalogSearch {{background: transparent; border: 1px solid {t.border}; padding: 8px 12px;}}
    QLineEdit#catalogSearch:focus {{border: 2px solid {t.accent}; padding: 7px 11px;}}
    QComboBox#shelfChooser {{background: transparent; border-bottom-color: transparent; padding-left: 2px;}}
    QComboBox#shelfChooser:focus {{border: 2px solid {t.accent}; padding-left: 1px;}}
    QComboBox {{padding-right: 22px; font-size: 14px;}}
    QComboBox::drop-down {{width: 20px; border: 0;}}
    QComboBox QAbstractItemView {{background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; selection-background-color: {t.accent}; selection-color: {t.on_accent};}}
    QComboBox#themeChooser {{background: transparent; border-bottom-color: transparent; font-size: 14px; padding: 6px 22px 6px 9px;}}
    QComboBox#themeChooser:focus {{border: 2px solid {t.accent}; padding: 5px 21px 5px 8px;}}
    QListWidget#books {{background: transparent; border: none; outline: none;}}
    QFrame#selectionBar {{background: transparent; border: none; border-top: 1px solid {t.inset};}}
    QFrame#emptyPanel {{background: transparent; border: none;}}
    QScrollArea#themeFields, QWidget#themeForm {{background: transparent; border: none;}}
    QLabel#themeValidation {{color: {t.muted}; font-size: 14px;}}
    QLabel#emptyTitle {{font: 600 22px 'Montserrat'; color: {t.text};}}
    QLabel#emptyBody {{font-size: 16px; color: {t.muted};}}
    QFrame#notice {{background: {t.inset}; border: none; border-left: 2px solid {t.accent};}}
    QFrame#notice[state="success"] {{border-left-color: {t.success};}}
    QFrame#notice[state="error"] {{border-left-color: {t.error};}}
    QLabel#noticeText {{font-size: 14px;}}
    QProgressBar {{border: none; background: {t.border}; max-height: 3px; min-height: 3px;}}
    QProgressBar::chunk {{background: {t.accent};}}
    QProgressBar#goalProgress {{min-height: 24px; max-height: 24px; color: {t.text}; background: {t.inset}; border: 1px solid {t.border}; text-align: center;}}
    QProgressBar#goalProgress::chunk {{background: {t.selected};}}
    QTextBrowser#epubPage {{background: {t.surface}; color: {t.text}; border: none; padding: 18px 32px; selection-background-color: {t.accent}; selection-color: {t.on_accent};}}
    QScrollArea#pdfScroll {{border: none; background: {t.inset};}}
    QScrollArea#pdfScroll QLabel {{background: {t.inset}; color: {t.muted};}}
    QMenuBar {{background: {t.background}; color: {t.text}; padding: 4px 12px; font-size: 14px;}}
    QMenuBar::item:selected {{background: {t.selected};}}
    QMenu {{background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; padding: 6px;}}
    QMenu::item {{padding: 9px 25px;}}
    QMenu::item:selected {{background: {t.accent}; color: {t.on_accent};}}
    QMenu::item:disabled {{color: {t.disabled};}}
    QStatusBar {{color: {t.muted}; background: {t.background}; font-size: 12px;}}
    QToolTip {{background: {t.sidebar}; color: {t.sidebar_text}; border: 1px solid {t.border}; padding: 7px;}}
    QCheckBox {{spacing: 8px;}}
    QCheckBox:focus {{color: {t.accent}; outline: 2px solid {t.accent};}}
    QCheckBox::indicator {{width: 18px; height: 18px;}}
    QToolButton {{color: {t.text}; background: transparent; border: 1px solid transparent; padding: 8px;}}
    QToolButton:hover {{background: {t.hover};}}
    QToolButton:focus {{border-color: {t.accent};}}
    QTreeWidget, QListWidget {{background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; selection-background-color: {t.selected}; selection-color: {t.text};}}
    QTabWidget::pane {{border: 1px solid {t.border}; background: {t.background};}}
    QTabBar::tab {{background: {t.background}; color: {t.muted}; border-bottom: 2px solid transparent; padding: 10px 16px;}}
    QTabBar::tab:selected {{color: {t.text}; border-bottom-color: {t.accent};}}
    QTabBar::tab:focus {{border: 2px solid {t.accent}; padding: 8px 14px;}}
    QHeaderView::section {{background: {t.inset}; color: {t.text}; border: none; padding: 8px;}}
    QScrollBar:vertical {{background: {t.inset}; width: 12px; margin: 0;}}
    QScrollBar::handle:vertical {{background: {t.border}; min-height: 30px;}}
    QScrollBar::handle:vertical:hover {{background: {t.accent};}}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{height: 0;}}
    QScrollBar:horizontal {{background: {t.inset}; height: 12px;}}
    QScrollBar::handle:horizontal {{background: {t.border}; min-width: 30px;}}
    """


def contrast_ratio(foreground, background):
    """WCAG luminance ratio, also used by the palette regression tests."""
    def luminance(color):
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        channels = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        return sum(channel * weight for channel, weight in zip(channels, (0.2126, 0.7152, 0.0722)))
    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def normalize_color(value):
    if not isinstance(value, str):
        raise ValueError("Ingresá un color hexadecimal, por ejemplo #1F1C1A.")
    value = value.strip().lower()
    if not value.startswith("#"):
        value = "#" + value
    if not re.fullmatch(r"#[0-9a-f]{6}", value):
        raise ValueError("Usá un color de seis dígitos, por ejemplo #1F1C1A.")
    return value


def contrast_issues(theme):
    checks = (
        ("Texto sobre el fondo", theme.text, theme.background),
        ("Texto sobre superficies", theme.text, theme.surface),
        ("Texto sobre selección", theme.text, theme.selected),
        ("Texto secundario sobre el fondo", theme.muted, theme.background),
        ("Texto secundario sobre superficies", theme.muted, theme.surface),
        ("Texto secundario sobre selección", theme.muted, theme.selected),
        ("Acento sobre el fondo", theme.accent, theme.background),
        ("Acento sobre superficies", theme.accent, theme.surface),
        ("Acento sobre selección", theme.accent, theme.selected),
        ("Texto del botón principal", theme.on_accent, theme.accent),
    )
    return [(label, contrast_ratio(foreground, background)) for label, foreground, background in checks if contrast_ratio(foreground, background) < 4.5]


def make_custom_theme(base, theme_id, name, colors):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 48:
        raise ValueError("Elegí un nombre de entre 1 y 48 caracteres.")
    if not re.fullmatch(r"custom-[a-z0-9-]{1,64}", theme_id):
        raise ValueError("El identificador del tema personalizado es inválido.")
    colors = {key: normalize_color(colors[key]) for key, _ in EDITABLE_COLORS}
    on_accent = base.on_accent
    if contrast_ratio(on_accent, colors["accent"]) < 4.5:
        on_accent = max(("#000000", "#ffffff"), key=lambda color: contrast_ratio(color, colors["accent"]))

    def status_color(preferred):
        return preferred if all(contrast_ratio(preferred, colors[key]) >= 4.5 for key in ("background", "surface", "selected")) else colors["text"]

    return replace(
        base, id=theme_id, name=name.strip(),
        description="Tema personalizado.",
        dark=contrast_ratio("#ffffff", colors["background"]) > contrast_ratio("#000000", colors["background"]),
        **colors,
        inset=base.inset if colors["surface"] == base.surface else colors["surface"],
        hover=base.hover if colors["selected"] == base.selected else colors["selected"],
        sidebar=colors["background"], sidebar_text=colors["text"], sidebar_muted=colors["muted"],
        on_accent=on_accent, disabled=colors["border"],
        success=status_color(base.success), error=status_color(base.error),
    )


def theme_from_dict(data):
    if not isinstance(data, dict) or set(data) != {field.name for field in fields(Theme)}:
        raise ValueError("Los datos del tema personalizado están incompletos.")
    if not isinstance(data["id"], str) or not re.fullmatch(r"custom-[a-z0-9-]{1,64}", data["id"]):
        raise ValueError("El identificador del tema personalizado es inválido.")
    if not isinstance(data["name"], str) or not 1 <= len(data["name"].strip()) <= 48:
        raise ValueError("El nombre del tema personalizado es inválido.")
    if type(data["dark"]) is not bool or not isinstance(data["description"], str):
        raise ValueError("Los datos del tema personalizado son inválidos.")
    theme = Theme(**{**data, "name": data["name"].strip(), **{field: normalize_color(data[field]) for field in COLOR_FIELDS}})
    if contrast_issues(theme):
        raise ValueError(f"El tema «{theme.name}» no tiene contraste suficiente.")
    return theme


def load_custom_themes(data_dir):
    path = Path(data_dir) / CUSTOM_THEME_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("No se pudo leer el archivo de temas personalizados.") from exc
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("themes"), list):
        raise ValueError("El archivo de temas personalizados tiene un formato desconocido.")
    result = {}
    names = {theme.name.casefold() for theme in THEMES.values()}
    for entry in data["themes"]:
        theme = theme_from_dict(entry)
        if theme.id in result or theme.name.casefold() in names:
            raise ValueError("El archivo contiene temas personalizados duplicados.")
        result[theme.id] = theme
        names.add(theme.name.casefold())
    return result


def save_custom_themes(data_dir, themes):
    entries = []
    names = {theme.name.casefold() for theme in THEMES.values()}
    for theme in themes.values():
        theme = theme_from_dict(asdict(theme))
        if theme.name.casefold() in names:
            raise ValueError("Ya existe un tema con ese nombre. Elegí otro nombre.")
        names.add(theme.name.casefold())
        entries.append(asdict(theme))
    path = Path(data_dir) / CUSTOM_THEME_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": 1, "themes": entries}, ensure_ascii=False, indent=2))
    temporary.replace(path)
