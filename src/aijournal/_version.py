from __future__ import annotations

from pathlib import Path

__version__ = ""

if not __version__:
    try:
        import versioningit
    except ImportError:  # pragma: no cover
        import importlib.metadata

        __version__ = importlib.metadata.version("aijournal")
    else:
        PROJECT_DIR = Path(__file__).resolve().parent.parent
        __version__ = versioningit.get_version(project_dir=PROJECT_DIR)
