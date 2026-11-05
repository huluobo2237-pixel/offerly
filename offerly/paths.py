"""Application paths are resolved from this checkout, never the old project."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
