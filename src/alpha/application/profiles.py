"""Local profiles: separate data folders on this computer (ADR-021).

A profile is a folder with its own database, stored GPX copies, and tiles.
There are no accounts or logins: ALPHA stays single-user and local. The
``default`` profile is the data folder itself (``data/local``), so existing
data stays where it is; every other profile lives in
``<data folder>/profiles/<name>/``.

A profile marked ``synthetic`` may contain generated activities, and its
personal speed fits use them. Every output from such a fit says so. Other
profiles never fit on synthetic activities.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROFILE = "default"
PROFILE_FILE = "profile.json"
# GPX activity type of generated hikes (scripts/synthetic_hikes.py). Only a
# synthetic profile fits on it; everywhere else it is excluded by type.
SYNTHETIC_ACTIVITY_TYPE = "synthetic-hiking"
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


class ProfileError(ValueError):
    """An invalid or unknown profile."""


@dataclass(frozen=True)
class Profile:
    name: str
    data_dir: Path  # database and stored GPX files of this profile
    synthetic: bool  # generated activities allowed and used in fits
    dem_dir: Path  # terrain tiles: shared by all profiles (not personal)

    @property
    def exists(self) -> bool:
        return self.name == DEFAULT_PROFILE or (self.data_dir / PROFILE_FILE).is_file()


def profile_dir(base: Path, name: str) -> Path:
    if name == DEFAULT_PROFILE:
        return base
    if not _NAME.fullmatch(name):
        raise ProfileError(
            f"invalid profile name {name!r}: lowercase letters, digits, '-', '_'"
        )
    return base / "profiles" / name


def open_profile(base: Path, name: str = DEFAULT_PROFILE) -> Profile:
    """The profile ``name`` under ``base``. Unknown non-default profiles are
    an error, so a typo never silently creates an empty workspace."""
    directory = profile_dir(base, name)
    settings: dict[str, object] = {}
    file = directory / PROFILE_FILE
    if file.is_file():
        settings = json.loads(file.read_text(encoding="utf-8"))
    elif name != DEFAULT_PROFILE:
        raise ProfileError(
            f"no profile {name!r}; create it with 'alpha profile create {name}'"
        )
    return Profile(
        name, directory, bool(settings.get("synthetic", False)), base / "dem"
    )


def create_profile(base: Path, name: str, *, synthetic: bool) -> Profile:
    if name == DEFAULT_PROFILE:
        raise ProfileError("the default profile always exists")
    directory = profile_dir(base, name)
    if (directory / PROFILE_FILE).is_file():
        raise ProfileError(f"profile {name!r} already exists")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / PROFILE_FILE).write_text(
        json.dumps({"synthetic": synthetic}, indent=2), encoding="utf-8"
    )
    return Profile(name, directory, synthetic, base / "dem")


def list_profiles(base: Path) -> list[Profile]:
    profiles = [open_profile(base)]
    root = base / "profiles"
    if root.is_dir():
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            if (directory / PROFILE_FILE).is_file() and _NAME.fullmatch(directory.name):
                profiles.append(open_profile(base, directory.name))
    return profiles
