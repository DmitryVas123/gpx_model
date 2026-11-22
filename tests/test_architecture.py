"""Enforce the dependency rules from ARCHITECTURE.md §1 and CLAUDE.md §4."""

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "alpha"

# pyproj is used only for pure geodesic math (no I/O); see ARCHITECTURE.md §1.
DOMAIN_ALLOWED_THIRD_PARTY = {"numpy", "pyproj"}


def _python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _imports(path: Path) -> Iterator[tuple[str, int]]:
    """Yield (absolute-or-relative module name, relative level) for each import."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, 0
        elif isinstance(node, ast.ImportFrom):
            yield node.module or "", node.level


def test_source_tree_exists() -> None:
    assert (SRC / "domain").is_dir()
    assert (SRC / "gpx").is_dir()


@pytest.mark.parametrize("path", _python_files(SRC / "domain"), ids=lambda p: p.name)
def test_domain_imports_only_stdlib_numpy_and_domain(path: Path) -> None:
    for module, level in _imports(path):
        if level > 0:
            # Relative imports must stay inside the domain package.
            depth_in_domain = len(path.relative_to(SRC / "domain").parts) - 1
            assert level <= depth_in_domain + 1, (
                f"{path.name}: relative import escapes alpha.domain"
            )
            continue
        top = module.split(".")[0]
        if top == "alpha":
            assert module == "alpha.domain" or module.startswith("alpha.domain."), (
                f"{path.name}: domain must not import {module}"
            )
        else:
            assert (
                top in sys.stdlib_module_names or top in DOMAIN_ALLOWED_THIRD_PARTY
            ), f"{path.name}: domain must not import {module}"


@pytest.mark.parametrize("path", _python_files(SRC), ids=lambda p: p.name)
def test_gpxpy_only_imported_by_gpx_adapter(path: Path) -> None:
    if (SRC / "gpx") in path.parents:
        return
    for module, _ in _imports(path):
        assert module.split(".")[0] != "gpxpy", (
            f"{path.relative_to(SRC)}: gpxpy may only be imported in alpha.gpx"
        )
