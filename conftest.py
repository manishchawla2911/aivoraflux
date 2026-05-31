"""Pytest config — ensure the project root is on sys.path so tests can import top-level packages."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
