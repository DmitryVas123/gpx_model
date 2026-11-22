"""Storage of original GPX files, named by content hash (never modified)."""

from pathlib import Path, PurePosixPath


class FileStore:
    """Keeps an unmodified copy of each imported file under ``<root>/files/``.

    Paths returned and accepted are relative to ``root`` (POSIX style), so the
    data directory can be moved as a whole.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, sha256: str, data: bytes) -> tuple[str, bool]:
        """Store ``data``; return (relative path, whether a new file was written)."""
        relative = PurePosixPath("files") / f"{sha256}.gpx"
        target = self.root / relative
        if target.exists():
            return str(relative), False
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(target)  # atomic on the same volume
        return str(relative), True

    def read(self, relative: str) -> bytes:
        return (self.root / relative).read_bytes()

    def delete(self, relative: str) -> None:
        (self.root / relative).unlink(missing_ok=True)
