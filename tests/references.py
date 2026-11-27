"""Personal reference files, by neutral key (plan_1, activity_a, ...).

The files themselves and the mapping from key to file name live in
``data/local/`` (git-ignored), so neither the GPX nor its name is committed.
``data/references.example.json`` shows the format. A test that needs a
reference file is skipped when the mapping or the file is absent.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "data" / "local"
MAPPING = LOCAL / "references.json"
GOLDEN = LOCAL / "golden"


def _mapping() -> dict[str, str]:
    if not MAPPING.is_file():
        return {}
    data = json.loads(MAPPING.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()}


def reference_path(key: str) -> Path | None:
    """The file for ``key``, or ``None`` if it is not available here."""
    name = _mapping().get(key)
    if name is None:
        return None
    path = LOCAL / name
    return path if path.is_file() else None


def reference(key: str) -> Path:
    """The file for ``key``; skips the calling test if it is not available."""
    path = reference_path(key)
    if path is None:
        pytest.skip(f"reference {key} not available (personal data, {MAPPING.name})")
    return path
