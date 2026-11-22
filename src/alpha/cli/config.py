"""Read settings for entry points. Secrets are read, never printed or logged."""

import os
from pathlib import Path

OPENTOPOGRAPHY_KEY = "OPENTOPOGRAPHY_API_KEY"


def read_env_file(path: Path) -> dict[str, str]:
    """Minimal ``.env`` reader: ``NAME=value`` lines, ``#`` comments, optional
    quotes. Missing file -> empty dict."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[name] = value
    return values


def opentopography_api_key(env_file: Path = Path(".env")) -> str | None:
    """Environment variable first, then ``.env`` in the working directory."""
    key = os.environ.get(OPENTOPOGRAPHY_KEY) or read_env_file(env_file).get(
        OPENTOPOGRAPHY_KEY
    )
    return key or None
