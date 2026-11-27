"""Committed files must not name personal hikes or places (CLAUDE.md §8).

The words to look for come from the real file names in the git-ignored
``data/local/references.json``, so the names themselves are never committed.
Without that file (a fresh clone) the test is skipped.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.references import MAPPING, ROOT

# Words in reference file names that are not identifying on their own.
GENERIC = {"input", "input2", "activity", "track", "route", "planned"}


def _personal_words() -> set[str]:
    if not MAPPING.is_file():
        pytest.skip("no data/local/references.json (personal data)")
    names = json.loads(MAPPING.read_text(encoding="utf-8")).values()
    words = set()
    for name in names:
        for word in re.split(r"[^A-Za-zÀ-ÿ]+", Path(str(name)).stem):
            if len(word) >= 5 and word.lower() not in GENERIC:
                words.add(word.lower())
    return words


def _committed_text_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    suffixes = {".md", ".py", ".toml", ".yml", ".yaml", ".json", ".html", ".txt"}
    return [
        ROOT / f for f in listed if Path(f).suffix in suffixes and (ROOT / f).is_file()
    ]


def test_no_personal_names_in_committed_files() -> None:
    words = _personal_words()
    assert words, "the mapping should name at least one file"
    found = []
    for path in _committed_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        hits = sorted(w for w in words if re.search(rf"\b{re.escape(w)}\b", text))
        if hits:
            found.append(f"{path.relative_to(ROOT)}: {len(hits)} personal name(s)")
    # Report files and counts only, never the names themselves.
    assert not found, "\n".join(found)
