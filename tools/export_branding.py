"""Export SVG logo, symbol and app icon for each of Arandu's six themes."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from branding import ASSETS, logo_svg
from themes import DEFAULT_THEME, THEMES


def main():
    output = ASSETS / "themes"
    output.mkdir(parents=True, exist_ok=True)
    for theme in THEMES.values():
        for variant in ("logo", "mark", "icon"):
            (output / f"arandu-{theme.id}-{variant}.svg").write_bytes(logo_svg(theme, variant))
    (ASSETS.parent / "arandu.svg").write_bytes(logo_svg(THEMES[DEFAULT_THEME], "icon"))
    print(f"Logos de los seis temas guardados en {output}")


if __name__ == "__main__":
    main()
