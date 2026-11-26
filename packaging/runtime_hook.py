"""Herramientas nativas incluidas en el paquete standalone."""

import os
from pathlib import Path
import sys

bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
os.environ["PATH"] = str(bundle / "support" / "bin") + os.pathsep + os.environ.get("PATH", "")
