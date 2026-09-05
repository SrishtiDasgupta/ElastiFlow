"""
Repository-relative paths.

SRC_MAIN is the directory that holds config/, scheduler/, scripts/, workflow/ and
the runners. Every default file location in the framework is derived from it, so
the code runs from any checkout location without editing source.
"""
from pathlib import Path

SRC_MAIN = Path(__file__).resolve().parents[1]
REPO_ROOT = SRC_MAIN.parents[1]
