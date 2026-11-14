"""Loads config/thresholds.toml. The only place code reads a threshold from."""
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config" / "thresholds.toml"


def load(path=None) -> dict:
    with open(path or DEFAULT_PATH, "rb") as f:
        return tomllib.load(f)
