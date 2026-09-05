"""
Repository-relative paths.

PACKAGE_DIR is the elastiflow package directory, which holds config/, scheduler/,
scripts/, workflow/ and the runners. Every default file location in the framework is derived from it, so
the code runs from any checkout location without editing source.
"""
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parent
